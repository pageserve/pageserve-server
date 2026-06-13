from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.config import settings
from app.db.models import User

logger = logging.getLogger("pageserve.seed")


async def seed_admin(db: AsyncSession) -> None:
    """Create the default admin if no user exists yet."""
    count = await db.scalar(select(func.count()).select_from(User))
    if count and count > 0:
        return

    admin = User(
        email=settings.ADMIN_EMAIL.lower().strip(),
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        full_name="Administrator",
        role="admin",
    )
    db.add(admin)
    await db.commit()
    logger.info("Default admin created: %s", settings.ADMIN_EMAIL)
    logger.warning("Change the admin password right after the first login!")
