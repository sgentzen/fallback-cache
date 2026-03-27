"""FallbackCache — Redis-primary cache with transparent in-memory LRU fallback."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _default_serializer(data: Any) -> str:
    return json.dumps(data, default=str)


_DEFAULT_DESERIALIZER: Callable[[str | bytes], Any] = json.loads


class FallbackCache:
    """Cache with Redis primary and in-memory LRU fallback.

    When a redis_client is provided, set() dual-writes to both Redis and
    in-memory. get() reads Redis first; on miss or failure, falls through
    to the in-memory copy. Without redis_client, operates as pure in-memory cache.
    """

    def __init__(
        self,
        redis_client: Any = None,
        default_ttl: int = 300,
        max_entries: int = 100,
        key_prefix: str = "",
        serializer: Callable[[Any], str | bytes] = _default_serializer,
        deserializer: Callable[[str | bytes], Any] = _DEFAULT_DESERIALIZER,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError(f"default_ttl must be positive, got {default_ttl}")

        self._redis = redis_client
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._key_prefix = key_prefix
        self._serializer = serializer
        self._deserializer = deserializer

        # In-memory backend: OrderedDict for LRU ordering (most-recently-used at end)
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._ttls: dict[str, int] = {}

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
        if self._redis is not None:
            try:
                self._redis.setex(full_key, effective_ttl, self._serializer(data))
            except Exception:
                logger.warning("Redis set failed for key %r", full_key, exc_info=True)

        # Always write to in-memory
        self._memory_set(full_key, data, effective_ttl)

    def get(self, key: str) -> Any | None:
        """Retrieve value for key, or None if missing/expired."""
        full_key = self._full_key(key)

        # Try Redis first
        if self._redis is not None:
            try:
                raw = self._redis.get(full_key)
                if raw is not None:
                    return self._deserializer(raw)
                # Redis miss — fall through to memory
            except Exception:
                logger.warning("Redis get failed for key %r", full_key, exc_info=True)

        return self._memory_get(full_key)

    def delete(self, key: str) -> bool:
        """Delete key from both backends. Returns True if key existed in either."""
        full_key = self._full_key(key)
        existed = False

        if self._redis is not None:
            try:
                count = self._redis.delete(full_key)
                if count and count > 0:
                    existed = True
            except Exception:
                logger.warning("Redis delete failed for key %r", full_key, exc_info=True)

        if self._memory_delete(full_key):
            existed = True

        return existed

    def invalidate_prefix(self, prefix: str) -> None:
        """Delete all keys whose full key starts with key_prefix + prefix."""
        full_prefix = self._key_prefix + prefix

        if self._redis is not None:
            try:
                cursor = 0
                pattern = f"{full_prefix}*"
                while True:
                    cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        self._redis.delete(*keys)
                    if cursor == 0:
                        break
            except Exception:
                logger.warning(
                    "Redis invalidate_prefix failed for prefix %r", full_prefix, exc_info=True
                )

        # Always clean memory
        to_delete = [k for k in self._cache.keys() if k.startswith(full_prefix)]
        for k in to_delete:
            self._memory_delete(k)

    def clear(self) -> None:
        """Remove all entries from both backends.

        Deletes only the keys tracked by this cache instance rather than
        flushing the entire Redis database.
        """
        if self._redis is not None:
            keys_to_delete = list(self._cache.keys())
            if keys_to_delete:
                try:
                    self._redis.delete(*keys_to_delete)
                except Exception:
                    logger.warning("Redis delete failed during clear()", exc_info=True)

        self._cache.clear()
        self._timestamps.clear()
        self._ttls.clear()

    def stats(self) -> dict[str, Any]:
        """Return runtime statistics for the cache."""
        if self._redis is not None:
            return {
                "backend": "redis",
                "memory_entries": len(self._cache),
                "key_prefix": self._key_prefix,
            }

        oldest_age: float | None = None
        if self._timestamps:
            now = datetime.now(timezone.utc).timestamp()
            oldest_ts = min(self._timestamps.values())
            oldest_age = now - oldest_ts

        return {
            "backend": "memory",
            "entries": len(self._cache),
            "max_entries": self._max_entries,
            "oldest_age_seconds": oldest_age,
        }

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
        Returns 'prefix:<hash>'.
        """
        filtered = {k: v for k, v in params.items() if v is not None}
        canonical = json.dumps(filtered, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
        return f"{prefix}:{digest}"

    # ------------------------------------------------------------------
    # In-memory backend internals
    # ------------------------------------------------------------------

    def _memory_set(self, full_key: str, data: Any, ttl: int) -> None:
        """Write to in-memory LRU cache, evicting LRU entry if at capacity."""
        # If key already exists, remove it so we can re-insert at end (most recent)
        if full_key in self._cache:
            del self._cache[full_key]
        elif len(self._cache) >= self._max_entries:
            # Evict the least-recently-used (first) entry
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

        # Lazy TTL expiry
        stored_at = self._timestamps.get(full_key)
        ttl = self._ttls.get(full_key, self._default_ttl)
        if stored_at is not None:
            age = datetime.now(timezone.utc).timestamp() - stored_at
            if age >= ttl:
                self._memory_delete(full_key)
                return None

        # Promote to most-recently-used position
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
