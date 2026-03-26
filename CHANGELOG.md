# Changelog

## 0.1.0 (Unreleased)

- Initial release
- `FallbackCache` class with Redis primary + in-memory LRU fallback
- Dual-write on `set()` for resilient fallback reads
- Pluggable serializers (default: JSON)
- Per-key TTL with LRU eviction
- `build_key()` static helper for deterministic cache keys
- `invalidate_prefix()` for bulk key deletion
- `stats()` for cache introspection
