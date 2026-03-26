"""Tests for Redis failure -> memory fallback (the core differentiator)."""
import json
from unittest.mock import MagicMock

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
    assert cache._cache[full_key] == "value1"


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
