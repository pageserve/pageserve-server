from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.password import hash_password
from app.db.models import ProjectMember, User
from app.db.session import get_db, get_redis
from app.schemas import CreateUserRequest, UpdateUserRequest, UserInfo

router = APIRouter(prefix="/admin", tags=["admin:users"])


@router.get("/users", response_model=list[UserInfo])
async def list_users(
    role: str | None = None,
    active: bool = True,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserInfo]:
    q = select(User)
    if role:
        q = q.where(User.role == role)
    if active:
        q = q.where(User.is_active == True)  # noqa: E712
    users = await db.scalars(q.order_by(User.created_at.desc()))
    return [UserInfo.from_user(u) for u in users]


@router.post("/users", status_code=201)
async def create_user(
    req: CreateUserRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    existing = await db.scalar(select(User).where(User.email == req.email.lower().strip()))
    if existing:
        raise HTTPException(409, "Email đã tồn tại")

    password = req.password or secrets.token_urlsafe(12)
    user = User(
        email=req.email.lower().strip(),
        password_hash=hash_password(password),
        full_name=req.full_name,
        role=req.role,
        created_by=admin.id,
    )
    db.add(user)
    await db.flush()

    for pid in req.project_ids:
        db.add(ProjectMember(user_id=user.id, project_id=pid))
    await db.commit()

    info = UserInfo.from_user(user).model_dump()
    info["temp_password"] = password if not req.password else None
    return info


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    memberships = await db.scalars(select(ProjectMember).where(ProjectMember.user_id == user_id))
    info = UserInfo.from_user(user).model_dump()
    info["memberships"] = [{"project_id": str(m.project_id), "role": m.role} for m in memberships]
    return info


@router.put("/users/{user_id}", response_model=UserInfo)
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserInfo:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if str(user.id) == str(admin.id) and req.role == "member":
        raise HTTPException(400, "Không thể tự hạ quyền admin")

    if req.role is not None:
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.full_name is not None:
        user.full_name = req.full_name
    await db.commit()
    await redis.delete(f"user:{user_id}")
    return UserInfo.from_user(user)


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if str(user.id) == str(admin.id):
        raise HTTPException(400, "Không thể tự vô hiệu hóa tài khoản")
    user.is_active = False
    await db.commit()
    await redis.delete(f"user:{user_id}")
