"""FallbackCache — Redis-primary cache with transparent in-memory LRU fallback."""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, NamedTuple

from fallback_cache._circuit_breaker import CircuitBreaker
from fallback_cache._keys import build_key as _build_key
from fallback_cache._serializers import DEFAULT_DESERIALIZER, default_serializer

logger = logging.getLogger(__name__)


class FallbackCache:
    """Cache with Redis primary and in-memory LRU fallback.

    When a redis_client is provided, set() dual-writes to both Redis and
    in-memory. get() reads Redis first; on miss or failure, falls through
    to the in-memory copy. Without redis_client, operates as pure in-memory cache.

    A built-in circuit breaker stops probing Redis after repeated failures
    and automatically re-tests after a cooldown period.
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

        # In-memory backend: single OrderedDict for LRU ordering (MRU at end).
        # RLock because public methods (e.g. set) may hold the lock while
        # calling internal helpers (_memory_set) that also acquire it.
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, _Entry] = OrderedDict()

        # Tracks every full key successfully written to Redis (survives LRU eviction)
        self._redis_keys: set[str] = set()

        # Redis health counters (best-effort; always updated under self._lock)
        self._redis_failures: int = 0
        self._redis_last_error: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set(self, key: str, data: Any, ttl: int | None = None) -> None:
        """Store data under key with optional per-key TTL override."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            raise ValueError(f"TTL must be positive, got {effective_ttl}")

        full_key = self._full_key(key)

        # Try Redis first (best-effort)
        if self._redis is not None and self._breaker.should_attempt():
            try:
                self._redis.setex(full_key, effective_ttl, self._serializer(data))
                self._breaker.record_success()
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis set failed for key %r", full_key, exc_info=True)
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)

        # Always write to in-memory
        self._memory_set(full_key, data, effective_ttl)

    def get(self, key: str) -> Any | None:
        """Retrieve value for key, or None if missing/expired."""
        full_key = self._full_key(key)

        # Try Redis first
        if self._redis is not None and self._breaker.should_attempt():
            try:
                raw = self._redis.get(full_key)
                self._breaker.record_success()
                if raw is not None:
                    return self._deserializer(raw)
                # Redis miss — fall through to memory
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis get failed for key %r", full_key, exc_info=True)
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)

        return self._memory_get(full_key)

    def delete(self, key: str) -> bool:
        """Delete key from both backends. Returns True if key existed in either."""
        full_key = self._full_key(key)
        existed = False

        if self._redis is not None and self._breaker.should_attempt():
            try:
                count = self._redis.delete(full_key)
                self._breaker.record_success()
                if count and count > 0:
                    existed = True
            except Exception:
                self._breaker.record_failure()
                logger.warning("Redis delete failed for key %r", full_key, exc_info=True)
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)

        if self._memory_delete(full_key):
            existed = True

        return existed

    def invalidate_prefix(self, prefix: str) -> None:
        """Delete all keys whose full key starts with key_prefix + prefix."""
        full_prefix = self._key_prefix + prefix
        if not full_prefix:
            raise ValueError("invalidate_prefix requires a non-empty prefix or key_prefix")

        if self._redis is not None and self._breaker.should_attempt():
            try:
                cursor = 0
                pattern = f"{full_prefix}*"
                while True:
                    cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        self._redis.delete(*keys)
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
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)

        # Always clean memory
        with self._lock:
            to_delete = [k for k in list(self._cache.keys()) if k.startswith(full_prefix)]
            for k in to_delete:
                self._cache.pop(k, None)

    def clear(self) -> None:
        """Remove all entries from both backends.

        Deletes only the keys tracked by this cache instance rather than
        flushing the entire Redis database. If the Redis delete call raises,
        the in-memory store is still cleared; a subsequent clear() will
        retry the Redis deletion using the still-populated _redis_keys set.
        """
        if self._redis is not None and self._breaker.should_attempt():
            keys_to_delete = list(self._cache.keys())
            if keys_to_delete:
                try:
                    self._redis.delete(*keys_to_delete)
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
        """Build a deterministic, content-addressed cache key.

        None-valued params are excluded. Remaining params are sorted,
        JSON-serialized, and SHA-256 hashed (first 12 hex chars).
        Returns ``'prefix:<hash>'``.

        **Param contract:** all values must be JSON-serializable (str, int,
        float, bool, None, list, dict with string keys). Sets, bare objects,
        and other non-serializable types will raise ``TypeError``. If you need
        to include a custom type, convert it to a string or dict first.
        """
        return _build_key(prefix, **params)

    # ------------------------------------------------------------------
    # In-memory backend internals
    # ------------------------------------------------------------------

    def _memory_set(self, full_key: str, data: Any, ttl: int) -> None:
        """Write to in-memory LRU cache, evicting LRU entry if at capacity."""
        with self._lock:
            if full_key in self._cache:
                del self._cache[full_key]
            elif len(self._cache) >= self._max_entries:
                # Evict the least-recently-used (first) entry
                self._cache.popitem(last=False)

            self._cache[full_key] = _Entry(
                value=data,
                stored_at=time.monotonic(),
                ttl=ttl,
            )

    def _memory_get(self, full_key: str) -> Any | None:
        """Read from in-memory cache with lazy TTL expiry and LRU promotion."""
        with self._lock:
            entry = self._cache.get(full_key)
            if entry is None:
                return None

            # Lazy TTL expiry
            age = time.monotonic() - entry.stored_at
            if age >= entry.ttl:
                del self._cache[full_key]
                return None

            # Promote to most-recently-used position
            self._cache.move_to_end(full_key)
            return entry.value

    def _memory_delete(self, full_key: str) -> bool:
        """Remove a single key from in-memory storage. Returns True if it existed."""
        with self._lock:
            if full_key not in self._cache:
                return False
            del self._cache[full_key]
            return True
