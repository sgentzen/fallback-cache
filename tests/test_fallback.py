"""Tests for Redis failure -> memory fallback (the core differentiator)."""
import json
from unittest.mock import MagicMock

import pytest

from fallback_cache import FallbackCache


def _failing_redis():
    """Create a mock Redis where all operations raise ConnectionError."""
    client = MagicMock()
    client.get.side_effect = ConnectionError("Redis down")
    client.setex.side_effect = ConnectionError("Redis down")
    client.delete.side_effect = ConnectionError("Redis down")
    client.scan.side_effect = ConnectionError("Redis down")
    return client


def test_get_falls_back_to_memory_on_redis_error():
    redis = _failing_redis()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    # set() will fail on Redis but succeed on memory (dual-write)
    cache.set("key1", {"v": 1})
    result = cache.get("key1")
    assert result == {"v": 1}


def test_set_stores_in_memory_when_redis_fails():
    redis = _failing_redis()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value1")
    full_key = cache._full_key("key1")
    assert cache._cache[full_key].value == "value1"


def test_get_after_set_with_redis_down():
    redis = _failing_redis()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", {"status": "ok"})
    result = cache.get("key1")
    assert result == {"status": "ok"}


def test_delete_cleans_memory_when_redis_fails():
    redis = _failing_redis()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value1")
    result = cache.delete("key1")
    assert result is True
    assert cache.get("key1") is None


def test_invalidate_prefix_cleans_memory_when_redis_fails():
    redis = _failing_redis()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("users:1", "alice")
    cache.set("users:2", "bob")
    cache.set("items:1", "widget")
    cache.invalidate_prefix("users:")
    assert cache.get("users:1") is None
    assert cache.get("users:2") is None
    assert cache.get("items:1") == "widget"


def test_next_call_retries_redis():
    """After a Redis failure, next call should try Redis again."""
    redis = MagicMock()
    redis.get.side_effect = [ConnectionError("Redis down"), json.dumps("value1").encode()]
    redis.setex.return_value = None
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value1")
    # First get: Redis fails, falls back to memory
    result1 = cache.get("key1")
    assert result1 == "value1"
    # Second get: Redis works
    result2 = cache.get("key1")
    assert result2 == "value1"
    assert redis.get.call_count == 2


def test_dual_write_healthy_redis():
    """When Redis is healthy, set() writes to BOTH Redis and memory."""
    redis = MagicMock()
    redis.setex.return_value = None
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value1")
    redis.setex.assert_called_once()
    assert "key1" in cache._cache


def test_invalidate_prefix_raises_on_empty_prefix():
    """invalidate_prefix with empty prefix and no key_prefix would wipe the DB."""
    cache = FallbackCache(default_ttl=300)
    with pytest.raises(ValueError, match="non-empty prefix"):
        cache.invalidate_prefix("")


def test_invalidate_prefix_empty_prefix_with_key_prefix_is_allowed():
    """key_prefix alone is sufficient to scope the operation."""
    cache = FallbackCache(default_ttl=300, key_prefix="app:")
    cache.set("users:1", "alice")
    cache.set("users:2", "bob")
    cache.invalidate_prefix("")  # full_prefix == "app:" — safe
    assert cache.get("users:1") is None
    assert cache.get("users:2") is None


def test_redis_hit_keeps_key_warm_in_memory_fallback(mock_redis):
    """A key read from Redis is promoted in the in-memory LRU so it survives
    eviction. The fallback should stay warm with the keys actually being *read*,
    not just the ones most recently *written*.
    """
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300, max_entries=3)
    cache.set("hot", {"v": 1})
    # Redis serves "hot" on every read, so each get() is a Redis hit.
    mock_redis.get.return_value = json.dumps({"v": 1})

    # Read "hot" repeatedly, interleaved with writes that fill past max_entries.
    # Without read-promotion, "hot" is the least-recently-used entry and is
    # evicted; with promotion it stays resident.
    for k in ("a", "b", "c", "d", "e"):
        assert cache.get("hot") == {"v": 1}   # Redis hit → must promote in memory
        cache.set(k, k)

    assert "hot" in cache._cache
    assert cache._cache["hot"].value == {"v": 1}


def test_redis_hit_does_not_promote_expired_memory_copy(mock_redis):
    """Promotion on a Redis hit must not resurrect an expired in-memory entry."""
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300, max_entries=3)
    cache.set("k", "v")
    mock_redis.get.return_value = json.dumps("v")

    # Force the in-memory copy to look expired.
    expired = cache._cache["k"]._replace(ttl=0)
    cache._cache["k"] = expired

    assert cache.get("k") == "v"          # Redis still serves it
    assert "k" not in cache._cache        # but the stale memory copy is dropped


def test_redis_deserialize_error_propagates_and_is_not_a_redis_failure(mock_redis):
    """A corrupt Redis payload is a data error for the caller, not a Redis
    outage. It must propagate (mirroring how set() lets serialization errors
    propagate) rather than being swallowed, miscounted as a Redis failure, and
    silently masked by the in-memory copy.
    """
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("k", {"v": 1})                     # memory holds a good copy
    mock_redis.get.return_value = b"not-json{"   # Redis returns garbage

    with pytest.raises(json.JSONDecodeError):
        cache.get("k")

    # The error is the deserializer's, not Redis's — counters stay clean.
    stats = cache.stats()
    assert stats["redis_failures"] == 0
    assert stats["redis_last_error"] is None
