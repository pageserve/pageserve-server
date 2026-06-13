from __future__ import annotations

import os
from typing import Any

import psutil
from arq.connections import RedisSettings

from app.db.session import get_redis
from app.services.indexer import index_document_task, recover_orphaned_documents


def _auto_max_jobs() -> int:
    """Concurrent indexing jobs; WORKER_MAX_JOBS env overrides the RAM heuristic."""
    env_val = os.environ.get("WORKER_MAX_JOBS")
    if env_val:
        return int(env_val)
    ram_gb = psutil.virtual_memory().total / (1024**3)
    if ram_gb <= 4:
        return 1
    if ram_gb <= 8:
        return 2
    if ram_gb <= 16:
        return 3
    return 4


async def startup(ctx: dict[str, Any]) -> None:
    # Shared Redis client; each task opens its own short-lived DB session.
    ctx["redis"] = await get_redis()
    # Recover documents left mid-index by a previous crashed/restarted worker.
    await recover_orphaned_documents()


async def shutdown(ctx: dict[str, Any]) -> None:
    # Nothing to close: Redis is a shared singleton, DB sessions are per-task.
    pass


class WorkerSettings:
    functions = [index_document_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ["REDIS_URL"])
    max_jobs = _auto_max_jobs()
    max_tries = 3  # retry up to 3 times
    retry_delay = 30  # wait 30s between retries
    job_timeout = 1800  # 30 min timeout per job
    keep_result = 3600  # keep results for 1 hour
    queue_name = "arq:queue"
