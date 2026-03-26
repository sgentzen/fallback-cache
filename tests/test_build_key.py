"""Tests for FallbackCache.build_key() static helper."""
from fallback_cache import FallbackCache


def test_build_key_basic():
    key = FallbackCache.build_key("users", user_id="123")
    assert key.startswith("users:")
    assert len(key) == len("users:") + 12


def test_build_key_deterministic():
    k1 = FallbackCache.build_key("ns", a="1", b="2")
    k2 = FallbackCache.build_key("ns", b="2", a="1")
    assert k1 == k2


def test_build_key_none_params_excluded():
    k1 = FallbackCache.build_key("ns", a="1")
    k2 = FallbackCache.build_key("ns", a="1", b=None)
    assert k1 == k2


def test_build_key_different_prefix():
    k1 = FallbackCache.build_key("users", id="1")
    k2 = FallbackCache.build_key("items", id="1")
    assert k1 != k2


def test_build_key_different_params():
    k1 = FallbackCache.build_key("ns", id="1")
    k2 = FallbackCache.build_key("ns", id="2")
    assert k1 != k2
