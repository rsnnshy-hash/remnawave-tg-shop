"""Redis caching service for performance optimization."""
import json
import logging
from typing import Any, Optional, Callable, TypeVar
from functools import wraps

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None


T = TypeVar('T')


class CacheService:
    """
    Redis-based caching service with automatic fallback when Redis is unavailable.

    Usage:
        cache = CacheService(redis_url="redis://localhost:6379/0", default_ttl=300)
        await cache.connect()

        # Set/get values
        await cache.set("key", {"data": "value"})
        data = await cache.get("key")

        # Delete
        await cache.delete("key")

        # Use as decorator
        @cache.cached(prefix="user", ttl=60)
        async def get_user(user_id: int):
            return await db.get_user(user_id)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        default_ttl: int = 300,
        key_prefix: str = "tgshop:",
    ):
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        self._redis: Optional[Any] = None
        self._enabled = bool(redis_url and REDIS_AVAILABLE)

        if not REDIS_AVAILABLE and redis_url:
            logging.warning("Redis package not available. Caching disabled.")
        elif not redis_url:
            logging.info("Redis URL not configured. Caching disabled.")

    async def connect(self) -> bool:
        """Establish connection to Redis."""
        if not self._enabled:
            return False

        try:
            self._redis = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test connection
            await self._redis.ping()
            logging.info(f"Redis cache connected: {self.redis_url.split('@')[-1]}")
            return True
        except Exception as e:
            logging.warning(f"Failed to connect to Redis: {e}. Caching disabled.")
            self._redis = None
            self._enabled = False
            return False

    async def disconnect(self):
        """Close Redis connection."""
        if self._redis:
            try:
                await self._redis.close()
                logging.info("Redis cache disconnected.")
            except Exception as e:
                logging.warning(f"Error closing Redis connection: {e}")
            finally:
                self._redis = None

    def _make_key(self, key: str) -> str:
        """Create a prefixed cache key."""
        return f"{self.key_prefix}{key}"

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/error
        """
        if not self._redis:
            return None

        try:
            full_key = self._make_key(key)
            value = await self._redis.get(full_key)
            if value is None:
                return None
            return json.loads(value)
        except json.JSONDecodeError:
            return value
        except Exception as e:
            logging.debug(f"Cache get error for '{key}': {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (default: self.default_ttl)

        Returns:
            True if successful, False otherwise
        """
        if not self._redis:
            return False

        try:
            full_key = self._make_key(key)
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            await self._redis.setex(
                full_key,
                ttl or self.default_ttl,
                serialized,
            )
            return True
        except Exception as e:
            logging.debug(f"Cache set error for '{key}': {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self._redis:
            return False

        try:
            full_key = self._make_key(key)
            await self._redis.delete(full_key)
            return True
        except Exception as e:
            logging.debug(f"Cache delete error for '{key}': {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Glob pattern (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        if not self._redis:
            return 0

        try:
            full_pattern = self._make_key(pattern)
            keys = []
            async for key in self._redis.scan_iter(match=full_pattern):
                keys.append(key)

            if keys:
                await self._redis.delete(*keys)
            return len(keys)
        except Exception as e:
            logging.debug(f"Cache delete_pattern error for '{pattern}': {e}")
            return 0

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Get value from cache or compute and cache it.

        Args:
            key: Cache key
            factory: Async function to compute value if not cached
            ttl: Time to live in seconds

        Returns:
            Cached or computed value
        """
        # Try to get from cache
        cached = await self.get(key)
        if cached is not None:
            return cached

        # Compute value
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()

        # Cache the result
        await self.set(key, value, ttl)
        return value

    def cached(
        self,
        prefix: str = "",
        ttl: Optional[int] = None,
        key_builder: Optional[Callable[..., str]] = None,
    ):
        """
        Decorator for caching async function results.

        Args:
            prefix: Prefix for cache key
            ttl: Time to live in seconds
            key_builder: Custom function to build cache key from args

        Example:
            @cache.cached(prefix="user", ttl=60)
            async def get_user(user_id: int):
                return await db.get_user(user_id)
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> T:
                # Build cache key
                if key_builder:
                    cache_key = key_builder(*args, **kwargs)
                else:
                    # Default: use function name and args
                    key_parts = [prefix or func.__name__]
                    key_parts.extend(str(arg) for arg in args)
                    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                    cache_key = ":".join(key_parts)

                # Try cache
                cached = await self.get(cache_key)
                if cached is not None:
                    return cached

                # Call function
                result = await func(*args, **kwargs)

                # Cache result
                if result is not None:
                    await self.set(cache_key, result, ttl)

                return result

            return wrapper
        return decorator

    @property
    def is_available(self) -> bool:
        """Check if cache is available and connected."""
        return self._redis is not None


# Import asyncio for get_or_set
import asyncio

# Global cache instance
_cache_instance: Optional[CacheService] = None


def get_cache_service() -> Optional[CacheService]:
    """Get global cache service instance."""
    return _cache_instance


def init_cache_service(redis_url: Optional[str], default_ttl: int = 300) -> CacheService:
    """Initialize global cache service."""
    global _cache_instance
    _cache_instance = CacheService(redis_url=redis_url, default_ttl=default_ttl)
    return _cache_instance


# Common cache key builders
def user_cache_key(user_id: int) -> str:
    """Build cache key for user data."""
    return f"user:{user_id}"


def subscription_cache_key(user_id: int) -> str:
    """Build cache key for user subscription."""
    return f"subscription:{user_id}"


def panel_user_cache_key(panel_uuid: str) -> str:
    """Build cache key for panel user data."""
    return f"panel_user:{panel_uuid}"


def settings_cache_key(key: str) -> str:
    """Build cache key for settings."""
    return f"settings:{key}"
