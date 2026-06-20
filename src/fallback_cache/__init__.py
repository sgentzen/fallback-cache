"""fallback-cache: Redis-primary cache with transparent in-memory LRU fallback."""

__version__ = "0.1.1"

from fallback_cache.cache import FallbackCache

__all__ = ["FallbackCache", "__version__"]
