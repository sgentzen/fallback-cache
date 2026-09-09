# API Reference

## FallbackCache

Synchronous cache with Redis primary and in-memory LRU fallback.

### Constructor

```python
FallbackCache(
    redis_client=None,
    default_ttl=300,
    max_entries=100,
    key_prefix="",
    serializer=json.dumps,
    deserializer=json.loads,
    circuit_breaker_threshold=5,
    circuit_breaker_cooldown=30.0,
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `redis_client` | `None` | A `redis.Redis` instance. If omitted, operates as pure in-memory cache. |
| `default_ttl` | `300` | TTL in seconds applied when `set()` is called without an explicit `ttl`. |
| `max_entries` | `100` | Maximum in-memory LRU entries. Least-recently-used entry is evicted when full. |
| `key_prefix` | `""` | String prepended to every key, useful for namespacing. |
| `serializer` | `json.dumps` | Callable that converts data to `str` or `bytes` before writing to Redis. |
| `deserializer` | `json.loads` | Callable that converts raw Redis bytes back to Python objects. |
| `circuit_breaker_threshold` | `5` | Number of consecutive Redis failures before the circuit breaker opens. |
| `circuit_breaker_cooldown` | `30.0` | Seconds to wait before probing Redis after the circuit opens. |

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `set` | `set(key, data, ttl=None)` | Write data under key; dual-writes to Redis and memory. |
| `get` | `get(key) -> Any \| None` | Read key; tries Redis first, falls back to memory. |
| `delete` | `delete(key) -> bool` | Remove key from both backends. Returns `True` if it existed. |
| `invalidate_prefix` | `invalidate_prefix(prefix)` | Delete all keys starting with `key_prefix + prefix`. The prefix is matched literally; glob characters in it are escaped. |
| `clear` | `clear()` | Remove all entries tracked by this instance. |
| `stats` | `stats() -> dict` | Return runtime statistics including circuit breaker state. |
| `build_key` | `build_key(prefix, digest_length=12, /, **params)` *(static)* | Build a deterministic SHA-256-based cache key. |

## AsyncFallbackCache

Async version of `FallbackCache` with identical constructor parameters and methods.
All data methods (`set`, `get`, `delete`, `invalidate_prefix`, `clear`) are
`async`. `stats()` and `build_key()` remain synchronous.

The `redis_client` should be a `redis.asyncio.Redis` instance.

```python
cache = AsyncFallbackCache(redis_client=async_redis, default_ttl=300)
await cache.set("key", value)
result = await cache.get("key")
```

## build_key

Also available as a standalone function:

```python
from fallback_cache import build_key

key = build_key("users", user_id="123", org="acme")
# "users:a1b2c3d4e5f6"
```

None-valued params are excluded. Remaining params are sorted, JSON-serialized,
and SHA-256 hashed (first 12 hex chars). Returns `"prefix:<hash>"`.

### Digest width and untrusted params

`digest_length` controls how many hex characters of the SHA-256 are kept. It
defaults to `12` (48 bits), the width this library has always emitted, so
upgrading does not change any existing key.

48 bits means a birthday collision costs roughly `2**24` offline trials. That
is fine for keys built from trusted values, but if any param is
attacker-controlled, someone who finds a colliding pair can seed the entry
another caller then reads. Pass a wider digest whenever untrusted input reaches
a key:

```python
key = build_key("search", 32, q=user_query, tenant=tenant_id)
# "search:10abd756a4df2bcf74a45380a29aa62a"  (32 hex chars, 128 bits)
```

Widening changes the key, so a cache populated at one width will not find
entries written at another.

`prefix` and `digest_length` are positional-only — note the `/` in the
signature. **Pass the width positionally.** Written as a keyword it is not the
parameter at all; it is swallowed into `**params` and hashed, and you silently
get the default 12 characters:

```python
build_key("search", 32, q=user_query)               # 32 chars, as intended
build_key("search", q=user_query, digest_length=32) # 12 chars — the keyword is
                                                    # hashed as an ordinary param
```

That is the trade-off for `digest_length` not being a reserved param name.
