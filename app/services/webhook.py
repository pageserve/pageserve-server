from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Webhook
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("pageserve.webhook")


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(
    url: str, secret: str | None, payload: dict[str, Any], timeout: float = 10.0
) -> dict[str, Any]:
    """Deliver a single webhook. Returns {delivered, status_code, response}."""
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-PageServe-Signature"] = "sha256=" + _sign(secret, body)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body, headers=headers)
        return {
            "delivered": resp.status_code < 400,
            "status_code": resp.status_code,
            "response": resp.text[:500],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Webhook delivery to %s failed: %s", url, e)
        return {"delivered": False, "status_code": 0, "response": str(e)[:500]}


async def fire_event(db: AsyncSession, doc_id: str, event: str) -> None:
    """Fan out an event to every active webhook subscribed to it for the doc's project."""
    doc = await db.get(Document, doc_id)
    if not doc:
        return

    hooks = await db.scalars(
        select(Webhook).where(
            Webhook.project_id == doc.project_id,
            Webhook.is_active == True,  # noqa: E712
        )
    )
    payload = {
        "event": event,
        "doc_id": str(doc.id),
        "project_id": str(doc.project_id),
        "name": doc.name,
        "status": doc.status,
    }
    for hook in hooks:
        if event in (hook.events or []):
            await deliver(hook.url, hook.secret, payload)


async def fire_event_standalone(doc_id: str, event: str) -> None:
    """Same as fire_event but opens its own session (safe for create_task in the worker)."""
    async with AsyncSessionLocal() as db:
        await fire_event(db, doc_id, event)
