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
| `invalidate_prefix` | `invalidate_prefix(prefix)` | Delete all keys starting with `key_prefix + prefix`. |
| `clear` | `clear()` | Remove all entries tracked by this instance. |
| `stats` | `stats() -> dict` | Return runtime statistics including circuit breaker state. |
| `build_key` | `build_key(prefix, **params)` *(static)* | Build a deterministic SHA-256-based cache key. |

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
