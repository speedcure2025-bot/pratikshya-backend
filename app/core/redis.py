"""
app/core/redis.py — Compatibility shim: Redis → in-process LRU cache.

All existing call-sites that do:
    from app.core.redis import get_redis, init_redis, close_redis
continue to work without any changes.  The underlying implementation
now uses the in-process LRU cache store instead of a Redis connection.

NOTE: Celery still uses its own Redis broker/backend URLs from settings;
      those are untouched by this change.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Request  # noqa: F401 — kept for API compat

from app.core.lru_cache_store import (
    LRUCacheClient,
    close_lru_cache,
    get_cache_client,
    init_lru_cache,
)


async def init_redis() -> None:
    """Initialise the cache store.  Drop-in for the former Redis pool init."""
    init_lru_cache()


async def close_redis() -> None:
    """Release the cache store.  Drop-in for the former Redis pool close."""
    close_lru_cache()


def get_redis() -> LRUCacheClient:
    """
    Return the shared in-process cache client.

    Existing call-sites use this exactly as before:
        redis = get_redis()
        await redis.get("key")
        await redis.setex("key", 60, "value")
    """
    return get_cache_client()


async def get_redis_dep(request: Request) -> AsyncGenerator[LRUCacheClient, None]:  # noqa: ARG001
    """FastAPI dependency that yields the shared cache client."""
    yield get_redis()
