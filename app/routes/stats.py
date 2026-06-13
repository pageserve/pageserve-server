from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_scope
from app.db.models import AuditLog, Document, PlaygroundHistory, User
from app.db.session import get_db

router = APIRouter(tags=["stats"])

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _period_days(period: str) -> int:
    return _PERIOD_DAYS.get(period, 7)


async def _compute_stats(db: AsyncSession, project_id: str | None, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)

    # Documents by status
    doc_q = select(Document.status, func.count()).group_by(Document.status)
    if project_id:
        doc_q = doc_q.where(Document.project_id == project_id)
    docs_by_status = {status: count for status, count in (await db.execute(doc_q)).all()}

    # Query history within the period
    ph_q = select(PlaygroundHistory).where(PlaygroundHistory.created_at >= since)
    if project_id:
        ph_q = ph_q.where(PlaygroundHistory.project_id == project_id)
    history_rows = list(await db.scalars(ph_q))

    queries_total = len(history_rows)
    latencies = [h.elapsed_ms for h in history_rows if h.elapsed_ms]
    avg_latency_ms = int(sum(latencies) / len(latencies)) if latencies else 0

    # Per-day query buckets
    by_day: dict[str, int] = {}
    for h in history_rows:
        day = h.created_at.date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    queries_by_day = [{"date": d, "count": c} for d, c in sorted(by_day.items())]

    # Uploads by day from the audit log
    upload_q = select(AuditLog.created_at).where(
        AuditLog.action == "upload", AuditLog.created_at >= since
    )
    if project_id:
        upload_q = upload_q.where(AuditLog.project_id == project_id)
    uploads: dict[str, int] = {}
    for (created_at,) in (await db.execute(upload_q)).all():
        day = created_at.date().isoformat()
        uploads[day] = uploads.get(day, 0) + 1
    uploads_by_day = [{"date": d, "count": c} for d, c in sorted(uploads.items())]

    return {
        "queries_total": queries_total,
        "queries_by_day": queries_by_day,
        "uploads_by_day": uploads_by_day,
        "top_documents": [],  # reserved — requires per-doc query tracking
        "avg_latency_ms": avg_latency_ms,
        "cache_hit_rate": 0.0,
        "documents_by_status": docs_by_status,
    }


@router.get("/admin/stats")
async def admin_stats(
    project_id: str | None = None,
    period: str = "7d",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _compute_stats(db, project_id, _period_days(period))


@router.get("/v1/stats")
async def v1_stats(
    period: str = "7d",
    proj=Depends(require_scope("get_stats")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    return await _compute_stats(db, str(project.id), _period_days(period))
