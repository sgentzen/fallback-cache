# fallback-cache

A Python cache library that uses Redis as the primary store and transparently
falls back to an in-memory LRU cache when Redis is unavailable or returns a
miss.

## Key Features

- **Dual-write resilience** — every `set()` writes to both Redis and in-memory,
  so the in-memory copy is always warm.
- **Transparent fallback** — reads go to Redis first; if Redis errors or the key
  is absent, the in-memory entry is returned instead.
- **Circuit breaker** — after repeated Redis failures, the circuit breaker stops
  probing Redis and auto-recovers after a cooldown period.
- **Async support** — `AsyncFallbackCache` provides the same API with
  `async`/`await` for use with `redis.asyncio`.
- **Zero required dependencies** — works as a pure in-memory cache out of the
  box. Install `fallback-cache[redis]` to add Redis support.
- **Pluggable serializers** — use JSON (default), msgpack, or any custom
  serializer.

## Installation

```bash
pip install fallback-cache            # memory-only (zero deps)
pip install fallback-cache[redis]     # with Redis support
```

## Why fallback-cache?

Your application keeps serving cached data through transient Redis outages
without any special error-handling code. No existing Python cache library
combines Redis-primary operation with automatic dual-write fallback in a single,
dependency-free package.
