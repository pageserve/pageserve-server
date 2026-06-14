from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.models import Document, PlaygroundHistory, Project, ProjectMember, User
from app.db.session import AsyncSessionLocal, get_db, get_redis
from app.schemas import PlaygroundQueryRequest
from app.services import rag
from app.services.background import spawn

router = APIRouter(prefix="/admin/playground", tags=["admin:playground"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _check_project(user: User, project_id: str, db: AsyncSession) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if user.role != "admin":
        member = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.user_id == user.id, ProjectMember.project_id == project_id
            )
        )
        if not member:
            raise HTTPException(403, "No access to this project")
    return project


async def _resolve_doc_ids(project_id: str, doc_ids: list[str], db: AsyncSession) -> list[str]:
    """Empty list => all completed docs of the project."""
    q = select(Document).where(Document.project_id == project_id, Document.status == "completed")
    if doc_ids:
        q = q.where(Document.id.in_(doc_ids))
    docs = await db.scalars(q)
    return [str(d.id) for d in docs]


async def _save_history(user_id, project_id, doc_ids, question, mode, response, elapsed_ms) -> None:
    async with AsyncSessionLocal() as db:
        db.add(
            PlaygroundHistory(
                user_id=user_id,
                project_id=project_id,
                doc_ids=doc_ids,
                question=question,
                mode=mode,
                response=response,
                elapsed_ms=elapsed_ms,
            )
        )
        await db.commit()


@router.post("/query")
async def playground_query(
    req: PlaygroundQueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    await _check_project(user, req.project_id, db)
    doc_ids = await _resolve_doc_ids(req.project_id, req.doc_ids, db)
    if not doc_ids:
        raise HTTPException(400, "Không có tài liệu đã hoàn tất index để query")

    start = time.time()
    if req.mode == "search":
        retr = await rag.run_retrieve(doc_ids, req.question, redis)
        elapsed_ms = retr.get("elapsed_ms", int((time.time() - start) * 1000))
        response = {"mode": "search", "elapsed_ms": elapsed_ms, "results": retr["results"]}
    else:
        result = await rag.run_query(doc_ids, req.question, redis)
        elapsed_ms = result["elapsed_ms"]
        response = {"mode": "answer", **result}

    spawn(
        _save_history(
            user.id,
            req.project_id,
            doc_ids,
            req.question,
            req.mode,
            response,
            elapsed_ms,
        )
    )
    return response


@router.post("/query/stream")
async def playground_query_stream(
    req: PlaygroundQueryRequest,
    user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    # Short-lived session for validation only — not held during the stream.
    async with AsyncSessionLocal() as db:
        await _check_project(user, req.project_id, db)
        doc_ids = await _resolve_doc_ids(req.project_id, req.doc_ids, db)
    if not doc_ids:
        raise HTTPException(400, "Không có tài liệu đã hoàn tất index để query")

    async def generate():
        # Forward SSE lines while accumulating the result so we can save history.
        answer, sources, results = "", [], []
        t0 = time.time()
        async for line in rag.stream_query(doc_ids, req.question, req.mode, redis):
            yield line
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "token":
                answer += ev.get("content", "")
            elif kind == "sources":
                sources = ev.get("sources", [])
            elif kind == "done" and req.mode == "search":
                results = ev.get("results", [])
        elapsed_ms = int((time.time() - t0) * 1000)
        response = (
            {"mode": "search", "results": results, "elapsed_ms": elapsed_ms}
            if req.mode == "search"
            else {"mode": "answer", "answer": answer, "sources": sources, "elapsed_ms": elapsed_ms}
        )
        spawn(_save_history(user.id, req.project_id, doc_ids, req.question, req.mode, response, elapsed_ms))

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/history")
async def history(
    project_id: str | None = None,
    limit: int = 20,
    starred: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    q = select(PlaygroundHistory).where(PlaygroundHistory.user_id == user.id)
    if project_id:
        q = q.where(PlaygroundHistory.project_id == project_id)
    if starred:
        q = q.where(PlaygroundHistory.starred == True)  # noqa: E712
    rows = await db.scalars(q.order_by(PlaygroundHistory.created_at.desc()).limit(limit))
    return [
        {
            "id": str(h.id),
            "question": h.question,
            "mode": h.mode,
            "elapsed_ms": h.elapsed_ms,
            "starred": h.starred,
            "created_at": h.created_at,
            "doc_ids": [str(d) for d in (h.doc_ids or [])],
        }
        for h in rows
    ]


@router.get("/history/{history_id}")
async def history_detail(
    history_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    h = await db.get(PlaygroundHistory, history_id)
    if not h or str(h.user_id) != str(user.id):
        raise HTTPException(404, "Not found")
    return {
        "id": str(h.id),
        "question": h.question,
        "mode": h.mode,
        "elapsed_ms": h.elapsed_ms,
        "starred": h.starred,
        "created_at": h.created_at,
        "doc_ids": [str(d) for d in (h.doc_ids or [])],
        "response": h.response,
    }


@router.post("/history/{history_id}/star")
async def toggle_star(
    history_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    h = await db.get(PlaygroundHistory, history_id)
    if not h or str(h.user_id) != str(user.id):
        raise HTTPException(404, "Not found")
    h.starred = not h.starred
    await db.commit()
    return {"starred": h.starred}


@router.delete("/history/{history_id}", status_code=204)
async def delete_history(
    history_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    h = await db.get(PlaygroundHistory, history_id)
    if not h or str(h.user_id) != str(user.id):
        raise HTTPException(404, "Not found")
    await db.delete(h)
    await db.commit()
