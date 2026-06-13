from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_scope
from app.db.models import Document
from app.db.session import AsyncSessionLocal, get_db, get_redis
from app.schemas import QueryRequest
from app.services import rag
from app.services.audit import log_action
from app.services.background import spawn

router = APIRouter(prefix="/v1", tags=["query"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _resolve_doc_ids(req: QueryRequest, project_id, db: AsyncSession) -> list[str]:
    doc_ids = req.doc_ids or ([req.doc_id] if req.doc_id else [])
    if not doc_ids:
        raise HTTPException(400, "Cần doc_id hoặc doc_ids")
    # Validate ownership + readiness in a single query (audit H8).
    rows = await db.scalars(
        select(Document.id).where(
            Document.id.in_(doc_ids),
            Document.project_id == project_id,
            Document.status == "completed",
        )
    )
    found = {str(d) for d in rows}
    missing = [d for d in doc_ids if d not in found]
    if missing:
        raise HTTPException(404, f"Document {missing[0]} không tồn tại hoặc chưa sẵn sàng")
    return [d for d in doc_ids if d in found]


@router.post("/query")
async def query(
    req: QueryRequest,
    proj=Depends(require_scope("query")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    project, data = proj
    doc_ids = await _resolve_doc_ids(req, project.id, db)
    result = await rag.run_query(doc_ids, req.question, redis)

    spawn(
        log_action(
            user_id=data.get("user_id"),
            project_id=str(project.id),
            action="query",
            resource=",".join(doc_ids),
            detail={"question": req.question[:100], "cached": result.get("cached")},
        )
    )
    return {"doc_ids": doc_ids, "question": req.question, **result}


@router.post("/query/stream")
async def query_stream(
    req: QueryRequest,
    proj=Depends(require_scope("query")),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    project, data = proj
    # Validate with a short-lived session so we DON'T hold a DB connection
    # for the whole stream (the agent loop opens its own sessions per fetch).
    async with AsyncSessionLocal() as db:
        doc_ids = await _resolve_doc_ids(req, project.id, db)

    spawn(
        log_action(
            user_id=data.get("user_id"),
            project_id=str(project.id),
            action="query",
            resource=",".join(doc_ids),
            detail={"question": req.question[:100], "stream": True},
        )
    )

    async def generate():
        async for line in rag.stream_query(doc_ids, req.question, "answer", redis):
            yield line

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)
