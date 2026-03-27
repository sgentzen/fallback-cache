# Advanced Usage

## Circuit Breaker Tuning

The built-in circuit breaker prevents your application from repeatedly probing a
downed Redis instance. You can tune its behavior:

```python
cache = FallbackCache(
    redis_client=r,
    circuit_breaker_threshold=10,   # allow more failures before tripping
    circuit_breaker_cooldown=60.0,  # wait longer before probing
)
```

Monitor the circuit breaker via `stats()`:

```python
stats = cache.stats()
print(stats["circuit_breaker_state"])          # "closed", "open", or "half_open"
print(stats["circuit_breaker_failure_count"])   # consecutive failures
```

**States:**

- **closed** — normal operation, all calls go to Redis.
- **open** — Redis calls are skipped; only in-memory is used.
- **half_open** — cooldown elapsed; one probe call is allowed. If it succeeds,
  the breaker resets to closed. If it fails, the breaker returns to open.

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

## Key Prefixes

Use `key_prefix` to namespace keys in shared Redis instances:

```python
user_cache = FallbackCache(redis_client=r, key_prefix="users:")
item_cache = FallbackCache(redis_client=r, key_prefix="items:")
```

## Deterministic Cache Keys

`build_key()` generates content-addressed keys from arbitrary parameters:

```python
from fallback_cache import build_key

key = build_key("report", year=2026, quarter="Q1")
# "report:a1b2c3d4e5f6"
```

Parameter order does not matter. `None` values are excluded automatically.

## Monitoring with stats()

```python
stats = cache.stats()
```

**Memory-only mode** returns:

```python
{
    "backend": "memory",
    "entries": 42,
    "max_entries": 100,
    "oldest_age_seconds": 120.5,
    "circuit_breaker_state": "closed",
    "circuit_breaker_failure_count": 0,
}
```

**Redis mode** returns:

```python
{
    "backend": "redis",
    "memory_entries": 42,
    "key_prefix": "myapp:",
    "circuit_breaker_state": "closed",
    "circuit_breaker_failure_count": 0,
}
```

## Limitations

- **No write-back** — the in-memory store is not promoted to Redis when
  connectivity is restored; warm entries are only written on the next explicit
  `set()`.
- **No multi-process coherence** — the in-memory LRU is process-local; multiple
  workers will have independent in-memory state.
