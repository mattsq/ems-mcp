"""Unit tests for caching infrastructure."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ems_mcp.cache import CacheEntry, SimpleCache, SQLiteCache, make_cache_key


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_is_expired_false_for_fresh_entry(self) -> None:
        """Fresh cache entry should not be expired."""
        entry = CacheEntry(
            value="test",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert not entry.is_expired

    def test_is_expired_true_for_old_entry(self) -> None:
        """Old cache entry should be expired."""
        entry = CacheEntry(
            value="test",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert entry.is_expired

    def test_created_at_defaults_to_now(self) -> None:
        """created_at should default to approximately now."""
        before = datetime.now(timezone.utc)
        entry = CacheEntry(
            value="test",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        after = datetime.now(timezone.utc)

        assert before <= entry.created_at <= after


class TestSimpleCache:
    """Tests for SimpleCache class."""

    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        """Should store and retrieve values."""
        cache: SimpleCache[str] = SimpleCache()
        await cache.set("key", "value")
        result = await cache.get("key")
        assert result == "value"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self) -> None:
        """Should return None for missing keys."""
        cache: SimpleCache[str] = SimpleCache()
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_expired_entry(self) -> None:
        """Should return None and remove expired entries."""
        cache: SimpleCache[str] = SimpleCache(default_ttl=1)
        await cache.set("key", "value", ttl=0)  # Immediately expired

        # Wait a tiny bit to ensure expiry
        import asyncio
        await asyncio.sleep(0.01)

        result = await cache.get("key")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_removes_entry(self) -> None:
        """Should remove entry on delete."""
        cache: SimpleCache[str] = SimpleCache()
        await cache.set("key", "value")
        deleted = await cache.delete("key")
        assert deleted is True

        result = await cache.get("key")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_returns_false_for_missing_key(self) -> None:
        """Should return False when deleting nonexistent key."""
        cache: SimpleCache[str] = SimpleCache()
        deleted = await cache.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_clear_removes_all_entries(self) -> None:
        """Should remove all entries on clear."""
        cache: SimpleCache[str] = SimpleCache()
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")

        await cache.clear()

        assert await cache.get("key1") is None
        assert await cache.get("key2") is None
        assert cache.size == 0

    @pytest.mark.asyncio
    async def test_custom_ttl(self) -> None:
        """Should respect custom TTL per entry."""
        cache: SimpleCache[str] = SimpleCache(default_ttl=3600)
        await cache.set("long", "value", ttl=3600)
        await cache.set("short", "value", ttl=0)

        import asyncio
        await asyncio.sleep(0.01)

        assert await cache.get("long") == "value"
        assert await cache.get("short") is None

    @pytest.mark.asyncio
    async def test_size_property(self) -> None:
        """Should track number of entries."""
        cache: SimpleCache[str] = SimpleCache()
        assert cache.size == 0

        await cache.set("key1", "value1")
        assert cache.size == 1

        await cache.set("key2", "value2")
        assert cache.size == 2

        await cache.delete("key1")
        assert cache.size == 1

    @pytest.mark.asyncio
    async def test_eviction_on_capacity(self) -> None:
        """Should evict entries when at capacity."""
        cache: SimpleCache[str] = SimpleCache(max_entries=5)

        # Fill cache
        for i in range(5):
            await cache.set(f"key{i}", f"value{i}")

        assert cache.size == 5

        # Add one more, should trigger eviction
        await cache.set("new_key", "new_value")

        # Size should be reduced (eviction removes 10% = 0.5, so at least 1)
        assert cache.size <= 5

    @pytest.mark.asyncio
    async def test_stores_complex_types(self) -> None:
        """Should store complex types like dicts and lists."""
        cache: SimpleCache[dict] = SimpleCache()

        data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        await cache.set("complex", data)

        result = await cache.get("complex")
        assert result == data


class TestSQLiteCache:
    """Tests for the SQLite-backed persistent cache."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, tmp_path: Path) -> None:
        cache: SQLiteCache[dict] = SQLiteCache(
            db_path=tmp_path / "c.db", namespace="test",
        )
        await cache.set("k", {"a": 1, "nested": [1, 2, 3]})
        result = await cache.get("k")
        assert result == {"a": 1, "nested": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path: Path) -> None:
        """Values written by one instance must be visible to a fresh one."""
        db_path = tmp_path / "c.db"

        first: SQLiteCache[str] = SQLiteCache(db_path=db_path, namespace="test")
        await first.set("warm", "value", ttl=3600)

        second: SQLiteCache[str] = SQLiteCache(db_path=db_path, namespace="test")
        result = await second.get("warm")
        assert result == "value"

    @pytest.mark.asyncio
    async def test_ttl_expires(self, tmp_path: Path) -> None:
        import asyncio
        cache: SQLiteCache[str] = SQLiteCache(
            db_path=tmp_path / "c.db", namespace="test",
        )
        await cache.set("k", "value", ttl=0)
        await asyncio.sleep(0.01)
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path: Path) -> None:
        cache: SQLiteCache[str] = SQLiteCache(
            db_path=tmp_path / "c.db", namespace="test",
        )
        await cache.set("k", "value")
        assert await cache.delete("k") is True
        assert await cache.delete("k") is False
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_clear_only_affects_namespace(self, tmp_path: Path) -> None:
        """clear() should not wipe other namespaces in the same file."""
        db_path = tmp_path / "c.db"
        a: SQLiteCache[str] = SQLiteCache(db_path=db_path, namespace="A")
        b: SQLiteCache[str] = SQLiteCache(db_path=db_path, namespace="B")

        await a.set("k", "from-A")
        await b.set("k", "from-B")
        await a.clear()

        assert await a.get("k") is None
        assert await b.get("k") == "from-B"

    @pytest.mark.asyncio
    async def test_stores_lists_of_dicts(self, tmp_path: Path) -> None:
        """The shape returned by /fields?text=... should round-trip cleanly."""
        cache: SQLiteCache[list] = SQLiteCache(
            db_path=tmp_path / "c.db", namespace="field",
        )
        sample = [
            {"id": "f1", "name": "Altitude", "type": "number", "units": "ft"},
            {
                "id": "f2", "name": "Status", "type": "discrete",
                "discreteValues": [{"value": 0, "label": "Off"}],
            },
        ]
        await cache.set("k", sample)
        result = await cache.get("k")
        assert result == sample


class TestMakeCacheKey:
    """Tests for make_cache_key helper function."""

    def test_single_arg(self) -> None:
        """Should create key from single argument."""
        key = make_cache_key("value")
        assert key == "value"

    def test_multiple_args(self) -> None:
        """Should join multiple arguments with colon."""
        key = make_cache_key("ems", 1, "databases", "fdw")
        assert key == "ems:1:databases:fdw"

    def test_converts_to_string(self) -> None:
        """Should convert all arguments to strings."""
        key = make_cache_key(1, 2.5, True, None)
        assert key == "1:2.5:True:None"

    def test_empty_args(self) -> None:
        """Should handle no arguments."""
        key = make_cache_key()
        assert key == ""
