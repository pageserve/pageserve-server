from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
import psutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_project_access
from app.config import settings
from app.db.models import Document, Page, ProjectMember, Structure, User
from app.db.session import AsyncSessionLocal, get_db, get_redis
from app.routes.documents import (
    SSE_HEADERS,
    _doc_dict,
    _enqueue,
    _find_node,
    _reset_for_reindex,
    _trim_tree,
)
from app.services.audit import log_action
from app.services.background import spawn
from app.services.cache import invalidate_doc_caches
from app.services.rag import _parse_pages

router = APIRouter(prefix="/admin", tags=["admin:documents"])


async def _doc_with_access(doc_id: str, user: User, db: AsyncSession) -> Document:
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if user.role != "admin":
        member = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.user_id == user.id,
                ProjectMember.project_id == doc.project_id,
            )
        )
        if not member:
            raise HTTPException(403, "No access to this document")
    return doc


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    doc = await _doc_with_access(doc_id, user, db)
    return {**_doc_dict(doc), "project_id": str(doc.project_id)}


@router.get("/documents/{doc_id}/structure")
async def get_document_structure(
    doc_id: str,
    depth: int = 0,
    node_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    await _doc_with_access(doc_id, user, db)
    row = await db.scalar(select(Structure).where(Structure.doc_id == doc_id))
    if not row:
        raise HTTPException(404, "Structure not ready")
    if node_id:
        node = _find_node(row.tree, node_id)
        if not node:
            raise HTTPException(404, "Node not found")
        return node
    if depth and depth > 0:
        return _trim_tree(row.tree, depth)
    return row.tree


@router.get("/documents/{doc_id}/pages/{pages}")
async def get_document_pages(
    doc_id: str,
    pages: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await _doc_with_access(doc_id, user, db)
    page_nums = _parse_pages(pages)
    rows = await db.scalars(
        select(Page)
        .where(Page.doc_id == doc_id, Page.page_num.in_(page_nums))
        .order_by(Page.page_num)
    )
    return [{"page": r.page_num, "content": r.content} for r in rows]


@router.get("/projects/{project_id}/documents")
async def list_project_documents(
    project_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx=Depends(require_project_access()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = select(Document).where(Document.project_id == project_id)
    if status:
        q = q.where(Document.status == status)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.scalars(q.order_by(Document.created_at.desc()).limit(limit).offset(offset))
    return {"total": total, "documents": [_doc_dict(d) for d in rows]}


@router.post("/projects/{project_id}/documents", status_code=202)
async def upload_project_document(
    project_id: str,
    file: UploadFile,
    ctx=Depends(require_project_access()),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    user, project = ctx
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ chấp nhận file PDF")

    ram = psutil.virtual_memory()
    if ram.available / ram.total < 0.15:
        raise HTTPException(503, "Hệ thống đang quá tải (RAM). Thử lại sau.")

    existing = await db.scalar(
        select(Document).where(
            Document.project_id == project_id,
            Document.name == file.filename,
            Document.status != "failed",
        )
    )
    if existing:
        return {
            "doc_id": str(existing.id),
            "name": existing.name,
            "status": existing.status,
            "cached": True,
        }

    max_size = settings.max_file_size_mb * 1024 * 1024
    tmp_path = Path(settings.UPLOAD_DIR) / f"{uuid4()}.pdf"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)

    total_bytes = 0
    async with aiofiles.open(tmp_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > max_size:
                await f.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(413, f"File quá lớn. Tối đa {settings.max_file_size_mb}MB.")
            await f.write(chunk)

    doc = Document(
        project_id=project_id,
        name=file.filename,
        file_size=total_bytes,
        status="pending",
        created_by=user.id,
    )
    db.add(doc)
    await db.commit()

    dest_path = Path(settings.FILES_DIR) / f"{doc.id}.pdf"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(tmp_path, dest_path)
    tmp_path.unlink(missing_ok=True)

    job = await _enqueue(str(doc.id), str(dest_path))
    spawn(
        log_action(
            user_id=str(user.id),
            project_id=project_id,
            action="upload",
            resource=file.filename,
            detail={"doc_id": str(doc.id)},
        )
    )
    return {
        "doc_id": str(doc.id),
        "name": file.filename,
        "status": "pending",
        "job_id": job.job_id if job else None,
    }


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    doc = await _doc_with_access(doc_id, user, db)
    # Signal any in-flight indexing job to abort at its next checkpoint.
    await redis.setex(f"cancel:{doc_id}", 3600, "1")
    await db.delete(doc)
    await db.commit()
    Path(settings.FILES_DIR, f"{doc_id}.pdf").unlink(missing_ok=True)
    await invalidate_doc_caches(redis, doc_id)


@router.post("/documents/{doc_id}/reindex", status_code=202)
async def reindex_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    await _doc_with_access(doc_id, user, db)
    pdf_path = await _reset_for_reindex(doc_id, db, redis)
    await _enqueue(doc_id, pdf_path)
    return {"doc_id": doc_id, "status": "pending"}


@router.get("/documents/{doc_id}/progress")
async def document_progress(
    doc_id: str,
    user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    # Short session for the access check — don't hold a connection during the stream.
    async with AsyncSessionLocal() as db:
        doc = await _doc_with_access(doc_id, user, db)

    if doc.status in ("completed", "failed"):

        async def done():
            yield f"data: {json.dumps({'status': doc.status, 'progress': 100 if doc.status == 'completed' else 0})}\n\n"

        return StreamingResponse(done(), media_type="text/event-stream", headers=SSE_HEADERS)

    async def stream():
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"index:{doc_id}")
        yield f"data: {json.dumps({'status': doc.status, 'progress': 0})}\n\n"
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield f"data: {message['data']}\n\n"
                    if json.loads(message["data"]).get("status") in (
                        "completed",
                        "failed",
                    ):
                        break
        finally:
            await pubsub.unsubscribe(f"index:{doc_id}")
            await pubsub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
