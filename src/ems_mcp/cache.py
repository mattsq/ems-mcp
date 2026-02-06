"""In-memory caching infrastructure for EMS MCP server.

Provides simple async-safe caching with TTL support for reducing
redundant API calls. Field IDs and database metadata are stable
and benefit from caching.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """A cached value with expiry tracking.

    Attributes:
        value: The cached value.
        expires_at: When this entry expires (UTC).
        created_at: When this entry was created (UTC).
    """

    value: T
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_expired(self) -> bool:
        """Check if this cache entry has expired."""
        return datetime.now(UTC) >= self.expires_at


class SimpleCache(Generic[T]):
    """Simple async-safe in-memory cache with TTL support.

    Thread-safe for concurrent async access using asyncio.Lock.

    Example:
        cache = SimpleCache[dict](default_ttl=3600)
        await cache.set("key", {"data": "value"})
        result = await cache.get("key")
    """

    def __init__(self, default_ttl: int = 3600, max_entries: int = 10000):
        """Initialize the cache.

        Args:
            default_ttl: Default time-to-live in seconds.
            max_entries: Maximum number of entries before eviction.
        """
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._cache: dict[str, CacheEntry[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        """Get a value from the cache.

        Args:
            key: Cache key.

        Returns:
            The cached value, or None if not found or expired.
        """
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired:
                del self._cache[key]
                logger.debug("Cache miss (expired): %s", key)
                return None
            logger.debug("Cache hit: %s", key)
            return entry.value

    async def set(self, key: str, value: T, ttl: int | None = None) -> None:
        """Set a value in the cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time-to-live in seconds. Uses default if not specified.
        """
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=ttl)

        async with self._lock:
            # Evict expired entries if at capacity
            if len(self._cache) >= self._max_entries:
                await self._evict_expired_unlocked()

            # If still at capacity, evict oldest entries (at least 1)
            if len(self._cache) >= self._max_entries:
                evict_count = max(1, len(self._cache) // 10)
                await self._evict_oldest_unlocked(evict_count)

            self._cache[key] = CacheEntry(value=value, expires_at=expires_at)
            logger.debug("Cache set: %s (TTL: %ds)", key, ttl)

    async def delete(self, key: str) -> bool:
        """Delete a value from the cache.

        Args:
            key: Cache key.

        Returns:
            True if the key was found and deleted.
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug("Cache delete: %s", key)
                return True
            return False

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        async with self._lock:
            self._cache.clear()
            logger.debug("Cache cleared")

    async def _evict_expired_unlocked(self) -> int:
        """Evict expired entries. Must be called with lock held.

        Returns:
            Number of entries evicted.
        """
        expired_keys = [k for k, v in self._cache.items() if v.is_expired]
        for key in expired_keys:
            del self._cache[key]
        if expired_keys:
            logger.debug("Evicted %d expired cache entries", len(expired_keys))
        return len(expired_keys)

    async def _evict_oldest_unlocked(self, count: int) -> None:
        """Evict oldest entries. Must be called with lock held.

        Args:
            count: Number of entries to evict.
        """
        if not self._cache:
            return

        # Sort by creation time and remove oldest
        sorted_entries = sorted(self._cache.items(), key=lambda x: x[1].created_at)
        for key, _ in sorted_entries[:count]:
            del self._cache[key]
        logger.debug("Evicted %d oldest cache entries", min(count, len(sorted_entries)))

    @property
    def size(self) -> int:
        """Get the current number of entries in the cache."""
        return len(self._cache)


# Global cache instances for different data types
field_cache: SimpleCache[Any] = SimpleCache(default_ttl=3600)
database_cache: SimpleCache[Any] = SimpleCache(default_ttl=3600)
asset_cache: SimpleCache[Any] = SimpleCache(default_ttl=3600)


def make_cache_key(*args: Any) -> str:
    """Create a cache key from multiple arguments.

    Args:
        *args: Values to include in the key.

    Returns:
        A string cache key.
    """
    return ":".join(str(arg) for arg in args)
