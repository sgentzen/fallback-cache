"""Tests for the shared _BaseCache: config handling and the storage hook."""
import inspect
import threading
from collections import OrderedDict

import pytest

from fallback_cache import AsyncFallbackCache, FallbackCache
from fallback_cache._base import _BaseCache

_CACHE_CLASSES = [FallbackCache, AsyncFallbackCache]


def test_base_cache_requires_a_storage_hook():
    """_BaseCache is not usable on its own — subclasses must set up storage."""
    with pytest.raises(NotImplementedError):
        _BaseCache()


def test_sync_and_async_constructors_stay_identical():
    """Both classes inherit one __init__, so neither can drift from the other.

    If a subclass ever declares its own __init__, this catches the divergence
    immediately rather than leaving the two public signatures to drift.
    """
    assert inspect.signature(FallbackCache.__init__) == inspect.signature(
        AsyncFallbackCache.__init__
    )


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_every_constructor_argument_reaches_the_instance(cls):
    """All eight shared arguments are stored, not just the ones used in passing."""

    def my_serializer(value):
        return str(value)

    def my_deserializer(raw):
        return raw

    redis_client = object()
    cache = cls(
        redis_client=redis_client,
        default_ttl=42,
        max_entries=7,
        key_prefix="app:",
        serializer=my_serializer,
        deserializer=my_deserializer,
        circuit_breaker_threshold=9,
        circuit_breaker_cooldown=1.5,
    )

    assert isinstance(cache, _BaseCache)
    assert cache._redis is redis_client
    assert cache._default_ttl == 42
    assert cache._max_entries == 7
    assert cache._key_prefix == "app:"
    assert cache._serializer is my_serializer
    assert cache._deserializer is my_deserializer
    assert cache._breaker._threshold == 9
    assert cache._breaker._cooldown == 1.5


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_full_key_prefixes_only_when_a_prefix_is_configured(cls):
    assert cls(key_prefix="app:")._full_key("k") == "app:k"
    assert cls()._full_key("k") == "k"


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_both_caches_derive_keys_identically(cls):
    assert cls.build_key("u", id=1) == FallbackCache.build_key("u", id=1)


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_both_caches_reject_a_non_positive_default_ttl(cls):
    with pytest.raises(ValueError, match="default_ttl must be positive"):
        cls(default_ttl=0)


def test_sync_storage_hook_sets_up_every_attribute_the_class_relies_on():
    """_init_storage runs during __init__ and leaves the sync internals ready."""
    cache = FallbackCache()

    assert cache._cache == OrderedDict()
    assert isinstance(cache._lock, type(threading.RLock()))
    assert cache._redis_keys == set()
    assert cache._redis_failures == 0
    assert cache._redis_last_error is None
    # stats() reads all of the above, so it must work on a fresh instance.
    assert cache.stats()["entries"] == 0


def test_async_storage_hook_sets_up_every_attribute_the_class_relies_on():
    cache = AsyncFallbackCache()

    assert cache._cache == OrderedDict()
    assert cache._timestamps == {}
    assert cache._ttls == {}
    assert cache.stats()["entries"] == 0


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_storage_hook_runs_after_the_shared_config_is_applied(cls):
    """_init_storage may rely on config the base has already assigned."""
    seen = {}

    class _Probe(cls):
        def _init_storage(self):
            seen["default_ttl"] = self._default_ttl
            seen["key_prefix"] = self._key_prefix
            super()._init_storage()

    _Probe(default_ttl=11, key_prefix="p:")
    assert seen == {"default_ttl": 11, "key_prefix": "p:"}


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_both_caches_refuse_a_prefix_that_would_match_everything(cls):
    """An empty full prefix would scan-and-delete the whole Redis keyspace.

    The sync class always guarded this; the async class did not, so the two
    disagreed on a call that could wipe a shared Redis DB. The guard now lives
    on the shared base and neither class can lose it independently.
    """
    with pytest.raises(ValueError, match="non-empty prefix"):
        cls()._full_prefix("")

    # A configured key_prefix alone is enough to scope the operation.
    assert cls(key_prefix="app:")._full_prefix("") == "app:"
    assert cls(key_prefix="app:")._full_prefix("users:") == "app:users:"


@pytest.mark.parametrize("cls", _CACHE_CLASSES)
def test_both_caches_resolve_ttl_identically(cls):
    cache = cls(default_ttl=30)
    assert cache._effective_ttl(None) == 30
    assert cache._effective_ttl(5) == 5
    with pytest.raises(ValueError, match="TTL must be positive"):
        cache._effective_ttl(0)
    with pytest.raises(ValueError, match="TTL must be positive"):
        cache._effective_ttl(-1)
