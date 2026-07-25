# Changelog

## 0.2.0

- `AsyncFallbackCache` for async/await usage with `redis.asyncio`
- Built-in circuit breaker for both sync and async classes
  - Configurable threshold and cooldown
  - Three states: closed, open, half_open
  - Exposed in `stats()` output
- `build_key()` available as a standalone function
- MkDocs documentation site with Material theme
- PyPI publish workflow with OIDC trusted publishing
- Dependabot configuration for pip and GitHub Actions
- Coverage threshold (90%) in CI
- Community files: CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
- GitHub issue and PR templates

## 0.1.1 - 2026-06-20

### Bug Fixes

- `get()` now keeps the in-memory fallback warm with actively-read keys by
  promoting them on a Redis hit. The fallback stays warm with *hot* keys instead
  of only the most recently *written* ones, so it is genuinely useful if Redis
  later becomes unavailable. Expired in-memory copies are dropped rather than
  promoted.
- `set()` and `delete()` now mutate the internal Redis-key tracking set under the
  lock. Previously a concurrent `clear()` / `invalidate_prefix()` rebuilding that
  set could raise `RuntimeError: Set changed size during iteration`, which was
  swallowed and miscounted as a Redis failure — a phantom outage with Redis
  perfectly healthy.
- Deserialization errors in `get()` now propagate to the caller instead of being
  swallowed, miscounted as a Redis failure, and silently masked by the in-memory
  copy. This mirrors `set()`, which already lets serialization errors propagate.

## 0.1.0

- Initial release
- `FallbackCache` class with Redis primary + in-memory LRU fallback
- Dual-write on `set()` for resilient fallback reads
- Pluggable serializers (default: JSON)
- Per-key TTL with LRU eviction
- `build_key()` static helper for deterministic cache keys
- `invalidate_prefix()` for bulk key deletion
- `stats()` for cache introspection
