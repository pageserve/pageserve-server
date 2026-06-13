from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.jwt import create_access_token, create_refresh_token, hash_refresh_token
from app.auth.password import hash_password, verify_password
from app.config import settings
from app.db.models import RefreshToken, User
from app.db.session import get_db, get_redis
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    UpdateMeRequest,
    UserInfo,
)
from app.services.audit import log_action
from app.services.background import spawn

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> LoginResponse:
    user = await db.scalar(select(User).where(User.email == req.email.lower().strip()))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Sai email hoặc mật khẩu")
    if not user.is_active:
        raise HTTPException(403, "Tài khoản đã bị vô hiệu hóa")

    access_token = create_access_token(str(user.id), user.email, user.role)
    raw_refresh, rt_hash = create_refresh_token()

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=rt_hash,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
            expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    user.last_login_at = datetime.now(UTC)
    await db.commit()

    spawn(
        log_action(
            user_id=str(user.id),
            action="login",
            ip=request.client.host if request.client else None,
            detail={"email": user.email},
        )
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
        user=UserInfo.from_user(user),
    )


@router.post("/auth/refresh")
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rt_hash = hash_refresh_token(req.refresh_token)
    rt = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == rt_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    if not rt:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = await db.get(User, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")

    rt.last_used_at = datetime.now(UTC)
    await db.commit()

    return {
        "access_token": create_access_token(str(user.id), user.email, user.role),
        "token_type": "bearer",
        "expires_in": settings.JWT_EXPIRE_HOURS * 3600,
    }


@router.post("/auth/logout", status_code=204)
async def logout(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    rt_hash = hash_refresh_token(req.refresh_token)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == rt_hash, RefreshToken.user_id == user.id)
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    await redis.delete(f"user:{user.id}")


@router.get("/auth/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user)) -> UserInfo:
    return UserInfo.from_user(user)


@router.put("/auth/me", response_model=UserInfo)
async def update_me(
    req: UpdateMeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserInfo:
    db_user = await db.get(User, user.id)
    if req.full_name is not None:
        db_user.full_name = req.full_name
    if req.avatar_url is not None:
        db_user.avatar_url = req.avatar_url
    await db.commit()
    await redis.delete(f"user:{user.id}")
    return UserInfo.from_user(db_user)


@router.put("/auth/me/password", status_code=204)
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    db_user = await db.get(User, user.id)
    if not verify_password(req.current_password, db_user.password_hash):
        raise HTTPException(400, "Current password is incorrect")

    db_user.password_hash = hash_password(req.new_password)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    await redis.delete(f"user:{user.id}")
