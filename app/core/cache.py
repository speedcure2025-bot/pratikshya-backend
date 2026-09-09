"""
app/core/cache.py — Reusable in-process LRU cache helpers.

Provides:
  - CacheService: typed get/set/delete wrapper over the shared LRU cache client.
  - invalidate_pattern(): delete all keys matching a glob pattern.
  - Key-namespace constants used across services.

Key namespaces
──────────────────────────────────────────────────────────────────
  pratikshya:cache:*      fastapi-cache2 HTTP response cache
  blacklist:access:{jti}  JWT access-token blacklist
  blacklist:refresh:{jti} JWT refresh-token blacklist
  rbac:{user_id}          RBAC roles+permissions JSON
  otp:{purpose}:{user_id} OTP bcrypt hash
  pwd_reset:{user_id}     Password-reset raw token
  cart:{customer_id}      Serialised CartResponse JSON
  rv:{customer_id}        Recently-viewed product-id list
  idempotency:{key}       Checkout/payment idempotency response cache
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL constants (seconds) — single source of truth for all services
# ---------------------------------------------------------------------------

TTL_RBAC = 300            # 5 min
TTL_CART = 300            # 5 min — evicted on every mutation anyway
TTL_RECENTLY_VIEWED = 60 * 60 * 24 * 30  # 30 days
TTL_IDEMPOTENCY = 60 * 60 * 24          # 24 hours (Req 9.11)
TTL_OTP = 600             # 10 min
TTL_PASSWORD_RESET = 3600 # 1 hour

# HTTP response cache TTLs (used with fastapi-cache2 @cache decorator)
TTL_CATEGORIES = 300      # 5 min
TTL_PRODUCTS_LIST = 120   # 2 min
TTL_PRODUCT_DETAIL = 120  # 2 min
TTL_RECOMMENDATIONS = 600 # 10 min
TTL_COLLECTIONS = 300     # 5 min


# ---------------------------------------------------------------------------
# CacheService
# ---------------------------------------------------------------------------

class CacheService:
    """
    Thin typed wrapper over the shared in-process LRU cache client.

    Methods all swallow cache errors gracefully — a cache miss
    is always safe to recover from; a write failure is logged but not raised.
    """

    # ── JSON helpers ─────────────────────────────────────────────────────────

    async def get_json(self, key: str) -> Optional[Any]:
        """Return parsed JSON value for *key*, or None on miss / error."""
        try:
            raw = await get_redis().get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("cache get_json(%s) failed: %s", key, exc)
            return None

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        """Serialise *value* to JSON and store with *ttl* seconds expiry."""
        try:
            await get_redis().setex(key, ttl, json.dumps(value, default=str))
        except Exception as exc:
            logger.warning("cache set_json(%s) failed: %s", key, exc)

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys."""
        try:
            if keys:
                await get_redis().delete(*keys)
        except Exception as exc:
            logger.warning("cache delete(%s) failed: %s", keys, exc)

    # ── String helpers ────────────────────────────────────────────────────────

    async def get_str(self, key: str) -> Optional[str]:
        try:
            return await get_redis().get(key)
        except Exception as exc:
            logger.warning("cache get_str(%s) failed: %s", key, exc)
            return None

    async def set_str(self, key: str, value: str, ttl: int) -> None:
        try:
            await get_redis().setex(key, ttl, value)
        except Exception as exc:
            logger.warning("cache set_str(%s) failed: %s", key, exc)

    async def exists(self, key: str) -> bool:
        try:
            return bool(await get_redis().exists(key))
        except Exception as exc:
            logger.warning("cache exists(%s) failed: %s", key, exc)
            return False

    # ── Redis List helpers (recently viewed) ─────────────────────────────────

    async def list_push(self, key: str, value: str, maxlen: int, ttl: int) -> None:
        """
        LPUSH *value* onto *key*, trim to *maxlen*, reset TTL.
        Removes duplicate values before pushing (most-recent-first dedup).
        """
        try:
            redis = get_redis()
            # Remove existing occurrence to re-insert at front
            await redis.lrem(key, 0, value)
            await redis.lpush(key, value)
            await redis.ltrim(key, 0, maxlen - 1)
            await redis.expire(key, ttl)
        except Exception as exc:
            logger.warning("cache list_push(%s) failed: %s", key, exc)

    async def list_range(self, key: str, start: int = 0, end: int = -1) -> list:
        """Return elements of the list between *start* and *end*."""
        try:
            return await get_redis().lrange(key, start, end)
        except Exception as exc:
            logger.warning("cache list_range(%s) failed: %s", key, exc)
            return []

    # ── Pattern invalidation ──────────────────────────────────────────────────

    async def invalidate_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching *pattern* using SCAN (non-blocking).
        Returns the number of keys deleted.
        """
        try:
            redis = get_redis()
            deleted = 0
            async for key in redis.scan_iter(match=pattern, count=100):
                await redis.delete(key)
                deleted += 1
            if deleted:
                logger.debug("Invalidated %d keys matching %s", deleted, pattern)
            return deleted
        except Exception as exc:
            logger.warning("cache invalidate_pattern(%s) failed: %s", pattern, exc)
            return 0


# Module-level singleton — import and use directly
cache = CacheService()


async def invalidate_response_cache() -> None:
    """
    Drop the `fastapi-cache2` HTTP response cache (the @cache decorator layer
    backed by the in-memory ``pratikshya:cache`` namespace).

    Catalogue write paths (products, categories, subcategories, collections,
    offers) call this after a mutation so storefront reads served through
    `GET /products`, `GET /categories/*`, `GET /collections/*` reflect the
    change immediately instead of up to TTL stale. The in-memory backend
    exposes only a global clear — that is acceptable for the single-process
    runtime this app ships with; a Redis-backed deployment would move to
    per-key invalidation.

    Never raises: the response cache is an optimization, and a failure here
    must not fail the business write that already succeeded.
    """
    try:
        from fastapi_cache import FastAPICache

        await FastAPICache.clear()
    except Exception as exc:  # pragma: no cover — cache is best-effort
        logger.debug("response cache invalidation skipped: %s", exc)
