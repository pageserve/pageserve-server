from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.db.models import AuditLog, Project, User
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin:audit"])


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _build_query(project_id, user_id, action, date_from, date_to):
    q = select(AuditLog)
    if project_id:
        q = q.where(AuditLog.project_id == project_id)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if action:
        q = q.where(AuditLog.action == action)
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        q = q.where(AuditLog.created_at >= df)
    if dt:
        q = q.where(AuditLog.created_at <= dt)
    return q


@router.get("/audit")
async def list_audit(
    project_id: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    from_: str | None = None,
    limit: int = 50,
    offset: int = 0,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = _build_query(project_id, user_id, action, from_, None)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.scalars(q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))

    items = []
    user_cache: dict[str, str] = {}
    project_cache: dict[str, str] = {}
    for log in rows:
        user_email = None
        if log.user_id:
            uid = str(log.user_id)
            if uid not in user_cache:
                u = await db.get(User, log.user_id)
                user_cache[uid] = u.email if u else uid
            user_email = user_cache[uid]
        project_name = None
        if log.project_id:
            pid = str(log.project_id)
            if pid not in project_cache:
                p = await db.get(Project, log.project_id)
                project_cache[pid] = p.name if p else pid
            project_name = project_cache[pid]
        items.append(
            {
                "id": str(log.id),
                "user_email": user_email,
                "project_name": project_name,
                "action": log.action,
                "resource": log.resource,
                "detail": log.detail,
                "ip": log.ip_address,
                "created_at": log.created_at,
            }
        )
    return {"total": total, "items": items}


@router.get("/audit/export")
async def export_audit(
    format: str = "csv",
    from_: str | None = None,
    to: str | None = None,
    project_id: str | None = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    q = _build_query(project_id, None, None, from_, to)
    rows = await db.scalars(q.order_by(AuditLog.created_at.desc()))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "user_id", "project_id", "action", "resource", "ip", "created_at"])
    for log in rows:
        writer.writerow(
            [
                str(log.id),
                str(log.user_id) if log.user_id else "",
                str(log.project_id) if log.project_id else "",
                log.action,
                log.resource or "",
                log.ip_address or "",
                log.created_at.isoformat() if log.created_at else "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
    )
