# Quick Start

## Memory-only mode

```python
from fallback_cache import FallbackCache

cache = FallbackCache(default_ttl=60, max_entries=500)

cache.set("user:42", {"name": "Alice", "role": "admin"})
user = cache.get("user:42")   # {"name": "Alice", "role": "admin"}
cache.delete("user:42")
```

## Sync Redis mode

```python
import redis
from fallback_cache import FallbackCache

r = redis.Redis(host="localhost", port=6379, decode_responses=False)
cache = FallbackCache(redis_client=r, default_ttl=300, key_prefix="myapp:")

cache.set("report:q1", report_data, ttl=3600)
result = cache.get("report:q1")   # returns from Redis; falls back to memory
cache.delete("report:q1")
```

## Async Redis mode

```python
import redis.asyncio as aioredis
from fallback_cache import AsyncFallbackCache

r = aioredis.Redis(host="localhost", port=6379, decode_responses=False)
cache = AsyncFallbackCache(redis_client=r, default_ttl=300, key_prefix="myapp:")

await cache.set("report:q1", report_data, ttl=3600)
result = await cache.get("report:q1")
await cache.delete("report:q1")
```

## Fallback Behavior

**On `set()`**: data is written to Redis first (best-effort), then always written
to in-memory. A Redis failure is logged as a warning and does not raise.

**On `get()`**: Redis is tried first. On a hit, the deserialized value is
returned immediately. On a Redis miss *or* any Redis exception, the in-memory
LRU is consulted. In-memory entries respect TTL with lazy expiry on read.

**Circuit breaker**: after 5 consecutive Redis failures (configurable), the
cache stops probing Redis entirely. After a 30-second cooldown (configurable),
it sends one probe request. If the probe succeeds, normal operation resumes.
