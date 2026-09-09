"""FallbackCache — Redis-primary cache with transparent in-memory LRU fallback."""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, NamedTuple

from fallback_cache._base import _BaseCache

logger = logging.getLogger(__name__)


class _Entry(NamedTuple):
    """Single in-memory cache record."""

    value: Any
    stored_at: float  # time.monotonic() at write time
    ttl: int          # seconds until expiry


class FallbackCache(_BaseCache):
    """Cache with Redis primary and in-memory LRU fallback.

    When a redis_client is provided, set() dual-writes to both Redis and
    in-memory. get() reads Redis first; on miss or failure, falls through
    to the in-memory copy. Without redis_client, operates as pure in-memory cache.

    A built-in circuit breaker stops probing Redis after repeated failures
    and automatically re-tests after a cooldown period.
    """

    def _init_storage(self) -> None:
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
        effective_ttl = self._effective_ttl(ttl)
        full_key = self._full_key(key)

        # Try Redis first (best-effort).
        # Serialization happens outside the try so a TypeError from bad input
        # propagates to the caller rather than being miscounted as a Redis failure.
        if self._redis is not None and self._breaker.should_attempt():
            serialized = self._serializer(data)
            try:
                self._redis.setex(full_key, effective_ttl, serialized)
                self._breaker.record_success()
                # Mutate _redis_keys under the lock: a concurrent clear()/
                # invalidate_prefix() rebuilds this set while holding self._lock.
                with self._lock:
                    self._redis_keys.add(full_key)
            except Exception as exc:
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
            except Exception as exc:
                self._breaker.record_failure()
                logger.warning("Redis get failed for key %r", full_key, exc_info=True)
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)
            else:
                self._breaker.record_success()
                if raw is not None:
                    # Deserialize outside the except so a corrupt payload
                    # propagates to the caller rather than being miscounted as a
                    # Redis failure and silently masked by the in-memory copy
                    # (mirrors set(), which serializes outside the try).
                    value = self._deserializer(raw)
                    # Keep the fallback warm with keys that are actively read,
                    # not just recently written, so it stays useful if Redis
                    # later fails.
                    self._memory_promote(full_key)
                    return value
                # Redis miss — fall through to memory

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
                # Mutate _redis_keys under the lock (see set() for rationale).
                with self._lock:
                    self._redis_keys.discard(full_key)
            except Exception as exc:
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
        full_prefix = self._full_prefix(prefix)

        if self._redis is not None and self._breaker.should_attempt():
            try:
                cursor = 0
                pattern = self._scan_pattern(full_prefix)
                while True:
                    cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        self._redis.delete(*keys)
                    if cursor == 0:
                        break
                self._breaker.record_success()
                with self._lock:
                    self._redis_keys = {
                        k for k in self._redis_keys if not k.startswith(full_prefix)
                    }
            except Exception as exc:
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
            to_delete = [k for k in self._cache if k.startswith(full_prefix)]
            for k in to_delete:
                self._cache.pop(k, None)

    def clear(self) -> None:
        """Remove all entries from both backends.

        Deletes only the keys tracked by this cache instance rather than
        flushing the entire Redis database. If the Redis delete call raises,
        the in-memory store is still cleared; a subsequent clear() will
        retry the Redis deletion using the still-populated _redis_keys set.
        """
        # Snapshot the keys to purge and clear memory in one lock block so no
        # concurrent set() can slip a key into _cache after we've cleared it.
        with self._lock:
            keys_to_delete = list(self._redis_keys | set(self._cache.keys()))
            self._cache.clear()

        # Redis I/O outside the lock; subtract only what we successfully deleted
        # so any keys added by a concurrent set() after the snapshot are retained.
        if self._redis is not None and keys_to_delete and self._breaker.should_attempt():
            try:
                self._redis.delete(*keys_to_delete)
                self._breaker.record_success()
                with self._lock:
                    self._redis_keys -= set(keys_to_delete)
            except Exception as exc:
                self._breaker.record_failure()
                logger.warning("Redis delete failed during clear()", exc_info=True)
                with self._lock:
                    self._redis_failures += 1
                    self._redis_last_error = repr(exc)

    def stats(self) -> dict[str, Any]:
        """Return runtime statistics for the cache."""
        result: dict[str, Any] = {}

        # Read shared in-memory state under the lock: len() and iteration over
        # self._cache otherwise race with a concurrent set()/clear(), which can
        # raise "dictionary changed size during iteration".
        with self._lock:
            if self._redis is not None:
                result.update({
                    "backend": "redis",
                    "memory_entries": len(self._cache),
                    "key_prefix": self._key_prefix,
                    "redis_failures": self._redis_failures,
                    "redis_last_error": self._redis_last_error,
                })
            else:
                oldest_age: float | None = None
                if self._cache:
                    now = time.monotonic()
                    oldest_ts = min(entry.stored_at for entry in self._cache.values())
                    oldest_age = now - oldest_ts

                result.update({
                    "backend": "memory",
                    "entries": len(self._cache),
                    "max_entries": self._max_entries,
                    "oldest_age_seconds": oldest_age,
                })

        # The breaker guards its own state; call it outside our lock.
        result.update(self._breaker.stats())
        return result

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

    @staticmethod
    def _is_expired(entry: _Entry) -> bool:
        """True if the entry's TTL has elapsed (monotonic clock)."""
        return time.monotonic() - entry.stored_at >= entry.ttl

    def _memory_get(self, full_key: str) -> Any | None:
        """Read from in-memory cache with lazy TTL expiry and LRU promotion."""
        with self._lock:
            entry = self._cache.get(full_key)
            if entry is None:
                return None

            # Lazy TTL expiry
            if self._is_expired(entry):
                del self._cache[full_key]
                return None

            # Promote to most-recently-used position
            self._cache.move_to_end(full_key)
            return entry.value

    def _memory_promote(self, full_key: str) -> None:
        """Promote an in-memory entry to most-recently-used after a Redis hit.

        Keeps the fallback warm with actively-read keys so they survive LRU
        eviction. An expired copy is dropped rather than promoted; it is not
        repopulated from the Redis value because the remaining TTL is unknown
        without an extra round trip.
        """
        with self._lock:
            entry = self._cache.get(full_key)
            if entry is None:
                return
            if self._is_expired(entry):
                del self._cache[full_key]
                return
            self._cache.move_to_end(full_key)

    def _memory_delete(self, full_key: str) -> bool:
        """Remove a single key from in-memory storage. Returns True if it existed."""
        with self._lock:
            if full_key not in self._cache:
                return False
            del self._cache[full_key]
            return True
