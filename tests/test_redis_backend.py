"""Tests for Redis backend operations (via mock client)."""
import json

from fallback_cache import FallbackCache


def test_set_calls_redis_setex(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("key1", {"name": "Alice"})
    mock_redis.setex.assert_called_once()
    args = mock_redis.setex.call_args
    assert args[0][0] == "key1"
    assert args[0][1] == 300
    assert json.loads(args[0][2]) == {"name": "Alice"}


def test_get_calls_redis_get(mock_redis):
    mock_redis.get.return_value = json.dumps({"name": "Alice"})
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    result = cache.get("key1")
    mock_redis.get.assert_called_once_with("key1")
    assert result == {"name": "Alice"}


def test_get_returns_none_on_redis_miss(mock_redis):
    mock_redis.get.return_value = None
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    # Need to set first so memory has it, then verify Redis miss falls to memory
    # Actually for a pure Redis miss test: nothing in memory either
    result = cache.get("key1")
    assert result is None


def test_delete_calls_redis_delete(mock_redis):
    mock_redis.delete.return_value = 1
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("key1", "value")
    result = cache.delete("key1")
    assert result is True
    mock_redis.delete.assert_called_with("key1")


def test_invalidate_prefix_scans_and_deletes(mock_redis):
    mock_redis.scan.return_value = (0, [b"users:1", b"users:2"])
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.invalidate_prefix("users:")
    mock_redis.scan.assert_called()
    mock_redis.delete.assert_called_once_with(b"users:1", b"users:2")


def test_set_dual_writes_to_memory(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("key1", {"name": "Alice"})
    assert cache._cache["key1"] == {"name": "Alice"}


def test_stats_redis_mode(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("a", 1)
    stats = cache.stats()
    assert stats["backend"] == "redis"
    assert stats["memory_entries"] == 1
    # Verify NO extra keys like "entries" or "max_entries"
    assert "entries" not in stats
    assert "max_entries" not in stats


def test_key_prefix_applied_to_redis(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300, key_prefix="app:")
    cache.set("key1", "value")
    args = mock_redis.setex.call_args
    assert args[0][0] == "app:key1"
