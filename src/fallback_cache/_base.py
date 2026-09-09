"""Configuration and key helpers shared by the sync and async caches."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fallback_cache._circuit_breaker import CircuitBreaker
from fallback_cache._keys import DEFAULT_DIGEST_LENGTH
from fallback_cache._keys import build_key as _build_key
from fallback_cache._serializers import DEFAULT_DESERIALIZER, default_serializer

# Redis glob metacharacters, per its stringmatchlen matcher. Each is escaped
# with a backslash so a prefix containing one is matched literally.
_GLOB_METACHARACTERS = str.maketrans({char: "\\" + char for char in r"\*?[]"})


class _BaseCache:
    """Common constructor arguments, validation, and key helpers.

    ``FallbackCache`` and ``AsyncFallbackCache`` accept the same configuration
    and derive keys identically; only their storage internals and their Redis
    call style (blocking vs awaited) differ. Both live here so the two public
    classes cannot drift apart.

    Not part of the public API.
    """

    def __init__(
        self,
        redis_client: Any = None,
        default_ttl: int = 300,
        max_entries: int = 100,
        key_prefix: str = "",
        serializer: Callable[[Any], str | bytes] = default_serializer,
        deserializer: Callable[[str | bytes], Any] = DEFAULT_DESERIALIZER,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: float = 30.0,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError(f"default_ttl must be positive, got {default_ttl}")

        self._redis = redis_client
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._key_prefix = key_prefix
        self._serializer = serializer
        self._deserializer = deserializer
        self._breaker = CircuitBreaker(
            threshold=circuit_breaker_threshold,
            cooldown=circuit_breaker_cooldown,
        )
        self._init_storage()

    def _init_storage(self) -> None:
        """Set up the subclass's in-memory storage.

        A template-method hook rather than a second ``__init__`` in each
        subclass: the two public constructors take exactly the same arguments,
        so declaring the signature once here keeps them from drifting apart.
        """
        raise NotImplementedError

    def _effective_ttl(self, ttl: int | None) -> int:
        """Resolve a per-call TTL against the default, rejecting non-positive values."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            raise ValueError(f"TTL must be positive, got {effective_ttl}")
        return effective_ttl

    def _scan_pattern(self, full_prefix: str) -> str:
        """Build a Redis ``SCAN MATCH`` pattern matching ``full_prefix`` literally.

        Redis globs treat ``*``, ``?``, ``[``, ``]`` and backslash specially, so an
        unescaped prefix carrying any of them matches more keys than the caller
        named — ``invalidate_prefix(f"user:{user_id}:")`` with a ``user_id`` of
        ``*`` would purge every user. Escaping also keeps the two backends in
        step: the in-memory sweep matches with ``str.startswith``, which has no
        wildcards, so an unescaped pattern made Redis and memory delete
        different key sets.
        """
        return f"{full_prefix.translate(_GLOB_METACHARACTERS)}*"

    def _full_prefix(self, prefix: str) -> str:
        """Resolve a prefix for invalidation, refusing one that matches everything.

        An empty result would scan-and-delete the whole Redis keyspace, taking
        any co-located non-cache data with it, so it is rejected outright.
        A configured ``key_prefix`` is enough to scope the operation.
        """
        full_prefix = self._key_prefix + prefix
        if not full_prefix:
            raise ValueError("invalidate_prefix requires a non-empty prefix or key_prefix")
        return full_prefix

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _full_key(self, key: str) -> str:
        """Prepend key_prefix to the key if configured."""
        return f"{self._key_prefix}{key}" if self._key_prefix else key

    @staticmethod
    def build_key(
        prefix: str,
        digest_length: int = DEFAULT_DIGEST_LENGTH,
        /,
        **params: Any,
    ) -> str:
        """Build a deterministic, content-addressed cache key.

        Exposed on both cache classes for convenience; see
        :func:`fallback_cache.build_key` for the full contract, including the
        collision-resistance note on ``digest_length`` and untrusted params.
        """
        return _build_key(prefix, digest_length, **params)
