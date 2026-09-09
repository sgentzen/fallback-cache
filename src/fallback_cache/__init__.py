"""fallback-cache: Redis-primary cache with transparent in-memory LRU fallback."""

__version__ = "0.2.0"

from fallback_cache._keys import build_key
from fallback_cache.async_cache import AsyncFallbackCache
from fallback_cache.cache import FallbackCache

__all__ = ["AsyncFallbackCache", "FallbackCache", "build_key", "__version__"]
