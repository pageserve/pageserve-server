from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_key import generate_key_pair
from app.auth.deps import require_scope
from app.db.models import ApiKey
from app.db.session import get_db, get_redis
from app.schemas import CreateKeyRequest
from app.services.audit import log_action
from app.services.background import spawn

router = APIRouter(prefix="/v1/keys", tags=["keys"])


@router.post("", status_code=201)
async def create_key(
    req: CreateKeyRequest,
    proj=Depends(require_scope("create_key")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project, data = proj
    kp = generate_key_pair(req.key_type)

    key = ApiKey(
        project_id=project.id,
        user_id=data["user_id"],
        name=req.name,
        public_key=kp["public_key"],
        secret_hash=kp["secret_hash"],
        secret_prefix=kp["secret_prefix"],
        key_type=req.key_type,
        scopes=req.scopes,
        expires_at=req.expires_at,
    )
    db.add(key)
    await db.commit()

    spawn(
        log_action(
            user_id=data["user_id"],
            project_id=str(project.id),
            action="key_create",
            resource=kp["public_key"],
            detail={"name": req.name, "key_type": req.key_type},
        )
    )

    return {
        "id": str(key.id),
        "name": key.name,
        "public_key": kp["public_key"],
        "secret_key": kp["secret_key"],  # shown ONCE
        "secret_prefix": kp["secret_prefix"],
        "key_type": key.key_type,
        "scopes": key.scopes,
        "expires_at": key.expires_at,
        "created_at": key.created_at,
    }


@router.get("")
async def list_keys(
    proj=Depends(require_scope("list_keys")), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    project, _ = proj
    keys = await db.scalars(
        select(ApiKey).where(ApiKey.project_id == project.id).order_by(ApiKey.created_at.desc())
    )
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "public_key": k.public_key,
            "secret_prefix": k.secret_prefix,
            "key_type": k.key_type,
            "scopes": list(k.scopes or []),
            "is_active": k.is_active,
            "request_count": k.request_count,
            "last_used_at": k.last_used_at,
            "expires_at": k.expires_at,
            "created_at": k.created_at,
        }
        for k in keys
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: str,
    proj=Depends(require_scope("revoke_key")),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    project, _ = proj
    key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project.id)
    )
    if not key:
        raise HTTPException(404, "Key not found")
    key.is_active = False
    await db.commit()
    await redis.delete(f"pk:{key.public_key}")
