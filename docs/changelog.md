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

## 0.1.0

- Initial release
- `FallbackCache` class with Redis primary + in-memory LRU fallback
- Dual-write on `set()` for resilient fallback reads
- Pluggable serializers (default: JSON)
- Per-key TTL with LRU eviction
- `build_key()` static helper for deterministic cache keys
- `invalidate_prefix()` for bulk key deletion
- `stats()` for cache introspection
