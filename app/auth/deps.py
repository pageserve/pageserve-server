from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_key import hash_secret
from app.auth.jwt import decode_access_token
from app.config import settings
from app.db.models import ApiKey, Project, ProjectMember, User
from app.db.session import AsyncSessionLocal, get_db, get_redis
from app.services.background import spawn

bearer = HTTPBearer(auto_error=False)

# Data extracted from a verified API key pair.
KeyData = dict[str, Any]
# What an API-key dependency resolves to.
ProjectKey = tuple[Project, KeyData]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    if not credentials:
        raise HTTPException(401, "Authentication required")

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError()
    except (JWTError, ValueError):
        raise HTTPException(401, "Invalid or expired token") from None

    cache_key = f"user:{user_id}"
    cached = await redis.get(cache_key)
    if cached:
        return User(**json.loads(cached))

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")

    await redis.setex(
        cache_key,
        300,
        json.dumps(
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_active": user.is_active,
                "avatar_url": user.avatar_url,
            }
        ),
    )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def require_project_access() -> Callable[..., Awaitable[tuple[User, Project]]]:
    """Dependency factory — verify the user belongs to the path's project. Admin bypasses."""

    async def check(
        project_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> tuple[User, Project]:
        project = await db.get(Project, project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        if user.role == "admin":
            return user, project

        member = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.user_id == user.id,
                ProjectMember.project_id == project_id,
            )
        )
        if not member:
            raise HTTPException(403, "No access to this project")
        return user, project

    return check


SCOPE_MAP: dict[str, list[str]] = {
    "read": [
        "list_documents",
        "get_document",
        "get_structure",
        "get_pages",
        "query",
        "list_keys",
        "get_stats",
        "list_webhooks",
    ],
    "write": [
        "upload",
        "delete_document",
        "reindex",
        "create_key",
        "revoke_key",
        "create_webhook",
        "delete_webhook",
        "test_webhook",
    ],
}


async def _record_key_usage(redis: Redis, key_id: str, ip: str | None) -> None:
    """Count usage in Redis (cheap) and flush to DB at most once per minute per key.

    Avoids a DB write + same-row lock contention on every single API request (audit H2).
    """
    await redis.incr(f"ku:cnt:{key_id}")
    if ip:
        await redis.set(f"ku:ip:{key_id}", ip)
    # Throttle: the first request after the 60s lock expires triggers a flush.
    if await redis.set(f"ku:lock:{key_id}", "1", nx=True, ex=60):
        spawn(_flush_key_usage(key_id))


async def _flush_key_usage(key_id: str) -> None:
    """Fold the accumulated Redis counter into the api_keys row."""
    redis = await get_redis()
    raw = await redis.getdel(f"ku:cnt:{key_id}")  # atomic read + reset
    delta = int(raw or 0)
    ip = await redis.get(f"ku:ip:{key_id}")
    if delta == 0 and ip is None:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(
                last_used_at=datetime.now(UTC),
                last_used_ip=ip,
                request_count=ApiKey.request_count + delta,
            )
        )
        await db.commit()


async def get_project_from_keypair(
    request: Request,
    authorization: str | None = Header(None),
    x_public_key: str | None = Header(None, alias="X-PageServe-Public-Key"),
    x_secret_key: str | None = Header(None, alias="X-PageServe-Secret-Key"),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ProjectKey:
    """Return (project, key_data). key_data = {project_id, scopes, key_id, user_id}."""
    # Parse credentials — Basic Auth or X-PageServe-* headers
    if authorization and authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:]).decode()
            public_key, secret_key = decoded.split(":", 1)
        except Exception:
            raise HTTPException(401, "Invalid Basic Auth format") from None
    elif x_public_key and x_secret_key:
        public_key, secret_key = x_public_key, x_secret_key
    else:
        raise HTTPException(401, "API key required. Use Basic Auth or X-PageServe-* headers")

    # Optional rate limiting
    if settings.RATE_LIMIT_PER_MINUTE > 0:
        minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
        rl_key = f"ratelimit:{public_key}:{minute}"
        count = await redis.incr(rl_key)
        if count == 1:
            await redis.expire(rl_key, 60)
        if count > settings.RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                429,
                f"Rate limit: {settings.RATE_LIMIT_PER_MINUTE} requests/phút",
                headers={"Retry-After": "60"},
            )

    # Redis cache lookup
    cache_key = f"pk:{public_key}"
    cached = await redis.get(cache_key)
    if cached:
        data = json.loads(cached)
    else:
        key_row = await db.scalar(
            select(ApiKey).where(ApiKey.public_key == public_key, ApiKey.is_active)  # noqa: E712
        )
        if not key_row:
            raise HTTPException(401, "Invalid API key")
        if key_row.expires_at and key_row.expires_at < datetime.now(UTC):
            raise HTTPException(401, "API key expired")

        data = {
            "secret_hash": key_row.secret_hash,
            "project_id": str(key_row.project_id),
            "scopes": list(key_row.scopes),
            "key_id": str(key_row.id),
            "user_id": str(key_row.user_id),
        }
        await redis.setex(cache_key, 300, json.dumps(data))

    # Verify secret
    if hash_secret(secret_key) != data["secret_hash"]:
        raise HTTPException(401, "Invalid secret key")

    # Record usage in Redis (cheap); flushed to DB at most once/min per key.
    spawn(_record_key_usage(redis, data["key_id"], request.client.host if request.client else None))

    project = await db.get(Project, data["project_id"])
    if not project:
        raise HTTPException(404, "Project not found")

    return project, data


def require_scope(action: str) -> Callable[..., Awaitable[ProjectKey]]:
    """Dependency factory — ensure the key's scopes permit `action`."""

    async def check(
        proj_data: ProjectKey = Depends(get_project_from_keypair),
    ) -> ProjectKey:
        project, data = proj_data
        allowed = [act for scope in data["scopes"] for act in SCOPE_MAP.get(scope, [])]
        if action not in allowed:
            need = "write" if action in SCOPE_MAP["write"] else "read"
            raise HTTPException(403, f"Key không có permission '{action}'. Cần scope: {need}")
        return project, data

    return check
