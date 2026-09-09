"""Tests for AsyncFallbackCache."""
import json
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
