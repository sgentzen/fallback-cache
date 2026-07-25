"""AsyncFallbackCache — async Redis-primary cache with in-memory LRU fallback."""
from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fallback_cache._circuit_breaker import CircuitBreaker
from fallback_cache._keys import build_key as _build_key
from fallback_cache._serializers import DEFAULT_DESERIALIZER, default_serializer

logger = logging.getLogger(__name__)


class AsyncFallbackCache:
    """Async cache with Redis primary and in-memory LRU fallback.

    When a redis_client is provided, set() dual-writes to both Redis and
    in-memory. get() reads Redis first; on miss or failure, falls through
    to the in-memory copy. Without redis_client, operates as pure in-memory cache.

    A built-in circuit breaker stops probing Redis after repeated failures
    and automatically re-tests after a cooldown period.

    The redis_client should be a ``redis.asyncio.Redis`` instance.
    """

    def __init__(
        self,
        redis_client: Any = None,
        default_ttl: int = 300,
        max_entries: int = 100,
        key_prefix: str = "",
        serializer: Callable[[Any], str | bytes] = default_serializer,
        deserializer: Callable[[str | bytes], Any] = DEFAULT_DESERIALIZER,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: float = 30.0,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError(f"default_ttl must be positive, got {default_ttl}")

        self._redis = redis_client
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._key_prefix = key_prefix
        self._serializer = serializer
        self._deserializer = deserializer
        self._breaker = CircuitBreaker(
            threshold=circuit_breaker_threshold,
            cooldown=circuit_breaker_cooldown,
        )

        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._ttls: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def set(self, key: str, data: Any, ttl: int | None = None) -> None:
        """Store data under key with optional per-key TTL override."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            raise ValueError(f"TTL must be positive, got {effective_ttl}")

        full_key = self._full_key(key)

        if self._redis is not None and self._breaker.should_attempt():
            try:
                await self._redis.setex(full_key, effective_ttl, self._serializer(data))
                self._breaker.record_success()
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis set failed for key %r", full_key, exc_info=True)

        self._memory_set(full_key, data, effective_ttl)

    async def get(self, key: str) -> Any | None:
        """Retrieve value for key, or None if missing/expired."""
        full_key = self._full_key(key)

        if self._redis is not None and self._breaker.should_attempt():
            try:
                raw = await self._redis.get(full_key)
                self._breaker.record_success()
                if raw is not None:
                    return self._deserializer(raw)
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis get failed for key %r", full_key, exc_info=True)

        return self._memory_get(full_key)

    async def delete(self, key: str) -> bool:
        """Delete key from both backends. Returns True if key existed in either."""
        full_key = self._full_key(key)
        existed = False

        if self._redis is not None and self._breaker.should_attempt():
            try:
                count = await self._redis.delete(full_key)
                self._breaker.record_success()
                if count and count > 0:
                    existed = True
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis delete failed for key %r", full_key, exc_info=True)

        if self._memory_delete(full_key):
            existed = True

        return existed

    async def invalidate_prefix(self, prefix: str) -> None:
        """Delete all keys whose full key starts with key_prefix + prefix."""
        full_prefix = self._key_prefix + prefix

        if self._redis is not None and self._breaker.should_attempt():
            try:
                cursor = 0
                pattern = f"{full_prefix}*"
                while True:
                    cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self._redis.delete(*keys)
                    if cursor == 0:
                        break
                self._breaker.record_success()
            except Exception:
                self._breaker.record_failure()
                logger.warning(
                    "Redis invalidate_prefix failed for prefix %r",
                    full_prefix,
                    exc_info=True,
                )

        to_delete = [k for k in self._cache.keys() if k.startswith(full_prefix)]
        for k in to_delete:
            self._memory_delete(k)

    async def clear(self) -> None:
        """Remove all entries from both backends."""
        if self._redis is not None and self._breaker.should_attempt():
            keys_to_delete = list(self._cache.keys())
            if keys_to_delete:
                try:
                    await self._redis.delete(*keys_to_delete)
                    self._breaker.record_success()
                except Exception:
                    self._breaker.record_failure()
                    logger.warning("Redis delete failed during clear()", exc_info=True)

        self._cache.clear()
        self._timestamps.clear()
        self._ttls.clear()

    def stats(self) -> dict[str, Any]:
        """Return runtime statistics for the cache."""
        result: dict[str, Any] = {}

        if self._redis is not None:
            result.update({
                "backend": "redis",
                "memory_entries": len(self._cache),
                "key_prefix": self._key_prefix,
            })
        else:
            oldest_age: float | None = None
            if self._timestamps:
                now = datetime.now(timezone.utc).timestamp()
                oldest_ts = min(self._timestamps.values())
                oldest_age = now - oldest_ts

            result.update({
                "backend": "memory",
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "oldest_age_seconds": oldest_age,
            })

        result.update(self._breaker.stats())
        return result

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _full_key(self, key: str) -> str:
        """Prepend key_prefix to the key if configured."""
        return f"{self._key_prefix}{key}" if self._key_prefix else key

    @staticmethod
    def build_key(prefix: str, **params: Any) -> str:
        """Build a deterministic, content-addressed cache key."""
        return _build_key(prefix, **params)

    # ------------------------------------------------------------------
    # In-memory backend internals
    # ------------------------------------------------------------------

    def _memory_set(self, full_key: str, data: Any, ttl: int) -> None:
        """Write to in-memory LRU cache, evicting LRU entry if at capacity."""
        if full_key in self._cache:
            del self._cache[full_key]
        elif len(self._cache) >= self._max_entries:
            evicted_key, _ = self._cache.popitem(last=False)
            self._timestamps.pop(evicted_key, None)
            self._ttls.pop(evicted_key, None)

        self._cache[full_key] = data
        self._timestamps[full_key] = datetime.now(timezone.utc).timestamp()
        self._ttls[full_key] = ttl

    def _memory_get(self, full_key: str) -> Any | None:
        """Read from in-memory cache with lazy TTL expiry and LRU promotion."""
        if full_key not in self._cache:
            return None

        stored_at = self._timestamps.get(full_key)
        ttl = self._ttls.get(full_key, self._default_ttl)
        if stored_at is not None:
            age = datetime.now(timezone.utc).timestamp() - stored_at
            if age >= ttl:
                self._memory_delete(full_key)
                return None

        self._cache.move_to_end(full_key)
        return self._cache[full_key]

    def _memory_delete(self, full_key: str) -> bool:
        """Remove a single key from in-memory storage. Returns True if it existed."""
        if full_key not in self._cache:
            return False
        del self._cache[full_key]
        self._timestamps.pop(full_key, None)
        self._ttls.pop(full_key, None)
        return True
