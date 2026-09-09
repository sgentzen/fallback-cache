"""Tests for FallbackCache.build_key() static helper."""
import pytest

from fallback_cache import AsyncFallbackCache, FallbackCache, build_key
from fallback_cache._keys import DEFAULT_DIGEST_LENGTH, MAX_DIGEST_LENGTH


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


# ------------------------------------------------------------------
# digest_length
# ------------------------------------------------------------------


def test_default_digest_length_is_unchanged():
    """The default must stay 12 so upgrading does not invalidate deployed caches."""
    assert DEFAULT_DIGEST_LENGTH == 12
    key = build_key("users", user_id="123")
    # Pinned literally: this is the value already written into deployed caches,
    # so a change here silently invalidates every one of them.
    assert key == "users:bb73de0958e0"


def test_digest_length_widens_the_hash():
    key = build_key("search", 32, q="term")
    assert len(key.split(":", 1)[1]) == 32
    assert key.startswith("search:")


def test_widening_is_a_prefix_of_the_narrower_digest():
    """Both truncate the same SHA-256, so 12 chars is the head of 32."""
    narrow = build_key("ns", 12, a="1").split(":", 1)[1]
    wide = build_key("ns", 32, a="1").split(":", 1)[1]
    assert wide.startswith(narrow)


def test_digest_length_changes_the_key():
    """Documented consequence: a cache written at one width will not read another."""
    assert build_key("ns", 12, a="1") != build_key("ns", 32, a="1")


@pytest.mark.parametrize("bad", [0, -1, MAX_DIGEST_LENGTH + 1, 999])
def test_out_of_range_digest_length_is_rejected(bad):
    with pytest.raises(ValueError, match="digest_length must be between"):
        build_key("ns", bad, a="1")


@pytest.mark.parametrize("bad", [True, False, "32", 12.9, None, b"32"])
def test_non_int_digest_length_is_rejected(bad):
    """bool is an int subclass and a valid slice index.

    Left unchecked, ``True`` would truncate the digest to a single hex
    character — 16 possible keys for the whole namespace — while looking like
    an ordinary call.
    """
    with pytest.raises(TypeError, match="digest_length must be an int"):
        build_key("ns", bad, a="1")


def test_minimum_digest_length_is_accepted():
    """1 is a legal, if unwise, width — the boundary must not be off by one."""
    assert len(build_key("ns", 1, a="1").split(":", 1)[1]) == 1


def test_max_digest_length_is_the_full_sha256():
    assert MAX_DIGEST_LENGTH == 64
    assert len(build_key("ns", MAX_DIGEST_LENGTH, a="1").split(":", 1)[1]) == 64


def test_digest_length_and_prefix_are_not_reserved_param_names():
    """Both are positional-only, so either name is still usable as a cache param."""
    assert build_key("ns", digest_length="a-value") != build_key("ns")
    assert build_key("ns", prefix="a-value") != build_key("ns")
    # Passed as a param it is hashed, not interpreted: the digest stays 12 wide.
    assert len(build_key("ns", digest_length=32).split(":", 1)[1]) == 12


def test_both_cache_classes_expose_the_same_digest_length_behaviour():
    for cls in (FallbackCache, AsyncFallbackCache):
        assert cls.build_key("u", id=1) == build_key("u", id=1)
        assert cls.build_key("u", 32, id=1) == build_key("u", 32, id=1)
