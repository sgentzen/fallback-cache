# fallback-cache

A Python cache library that uses Redis as the primary store and transparently
falls back to an in-memory LRU cache when Redis is unavailable or returns a
miss. Every `set()` dual-writes to both backends so the in-memory copy is always
warm. Reads go to Redis first; if Redis errors or the key is absent, the
in-memory entry is returned instead. This means your application keeps serving
cached data through transient Redis outages without any special error-handling
code. No existing Python cache library combines Redis-primary operation with
automatic dual-write fallback in a single, dependency-free package.

## Installation

```bash
pip install fallback-cache            # memory-only (zero deps)
pip install fallback-cache[redis]     # with Redis support
```

## Quick Start

### Memory-only mode

```python
from fallback_cache import FallbackCache

cache = FallbackCache(default_ttl=60, max_entries=500)

cache.set("user:42", {"name": "Alice", "role": "admin"})
user = cache.get("user:42")   # {"name": "Alice", "role": "admin"}
cache.delete("user:42")
```

### Redis mode

```python
import redis
from fallback_cache import FallbackCache

r = redis.Redis(host="localhost", port=6379, decode_responses=False)
cache = FallbackCache(redis_client=r, default_ttl=300, key_prefix="myapp:")

cache.set("report:q1", report_data, ttl=3600)
result = cache.get("report:q1")   # returns from Redis; falls back to memory
cache.delete("report:q1")
```

## API Reference

| Method | Signature | Description |
|--------|-----------|-------------|
| `set` | `set(key, data, ttl=None)` | Write data under key; dual-writes to Redis and memory. Per-key TTL overrides `default_ttl`. |
| `get` | `get(key)` | Read key; tries Redis first, falls back to in-memory copy on miss or error. Returns `None` if absent/expired. |
| `delete` | `delete(key)` | Remove key from both backends. Returns `True` if it existed in either. |
| `invalidate_prefix` | `invalidate_prefix(prefix)` | Delete all keys whose full key starts with `key_prefix + prefix`. Uses Redis `SCAN` to avoid blocking. |
| `clear` | `clear()` | Remove all entries tracked by this instance (does not `FLUSHDB`). |
| `stats` | `stats()` | Return a dict of runtime statistics (backend, entry count, oldest age). |
| `build_key` | `build_key(prefix, **params)` *(static)* | Build a deterministic SHA-256-based cache key: `"prefix:<12-hex-chars>"`. `None`-valued params are excluded. |

### Constructor parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `redis_client` | `None` | A `redis.Redis` instance. If omitted, operates as pure in-memory cache. |
| `default_ttl` | `300` | TTL in seconds applied when `set()` is called without an explicit `ttl`. |
| `max_entries` | `100` | Maximum in-memory LRU entries. Least-recently-used entry is evicted when full. |
| `key_prefix` | `""` | String prepended to every key, useful for namespacing multi-tenant deployments. |
| `serializer` | `json.dumps` | Callable that converts data to `str` or `bytes` before writing to Redis. |
| `deserializer` | `json.loads` | Callable that converts raw Redis bytes back to Python objects. |

## Fallback Behavior

**On `set()`**: data is written to Redis first (best-effort), then always written
to in-memory. A Redis failure is logged as a warning and does not raise.

**On `get()`**: Redis is tried first. On a hit, the deserialized value is
returned immediately. On a Redis miss *or* any Redis exception, the in-memory
LRU is consulted. In-memory entries respect TTL with lazy expiry on read.

## Custom Serializers

Use any format that produces `str` or `bytes`:

```python
import msgpack
from fallback_cache import FallbackCache

cache = FallbackCache(
    redis_client=r,
    serializer=msgpack.packb,
    deserializer=msgpack.unpackb,
)
```

## Limitations

- **No async support** — all operations are synchronous; use a thread pool to integrate with async frameworks.
- **No circuit-breaking** — every operation probes Redis even during extended outages; implement a circuit breaker externally if needed.
- **No write-back** — the in-memory store is not promoted to Redis when connectivity is restored; warm entries are only written on the next explicit `set()`.
- **No multi-process coherence** — the in-memory LRU is process-local; multiple workers will have independent in-memory state.

## License

Apache 2.0 — see [LICENSE](LICENSE).
