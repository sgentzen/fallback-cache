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
    assert cache._cache["key1"].value == {"name": "Alice"}


def test_stats_redis_mode(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300)
    cache.set("a", 1)
    stats = cache.stats()
    assert stats["backend"] == "redis"
    assert stats["memory_entries"] == 1
    assert stats["redis_failures"] == 0
    assert stats["redis_last_error"] is None
    # Verify NO memory-mode keys bleed through
    assert "entries" not in stats
    assert "max_entries" not in stats


def test_stats_reports_redis_failures():
    """stats() exposes redis_failures and redis_last_error after failed Redis ops."""
    from unittest.mock import MagicMock

    redis = MagicMock()
    redis.setex.side_effect = ConnectionError("Redis down")
    redis.get.side_effect = ConnectionError("Redis down")
    cache = FallbackCache(redis_client=redis, default_ttl=300)

    cache.set("key1", "value")   # set fails → failure #1
    cache.get("key1")             # get fails → failure #2

    stats = cache.stats()
    assert stats["redis_failures"] == 2
    assert stats["redis_last_error"] is not None
    assert "ConnectionError" in stats["redis_last_error"]


def test_stats_redis_failures_reset_not_expected_after_success():
    """Failure counter accumulates; a successful op does not reset it."""
    from unittest.mock import MagicMock

    redis = MagicMock()
    redis.setex.side_effect = [ConnectionError("Redis down"), None]
    redis.get.return_value = None
    cache = FallbackCache(redis_client=redis, default_ttl=300)

    cache.set("key1", "bad")    # fails → counter = 1
    cache.set("key2", "good")   # succeeds → counter stays at 1

    assert cache.stats()["redis_failures"] == 1


def test_stats_redis_failure_on_delete():
    """delete() Redis failure is reflected in the failure counter."""
    from unittest.mock import MagicMock

    redis = MagicMock()
    redis.setex.return_value = None
    redis.delete.side_effect = ConnectionError("Redis down")
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value")
    cache.delete("key1")

    stats = cache.stats()
    assert stats["redis_failures"] == 1
    assert "ConnectionError" in stats["redis_last_error"]


def test_stats_redis_failure_on_invalidate_prefix():
    """invalidate_prefix() Redis failure is reflected in the failure counter."""
    from unittest.mock import MagicMock

    redis = MagicMock()
    redis.setex.return_value = None
    redis.scan.side_effect = ConnectionError("Redis down")
    cache = FallbackCache(redis_client=redis, default_ttl=300, key_prefix="ns:")
    cache.set("key1", "value")
    cache.invalidate_prefix("key")

    stats = cache.stats()
    assert stats["redis_failures"] == 1
    assert "ConnectionError" in stats["redis_last_error"]


def test_stats_redis_failure_on_clear():
    """clear() Redis failure is reflected in the failure counter."""
    from unittest.mock import MagicMock

    redis = MagicMock()
    redis.setex.return_value = None
    redis.delete.side_effect = ConnectionError("Redis down")
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    cache.set("key1", "value")
    cache.clear()

    stats = cache.stats()
    assert stats["redis_failures"] == 1
    assert "ConnectionError" in stats["redis_last_error"]


def test_key_prefix_applied_to_redis(mock_redis):
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300, key_prefix="app:")
    cache.set("key1", "value")
    args = mock_redis.setex.call_args
    assert args[0][0] == "app:key1"


def test_clear_deletes_evicted_redis_keys(mock_redis):
    """clear() must delete Redis keys that were LRU-evicted from in-memory store."""
    max_entries = 3
    cache = FallbackCache(redis_client=mock_redis, default_ttl=300, max_entries=max_entries)

    # Write more keys than max_entries so early keys are evicted from memory
    total = max_entries + 2
    for i in range(total):
        cache.set(f"key{i}", i)

    # The first two keys were evicted from _cache but should still be in _redis_keys
    assert len(cache._cache) == max_entries
    assert len(cache._redis_keys) == total

    cache.clear()

    # All keys — including evicted ones — must have been passed to Redis delete
    all_deleted: set[str] = set()
    for call in mock_redis.delete.call_args_list:
        all_deleted.update(call[0])
    for i in range(total):
        assert f"key{i}" in all_deleted
