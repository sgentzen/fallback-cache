"""Deterministic cache key generation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

#: Hex characters of SHA-256 kept by default. 12 chars is 48 bits, which is the
#: width this library has always emitted; it is kept as the default so upgrading
#: does not change any existing key and invalidate deployed caches.
DEFAULT_DIGEST_LENGTH = 12

#: A SHA-256 hex digest is 64 characters, the most that can be kept.
MAX_DIGEST_LENGTH = 64


def build_key(
    prefix: str,
    digest_length: int = DEFAULT_DIGEST_LENGTH,
    /,
    **params: Any,
) -> str:
    """Build a deterministic, content-addressed cache key.

    None-valued params are excluded. Remaining params are sorted,
    JSON-serialized, and SHA-256 hashed, keeping the first ``digest_length``
    hex characters. Returns ``'prefix:<hash>'``.

    ``prefix`` and ``digest_length`` are positional-only, so neither name is
    reserved: a param of either name still becomes part of the hashed payload.

    **Collision resistance.** The default of 12 hex characters is 48 bits, so a
    birthday collision costs roughly 2**24 offline trials — seconds of work.
    That is fine for keys built from trusted values, but if any param is
    attacker-controlled, an attacker who finds a colliding pair can seed the
    entry another caller then reads. Pass a wider ``digest_length`` (32, or 128
    bits, is ample) whenever untrusted input reaches a key::

        build_key("search", 32, q=user_query, tenant=tenant_id)

    Widening changes the key, so a cache built with one width will not find
    entries written with another.

    **Param contract:** all values must be JSON-serializable (str, int, float,
    bool, None, list, dict with string keys). Sets, bare objects, and other
    non-serializable types will raise ``TypeError``. If you need to include a
    custom type, convert it to a string or dict first.
    """
    # bool is an int subclass and a valid slice index, so True would silently
    # truncate the digest to a single hex character — 16 possible keys for the
    # whole namespace. Reject it, and every other non-int, before the range check.
    if isinstance(digest_length, bool) or not isinstance(digest_length, int):
        raise TypeError(
            f"digest_length must be an int, got {type(digest_length).__name__}"
        )
    if not 1 <= digest_length <= MAX_DIGEST_LENGTH:
        raise ValueError(
            f"digest_length must be between 1 and {MAX_DIGEST_LENGTH}, got {digest_length}"
        )

    filtered = {k: v for k, v in params.items() if v is not None}
    canonical = json.dumps(filtered, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:digest_length]
    return f"{prefix}:{digest}"
