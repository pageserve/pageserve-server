from __future__ import annotations

import hashlib
import json
from typing import Any

from redis.asyncio import Redis


def query_cache_key(doc_ids: list[str], question: str) -> str:
    """Stable key for a (docs, question) pair. Normalised to raise hit rate."""
    normalized = " ".join(question.strip().lower().split())
    key = "|".join(sorted(doc_ids)) + ":" + normalized
    return "qcache:" + hashlib.md5(key.encode()).hexdigest()


QUERY_TTL = 1800  # 30 minutes


async def get_cached_query(redis: Redis, cache_key: str) -> dict[str, Any] | None:
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    return None


async def cache_query_result(
    redis: Redis, cache_key: str, result: dict[str, Any], doc_ids: list[str]
) -> None:
    """Cache a query result for 30 minutes and register it under each doc for invalidation."""
    await redis.setex(cache_key, QUERY_TTL, json.dumps(result, ensure_ascii=False))
    # Track which query-cache keys touch each doc so they can be purged on reindex/delete.
    for doc_id in doc_ids:
        await redis.sadd(f"qkeys:{doc_id}", cache_key)
        await redis.expire(f"qkeys:{doc_id}", QUERY_TTL)


async def invalidate_doc_caches(redis: Redis, doc_id: str) -> None:
    """Drop structure/meta AND every cached query result that referenced this doc."""
    await redis.delete(f"structure:{doc_id}")
    await redis.delete(f"doc:meta:{doc_id}")
    qkeys = await redis.smembers(f"qkeys:{doc_id}")
    if qkeys:
        await redis.delete(*qkeys)
    await redis.delete(f"qkeys:{doc_id}")
