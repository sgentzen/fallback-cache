# Changelog

## Unreleased

### Security

- **A prefix containing glob characters no longer over-matches in Redis.**
  `invalidate_prefix()` interpolated the caller's prefix straight into a Redis
  `SCAN MATCH` pattern, so `*`, `?`, `[`, `]` and backslash were treated as
  wildcards. An application building a prefix from request data — say
  `invalidate_prefix(f"user:{user_id}:")` — could be made to purge far more
  than it named by supplying `*` as the id. The prefix is now escaped and
  matched literally.

  This also settles a divergence between the two backends: the in-memory sweep
  matches with `str.startswith`, which has no wildcards, so Redis and memory
  previously deleted different key sets. A prefix of `x[0-9]:` was the starkest
  case — Redis matched `x5:` and *missed* the literal `x[0-9]:` key that memory
  removed.

  If you relied on passing a glob to `invalidate_prefix()`, that no longer
  works; prefixes are now literal.

- **`build_key()` takes an optional `digest_length`.** The digest remains 12 hex
  characters (48 bits) by default, so every existing key is byte-for-byte
  unchanged and no deployed cache is invalidated by upgrading. 48 bits puts a
  birthday collision at roughly `2**24` offline trials, which is not enough when
  a param is attacker-controlled: whoever finds a colliding pair can seed the
  entry another caller then reads. Pass a wider digest in that case:

  ```python
  key = build_key("search", 32, q=user_query, tenant=tenant_id)
  ```

  `prefix` and `digest_length` are positional-only, so neither name is reserved
  and a param called either still gets hashed as an ordinary param. Note this
  means the width must be passed **positionally**: written as a keyword it is
  swallowed into `**params` and you get the default 12 characters. Widening
  changes the key, so a cache written at one width will not read another.
  A non-`int` width — including `True`, which is an `int` subclass and would
  otherwise have truncated the digest to a single hex character — raises
  `TypeError`.

### Fixed

- **`AsyncFallbackCache.invalidate_prefix("")` could delete the entire Redis
  keyspace.** `FallbackCache` refused a prefix that resolves to empty, because
  the resulting `SCAN` pattern is `*`; `AsyncFallbackCache` had no such guard,
  so the same call on an async cache with no `key_prefix` walked and deleted
  every key in the database, including any co-located non-cache data. The
  guard now lives on the shared base, so neither class can lose it
  independently. A configured `key_prefix` alone still scopes the call, as
  before.

### Changed

- **Shared cache internals live in one place.** `FallbackCache` and
  `AsyncFallbackCache` now inherit a private `_BaseCache` that owns the
  constructor arguments and their validation, TTL resolution, and the
  `_full_key` / `build_key` helpers. The two classes previously carried 46
  duplicated lines between them, so the sync and async constructors could
  drift apart silently. Each subclass now supplies only its own storage via a
  `_init_storage()` hook.

  No public API change: both classes keep exactly the same constructor
  signature, defaults, and methods, and `build_key` still produces identical
  keys from either class.

### Removed

- **The manual SonarCloud workflow.** `.github/workflows/sonarcloud.yml` ran
  `npm ci` and `npm run test:coverage` against this Python project, which has
  no `package.json`, so it could only ever fail. SonarCloud Automatic Analysis
  already covers the repository.

## 0.2.1 - 2026-07-26

Repairs the non-functional `0.2.0` and forward-ports the `0.1.1` correctness
fixes onto the `0.2.x` line.

`0.2.0` was tagged and released on GitHub but never reached PyPI — its publish
run failed at the upload step — so `0.1.0` is still the only version on PyPI and
`0.2.1` is the first working `0.2.x` available there. Anyone who installed
`0.2.0` from the git tag should move to `0.2.1`.

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

> **Do not use this version.** The tagged code is non-functional — see `0.2.1`,
> which repairs it. It was never published to PyPI (the publish run failed), so
> it is only reachable by installing from the git tag. The features below are
> accurate but only work from `0.2.1` onward.

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

> Maintenance release on the `0.1.x` back-line, tagged on GitHub but not
> published to PyPI. These fixes reach PyPI in `0.2.1`.

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
