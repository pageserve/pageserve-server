"""FastAPI application entrypoint for pageserve-server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pageserve")


def run_migrations() -> None:
    """Apply Alembic migrations up to head."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. Set LLM env vars BEFORE any pageindex_src import happens downstream
    os.environ["OPENAI_API_BASE"] = settings.LLM_BASE_URL
    os.environ["OPENAI_API_KEY"] = settings.LLM_API_KEY or "empty"

    # 2. Ensure data directories exist
    for d in (
        settings.FILES_DIR,
        settings.UPLOAD_DIR,
        str(Path(settings.FILES_DIR) / "workspace"),
    ):
        Path(d).mkdir(parents=True, exist_ok=True)

    # 3. Run DB migrations in a worker thread — Alembic's env.py uses asyncio.run(),
    #    which cannot run inside this already-running event loop.
    try:
        await asyncio.to_thread(run_migrations)
    except Exception as e:  # noqa: BLE001
        logger.error("Migration failed: %s", e)
        raise

    # 4. Seed default admin
    from app.db.session import AsyncSessionLocal
    from app.services.seed import seed_admin

    async with AsyncSessionLocal() as db:
        await seed_admin(db)

    # 5. Verify Redis connectivity
    from app.db.session import get_redis

    redis = await get_redis()
    await redis.ping()

    logger.info("pageserve-server started (v%s)", settings.VERSION)
    yield


app = FastAPI(title="pageserve-server", version=settings.VERSION, lifespan=lifespan)

# Routers
from app.routes.admin_audit import router as admin_audit_router  # noqa: E402
from app.routes.admin_documents import router as admin_documents_router  # noqa: E402
from app.routes.admin_keys import router as admin_keys_router  # noqa: E402
from app.routes.admin_playground import router as admin_playground_router  # noqa: E402
from app.routes.admin_projects import router as admin_projects_router  # noqa: E402
from app.routes.admin_users import router as admin_users_router  # noqa: E402
from app.routes.auth import router as auth_router  # noqa: E402
from app.routes.documents import router as documents_router  # noqa: E402
from app.routes.keys import router as keys_router  # noqa: E402
from app.routes.query import router as query_router  # noqa: E402
from app.routes.stats import router as stats_router  # noqa: E402
from app.routes.webhooks import router as webhooks_router  # noqa: E402

app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(admin_projects_router)
app.include_router(admin_playground_router)
app.include_router(admin_audit_router)
app.include_router(admin_documents_router)
app.include_router(admin_keys_router)
app.include_router(stats_router)
app.include_router(documents_router)
app.include_router(keys_router)
app.include_router(query_router)
app.include_router(webhooks_router)

# Static mounts (created in lifespan; create here too so mount does not fail at import)
Path(settings.FILES_DIR).mkdir(parents=True, exist_ok=True)
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"
if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
app.mount("/files", StaticFiles(directory=settings.FILES_DIR), name="files")


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/index.html")


async def _llm_health() -> dict[str, Any]:
    """Probe the LLM backend, caching the result in Redis for 60s."""
    from app.db.session import get_redis

    try:
        redis = await get_redis()
        cached = await redis.get("health:llm")
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001
        redis = None

    try:
        t0 = time.time()
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.LLM_BASE_URL}/models", timeout=5)
        result = {
            "status": "ok" if r.status_code == 200 else "degraded",
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except Exception:  # noqa: BLE001
        result = {"status": "unreachable"}

    if redis is not None:
        try:
            await redis.setex("health:llm", 60, json.dumps(result))
        except Exception:  # noqa: BLE001
            pass
    return result


@app.get("/health")
async def health() -> dict[str, Any]:
    from app.db.session import AsyncSessionLocal, get_redis

    checks: dict[str, Any] = {}
    overall = "ok"

    # Database
    try:
        t0 = time.time()
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = {
            "status": "ok",
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        checks["database"] = {"status": "error", "error": str(e)}
        overall = "degraded"

    # Redis
    try:
        t0 = time.time()
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = {"status": "ok", "latency_ms": int((time.time() - t0) * 1000)}
        queue_len = await redis.llen("arq:queue")
    except Exception as e:  # noqa: BLE001
        checks["redis"] = {"status": "error", "error": str(e)}
        overall = "degraded"
        queue_len = None

    # LLM — cached 60s so the 30s Docker healthcheck doesn't ping the LLM every time.
    checks["llm"] = await _llm_health()

    # Storage
    disk = psutil.disk_usage(settings.FILES_DIR)
    checks["storage"] = {
        "status": "ok" if disk.free > 1 * (1024**3) else "low",
        "free_gb": round(disk.free / (1024**3), 1),
        "total_gb": round(disk.total / (1024**3), 1),
    }

    ram = psutil.virtual_memory()
    return {
        "status": overall,
        "version": settings.VERSION,
        "checks": checks,
        "queue": {"pending": queue_len, "max_jobs": settings.worker_max_jobs},
        "system": {
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "ram_available_gb": round(ram.available / (1024**3), 1),
            "ram_used_pct": round((1 - ram.available / ram.total) * 100, 1),
            "max_file_mb": settings.max_file_size_mb,
        },
    }
