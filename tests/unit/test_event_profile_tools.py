"""Unit tests for the APM event-profile discovery tools.

Covers list_event_profiles and find_event_types added to steer LLMs away
from the wrong-profile failure mode where find_fields silently misses
event names that live as discrete VALUES inside each profile's Event Type
field rather than as field names.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_mcp.cache import database_cache, field_cache, make_cache_key
from ems_mcp.tools.discovery import (
    _format_database_group,
    _get_stored_result,
    _reset_result_store,
    find_event_types,
    list_event_profiles,
)

# Unwrap the FastMCP FunctionTool wrappers so tests can await the bare coro.
_list_event_profiles = list_event_profiles.fn
_find_event_types = find_event_types.fn


def _make_root_response(
    *,
    apm_at_root: bool = True,
    nested_under: str | None = None,
    profiles: list[dict[str, Any]] | None = None,
    extra_root_groups: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build a root database-groups response plus a routing table.

    Returns:
        (root_payload, routes) where routes maps groupId -> payload that
        the mock client should return for ?groupId={id} calls.
    """
    profiles = profiles or []
    extra_root_groups = extra_root_groups or []

    apm_group_payload = {
        "id": "g-apm",
        "name": "APM Events",
        "databases": profiles,
        "groups": [],
    }

    routes: dict[str, dict[str, Any]] = {}

    if apm_at_root and nested_under is None:
        root = {
            "id": "[none]",
            "name": "Root",
            "databases": [],
            "groups": [
                {"id": "g-apm", "name": "APM Events"},
                *extra_root_groups,
            ],
        }
        routes["g-apm"] = apm_group_payload
    elif nested_under:
        # APM Events nested one level deep under the named parent group.
        parent_payload = {
            "id": "g-parent",
            "name": nested_under,
            "databases": [],
            "groups": [{"id": "g-apm", "name": "APM Events"}],
        }
        root = {
            "id": "[none]",
            "name": "Root",
            "databases": [],
            "groups": [
                {"id": "g-parent", "name": nested_under},
                *extra_root_groups,
            ],
        }
        routes["g-parent"] = parent_payload
        routes["g-apm"] = apm_group_payload
    else:
        # APM Events missing entirely.
        root = {
            "id": "[none]",
            "name": "Root",
            "databases": [],
            "groups": list(extra_root_groups),
        }

    return root, routes


def _make_mock_client(
    root_payload: dict[str, Any],
    routes: dict[str, dict[str, Any]],
    *,
    field_groups: dict[str, dict[str, Any]] | None = None,
    field_info: dict[str, dict[str, Any]] | None = None,
) -> MagicMock:
    """Mock client that routes GET by URL pattern.

    Returns ``root_payload`` for the bare database-groups path, the
    matching routed payload for ``?groupId=`` calls. When provided,
    ``field_groups`` maps database_id -> root-level field-group payload
    (used by _recursive_field_search), and ``field_info`` maps
    (database_id, raw_field_id) -> field-info dict (used by
    get_field_info / _fetch_event_type_discrete_values).
    """
    field_groups = field_groups or {}
    field_info = field_info or {}

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        if path.endswith("/database-groups"):
            return root_payload
        if "/database-groups?groupId=" in path:
            group_id = path.split("groupId=", 1)[1]
            if group_id in routes:
                return routes[group_id]
            raise AssertionError(f"Unexpected groupId: {group_id}")
        if "/field-groups" in path:
            # /api/v2/ems-systems/{id}/databases/{db}/field-groups[?groupId=...]
            db_marker = "/databases/"
            db_part = path.split(db_marker, 1)[1]
            db_id, _tail = db_part.split("/field-groups", 1)
            if db_id in field_groups:
                return field_groups[db_id]
            raise AssertionError(f"Unexpected field-groups GET for db: {db_id}")
        if "/fields/" in path:
            # /api/v2/ems-systems/{id}/databases/{db}/fields/{encoded_field_id}
            db_marker = "/databases/"
            tail = path.split(db_marker, 1)[1]
            db_id, _, encoded = tail.partition("/fields/")
            from urllib.parse import unquote
            raw_field_id = unquote(encoded)
            key = (db_id, raw_field_id)
            if key in field_info:
                return field_info[key]
            raise AssertionError(
                f"Unexpected field-info GET: db={db_id!r}, field={raw_field_id!r}"
            )
        raise AssertionError(f"Unexpected GET: {path}")

    client = MagicMock()
    client.get = AsyncMock(side_effect=get)
    return client


def _profile_db(code: str, *, name: str | None = None, description: str = "") -> dict[str, Any]:
    """Build a fake APM profile database descriptor."""
    return {
        "id": f"[ems-core][entity-type][{code.lower()}-events]",
        "name": name or f"{code}: Events",
        "description": description,
    }


async def _seed_event_type_field(
    ems_system_id: int,
    database_id: str,
    profile_code: str,
    field_id: str,
    discrete_values: list[dict[str, Any]],
) -> None:
    """Pre-populate caches so find_event_types skips the field-resolve step.

    Avoids needing to mock _recursive_field_search end-to-end -- the field
    resolver consults its own cache key first, and _fetch_event_type_discrete_values
    consults the standard field_info cache key.
    """
    await field_cache.set(
        make_cache_key("event_type_field", ems_system_id, database_id, profile_code),
        [field_id, f"{profile_code}: Event Type"],
    )
    await field_cache.set(
        make_cache_key("field_info", ems_system_id, database_id, field_id),
        {
            "id": field_id,
            "name": f"{profile_code}: Event Type",
            "type": "discrete",
            "discreteValues": discrete_values,
        },
    )


class TestListEventProfiles:
    """Tests for list_event_profiles."""

    @pytest.fixture(autouse=True)
    async def _clear(self) -> None:
        await database_cache.clear()
        await field_cache.clear()
        _reset_result_store()

    @pytest.mark.asyncio
    async def test_lists_profiles_under_apm_events_at_root(self) -> None:
        profiles = [
            _profile_db("P14", description="Ground Operations Profile"),
            _profile_db("P40", description="FDAP Landing Profile"),
            _profile_db("P600", description="Engineering Limits Profile"),
            _profile_db("P796", description="Generic Safety Events"),
        ]
        root, routes = _make_root_response(profiles=profiles)
        client = _make_mock_client(root, routes)

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _list_event_profiles(ems_system_id=1)

        # Header + one line per profile + footer pointing at find_event_types.
        assert "Found 4 APM event profile(s):" in result
        for code in ("P14", "P40", "P600", "P796"):
            assert code in result
        assert "FDAP Landing Profile" in result
        assert "find_event_types" in result
        # Stable order: numeric ascending by profile code.
        idx_p14 = result.index("P14")
        idx_p40 = result.index("P40")
        idx_p600 = result.index("P600")
        idx_p796 = result.index("P796")
        assert idx_p14 < idx_p40 < idx_p600 < idx_p796

    @pytest.mark.asyncio
    async def test_finds_apm_events_when_nested(self) -> None:
        profiles = [_profile_db("P40", description="FDAP Landing")]
        root, routes = _make_root_response(
            apm_at_root=False, nested_under="FDW", profiles=profiles,
        )
        client = _make_mock_client(root, routes)

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _list_event_profiles(ems_system_id=1)

        assert "Found 1 APM event profile(s):" in result
        assert "P40" in result

    @pytest.mark.asyncio
    async def test_missing_apm_events_returns_explicit_error(self) -> None:
        root, routes = _make_root_response(
            apm_at_root=False,
            extra_root_groups=[
                {"id": "g-fdw", "name": "FDW"},
                {"id": "g-other", "name": "Operational"},
            ],
        )
        # FDW and Operational must each be reachable for the BFS walk.
        routes["g-fdw"] = {
            "id": "g-fdw", "name": "FDW",
            "databases": [], "groups": [],
        }
        routes["g-other"] = {
            "id": "g-other", "name": "Operational",
            "databases": [], "groups": [],
        }
        client = _make_mock_client(root, routes)

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _list_event_profiles(ems_system_id=1)

        assert "Error" in result
        assert "APM Events" in result
        # Mentions what was actually at root so the caller can navigate manually.
        assert "FDW" in result and "Operational" in result

    @pytest.mark.asyncio
    async def test_catalog_is_cached_across_calls(self) -> None:
        profiles = [_profile_db("P40", description="FDAP Landing")]
        root, routes = _make_root_response(profiles=profiles)
        client = _make_mock_client(root, routes)

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            await _list_event_profiles(ems_system_id=1)
            first_call_count = client.get.call_count
            await _list_event_profiles(ems_system_id=1)

        assert client.get.call_count == first_call_count, (
            "second list_event_profiles call should hit the cache"
        )


class TestFindEventTypes:
    """Tests for find_event_types."""

    @pytest.fixture(autouse=True)
    async def _clear(self) -> None:
        await database_cache.clear()
        await field_cache.clear()
        _reset_result_store()

    async def _setup(self, profiles_with_values: dict[str, list[dict[str, Any]]]) -> MagicMock:
        """Build a populated EMS environment for find_event_types tests.

        Each entry in ``profiles_with_values`` is profile_code -> list of
        discrete values for that profile's Event Type field. The
        corresponding caches are pre-seeded so the tool does not need
        real field-search HTTP traffic.
        """
        profile_dbs = [
            _profile_db(code, description=f"{code} description")
            for code in profiles_with_values
        ]
        root, routes = _make_root_response(profiles=profile_dbs)
        client = _make_mock_client(root, routes)

        # Seed the field caches.
        for db, (code, values) in zip(profile_dbs, profiles_with_values.items()):
            await _seed_event_type_field(
                ems_system_id=1,
                database_id=db["id"],
                profile_code=code,
                field_id=f"event-type-field-{code.lower()}",
                discrete_values=values,
            )

        return client

    @pytest.mark.asyncio
    async def test_empty_profiles_returns_pointer_to_catalog(self) -> None:
        result = await _find_event_types(
            ems_system_id=1, query="hard landing", profiles=[],
        )
        assert "Error" in result
        assert "profiles" in result.lower()
        assert "list_event_profiles" in result

    @pytest.mark.asyncio
    async def test_unknown_profile_code_returns_valid_list(self) -> None:
        client = await self._setup({
            "P40": [{"value": 1, "label": "Hard Landing"}],
            "P796": [{"value": 1, "label": "Hard Landing (Generic)"}],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P999"],
            )
        assert "Error" in result
        assert "P999" in result
        assert "P40" in result and "P796" in result

    @pytest.mark.asyncio
    async def test_returns_matches_grouped_by_profile(self) -> None:
        client = await self._setup({
            "P40": [
                {"value": 47, "label": "Hard Landing (Acceleration Method) (FDAP)"},
                {"value": 48, "label": "Hard Landing (Pitch Method) (FDAP)"},
                {"value": 22, "label": "Bounced Landing"},
            ],
            "P600": [
                {"value": 12, "label": "High Vertical Acceleration at Landing"},
            ],
            "P796": [
                {"value": 3, "label": "Hard Landing (Generic)"},
                {"value": 4, "label": "Tail Strike"},
            ],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1,
                query="hard landing",
                profiles=["P40", "P796"],
            )

        # Header reports total match count and profiles scanned.
        assert "Found 3 event match(es)" in result
        assert "P40" in result and "P796" in result
        # Each matching event label appears once.
        assert "Hard Landing (Acceleration Method) (FDAP)" in result
        assert "Hard Landing (Pitch Method) (FDAP)" in result
        assert "Hard Landing (Generic)" in result
        # Adjacent events that do NOT contain both tokens are not falsely included.
        assert "Bounced Landing" not in result
        assert "Tail Strike" not in result
        # P600 wasn't requested so its values are not shown even if they
        # might have matched.
        assert "High Vertical Acceleration" not in result
        # Codes are surfaced for use as filter values.
        assert "code=47" in result and "code=48" in result and "code=3" in result

    @pytest.mark.asyncio
    async def test_multi_term_query_is_anded(self) -> None:
        client = await self._setup({
            "P40": [
                {"value": 1, "label": "Hard Landing (FDAP)"},
                {"value": 2, "label": "Hard Touchdown"},
                {"value": 3, "label": "Soft Landing"},
            ],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1,
                query=["hard", "landing"],
                profiles=["P40"],
            )
        assert "Hard Landing (FDAP)" in result
        assert "Hard Touchdown" not in result
        assert "Soft Landing" not in result

    @pytest.mark.asyncio
    async def test_no_match_returns_explicit_empty_with_profiles_scanned(self) -> None:
        client = await self._setup({
            "P40": [{"value": 1, "label": "Bounced Landing"}],
            "P796": [{"value": 1, "label": "Tail Strike"}],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1,
                query="hard landing",
                profiles=["P40", "P796"],
            )
        assert "No matches" in result
        assert "P40" in result and "P796" in result
        # Must explicitly warn against silent fallback to find_fields.
        assert "find_fields" in result

    @pytest.mark.asyncio
    async def test_matches_register_numbered_references(self) -> None:
        client = await self._setup({
            "P40": [{"value": 47, "label": "Hard Landing (Acceleration Method) (FDAP)"}],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1,
                query="hard landing",
                profiles=["P40"],
            )
        # First match should get [0].
        assert "[0]" in result
        entry = _get_stored_result(0)
        assert entry is not None
        assert entry["type"] == "event-value"
        assert entry["id"] == "47"
        assert "Hard Landing" in entry["name"]

    @pytest.mark.asyncio
    async def test_repeat_call_uses_cached_event_type_field(self) -> None:
        client = await self._setup({
            "P40": [{"value": 1, "label": "Hard Landing"}],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )
            first = client.get.call_count
            await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )
            second = client.get.call_count
        assert second == first, (
            "second find_event_types call should hit cached field info "
            "and catalog, issuing no new HTTP calls"
        )

    @pytest.mark.asyncio
    async def test_case_insensitive_profile_codes(self) -> None:
        client = await self._setup({
            "P40": [{"value": 1, "label": "Hard Landing"}],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["p40"],
            )
        assert "Hard Landing" in result
        assert "Error" not in result.splitlines()[0]


class TestFindEventTypesColdPath:
    """End-to-end resolution without pre-seeded field caches.

    These tests exercise the full _resolve_event_type_field_id /
    _recursive_field_search / _fetch_event_type_discrete_values stack
    so the candidate-name fallback and cold->warm cache transitions
    are actually verified, not just shape-checked.
    """

    @pytest.fixture(autouse=True)
    async def _clear(self) -> None:
        await database_cache.clear()
        await field_cache.clear()
        _reset_result_store()

    @pytest.mark.asyncio
    async def test_resolves_prefixed_event_type_field_name(self) -> None:
        """Field literally named 'P40: Event Type' should resolve via the
        first candidate."""
        db = _profile_db("P40", description="FDAP Landing")
        db_id = db["id"]
        root, routes = _make_root_response(profiles=[db])

        event_type_field_id = "[ems-core][field][p40-event-type]"
        field_groups = {
            db_id: {
                "id": "[none]",
                "name": "Root",
                "fields": [
                    {"id": event_type_field_id, "name": "P40: Event Type",
                     "type": "discrete"},
                    {"id": "other-field", "name": "Severity",
                     "type": "number"},
                ],
                "groups": [],
            },
        }
        field_info = {
            (db_id, event_type_field_id): {
                "id": event_type_field_id,
                "name": "P40: Event Type",
                "type": "discrete",
                "discreteValues": [
                    {"value": 47, "label": "Hard Landing (Acceleration Method) (FDAP)"},
                    {"value": 22, "label": "Bounced Landing"},
                ],
            },
        }
        client = _make_mock_client(
            root, routes, field_groups=field_groups, field_info=field_info,
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )

        assert "Hard Landing (Acceleration Method) (FDAP)" in result
        assert "code=47" in result
        assert "Bounced Landing" not in result

    @pytest.mark.asyncio
    async def test_falls_back_to_bare_event_type_name(self) -> None:
        """Field named just 'Event Type' (no prefix) should still resolve
        via the second candidate -- the resolver must not give up after
        the prefixed name fails."""
        db = _profile_db("P40", description="FDAP Landing")
        db_id = db["id"]
        root, routes = _make_root_response(profiles=[db])

        event_type_field_id = "[ems-core][field][bare-event-type]"
        field_groups = {
            db_id: {
                "id": "[none]",
                "name": "Root",
                "fields": [
                    # No "P40:" prefix here; first candidate must miss.
                    {"id": event_type_field_id, "name": "Event Type",
                     "type": "discrete"},
                ],
                "groups": [],
            },
        }
        field_info = {
            (db_id, event_type_field_id): {
                "id": event_type_field_id,
                "name": "Event Type",
                "type": "discrete",
                "discreteValues": [
                    {"value": 47, "label": "Hard Landing (Acceleration Method) (FDAP)"},
                ],
            },
        }
        client = _make_mock_client(
            root, routes, field_groups=field_groups, field_info=field_info,
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            result = await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )

        assert "Hard Landing (Acceleration Method) (FDAP)" in result
        assert "code=47" in result

    @pytest.mark.asyncio
    async def test_cold_to_warm_caches_event_type_field_and_info(self) -> None:
        """First call hits HTTP for field-groups + field-info; second call
        must NOT issue any new field-* GETs."""
        db = _profile_db("P40", description="FDAP Landing")
        db_id = db["id"]
        root, routes = _make_root_response(profiles=[db])

        event_type_field_id = "[ems-core][field][p40-event-type]"
        field_groups = {
            db_id: {
                "id": "[none]",
                "name": "Root",
                "fields": [
                    {"id": event_type_field_id, "name": "P40: Event Type",
                     "type": "discrete"},
                ],
                "groups": [],
            },
        }
        field_info = {
            (db_id, event_type_field_id): {
                "id": event_type_field_id,
                "name": "P40: Event Type",
                "type": "discrete",
                "discreteValues": [
                    {"value": 1, "label": "Hard Landing"},
                ],
            },
        }
        client = _make_mock_client(
            root, routes, field_groups=field_groups, field_info=field_info,
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )
            after_cold = client.get.call_count
            assert after_cold > 0, "cold call should hit HTTP"
            await _find_event_types(
                ems_system_id=1, query="hard landing", profiles=["P40"],
            )
            after_warm = client.get.call_count

        assert after_warm == after_cold, (
            "warm call must not issue any new HTTP requests"
        )


class TestFormatDatabaseGroupApmTag:
    """Tags applied to DBs when their parent group is APM Events."""

    def test_apm_events_group_tags_profile_dbs(self) -> None:
        group = {
            "id": "g-apm",
            "name": "APM Events",
            "databases": [
                {
                    "id": "[ems-core][entity-type][p40-events]",
                    "name": "P40: Events",
                    "description": "FDAP Landing Profile",
                },
                {
                    "id": "[ems-core][entity-type][p796-events]",
                    "name": "P796: Events",
                    "description": "Generic Safety Events",
                },
            ],
            "groups": [],
        }
        result = _format_database_group(group)
        assert "event-profile" in result
        assert "list_event_profiles" in result
        assert "find_event_types" in result
        # The default entity-type advice should NOT also be present for these DBs
        # (the tag replaces it).
        # Two profile lines, one tag each.
        assert result.count("event-profile") == 2

    def test_non_apm_group_does_not_tag(self) -> None:
        group = {
            "id": "g-other",
            "name": "FDW",
            "databases": [
                {
                    "id": "[ems-core][entity-type][some-events]",
                    "name": "Some Events",
                    "description": "x",
                },
            ],
            "groups": [],
        }
        result = _format_database_group(group)
        assert "event-profile" not in result
