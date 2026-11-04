"""Regression tests for issues found in the QA pass against production EMS.

Each test names the QA finding it covers (H = high, M = medium, L = low).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import TextContent

from ems_mcp.api.client import EMSNotFoundError
from ems_mcp.cache import database_cache, field_cache, make_cache_key
from ems_mcp.tools.assets import get_assets
from ems_mcp.tools.discovery import (
    _auto_shortlist_profiles,
    _reset_result_store,
    _resolve_database_id,
    _store_result,
    list_event_profiles,
)
from ems_mcp.tools.query import (
    _format_analytics_results_csv,
    _resolve_discrete_filter_value,
    query_database,
)

_query_database = query_database.fn
_get_assets = get_assets.fn
_list_event_profiles = list_event_profiles.fn

FDW = "[ems-core][entity-type][foqa-flights]"
PROFILE_DB = "[ems-core][entity-type][p972-events]"


@pytest.fixture(autouse=True)
async def _clean() -> None:
    await field_cache.clear()
    await database_cache.clear()
    _reset_result_store()


def _query_client(rows: list[list[Any]] | None = None) -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(return_value={
        "header": [{"name": "Flight Record"}],
        "rows": rows if rows is not None else [[1]],
    })
    client.get = AsyncMock(side_effect=AssertionError("unexpected GET"))
    return client


async def _run_query(client: MagicMock, **kwargs: Any) -> str:
    with patch("ems_mcp.tools.query.get_client", return_value=client), \
         patch("ems_mcp.tools.discovery.get_client", return_value=client):
        return await _query_database(ems_system_id=1, **kwargs)


def _sent_filter(client: MagicMock) -> dict[str, Any]:
    return client.post.call_args.kwargs["json"]["filter"]


class TestEventRefsInQueryDatabase:
    """H3: event [N] refs from find_event_types are usable in query_database."""

    def _store_event(self, code: str = "47") -> int:
        return _store_result(
            "Event Type = Hard Landing", code, result_type="event-value",
            ems_system_id=1, database_id=PROFILE_DB,
            extra={"event_type_field_id": "[-hub-][event-type]"},
        )

    @pytest.mark.asyncio
    async def test_event_ref_as_field_expands_to_event_type_equals_code(self) -> None:
        ref = self._store_event()
        client = _query_client()
        result = await _run_query(
            client, database_id=PROFILE_DB, fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": f"[{ref}]", "operator": "equal"}],
            apply_best_practice_filters=False,
        )
        assert not result.startswith("Error"), result
        f = _sent_filter(client)
        assert f["args"][0]["value"] == "[-hub-][event-type]"
        assert f["args"][1]["value"] == 47

    @pytest.mark.asyncio
    async def test_event_refs_as_in_values(self) -> None:
        r1 = self._store_event("47")
        r2 = self._store_event("48")
        client = _query_client()
        await _run_query(
            client, database_id=PROFILE_DB, fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": f"[{r1}]", "operator": "in", "value": [f"[{r1}]", f"[{r2}]"]}],
            apply_best_practice_filters=False,
        )
        f = _sent_filter(client)
        assert [a["value"] for a in f["args"][1:]] == [47, 48]

    @pytest.mark.asyncio
    async def test_event_ref_from_other_database_is_rejected(self) -> None:
        ref = self._store_event()
        result = await _run_query(
            _query_client(), database_id="[ems-core][entity-type][other]",
            fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": f"[{ref}]", "operator": "equal"}],
        )
        assert result.startswith("Error") and "different profile database" in result


class TestFilterNormalisation:
    @pytest.mark.asyncio
    async def test_in_with_scalar_is_wrapped(self) -> None:
        """L2."""
        client = _query_client()
        result = await _run_query(
            client, database_id="[db]", fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": "[-hub-][n]", "operator": "in", "value": 5}],
        )
        assert not result.startswith("Error"), result
        assert [a["value"] for a in _sent_filter(client)["args"][1:]] == [5]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["02/11/2026", "2/11/26", "02-11-2026", "02.11.2026 06:00"])
    async def test_non_iso_dates_rejected(self, value: str) -> None:
        """M1: day/month dates are read by EMS as month/day, so refuse them."""
        result = await _run_query(
            _query_client(), database_id="[db]", fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": "[-hub-][date]", "operator": "greaterThan", "value": value}],
        )
        assert result.startswith("Error") and "YYYY-MM-DD" in result

    @pytest.mark.asyncio
    async def test_iso_dates_pass(self) -> None:
        client = _query_client()
        result = await _run_query(
            client, database_id="[db]", fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": "[-hub-][date]", "operator": "between",
                      "value": ["2026-11-01", "2026-11-02T06:00:00"]}],
        )
        assert not result.startswith("Error"), result


class TestAutoFilterDisclosure:
    """M2: auto-applied best-practice filters are visible and user choice wins."""

    async def _seed_validity_fields(self) -> None:
        # Resolve the bundle's field names without a BFS.
        for name, fid in (("takeoff valid", "[tv]"), ("landing valid", "[lv]")):
            await field_cache.set(make_cache_key("field_resolve", 1, FDW, name), fid)

    @pytest.mark.asyncio
    async def test_applied_filters_listed_in_table_output(self) -> None:
        await self._seed_validity_fields()
        result = await _run_query(
            _query_client(), database_id=FDW, fields=[{"field_id": "[-hub-][rec]"}],
        )
        assert "Auto-applied data-quality filters: Takeoff Valid = True; Landing Valid = True" in result
        assert "apply_best_practice_filters=False" in result

    @pytest.mark.asyncio
    async def test_applied_filters_disclosed_on_zero_rows(self) -> None:
        await self._seed_validity_fields()
        result = await _run_query(
            _query_client(rows=[]), database_id=FDW, fields=[{"field_id": "[-hub-][rec]"}],
        )
        assert result.startswith("Query returned 0 rows.")
        assert "Auto-applied" in result

    @pytest.mark.asyncio
    async def test_json_output_has_auto_filters_key(self) -> None:
        import json

        await self._seed_validity_fields()
        result = await _run_query(
            _query_client(), database_id=FDW, fields=[{"field_id": "[-hub-][rec]"}],
            output_format="json",
        )
        assert json.loads(result)["auto_filters"] == ["Takeoff Valid = True", "Landing Valid = True"]

    @pytest.mark.asyncio
    async def test_user_filter_by_raw_id_overrides_auto_filter(self) -> None:
        await self._seed_validity_fields()
        client = _query_client()
        result = await _run_query(
            client, database_id=FDW, fields=[{"field_id": "[-hub-][rec]"}],
            filters=[{"field_id": "[tv]", "operator": "equal", "value": False}],
        )
        sent = _sent_filter(client)
        pairs = [
            (a["value"]["args"][0]["value"], a["value"]["args"][1]["value"])
            for a in sent["args"]
        ]
        assert ("[tv]", False) in pairs
        assert ("[tv]", True) not in pairs
        assert "Takeoff Valid = True" not in result


class TestDatabaseResolution:
    """H3: profile Events databases resolve by [N], name, or P-code."""

    async def _seed_catalog(self) -> None:
        await database_cache.set(make_cache_key("database_name_map", 1), {"fdw flights": FDW})
        await database_cache.set(make_cache_key("event_profile_catalog", 1), [
            {"code": "P972", "database_id": PROFILE_DB,
             "database_name": "P972: Hard landing extract Events - All Events",
             "description": "", "group_name": "Safety"},
            {"code": None, "database_id": "[db-a]", "database_name": "Risk of Tail Strike",
             "description": "", "group_name": "A320"},
            {"code": None, "database_id": "[db-b]", "database_name": "Risk of Tail Strike",
             "description": "", "group_name": "A330"},
        ])

    @pytest.mark.asyncio
    async def test_profile_database_by_name_and_code(self) -> None:
        await self._seed_catalog()
        name = "p972: hard landing extract events - all events"
        assert await _resolve_database_id(name, 1) == PROFILE_DB
        assert await _resolve_database_id("P972", 1) == PROFILE_DB

    @pytest.mark.asyncio
    async def test_duplicate_profile_names_ask_for_ref(self) -> None:
        await self._seed_catalog()
        with pytest.raises(ValueError, match=r"matches 2 event-profile databases") as exc:
            await _resolve_database_id("Risk of Tail Strike", 1)
        assert "(in A320)" in str(exc.value) and "(in A330)" in str(exc.value)

    @pytest.mark.asyncio
    async def test_event_profile_ref(self) -> None:
        ref = _store_result("Risk of Tail Strike", "[db-b]", result_type="event-profile",
                            ems_system_id=1)
        assert await _resolve_database_id(f"[{ref}]", 1) == "[db-b]"
        with pytest.raises(ValueError, match="not a known database reference"):
            await _resolve_database_id("[999]", 1)


class TestEventProfiles:
    def test_auto_shortlist_prefers_all_tokens(self) -> None:
        """H3: 'tail strike' must not match every 'All Tails' profile."""
        catalog = [
            {"code": None, "database_name": "Bleed Trip #1 (All Tails)", "description": ""},
            {"code": "P439", "database_name": "P439: Risk of Tail Strike during Landing",
             "description": ""},
        ]
        shortlist, truncated = _auto_shortlist_profiles(catalog, ["tail strike"], 10)
        assert [e["database_name"] for e in shortlist] == [
            "P439: Risk of Tail Strike during Landing",
        ]
        assert not truncated

    def test_auto_shortlist_falls_back_to_ranked_any_token(self) -> None:
        catalog = [
            {"code": None, "database_name": "Bleed Trip (All Tails)", "description": ""},
            {"code": "P1", "database_name": "P1: Airframe Strike", "description": "tail"},
        ]
        shortlist, _ = _auto_shortlist_profiles(catalog, ["tail strike sensor"], 10)
        assert shortlist[0]["code"] == "P1"  # 2 tokens beat 1

    @pytest.mark.asyncio
    async def test_list_event_profiles_rejects_negative_max_results(self) -> None:
        """H4."""
        result = await _list_event_profiles(ems_system_id=1, max_results=-1)
        assert result.startswith("Error: max_results must be between 1")


class TestEventTypeFieldLookup:
    """Finding a profile's Event Type field must be cheap and work without a P-code."""

    @pytest.mark.asyncio
    async def test_codeless_profile_found_in_event_group_first(self) -> None:
        from ems_mcp.tools.discovery import _resolve_event_type_field_id

        groups = {
            None: {"name": "Root", "fields": [], "groups": [
                {"id": "g-meas", "name": "Measurements"},
                {"id": "g-evt", "name": "Event Information"},
            ]},
            "g-evt": {"name": "Event Information", "fields": [
                {"id": "f-et", "name": "P315: Event Type"},
            ], "groups": []},
        }

        async def get(path: str, **kwargs: Any) -> Any:
            gid = path.split("groupId=", 1)[1] if "groupId=" in path else None
            if gid not in groups:
                raise AssertionError(f"should not need group {gid}")
            return groups[gid]

        client = MagicMock()
        client.get = AsyncMock(side_effect=get)
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            field_id, name = await _resolve_event_type_field_id(
                1, "Risk of Tail Strike during Landing", "[db-child]",
            )
        assert (field_id, name) == ("f-et", "P315: Event Type")
        assert client.get.call_count == 2  # root + the "event" group, not Measurements

    @pytest.mark.asyncio
    async def test_missing_field_gives_up_within_budget(self) -> None:
        from ems_mcp.tools.discovery import _EVENT_TYPE_MAX_GROUPS, _resolve_event_type_field_id

        counter = {"n": 0}

        async def get(path: str, **kwargs: Any) -> Any:
            counter["n"] += 1
            return {"name": "G", "fields": [], "groups": [
                {"id": f"g{counter['n']}-{i}", "name": "Sub"} for i in range(3)
            ]}

        client = MagicMock()
        client.get = AsyncMock(side_effect=get)
        with patch("ems_mcp.tools.discovery.get_client", return_value=client):
            field_id, error = await _resolve_event_type_field_id(1, "P1", "[db]")
        assert field_id is None and "Could not locate" in error
        assert client.get.call_count == _EVENT_TYPE_MAX_GROUPS


class TestAssets:
    @pytest.mark.asyncio
    async def test_bad_fleet_id_is_reported_as_fleet_not_system(self) -> None:
        """H7."""
        client = MagicMock()
        client.get = AsyncMock(side_effect=EMSNotFoundError(
            "Fleet not found.: The fleet with id 99999 was not found.", 404,
        ))
        with patch("ems_mcp.tools.assets.get_client", return_value=client):
            result = await _get_assets(ems_system_id=1, asset_type="aircraft", fleet_id=99999)
        assert result.startswith("Error: fleet 99999 not found")
        assert "asset_type='fleets'" in result
        assert "EMS system 1 not found" not in result


class TestOutputDetails:
    def test_csv_uses_consistent_line_endings(self) -> None:
        """L5."""
        out = _format_analytics_results_csv([{
            "flight_id": 1,
            "data": {"offsets": [0.0, 1.0506101220244048],
                     "results": [{"analyticId": "a", "values": [345.0, 344.00000000001]}]},
        }], analytic_names=["Alt"])
        assert "\r" not in out
        # M5/H5: offsets and values are rounded.
        assert "1.05,344.0" in out

    @pytest.mark.asyncio
    async def test_discrete_error_names_field_and_lists_partial_matches(self) -> None:
        """L1."""
        meta = {
            "id": "[-hub-][fleet]", "name": "Fleet", "type": "discrete",
            "discreteValues": {"1": "EFA - A321-231 (JSTF9)", "2": "QFA - A380-842",
                               "3": "JST - A321neo"},
        }
        await field_cache.set(make_cache_key("field_info", 1, "[db]", "[-hub-][fleet]"), meta)
        with pytest.raises(ValueError) as exc:
            await _resolve_discrete_filter_value("a321", "[-hub-][fleet]", 1, "[db]")
        message = str(exc.value)
        assert "field 'Fleet'" in message and "[-hub-]" not in message
        assert "EFA - A321-231 (JSTF9)" in message and "JST - A321neo" in message
        assert "A380" not in message


class TestErrorFlagging:
    """Tool failures must reach MCP clients as isError results."""

    @pytest.mark.asyncio
    async def test_error_text_is_raised_as_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        from ems_mcp.server import ErrorResultMiddleware

        result = MagicMock()
        result.content = [TextContent(type="text", text="Error: fleet 1 not found.")]
        with pytest.raises(ToolError, match="fleet 1 not found"):
            await ErrorResultMiddleware().on_call_tool(MagicMock(), AsyncMock(return_value=result))

    @pytest.mark.asyncio
    async def test_normal_text_passes_through(self) -> None:
        from ems_mcp.server import ErrorResultMiddleware

        result = MagicMock()
        result.content = [TextContent(type="text", text="Found 3 fleet(s): ...")]
        out = await ErrorResultMiddleware().on_call_tool(MagicMock(), AsyncMock(return_value=result))
        assert out is result
