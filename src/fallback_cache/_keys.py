"""Deterministic cache key generation."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def build_key(prefix: str, **params: Any) -> str:
    """Build a deterministic, content-addressed cache key.

    None-valued params are excluded. Remaining params are sorted,
    JSON-serialized, and SHA-256 hashed (first 12 hex chars).
    Returns 'prefix:<hash>'.
    """
    filtered = {k: v for k, v in params.items() if v is not None}
    canonical = json.dumps(filtered, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"
