from __future__ import annotations

import logging

from app.db.models import AuditLog
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("pageserve.audit")


async def log_action(
    user_id: str | None = None,
    project_id: str | None = None,
    action: str = "",
    resource: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """Insert an audit row. Intended to be scheduled via services.background.spawn(...)."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                AuditLog(
                    user_id=user_id,
                    project_id=project_id,
                    action=action,
                    resource=resource,
                    detail=detail or {},
                    ip_address=ip,
                )
            )
            await db.commit()
    except Exception as e:  # noqa: BLE001 — auditing must never raise
        logger.error("Audit log failed: %s", e)
