"""Tests for in-memory backend (no Redis)."""
import pytest

from fallback_cache import FallbackCache


def test_set_and_get_round_trip():
    cache = FallbackCache(default_ttl=300)
    cache.set("key1", {"name": "Alice"})
    assert cache.get("key1") == {"name": "Alice"}


def test_get_missing_key_returns_none():
    cache = FallbackCache(default_ttl=300)
    assert cache.get("nonexistent") is None


def test_delete_existing_key():
    cache = FallbackCache(default_ttl=300)
    cache.set("key1", "value1")
    assert cache.delete("key1") is True
    assert cache.get("key1") is None


def test_delete_missing_key_returns_false():
    cache = FallbackCache(default_ttl=300)
    assert cache.delete("nonexistent") is False


def test_set_overwrites_existing_key():
    cache = FallbackCache(default_ttl=300)
    cache.set("key1", "old")
    cache.set("key1", "new")
    assert cache.get("key1") == "new"


def test_ttl_zero_raises_value_error():
    cache = FallbackCache(default_ttl=300)
    with pytest.raises(ValueError):
        cache.set("key1", "value", ttl=0)


def test_negative_ttl_raises_value_error():
    cache = FallbackCache(default_ttl=300)
    with pytest.raises(ValueError):
        cache.set("key1", "value", ttl=-5)


def test_default_ttl_zero_raises_value_error():
    with pytest.raises(ValueError):
        FallbackCache(default_ttl=0)


def test_ttl_expiration():
    cache = FallbackCache(default_ttl=10)
    cache.set("key1", "value1")
    # Simulate time passing beyond TTL
    full_key = cache._full_key("key1")
    # Backdate stored_at so the entry appears 20 s old (TTL is 10 s)
    entry = cache._cache[full_key]
    cache._cache[full_key] = entry._replace(stored_at=entry.stored_at - 20)
    assert cache.get("key1") is None


def test_per_key_ttl_overrides_default():
    cache = FallbackCache(default_ttl=10)
    cache.set("short", "val", ttl=1)
    cache.set("long", "val", ttl=9999)
    # Backdate stored_at by 5 s so "short" (ttl=1) expires but "long" (ttl=9999) does not
    for k in cache._cache:
        entry = cache._cache[k]
        cache._cache[k] = entry._replace(stored_at=entry.stored_at - 5)
    assert cache.get("short") is None
    assert cache.get("long") == "val"


def test_lru_eviction_at_capacity():
    cache = FallbackCache(default_ttl=300, max_entries=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    cache.set("d", 4)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("d") == 4


def test_lru_access_promotes_key():
    cache = FallbackCache(default_ttl=300, max_entries=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    cache.get("a")  # promote
    cache.set("d", 4)  # should evict "b", not "a"
    assert cache.get("a") == 1
    assert cache.get("b") is None


def test_invalidate_prefix():
    cache = FallbackCache(default_ttl=300)
    cache.set("users:1", "alice")
    cache.set("users:2", "bob")
    cache.set("items:1", "widget")
    cache.invalidate_prefix("users:")
    assert cache.get("users:1") is None
    assert cache.get("users:2") is None
    assert cache.get("items:1") == "widget"


def test_clear_removes_all():
    cache = FallbackCache(default_ttl=300)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_stats_memory_mode():
    cache = FallbackCache(default_ttl=300, max_entries=50)
    cache.set("a", 1)
    cache.set("b", 2)
    stats = cache.stats()
    assert stats["backend"] == "memory"
    assert stats["entries"] == 2
    assert stats["max_entries"] == 50
    assert "oldest_age_seconds" in stats


def test_stats_oldest_age_seconds_monotonic():
    """oldest_age_seconds reflects time elapsed since the oldest entry was written."""
    cache = FallbackCache(default_ttl=300)
    cache.set("early", "value")
    full_key = cache._full_key("early")

    # Backdate the oldest entry's stored_at by a known delta
    backdated_seconds = 42.0
    entry = cache._cache[full_key]
    cache._cache[full_key] = entry._replace(stored_at=entry.stored_at - backdated_seconds)

    stats = cache.stats()
    age = stats["oldest_age_seconds"]
    assert isinstance(age, float), f"expected float, got {type(age)}"
    assert age >= backdated_seconds, f"age {age:.2f}s < expected {backdated_seconds}s"
    assert age < backdated_seconds + 5, f"age {age:.2f}s implausibly large (clock mismatch?)"


def test_key_prefix_prepended():
    cache = FallbackCache(default_ttl=300, key_prefix="myapp:")
    cache.set("key1", "value1")
    assert "myapp:key1" in cache._cache
    assert cache.get("key1") == "value1"


def test_invalidate_prefix_with_key_prefix():
    cache = FallbackCache(default_ttl=300, key_prefix="myapp:")
    cache.set("users:1", "alice")
    cache.set("users:2", "bob")
    cache.set("items:1", "widget")
    cache.invalidate_prefix("users:")
    assert cache.get("users:1") is None
    assert cache.get("users:2") is None
    assert cache.get("items:1") == "widget"
