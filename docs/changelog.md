# Changelog

## 0.2.1 - 2026-07-26

Repairs `0.2.0`, which was published in a non-functional state, and forward-ports
the `0.1.1` correctness fixes onto the `0.2.x` line. **Anyone on `0.2.0` should
upgrade.**

### Fixed

- **`0.2.0` was unusable with a Redis client.** Its squash-refactor dropped code
  that the rest of the module still referenced, so 48 of 99 tests failed and no
  `get()`/`set()` could complete against Redis. Restored, with the circuit
  breaker preserved:
  - Re-added the `_Entry` record used at construction and throughout the
    in-memory backend but never defined (`NameError: _Entry`).
  - Bound `as exc` in the four failure-counting `except` blocks, which called
    `repr(exc)` on an unbound name and raised `NameError: exc` on every Redis
    failure path.
  - `clear()` and `stats()` no longer reference the removed `_timestamps` /
    `_ttls` dicts or the dropped `datetime` imports. `stats()` derives
    `oldest_age_seconds` from `_Entry.stored_at` via `time.monotonic()`.
- **Fallback stays warm on reads.** `get()` promotes a key in the in-memory LRU
  on a Redis hit, so the fallback holds actively-*read* keys rather than only
  recently-*written* ones — the difference between a useful and a near-empty
  fallback when Redis goes away. An expired in-memory copy is dropped rather
  than promoted.
- **No more phantom Redis outages.** `set()` and `delete()` mutate the internal
  Redis-key tracking set under the lock. A concurrent `clear()` /
  `invalidate_prefix()` rebuilding that set could raise `RuntimeError: Set
  changed size during iteration`, which was swallowed and counted as a Redis
  failure while Redis was perfectly healthy.

### Changed

- **Deserialization errors in `get()` now propagate** instead of being swallowed,
  counted as a Redis failure, and masked by the in-memory copy. This mirrors
  `set()`, which already let serialization errors propagate. If you previously
  relied on a corrupt Redis payload silently falling back to memory, wrap the
  call or supply a deserializer that tolerates bad input.

### Internal

- CI pins `actions/checkout` to v7 and adds a manual SonarCloud workflow.
- Resolved SonarCloud S7504 code smells; no behavior change.

## 0.2.0

> **Do not use this release.** It shipped non-functional — see `0.2.1`, which
> repairs it. The features below are accurate but only work from `0.2.1` onward.

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

## 0.1.0 - 2026-03-27

- Initial release
- `FallbackCache` class with Redis primary + in-memory LRU fallback
- Dual-write on `set()` for resilient fallback reads
- Pluggable serializers (default: JSON)
- Per-key TTL with LRU eviction
- `build_key()` static helper for deterministic cache keys
- `invalidate_prefix()` for bulk key deletion
- `stats()` for cache introspection
