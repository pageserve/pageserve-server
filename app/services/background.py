"""Safe fire-and-forget task scheduling.

asyncio does not keep a strong reference to bare `create_task(...)` results, so
tasks can be garbage-collected mid-flight and their exceptions silently lost.
`spawn()` keeps a reference until completion and logs any failure, so audit logs,
usage counters, and webhooks don't vanish under GC pressure.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("pageserve.bg")

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule a coroutine, retaining a reference and logging failures."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("Background task failed: %r", exc)
