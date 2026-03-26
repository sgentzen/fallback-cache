"""Tests for pluggable serializer/deserializer."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fallback_cache import FallbackCache


def test_default_json_serializer():
    cache = FallbackCache(default_ttl=300)
    cache.set("key1", {"number": 42, "nested": {"a": 1}})
    assert cache.get("key1") == {"number": 42, "nested": {"a": 1}}


def test_default_json_handles_datetime():
    redis = MagicMock()
    redis.setex.return_value = None
    cache = FallbackCache(redis_client=redis, default_ttl=300)
    now = datetime.now(timezone.utc)
    cache.set("key1", {"timestamp": now})
    serialized = redis.setex.call_args[0][2]
    parsed = json.loads(serialized)
    assert parsed["timestamp"] == str(now)


def test_custom_serializer(mock_redis):
    calls = []

    def my_serializer(data):
        calls.append(("serialize", data))
        return json.dumps(data)

    def my_deserializer(raw):
        calls.append(("deserialize", raw))
        return json.loads(raw)

    mock_redis.get.return_value = json.dumps({"v": 1})
    mock_redis.setex.return_value = None
    cache = FallbackCache(
        redis_client=mock_redis,
        default_ttl=300,
        serializer=my_serializer,
        deserializer=my_deserializer,
    )
    cache.set("k", {"v": 1})
    cache.get("k")
    assert ("serialize", {"v": 1}) in calls
    assert any(op == "deserialize" for op, _ in calls)


def test_serializer_error_does_not_prevent_memory_write():
    """If serializer raises during Redis write, memory still gets the data."""
    def bad_serializer(data):
        raise TypeError("Cannot serialize")

    redis = MagicMock()
    cache = FallbackCache(
        redis_client=redis,
        default_ttl=300,
        serializer=bad_serializer,
    )
    cache.set("k", {"v": 1})
    full_key = cache._full_key("k")
    assert cache._cache[full_key] == {"v": 1}
