from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
import psutil
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_scope
from app.config import settings
from app.db.models import Document, Page, Project, Structure
from app.db.session import AsyncSessionLocal, get_db, get_redis
from app.schemas import BulkDocsRequest
from app.services.audit import log_action
from app.services.background import spawn
from app.services.cache import invalidate_doc_caches
from app.services.rag import _parse_pages

router = APIRouter(prefix="/v1/documents", tags=["documents"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _enqueue(doc_id: str, pdf_path: str) -> Any:
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        return await pool.enqueue_job("index_document_task", doc_id, pdf_path)
    finally:
        await pool.aclose()


def _doc_dict(doc: Document) -> dict:
    return {
        "doc_id": str(doc.id),
        "name": doc.name,
        "status": doc.status,
        "page_count": doc.page_count,
        "file_size": doc.file_size,
        "description": doc.description,
        "tags": list(doc.tags or []),
        "language": doc.language,
        "error_msg": doc.error_msg,
        "created_at": doc.created_at,
    }


@router.post("", status_code=202)
async def upload_document(
    file: UploadFile,
    request: Request,
    proj=Depends(require_scope("upload")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    project, data = proj
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ chấp nhận file PDF")

    # Backpressure — reject when the system is overloaded
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(settings.FILES_DIR)
    queue_len = await redis.llen("arq:queue")
    if ram.available / ram.total < 0.15:
        raise HTTPException(503, "Hệ thống đang quá tải (RAM). Thử lại sau.")
    if disk.free < 1 * (1024**3):
        raise HTTPException(507, "Không đủ dung lượng lưu trữ.")
    if queue_len > 20:
        raise HTTPException(503, f"Hàng đợi đang có {queue_len} tài liệu. Thử lại sau.")

    # Dedup by name within the project
    existing = await db.scalar(
        select(Document).where(
            Document.project_id == project.id,
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
            "message": f"File đã được index (status: {existing.status})",
        }

    # Stream to a temp file in 1MB chunks, enforcing the size limit
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
                raise HTTPException(
                    413,
                    f"File quá lớn. Tối đa {settings.max_file_size_mb}MB "
                    f"(server {psutil.virtual_memory().total // (1024**3)}GB RAM).",
                )
            await f.write(chunk)

    doc = Document(
        project_id=project.id,
        name=file.filename,
        file_size=total_bytes,
        status="pending",
        created_by=data.get("user_id"),
    )
    db.add(doc)
    await db.commit()

    dest_path = Path(settings.FILES_DIR) / f"{doc.id}.pdf"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(tmp_path, dest_path)
    tmp_path.unlink(missing_ok=True)

    job = await _enqueue(str(doc.id), str(dest_path))
    queue_position = await redis.llen("arq:queue")

    spawn(
        log_action(
            user_id=data.get("user_id"),
            project_id=str(project.id),
            action="upload",
            resource=file.filename,
            detail={"doc_id": str(doc.id), "file_size": total_bytes},
            ip=request.client.host if request.client else None,
        )
    )

    return {
        "doc_id": str(doc.id),
        "name": file.filename,
        "status": "pending",
        "queue_position": queue_position,
        "job_id": job.job_id if job else None,
        "max_file_mb": settings.max_file_size_mb,
    }


@router.get("")
async def list_documents(
    status: str | None = None,
    tags: str | None = None,
    limit: int = 20,
    offset: int = 0,
    proj=Depends(require_scope("list_documents")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    q = select(Document).where(Document.project_id == project.id)
    if status:
        q = q.where(Document.status == status)
    if tags:
        q = q.where(Document.tags.contains([tags]))

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.scalars(q.order_by(Document.created_at.desc()).limit(limit).offset(offset))
    return {"total": total, "documents": [_doc_dict(d) for d in rows]}


async def _get_owned_doc(doc_id: str, project: Project, db: AsyncSession) -> Document:
    doc = await db.scalar(
        select(Document).where(Document.id == doc_id, Document.project_id == project.id)
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    proj=Depends(require_scope("get_document")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    doc = await _get_owned_doc(doc_id, project, db)
    return _doc_dict(doc)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    proj=Depends(require_scope("delete_document")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    project, _ = proj
    doc = await _get_owned_doc(doc_id, project, db)
    # Signal any in-flight indexing job to abort at its next checkpoint.
    await redis.setex(f"cancel:{doc_id}", 3600, "1")
    await db.delete(doc)  # cascades to structure + pages
    await db.commit()
    Path(settings.FILES_DIR, f"{doc_id}.pdf").unlink(missing_ok=True)
    await invalidate_doc_caches(redis, doc_id)


def _trim_tree(nodes: list, max_depth: int, current: int = 0) -> list:
    result = []
    for node in nodes:
        n = {k: v for k, v in node.items() if k != "nodes"}
        sub = node.get("nodes", [])
        if current < max_depth and sub:
            n["nodes"] = _trim_tree(sub, max_depth, current + 1)
        elif sub:
            n["nodes"] = []
            n["has_children"] = True
        result.append(n)
    return result


@router.get("/{doc_id}/structure")
async def get_structure(
    doc_id: str,
    depth: int = 0,
    proj=Depends(require_scope("get_structure")),
    db: AsyncSession = Depends(get_db),
) -> Any:
    project, _ = proj
    await _get_owned_doc(doc_id, project, db)
    row = await db.scalar(select(Structure).where(Structure.doc_id == doc_id))
    if not row:
        raise HTTPException(404, "Structure not ready")
    if depth and depth > 0:
        return _trim_tree(row.tree, depth)
    return row.tree


def _find_node(nodes: list, node_id: str):
    for node in nodes:
        if node.get("node_id") == node_id:
            return node
        found = _find_node(node.get("nodes", []), node_id)
        if found:
            return found
    return None


@router.get("/{doc_id}/structure/{node_id}")
async def get_structure_node(
    doc_id: str,
    node_id: str,
    proj=Depends(require_scope("get_structure")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    await _get_owned_doc(doc_id, project, db)
    row = await db.scalar(select(Structure).where(Structure.doc_id == doc_id))
    if not row:
        raise HTTPException(404, "Structure not ready")
    node = _find_node(row.tree, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.get("/{doc_id}/pages/{pages}")
async def get_pages(
    doc_id: str,
    pages: str,
    proj=Depends(require_scope("get_pages")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    project, _ = proj
    await _get_owned_doc(doc_id, project, db)
    page_nums = _parse_pages(pages)
    rows = await db.scalars(
        select(Page)
        .where(Page.doc_id == doc_id, Page.page_num.in_(page_nums))
        .order_by(Page.page_num)
    )
    return [{"page": r.page_num, "content": r.content} for r in rows]


@router.get("/{doc_id}/progress")
async def progress(
    doc_id: str,
    proj=Depends(require_scope("get_document")),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    project, _ = proj
    # Short session for the ownership check — don't hold a connection while the
    # SSE stream waits (possibly minutes) for indexing to finish.
    async with AsyncSessionLocal() as db:
        doc = await _get_owned_doc(doc_id, project, db)

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
                    payload = json.loads(message["data"])
                    if payload.get("status") in ("completed", "failed"):
                        break
        finally:
            await pubsub.unsubscribe(f"index:{doc_id}")
            await pubsub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


async def _reset_for_reindex(doc_id: str, db: AsyncSession, redis: Redis) -> str:
    pdf_path = Path(settings.FILES_DIR) / f"{doc_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "PDF file not found. Hãy upload lại.")
    await db.execute(delete(Structure).where(Structure.doc_id == doc_id))
    await db.execute(delete(Page).where(Page.doc_id == doc_id))
    await db.execute(
        update(Document).where(Document.id == doc_id).values(status="pending", error_msg=None)
    )
    await db.commit()
    await redis.delete(f"cancel:{doc_id}")  # clear any stale cancel flag
    await invalidate_doc_caches(redis, doc_id)
    return str(pdf_path)


@router.post("/{doc_id}/reindex", status_code=202)
async def reindex_document(
    doc_id: str,
    proj=Depends(require_scope("reindex")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    project, _ = proj
    await _get_owned_doc(doc_id, project, db)
    pdf_path = await _reset_for_reindex(doc_id, db, redis)
    await _enqueue(doc_id, pdf_path)
    return {"doc_id": doc_id, "status": "pending", "message": "Reindex started"}


@router.post("/bulk-delete")
async def bulk_delete(
    req: BulkDocsRequest,
    proj=Depends(require_scope("delete_document")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    project, _ = proj
    deleted = 0
    failed: list[str] = []
    for doc_id in req.doc_ids:
        doc = await db.scalar(
            select(Document).where(Document.id == doc_id, Document.project_id == project.id)
        )
        if not doc:
            failed.append(doc_id)
            continue
        await redis.setex(f"cancel:{doc_id}", 3600, "1")
        await db.delete(doc)
        await db.commit()
        Path(settings.FILES_DIR, f"{doc_id}.pdf").unlink(missing_ok=True)
        await invalidate_doc_caches(redis, doc_id)
        deleted += 1
    return {"deleted": deleted, "failed": failed}


@router.post("/bulk-reindex")
async def bulk_reindex(
    req: BulkDocsRequest,
    proj=Depends(require_scope("reindex")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    project, _ = proj
    queued = 0
    for doc_id in req.doc_ids:
        doc = await db.scalar(
            select(Document).where(Document.id == doc_id, Document.project_id == project.id)
        )
        if not doc:
            continue
        try:
            pdf_path = await _reset_for_reindex(doc_id, db, redis)
        except HTTPException:
            continue
        await _enqueue(doc_id, pdf_path)
        queued += 1
    return {"queued": queued}
