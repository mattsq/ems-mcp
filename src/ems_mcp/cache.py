"""In-memory caching infrastructure for EMS MCP server.

Provides simple async-safe caching with TTL support for reducing
redundant API calls. Field IDs and database metadata are stable
and benefit from caching.

When ``EMS_CACHE_DIR`` is set, the field cache is backed by a SQLite
database in that directory so its contents survive server restarts.
Field IDs are stable for hours/days, so warm cache hits across sessions
significantly reduce first-call latency.
"""

import asyncio
import logging
import os
import pickle
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


class SQLiteCache(Generic[T]):
    """Persistent async-safe cache backed by SQLite.

    Drop-in replacement for ``SimpleCache`` that survives process restarts.
    Values are stored as pickled blobs (JSON would lose type information for
    nested dict/list payloads from the EMS API). Schema is partitioned by
    ``namespace`` so caches for multiple EMS endpoints can share one file.

    All blocking I/O is dispatched through ``asyncio.to_thread`` to keep the
    event loop responsive.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS cache_entries (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value BLOB NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (namespace, key)
        );
        CREATE INDEX IF NOT EXISTS idx_expires ON cache_entries(expires_at);
    """

    def __init__(
        self,
        db_path: Path | str,
        namespace: str,
        default_ttl: int = 3600,
    ) -> None:
        """Initialize the cache.

        Args:
            db_path: Path to the SQLite database file. Parent directory is
                created if it does not exist.
            namespace: Logical partition within the database. Lets multiple
                cache instances share one file without key collisions.
            default_ttl: Default time-to-live in seconds.
        """
        self._db_path = Path(db_path)
        self._namespace = namespace
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(self.SCHEMA)

    def _get_sync(self, key: str) -> T | None:
        now = datetime.now(UTC).timestamp()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache_entries "
                "WHERE namespace = ? AND key = ?",
                (self._namespace, key),
            ).fetchone()
            if row is None:
                return None
            value_blob, expires_at = row
            if expires_at <= now:
                conn.execute(
                    "DELETE FROM cache_entries WHERE namespace = ? AND key = ?",
                    (self._namespace, key),
                )
                conn.commit()
                return None
        loaded: T = pickle.loads(value_blob)
        return loaded

    def _set_sync(self, key: str, value: T, ttl: int) -> None:
        now = datetime.now(UTC).timestamp()
        expires_at = now + ttl
        blob = pickle.dumps(value)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(namespace, key, value, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._namespace, key, blob, expires_at, now),
            )
            conn.commit()

    def _delete_sync(self, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND key = ?",
                (self._namespace, key),
            )
            conn.commit()
            return cur.rowcount > 0

    def _clear_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM cache_entries WHERE namespace = ?", (self._namespace,)
            )
            conn.commit()

    def _size_sync(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE namespace = ?",
                (self._namespace,),
            ).fetchone()
            return int(row[0]) if row else 0

    async def get(self, key: str) -> T | None:
        async with self._lock:
            value = await asyncio.to_thread(self._get_sync, key)
            if value is None:
                logger.debug("SQLite cache miss: %s/%s", self._namespace, key)
            else:
                logger.debug("SQLite cache hit: %s/%s", self._namespace, key)
            return value

    async def set(self, key: str, value: T, ttl: int | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_sync, key, value, ttl if ttl is not None else self._default_ttl,
            )
            logger.debug(
                "SQLite cache set: %s/%s (TTL: %ds)",
                self._namespace,
                key,
                ttl if ttl is not None else self._default_ttl,
            )

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, key)

    async def clear(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._clear_sync)

    @property
    def size(self) -> int:
        return self._size_sync()


def _build_field_cache() -> "SimpleCache[Any] | SQLiteCache[Any]":
    """Build the field cache backend based on environment configuration.

    If ``EMS_CACHE_DIR`` is set, returns a SQLite-backed cache writing
    to ``{EMS_CACHE_DIR}/field_cache.db`` so field discovery survives
    server restarts. Otherwise returns the in-memory default.

    The SQLite namespace incorporates ``EMS_BASE_URL`` so that pointing the
    same cache directory at a different EMS instance does not return stale
    field IDs from another deployment.

    Field IDs are stable enough that 24h TTL is safe for the persistent
    backend; the in-memory backend keeps the original 1h TTL.
    """
    cache_dir = os.environ.get("EMS_CACHE_DIR")
    if cache_dir:
        try:
            base_url = os.environ.get("EMS_BASE_URL", "default").rstrip("/")
            namespace = f"field|{base_url}"
            return SQLiteCache[Any](
                db_path=Path(cache_dir) / "field_cache.db",
                namespace=namespace,
                default_ttl=86400,
            )
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "Failed to initialize SQLite field cache at %s (%s); "
                "falling back to in-memory cache.",
                cache_dir,
                e,
            )
    return SimpleCache[Any](default_ttl=3600)


# Global cache instances for different data types
field_cache: SimpleCache[Any] | SQLiteCache[Any] = _build_field_cache()
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
