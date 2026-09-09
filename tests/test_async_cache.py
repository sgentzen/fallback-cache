"""Tests for AsyncFallbackCache."""
import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from fallback_cache import AsyncFallbackCache

# ------------------------------------------------------------------
# Memory-only tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_round_trip():
    cache = AsyncFallbackCache(default_ttl=300)
    await cache.set("key1", {"name": "Alice"})
    assert await cache.get("key1") == {"name": "Alice"}


@pytest.mark.asyncio
async def test_get_missing_key_returns_none():
    cache = AsyncFallbackCache(default_ttl=300)
    assert await cache.get("nonexistent") is None


@pytest.mark.asyncio
async def test_delete_existing_key():
    cache = AsyncFallbackCache(default_ttl=300)
    await cache.set("key1", "value1")
    assert await cache.delete("key1") is True
    assert await cache.get("key1") is None


@pytest.mark.asyncio
async def test_delete_missing_key_returns_false():
    cache = AsyncFallbackCache(default_ttl=300)
    assert await cache.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_ttl_expiration():
    cache = AsyncFallbackCache(default_ttl=10)
    await cache.set("key1", "value1")
    full_key = cache._full_key("key1")
    cache._timestamps[full_key] -= 20
    assert await cache.get("key1") is None


@pytest.mark.asyncio
async def test_lru_eviction_at_capacity():
    cache = AsyncFallbackCache(default_ttl=300, max_entries=3)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)
    await cache.set("d", 4)
    assert await cache.get("a") is None
    assert await cache.get("b") == 2


@pytest.mark.asyncio
async def test_invalidate_prefix():
    cache = AsyncFallbackCache(default_ttl=300)
    await cache.set("users:1", "alice")
    await cache.set("users:2", "bob")
    await cache.set("items:1", "widget")
    await cache.invalidate_prefix("users:")
    assert await cache.get("users:1") is None
    assert await cache.get("items:1") == "widget"


@pytest.mark.asyncio
async def test_clear_removes_all():
    cache = AsyncFallbackCache(default_ttl=300)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.clear()
    assert await cache.get("a") is None
    assert await cache.get("b") is None


@pytest.mark.asyncio
async def test_stats_memory_mode():
    cache = AsyncFallbackCache(default_ttl=300, max_entries=50)
    await cache.set("a", 1)
    stats = cache.stats()
    assert stats["backend"] == "memory"
    assert stats["entries"] == 1
    assert stats["circuit_breaker_state"] == "closed"


@pytest.mark.asyncio
async def test_ttl_validation():
    cache = AsyncFallbackCache(default_ttl=300)
    with pytest.raises(ValueError):
        await cache.set("k", "v", ttl=0)


@pytest.mark.asyncio
async def test_default_ttl_zero_raises():
    with pytest.raises(ValueError):
        AsyncFallbackCache(default_ttl=0)


# ------------------------------------------------------------------
# Redis backend tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_calls_redis_setex(mock_async_redis):
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.set("key1", {"name": "Alice"})
    mock_async_redis.setex.assert_called_once()
    args = mock_async_redis.setex.call_args[0]
    assert args[0] == "key1"
    assert args[1] == 300


@pytest.mark.asyncio
async def test_get_calls_redis_get(mock_async_redis):
    mock_async_redis.get.return_value = json.dumps({"name": "Alice"})
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    result = await cache.get("key1")
    mock_async_redis.get.assert_called_once_with("key1")
    assert result == {"name": "Alice"}


@pytest.mark.asyncio
async def test_get_returns_none_on_redis_miss(mock_async_redis):
    mock_async_redis.get.return_value = None
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    result = await cache.get("key1")
    assert result is None


@pytest.mark.asyncio
async def test_stats_redis_mode(mock_async_redis):
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.set("a", 1)
    stats = cache.stats()
    assert stats["backend"] == "redis"
    assert stats["memory_entries"] == 1


# ------------------------------------------------------------------
# Fallback tests
# ------------------------------------------------------------------


def _failing_async_redis() -> AsyncMock:
    """Create a mock async Redis where all operations raise ConnectionError."""
    client = AsyncMock()
    client.get.side_effect = ConnectionError("Redis down")
    client.setex.side_effect = ConnectionError("Redis down")
    client.delete.side_effect = ConnectionError("Redis down")
    client.scan.side_effect = ConnectionError("Redis down")
    return client


@pytest.mark.asyncio
async def test_get_falls_back_to_memory_on_redis_error():
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(redis_client=redis, default_ttl=300)
    await cache.set("key1", {"v": 1})
    result = await cache.get("key1")
    assert result == {"v": 1}


@pytest.mark.asyncio
async def test_delete_cleans_memory_when_redis_fails():
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(redis_client=redis, default_ttl=300)
    await cache.set("key1", "value1")
    result = await cache.delete("key1")
    assert result is True
    assert await cache.get("key1") is None


# ------------------------------------------------------------------
# Circuit breaker tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_trips():
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=2,
    )
    await cache.set("a", 1)
    await cache.set("b", 2)
    # Circuit open — Redis should not be called
    redis.get.reset_mock()
    await cache.get("a")
    redis.get.assert_not_called()
    # Memory still works
    assert await cache.get("a") == 1


@pytest.mark.asyncio
async def test_circuit_breaker_probe_after_cooldown():
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(
        redis_client=redis, default_ttl=300,
        circuit_breaker_threshold=2, circuit_breaker_cooldown=10.0,
    )
    await cache.set("a", 1)
    await cache.set("b", 2)
    cache._breaker._last_failure_time -= 15.0
    redis.get.reset_mock()
    await cache.get("a")
    redis.get.assert_called_once()


# ------------------------------------------------------------------
# Serializer tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_json_handles_datetime(mock_async_redis):
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    now = datetime.now(timezone.utc)
    await cache.set("key1", {"timestamp": now})
    serialized = mock_async_redis.setex.call_args[0][2]
    parsed = json.loads(serialized)
    assert parsed["timestamp"] == str(now)


@pytest.mark.asyncio
async def test_build_key_static():
    key = AsyncFallbackCache.build_key("users", user_id="123")
    assert key.startswith("users:")
    assert len(key) == len("users:") + 12


# ------------------------------------------------------------------
# Coverage gap tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_calls_redis_delete(mock_async_redis):
    mock_async_redis.delete.return_value = 1
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.set("key1", "value")
    result = await cache.delete("key1")
    assert result is True
    mock_async_redis.delete.assert_called_with("key1")


@pytest.mark.asyncio
async def test_invalidate_prefix_with_redis(mock_async_redis):
    mock_async_redis.scan.return_value = (0, [b"users:1", b"users:2"])
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.invalidate_prefix("users:")
    mock_async_redis.scan.assert_called()
    mock_async_redis.delete.assert_called_once_with(b"users:1", b"users:2")


@pytest.mark.asyncio
async def test_clear_with_redis(mock_async_redis):
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.clear()
    assert await cache.get("a") is None
    # Redis delete was called during clear
    assert mock_async_redis.delete.called


@pytest.mark.asyncio
async def test_key_prefix_applied():
    cache = AsyncFallbackCache(default_ttl=300, key_prefix="myapp:")
    await cache.set("key1", "value1")
    assert "myapp:key1" in cache._cache
    assert await cache.get("key1") == "value1"


@pytest.mark.asyncio
async def test_invalidate_prefix_raises_on_empty_prefix():
    """Without this guard the SCAN pattern is "*" and the whole DB is deleted."""
    cache = AsyncFallbackCache(default_ttl=300)
    with pytest.raises(ValueError, match="non-empty prefix"):
        await cache.invalidate_prefix("")


@pytest.mark.asyncio
async def test_invalidate_prefix_empty_prefix_with_key_prefix_is_allowed():
    """key_prefix alone is sufficient to scope the operation."""
    cache = AsyncFallbackCache(default_ttl=300, key_prefix="app:")
    await cache.set("users:1", "alice")
    await cache.set("users:2", "bob")
    await cache.invalidate_prefix("")  # full_prefix == "app:" — safe
    assert await cache.get("users:1") is None
    assert await cache.get("users:2") is None


@pytest.mark.asyncio
async def test_invalidate_prefix_cleans_memory_when_redis_fails():
    """A Redis outage during invalidate_prefix must not surface to the caller.

    The in-memory sweep still has to run, or the fallback keeps serving entries
    the caller just invalidated.
    """
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(redis_client=redis, default_ttl=300)
    await cache.set("users:1", "alice")
    await cache.set("users:2", "bob")
    await cache.set("items:1", "widget")

    await cache.invalidate_prefix("users:")   # must not raise

    assert await cache.get("users:1") is None
    assert await cache.get("users:2") is None
    assert await cache.get("items:1") == "widget"
    assert cache.stats()["circuit_breaker_failure_count"] > 0


@pytest.mark.asyncio
async def test_clear_cleans_memory_when_redis_fails():
    """clear() must empty memory even when the Redis delete raises."""
    redis = _failing_async_redis()
    cache = AsyncFallbackCache(redis_client=redis, default_ttl=300)
    await cache.set("a", 1)
    await cache.set("b", 2)

    await cache.clear()                       # must not raise

    assert await cache.get("a") is None
    assert await cache.get("b") is None
    # A Redis-backed cache reports its in-memory size as "memory_entries".
    stats = cache.stats()
    assert stats["memory_entries"] == 0
    # The failure was recorded, not merely swallowed by a bare except.
    assert stats["circuit_breaker_failure_count"] > 0


@pytest.mark.asyncio
async def test_set_overwrites_existing_key_and_refreshes_lru_position():
    """Re-setting a key replaces the value in place; it never evicts another key.

    The overwritten key is deliberately *not* the least-recently-used one. If
    the overwrite branch were dropped and every write went through eviction,
    the cache would evict the LRU key ("a") to make room for a key it already
    held — which overwriting the LRU key would have hidden, since evicting and
    reinserting it yields the same state.
    """
    cache = AsyncFallbackCache(default_ttl=300, max_entries=3)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)                   # at capacity; LRU order a, b, c

    await cache.set("b", 99)                  # overwrite the middle key
    # Inspected directly rather than via get(), which would itself promote the
    # key and reorder the very thing under test.
    assert cache.stats()["entries"] == 3      # nothing was evicted
    assert "a" in cache._cache                # the LRU key survived
    assert cache._cache["b"] == 99

    # The overwrite refreshed "b", so the next insert evicts "a", not "b".
    await cache.set("d", 4)
    assert "a" not in cache._cache
    assert cache._cache["b"] == 99
    assert cache._cache["d"] == 4


@pytest.mark.asyncio
async def test_invalidate_prefix_escapes_glob_metacharacters(mock_async_redis):
    """A prefix containing glob characters must match literally, not as a wildcard.

    Unescaped, `invalidate_prefix("user:*:")` scans `user:*:*` and deletes every
    user's keys rather than the one literally named `user:*:`.
    """
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    await cache.invalidate_prefix("user:*:")

    pattern = mock_async_redis.scan.call_args.kwargs["match"]
    assert pattern == r"user:\*:*"


@pytest.mark.asyncio
async def test_invalidate_prefix_escapes_every_redis_metacharacter(mock_async_redis):
    """Mirrors the sync coverage so the two classes cannot drift apart."""
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    for prefix, expected in [
        ("plain:", "plain:*"),
        ("a?b:", r"a\?b:*"),
        ("x[0-9]:", r"x\[0-9\]:*"),
        ("back\\slash:", "back\\\\slash:*"),
    ]:
        await cache.invalidate_prefix(prefix)
        assert mock_async_redis.scan.call_args.kwargs["match"] == expected, prefix


@pytest.mark.asyncio
async def test_invalidate_prefix_leaves_no_unescaped_wildcard_in_the_pattern(mock_async_redis):
    """The blast-radius invariant: the only wildcard is the trailing one."""
    cache = AsyncFallbackCache(redis_client=mock_async_redis, default_ttl=300)
    for hostile in ("user:*:", "a?b:", "x[0-9]:", "*", "?", "[a-z]"):
        await cache.invalidate_prefix(hostile)
        pattern = mock_async_redis.scan.call_args.kwargs["match"]

        assert pattern.endswith("*"), hostile
        unescaped = re.sub(r"\\.", "", pattern[:-1])
        assert not set(unescaped) & set("*?[]"), (hostile, pattern)
