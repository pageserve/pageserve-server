from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_scope
from app.db.models import Webhook
from app.db.session import get_db
from app.schemas import CreateWebhookRequest
from app.services.webhook import deliver

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


def _hook_dict(h: Webhook) -> dict:
    return {
        "id": str(h.id),
        "url": h.url,
        "events": list(h.events or []),
        "is_active": h.is_active,
        "created_at": h.created_at,
    }


@router.get("")
async def list_webhooks(
    proj=Depends(require_scope("list_webhooks")), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    project, _ = proj
    hooks = await db.scalars(select(Webhook).where(Webhook.project_id == project.id))
    return [_hook_dict(h) for h in hooks]


@router.post("", status_code=201)
async def create_webhook(
    req: CreateWebhookRequest,
    proj=Depends(require_scope("create_webhook")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    hook = Webhook(project_id=project.id, url=req.url, secret=req.secret, events=req.events)
    db.add(hook)
    await db.commit()
    return _hook_dict(hook)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    proj=Depends(require_scope("delete_webhook")),
    db: AsyncSession = Depends(get_db),
):
    project, _ = proj
    hook = await db.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.project_id == project.id)
    )
    if not hook:
        raise HTTPException(404, "Webhook not found")
    await db.delete(hook)
    await db.commit()


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    proj=Depends(require_scope("test_webhook")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, _ = proj
    hook = await db.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.project_id == project.id)
    )
    if not hook:
        raise HTTPException(404, "Webhook not found")
    payload = {
        "event": "ping",
        "project_id": str(project.id),
        "message": "pageserve-server test event",
    }
    return await deliver(hook.url, hook.secret, payload)
