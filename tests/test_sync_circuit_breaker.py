"""Integration tests for FallbackCache with circuit breaker."""
from unittest.mock import MagicMock

from fallback_cache import FallbackCache


def _failing_redis() -> MagicMock:
    """Create a mock Redis where all operations raise ConnectionError."""
    client = MagicMock()
    client.get.side_effect = ConnectionError("Redis down")
    client.setex.side_effect = ConnectionError("Redis down")
    client.delete.side_effect = ConnectionError("Redis down")
    client.scan.side_effect = ConnectionError("Redis down")
    return client


def test_breaker_trips_after_threshold():
    redis = _failing_redis()
    cache = FallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=3,
    )
    # Each set() calls Redis once (fails), so 3 sets = 3 failures = trip
    for i in range(3):
        cache.set(f"key{i}", f"val{i}")

    # Circuit is now open — next get should NOT call Redis
    redis.get.reset_mock()
    cache.get("key0")
    redis.get.assert_not_called()

    # But memory fallback still works
    assert cache.get("key0") == "val0"


def test_memory_fallback_works_while_circuit_open():
    redis = _failing_redis()
    cache = FallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=2,
    )
    cache.set("a", 1)
    cache.set("b", 2)
    # Circuit open after 2 failures
    assert cache.get("a") == 1
    assert cache.get("b") == 2


def test_breaker_probe_after_cooldown():
    redis = _failing_redis()
    cache = FallbackCache(
        redis_client=redis,
        default_ttl=300,
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown=10.0,
    )
    # Trip the breaker
    cache.set("a", 1)
    cache.set("b", 2)

    # Simulate cooldown elapsed
    cache._breaker._last_failure_time -= 15.0

    # Next call should attempt Redis (probe)
    redis.get.reset_mock()
    cache.get("a")
    redis.get.assert_called_once()


def test_breaker_resets_on_redis_recovery():
    redis = MagicMock()
    redis.setex.side_effect = ConnectionError("Redis down")
    cache = FallbackCache(
        redis_client=redis,
        default_ttl=300,
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown=10.0,
    )
    # Trip the breaker
    cache.set("a", 1)
    cache.set("b", 2)

    # Simulate cooldown + Redis recovery
    cache._breaker._last_failure_time -= 15.0
    redis.setex.side_effect = None
    redis.setex.return_value = None

    # Probe succeeds — breaker should reset
    cache.set("c", 3)
    stats = cache.stats()
    assert stats["circuit_breaker_state"] == "closed"
    assert stats["circuit_breaker_failure_count"] == 0


def test_stats_include_circuit_breaker_state():
    cache = FallbackCache(default_ttl=300)
    stats = cache.stats()
    assert "circuit_breaker_state" in stats
    assert stats["circuit_breaker_state"] == "closed"
    assert stats["circuit_breaker_failure_count"] == 0


def test_stats_with_redis_include_circuit_breaker():
    redis = MagicMock()
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    stats = cache.stats()
    assert stats["backend"] == "redis"
    assert "circuit_breaker_state" in stats


def test_delete_skipped_when_circuit_open():
    redis = _failing_redis()
    cache = FallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=2,
    )
    cache.set("a", 1)
    cache.set("b", 2)
    # Circuit open now
    redis.delete.reset_mock()
    cache.delete("a")
    redis.delete.assert_not_called()


def test_invalidate_prefix_skipped_when_circuit_open():
    redis = _failing_redis()
    cache = FallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=2,
    )
    cache.set("users:1", "alice")
    cache.set("users:2", "bob")
    # Circuit open now
    redis.scan.reset_mock()
    cache.invalidate_prefix("users:")
    redis.scan.assert_not_called()
    # Memory cleanup still works
    assert cache.get("users:1") is None


def test_mixed_success_failure_resets_counter():
    redis = MagicMock()
    redis.setex.return_value = None
    cache = FallbackCache(
        redis_client=redis, default_ttl=300, circuit_breaker_threshold=3,
    )
    # 2 failures
    redis.setex.side_effect = ConnectionError("Redis down")
    cache.set("a", 1)
    cache.set("b", 2)
    # 1 success resets counter
    redis.setex.side_effect = None
    cache.set("c", 3)
    # 2 more failures — should NOT trip (counter reset to 0, then 2 < 3)
    redis.setex.side_effect = ConnectionError("Redis down")
    cache.set("d", 4)
    cache.set("e", 5)
    stats = cache.stats()
    assert stats["circuit_breaker_state"] == "closed"
