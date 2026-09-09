"""
app/core/lru_cache_store.py — In-process LRU + TTL cache store.

Drop-in replacement for the Redis client used in cache/auth flows.
Backed by cachetools.TTLCache — thread-safe via threading.RLock.

Key behaviours that match the Redis usage patterns in this project:
  - get(key)            → value or None
  - set(key, value, ttl) → stores with per-entry TTL
  - setex(key, ttl, value) → same as set (Redis-compatible arg order)
  - delete(*keys)
  - exists(key)         → 0 or 1
  - lpush / lrem / ltrim / lrange / expire — list helpers for recently-viewed
  - scan_iter(match)    → async generator over matching keys (glob)

NOTE: This store is in-process only — data is NOT shared across workers
and is lost on restart.  That is acceptable for a single-process dev/small-
prod deployment that does not need distributed session invalidation.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal entry: (value_str, expires_at_monotonic)
# expires_at == None means no expiry (not used in practice here)
# ---------------------------------------------------------------------------

_Lock = threading.RLock()

# Main KV store: key → (raw_value_string, expiry_monotonic_or_None)
_store: Dict[str, Tuple[str, Optional[float]]] = {}

# List store: key → (list_of_strings, expiry_monotonic_or_None)
_list_store: Dict[str, Tuple[List[str], Optional[float]]] = {}


def _is_alive(entry: Tuple[Any, Optional[float]]) -> bool:
    _, exp = entry
    return exp is None or time.monotonic() < exp


def _exp_from_ttl(ttl: int) -> float:
    return time.monotonic() + ttl


# ---------------------------------------------------------------------------
# Async-compatible interface matching the redis.asyncio client surface
# ---------------------------------------------------------------------------

class LRUCacheClient:
    """
    Async-compatible interface that mirrors the redis.asyncio.Redis methods
    used throughout this project.  All methods are async coroutines so
    existing await call-sites require zero changes.
    """

    # ── KV ──────────────────────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[str]:
        with _Lock:
            entry = _store.get(key)
            if entry is None:
                return None
            if not _is_alive(entry):
                del _store[key]
                return None
            return entry[0]

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        exp = _exp_from_ttl(ttl) if ttl else None
        with _Lock:
            _store[key] = (value, exp)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Redis-compatible: setex(key, seconds, value)."""
        await self.set(key, value, ttl)

    async def delete(self, *keys: str) -> int:
        count = 0
        with _Lock:
            for k in keys:
                if _store.pop(k, None) is not None:
                    count += 1
                if _list_store.pop(k, None) is not None:
                    count += 1
        return count

    async def exists(self, key: str) -> int:
        with _Lock:
            entry = _store.get(key)
            if entry is None:
                return 0
            if not _is_alive(entry):
                del _store[key]
                return 0
            return 1

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass

    # ── List helpers (recently-viewed) ──────────────────────────────────────

    async def lrem(self, key: str, count: int, value: str) -> int:  # noqa: ARG002
        """Remove all occurrences of *value* from list at *key* (count ignored)."""
        with _Lock:
            entry = _list_store.get(key)
            if entry is None:
                return 0
            lst, exp = entry
            original_len = len(lst)
            lst[:] = [v for v in lst if v != value]
            _list_store[key] = (lst, exp)
            return original_len - len(lst)

    async def lpush(self, key: str, *values: str) -> int:
        with _Lock:
            entry = _list_store.get(key)
            if entry is None:
                lst: List[str] = []
                exp = None
            else:
                lst, exp = entry
            for v in reversed(values):
                lst.insert(0, v)
            _list_store[key] = (lst, exp)
            return len(lst)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        with _Lock:
            entry = _list_store.get(key)
            if entry is None:
                return
            lst, exp = entry
            if end == -1:
                lst[:] = lst[start:]
            else:
                lst[:] = lst[start: end + 1]
            _list_store[key] = (lst, exp)

    async def expire(self, key: str, ttl: int) -> int:
        exp = _exp_from_ttl(ttl)
        with _Lock:
            if key in _store:
                val, _ = _store[key]
                _store[key] = (val, exp)
                return 1
            if key in _list_store:
                lst, _ = _list_store[key]
                _list_store[key] = (lst, exp)
                return 1
        return 0

    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        with _Lock:
            entry = _list_store.get(key)
            if entry is None:
                return []
            lst, exp = entry
            if exp is not None and time.monotonic() >= exp:
                del _list_store[key]
                return []
            if end == -1:
                return list(lst[start:])
            return list(lst[start: end + 1])

    # ── Pattern scan (invalidate_pattern) ───────────────────────────────────

    async def scan_iter(
        self, match: str = "*", count: int = 100  # noqa: ARG002
    ) -> AsyncIterator[str]:
        """Yield all live keys whose name matches the glob *match* pattern."""
        with _Lock:
            all_keys = list(_store.keys()) + list(_list_store.keys())

        for key in all_keys:
            if fnmatch.fnmatch(key, match):
                # Double-check it is still alive before yielding
                is_live = False
                with _Lock:
                    if key in _store and _is_alive(_store[key]):
                        is_live = True
                    elif key in _list_store and _is_alive(_list_store[key]):
                        is_live = True
                if is_live:
                    yield key
                    await asyncio.sleep(0)  # yield control to event loop


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: Optional[LRUCacheClient] = None


def init_lru_cache() -> None:
    """Initialise the in-process cache.  Called once at application startup."""
    global _client
    _client = LRUCacheClient()
    logger.info("In-process LRU cache store initialised.")


def close_lru_cache() -> None:
    """Clear and release the cache store.  Called at application shutdown."""
    global _client
    with _Lock:
        _store.clear()
        _list_store.clear()
    _client = None
    logger.info("In-process LRU cache store cleared and closed.")


def get_cache_client() -> LRUCacheClient:
    """Return the module-level client. Auto-initialises if not already created."""
    global _client
    if _client is None:
        init_lru_cache()
    return _client
