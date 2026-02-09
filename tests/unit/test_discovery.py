"""Unit tests for EMS MCP discovery tools."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_mcp.tools.discovery import (
    _do_browse_fields,
    _do_search_fields,
    _do_deep_search_fields,
    _format_analytics_search_results,
    _format_database_group,
    _format_deep_search_results,
    _format_ems_systems,
    _format_field_group,
    _format_field_info,
    _format_field_search_results,
    _get_stored_result,
    _is_entity_type_database,
    _recursive_field_search,
    _reset_result_store,
    _resolve_database_id,
    _resolve_field_id,
    _store_result,
    find_fields,
    get_field_info,
    get_result_id,
    list_databases,
    list_ems_systems,
    search_analytics,
)

# Access the underlying functions from the FastMCP FunctionTool wrappers
_list_ems_systems = list_ems_systems.fn
_list_databases = list_databases.fn
_find_fields = find_fields.fn
_get_field_info = get_field_info.fn
_search_analytics = search_analytics.fn
_get_result_id = get_result_id.fn


class TestFormatters:
    """Tests for formatting functions."""

    def test_format_ems_systems_empty(self) -> None:
        """Format empty systems list."""
        result = _format_ems_systems([])
        assert result == "No EMS systems found."

    def test_format_ems_systems_single(self) -> None:
        """Format single system."""
        systems = [{"id": 1, "name": "Production", "description": "Main system"}]
        result = _format_ems_systems(systems)
        assert "Found 1 EMS system(s):" in result
        assert "Production (ID: 1): Main system" in result

    def test_format_ems_systems_multiple(self) -> None:
        """Format multiple systems."""
        systems = [
            {"id": 1, "name": "Production", "description": "Main system"},
            {"id": 2, "name": "Test"},
        ]
        result = _format_ems_systems(systems)
        assert "Found 2 EMS system(s):" in result
        assert "Production (ID: 1): Main system" in result
        assert "Test (ID: 2)" in result

    def test_format_database_group_root(self) -> None:
        """Format root database group."""
        group = {
            "id": "[none]",
            "name": "Root",
            "databases": [
                {"id": "ems-core", "name": "FDW Flights", "description": "Flight Data Warehouse"}
            ],
            "groups": [
                {"id": "group-1", "name": "Profile Results"}
            ],
        }
        result = _format_database_group(group)
        assert "Group: Root" in result
        assert "FDW Flights: Flight Data Warehouse" in result
        # Database IDs should be hidden for non-group databases
        assert "(ID: ems-core)" not in result
        assert "Profile Results (ID: group-1)" in result
        assert "Use database names directly" in result

    def test_format_database_group_nested(self) -> None:
        """Format nested database group with pluralName."""
        group = {
            "id": "fdw-group",
            "name": "FDW",
            "databases": [
                {"id": "[ems-core][entity-type][foqa-flights]", "pluralName": "FDW Flights", "singularName": "FDW Flight"}
            ],
            "groups": [],
        }
        result = _format_database_group(group)
        assert "FDW Flights" in result

    def test_format_database_group_annotates_entity_type_group(self) -> None:
        """Database IDs containing entity-type-group should be annotated."""
        group = {
            "id": "fdw-group",
            "name": "FDW",
            "databases": [
                {"id": "[ems-core][entity-type-group][foqa-flights]", "name": "FOQA Flights"}
            ],
            "groups": [],
        }
        result = _format_database_group(group)
        assert "NOTE: this is a group ID" in result
        assert "list_databases" in result

    def test_format_database_group_empty(self) -> None:
        """Format empty database group."""
        group = {"id": "empty", "name": "Empty", "databases": [], "groups": []}
        result = _format_database_group(group)
        assert "(Empty group)" in result

    def test_format_field_group_with_fields(self) -> None:
        """Format field group with fields."""
        _reset_result_store()
        group = {
            "id": "[none]",
            "name": "Root",
            "fields": [
                {"id": "field-123", "name": "Flight Date", "type": "datetime"},
                {"id": "field-456", "name": "Duration", "type": "number"},
            ],
            "groups": [
                {"id": "identification", "name": "Identification"}
            ],
        }
        result = _format_field_group(group)
        assert "Field Group: Root" in result
        assert "Fields (2):" in result
        assert "Flight Date (datetime)" in result
        assert "Duration (number)" in result
        assert "Identification (ID: identification)" in result
        # Fields now use numbered references instead of showing raw IDs
        assert "[0]" in result or "[" in result

    def test_format_field_group_uses_numbered_refs(self) -> None:
        """Field group should use numbered [N] references instead of raw IDs."""
        _reset_result_store()
        long_id = "[-hub-][field][[[ems-core][entity-type][foqa-flights]][[ems-core][base-field][flight.uid]]]"
        group = {
            "id": "[none]",
            "name": "Root",
            "fields": [{"id": long_id, "name": "Test Field", "type": "string"}],
            "groups": [],
        }
        result = _format_field_group(group)
        # Raw ID should NOT appear inline; a numbered ref should be used instead
        assert long_id not in result
        assert "Test Field (string)" in result
        # The stored result should have the full ID
        ref_entry = _get_stored_result(0)
        assert ref_entry is not None
        assert ref_entry["id"] == long_id

    def test_format_field_search_results_empty(self) -> None:
        """Format empty search results."""
        result = _format_field_search_results([])
        assert "No fields found" in result

    def test_format_field_search_results_with_units(self) -> None:
        """Format search results including units."""
        _reset_result_store()
        fields = [
            {"id": "f1", "name": "Altitude", "type": "number", "units": "ft"},
            {"id": "f2", "name": "Airspeed", "type": "number", "units": "knots"},
        ]
        result = _format_field_search_results(fields)
        assert "Found 2 field(s):" in result
        assert "Altitude [number (ft)]" in result
        assert "Airspeed [number (knots)]" in result
        # Default: IDs hidden, numbered refs shown
        assert "ID:" not in result
        assert "[0]" in result
        assert "[1]" in result
        assert "reference numbers or field names" in result

    def test_format_field_search_results_show_ids(self) -> None:
        """Format search results with show_ids=True shows full IDs."""
        fields = [
            {"id": "f1", "name": "Altitude", "type": "number", "units": "ft"},
        ]
        result = _format_field_search_results(fields, show_ids=True)
        assert "ID: f1" in result
        assert "get_result_id" not in result

    def test_format_field_info_basic(self) -> None:
        """Format basic field info."""
        field = {
            "id": "field-123",
            "name": "Flight Date",
            "type": "datetime",
            "description": "Date of the flight",
        }
        result = _format_field_info(field)
        assert "Field: Flight Date" in result
        assert "Type: datetime" in result
        assert "Description: Date of the flight" in result
        assert "Field ID: field-123" in result

    def test_format_field_info_with_discrete_values(self) -> None:
        """Format field info with discrete values."""
        field = {
            "id": "field-456",
            "name": "Status",
            "type": "discrete",
            "discreteValues": [
                {"value": 0, "label": "Pending"},
                {"value": 1, "label": "Active"},
                {"value": 2, "label": "Complete"},
            ],
        }
        result = _format_field_info(field)
        assert "Discrete Values (3):" in result
        assert "0: Pending" in result
        assert "1: Active" in result
        assert "2: Complete" in result

    def test_format_field_info_with_dict_discrete_values(self) -> None:
        """Format field info with dict-format discrete values (key=code, value=label)."""
        field = {
            "id": "field-789",
            "name": "Airport",
            "type": "discrete",
            "discreteValues": {
                "676": "YPKA",
                "411": "YPKG",
                "123": "YSSY",
            },
        }
        result = _format_field_info(field)
        assert "Discrete Values (3):" in result
        assert "676: YPKA" in result
        assert "411: YPKG" in result
        assert "123: YSSY" in result

    def test_format_field_info_truncates_many_discrete_values(self) -> None:
        """Discrete values should be truncated at 50."""
        field = {
            "id": "field-456",
            "name": "Large Discrete",
            "type": "discrete",
            "discreteValues": [{"value": i, "label": f"Value {i}"} for i in range(100)],
        }
        result = _format_field_info(field)
        assert "Discrete Values (100):" in result
        assert "... and 50 more values" in result

    def test_format_analytics_search_results_empty(self) -> None:
        """Format empty analytics search."""
        result = _format_analytics_search_results([])
        assert "No analytics found" in result

    def test_format_analytics_search_results(self) -> None:
        """Format analytics search results with default (IDs hidden)."""
        _reset_result_store()
        analytics = [
            {
                "id": "analytic-123",
                "name": "Altitude (Baro)",
                "type": "number",
                "units": "ft",
                "description": "Barometric altitude",
            },
        ]
        result = _format_analytics_search_results(analytics)
        assert "Found 1 analytic(s):" in result
        assert "Altitude (Baro) [number (ft)]" in result
        assert "Barometric altitude" in result
        # Default: IDs hidden, numbered refs shown
        assert "ID:" not in result
        assert "[0]" in result
        assert "query_flight_analytics" in result

    def test_format_analytics_search_results_show_ids(self) -> None:
        """Format analytics search results with show_ids=True shows full IDs."""
        analytics = [
            {
                "id": "analytic-123",
                "name": "Altitude (Baro)",
                "type": "number",
                "units": "ft",
                "description": "Barometric altitude",
            },
        ]
        result = _format_analytics_search_results(analytics, show_ids=True)
        assert "ID: analytic-123" in result
        assert "query_flight_analytics" not in result


class TestListEmsSystems:
    """Tests for list_ems_systems tool."""

    @pytest.mark.asyncio
    async def test_list_ems_systems_success(self) -> None:
        """Tool should return formatted list of systems."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[
                {"id": 1, "name": "Production", "description": "Main system"},
                {"id": 2, "name": "Test", "description": "Test environment"},
            ]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _list_ems_systems()

        assert "Found 2 EMS system(s):" in result
        assert "Production" in result
        mock_client.get.assert_called_once_with("/api/v2/ems-systems")

    @pytest.mark.asyncio
    async def test_list_ems_systems_error(self) -> None:
        """Tool should return error message on failure."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSAPIError("Connection failed"))

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _list_ems_systems()

        assert "Error listing EMS systems" in result
        assert "Connection failed" in result


class TestListDatabases:
    """Tests for list_databases tool."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear database cache before each test."""
        from ems_mcp.cache import database_cache
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_list_databases_root(self) -> None:
        """Tool should list root level databases."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[none]",
                "name": "Root",
                "databases": [{"id": "ems-core", "name": "FDW Flights"}],
                "groups": [{"id": "g1", "name": "Profile Results"}],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _list_databases(ems_system_id=1)

        assert "FDW Flights" in result
        assert "Profile Results" in result
        mock_client.get.assert_called_once_with("/api/v2/ems-systems/1/database-groups")

    @pytest.mark.asyncio
    async def test_list_databases_with_group_id(self) -> None:
        """Tool should navigate to specific group."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "g1",
                "name": "Profile Results",
                "databases": [],
                "groups": [],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _list_databases(ems_system_id=1, group_id="g1")

        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/database-groups?groupId=g1"
        )

    @pytest.mark.asyncio
    async def test_list_databases_uses_cache(self) -> None:
        """Tool should use cached results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[none]",
                "name": "Root",
                "databases": [{"id": "ems-core", "name": "FDW Flights"}],
                "groups": [],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            # First call - hits API
            await _list_databases(ems_system_id=1)
            # Second call - should use cache
            await _list_databases(ems_system_id=1)

        # API should only be called once
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_list_databases_not_found(self) -> None:
        """Tool should handle not found errors."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _list_databases(ems_system_id=999)

        assert "Error" in result
        assert "Verify ems_system_id" in result


class TestFindFieldsBrowse:
    """Tests for find_fields tool in browse mode (formerly list_fields)."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache, database_cache
        await field_cache.clear()
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_browse_fields_root(self) -> None:
        """Tool should list root level fields."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[none]",
                "name": "Root",
                "fields": [{"id": "f1", "name": "Flight Date", "type": "datetime"}],
                "groups": [{"id": "ident", "name": "Identification"}],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[ems-core]", mode="browse"
            )

        assert "Flight Date" in result
        assert "Identification" in result

    @pytest.mark.asyncio
    async def test_browse_fields_with_group_id(self) -> None:
        """Tool should navigate to specific field group."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "ident",
                "name": "Identification",
                "fields": [{"id": "f2", "name": "Tail Number", "type": "string"}],
                "groups": [],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[ems-core]",
                mode="browse", group_id="ident",
            )

        assert "Tail Number" in result

    @pytest.mark.asyncio
    async def test_browse_fields_rejects_entity_type_group_id(self) -> None:
        """Tool should reject database IDs that are actually group IDs."""
        result = await _find_fields(
            ems_system_id=1,
            database_id="[ems-core][entity-type-group][foqa-flights]",
            mode="browse",
        )
        assert "Error" in result
        assert "GROUP ID" in result
        assert "list_databases" in result

    @pytest.mark.asyncio
    async def test_browse_fields_not_found(self) -> None:
        """Tool should handle database not found."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[invalid]", mode="browse"
            )

        assert "Error" in result
        assert "Use list_databases" in result


class TestFindFieldsSearch:
    """Tests for find_fields tool in search mode (formerly search_fields)."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache, database_cache
        await field_cache.clear()
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_search_requires_search_text(self) -> None:
        """Search mode should require search_text."""
        result = await _find_fields(
            ems_system_id=1,
            database_id="[ems-core]",
            mode="search",
        )
        assert "Error" in result
        assert "search_text" in result

    @pytest.mark.asyncio
    async def test_search_fields_rejects_entity_type_group_id(self) -> None:
        """Tool should reject database IDs that are actually group IDs."""
        result = await _find_fields(
            ems_system_id=1,
            database_id="[ems-core][entity-type-group][foqa-flights]",
            mode="search",
            search_text="altitude",
        )
        assert "Error" in result
        assert "GROUP ID" in result
        assert "list_databases" in result

    @pytest.mark.asyncio
    async def test_search_fields_success(self) -> None:
        """Tool should return matching fields."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[
                {"id": "f1", "name": "Flight Date", "type": "datetime"},
                {"id": "f2", "name": "Flight Duration", "type": "number", "units": "seconds"},
            ]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[ems-core]",
                mode="search", search_text="flight",
            )

        assert "Found 2 field(s):" in result
        assert "Flight Date" in result
        assert "Flight Duration" in result

    @pytest.mark.asyncio
    async def test_search_fields_respects_max_results(self) -> None:
        """Tool should limit results to max_results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[
                {"id": f"f{i}", "name": f"Field {i}", "type": "string"}
                for i in range(100)
            ]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1,
                database_id="[ems-core]",
                mode="search",
                search_text="field",
                max_results=10,
            )

        assert "Found 10 field(s):" in result

    @pytest.mark.asyncio
    async def test_search_fields_uses_cache(self) -> None:
        """Tool should use cached search results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[{"id": "f1", "name": "Test", "type": "string"}]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _find_fields(
                ems_system_id=1, database_id="[ems-core]",
                mode="search", search_text="test",
            )
            await _find_fields(
                ems_system_id=1, database_id="[ems-core]",
                mode="search", search_text="test",
            )

        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_search_fields_rejects_entity_type_database(self) -> None:
        """Tool should reject entity-type database IDs proactively."""
        result = await _find_fields(
            ems_system_id=1,
            database_id="[ems-core][entity-type][foqa-flights]",
            mode="search",
            search_text="altitude",
        )
        assert "Error" in result
        assert "entity-type database" in result
        assert "find_fields" in result or "mode='deep'" in result

    @pytest.mark.asyncio
    async def test_search_fields_405_mentions_entity_type(self) -> None:
        """405 error message should mention entity-type databases."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            side_effect=EMSAPIError("Method Not Allowed", status_code=405)
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1,
                database_id="[some-db-id]",
                mode="search",
                search_text="altitude",
            )

        assert "405" in result
        assert "entity-type" in result


class TestIsEntityTypeDatabase:
    """Tests for _is_entity_type_database helper."""

    def test_entity_type_database(self) -> None:
        """Should detect entity-type databases."""
        assert _is_entity_type_database("[ems-core][entity-type][foqa-flights]") is True

    def test_entity_type_group_not_detected(self) -> None:
        """Should not flag entity-type-group IDs (those are group IDs)."""
        assert _is_entity_type_database("[ems-core][entity-type-group][foqa-flights]") is False

    def test_plain_database_id(self) -> None:
        """Should not flag plain database IDs."""
        assert _is_entity_type_database("ems-core") is False
        assert _is_entity_type_database("[ems-core][base-type][flights]") is False

    def test_both_entity_type_and_group(self) -> None:
        """ID with both patterns should not be flagged (group takes precedence)."""
        assert _is_entity_type_database(
            "[ems-core][entity-type-group][entity-type][foqa]"
        ) is False


class TestGetFieldInfo:
    """Tests for get_field_info tool."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache, database_cache
        await field_cache.clear()
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_get_field_info_basic(self) -> None:
        """Tool should return field details."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[field-123]",
                "name": "Flight Date",
                "type": "datetime",
                "description": "Date of the flight",
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _get_field_info(
                ems_system_id=1, database_id="[ems-core]", field_id="[field-123]"
            )

        assert "Flight Date" in result
        assert "datetime" in result
        assert "Date of the flight" in result

    @pytest.mark.asyncio
    async def test_get_field_info_discrete(self) -> None:
        """Tool should return discrete value mappings."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[field-456]",
                "name": "Status",
                "type": "discrete",
                "discreteValues": [
                    {"value": 0, "label": "Pending"},
                    {"value": 1, "label": "Active"},
                ],
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _get_field_info(
                ems_system_id=1, database_id="[ems-core]", field_id="[field-456]"
            )

        assert "Discrete Values" in result
        assert "0: Pending" in result
        assert "1: Active" in result

    @pytest.mark.asyncio
    async def test_get_field_info_url_encodes_field_id(self) -> None:
        """Tool should URL-encode field IDs with special characters."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={"id": "[-hub-][field][test]", "name": "Test", "type": "string"}
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _get_field_info(
                ems_system_id=1, database_id="[ems-core]", field_id="[-hub-][field][test]"
            )

        # Check that the field_id was URL encoded in the API call
        # The second call is the field info request (first may be resolution)
        for call in mock_client.get.call_args_list:
            call_path = call[0][0]
            if "/fields/" in call_path:
                assert "%5B" in call_path  # URL encoded '['
                assert "%5D" in call_path  # URL encoded ']'
                break

    @pytest.mark.asyncio
    async def test_get_field_info_not_found(self) -> None:
        """Tool should handle field not found."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _get_field_info(
                ems_system_id=1, database_id="[ems-core]", field_id="[invalid]"
            )

        assert "Error" in result
        assert "Use find_fields" in result

    @pytest.mark.asyncio
    async def test_get_field_info_resolves_ref_number(self) -> None:
        """Tool should resolve [N] reference numbers."""
        _reset_result_store()
        _store_result("Flight Date", "[-hub-][field][flight-date]")

        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value={
                "id": "[-hub-][field][flight-date]",
                "name": "Flight Date",
                "type": "datetime",
            }
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _get_field_info(
                ems_system_id=1, database_id="[ems-core]", field_id=0
            )

        assert "Flight Date" in result
        assert "datetime" in result


class TestSearchAnalytics:
    """Tests for search_analytics tool."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_search_analytics_success(self) -> None:
        """Tool should return matching analytics."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[
                {
                    "id": "analytic-1",
                    "name": "Altitude (Baro)",
                    "type": "number",
                    "units": "ft",
                    "description": "Barometric altitude",
                },
                {
                    "id": "analytic-2",
                    "name": "Altitude (GPS)",
                    "type": "number",
                    "units": "ft",
                    "description": "GPS altitude",
                },
            ]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _search_analytics(ems_system_id=1, search_text="altitude")

        assert "Found 2 analytic(s):" in result
        assert "Altitude (Baro)" in result
        assert "Altitude (GPS)" in result
        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/analytics",
            params={"text": "altitude"},
        )

    @pytest.mark.asyncio
    async def test_search_analytics_with_group_id(self) -> None:
        """Tool should filter by group ID."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[])

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _search_analytics(
                ems_system_id=1, search_text="speed", group_id="airspeed-group"
            )

        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/analytics",
            params={"text": "speed", "groupId": "airspeed-group"},
        )

    @pytest.mark.asyncio
    async def test_search_analytics_respects_max_results(self) -> None:
        """Tool should limit results to max_results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[
                {"id": f"a{i}", "name": f"Analytic {i}", "type": "number"}
                for i in range(100)
            ]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _search_analytics(
                ems_system_id=1, search_text="analytic", max_results=20
            )

        assert "Found 20 analytic(s):" in result

    @pytest.mark.asyncio
    async def test_search_analytics_uses_cache(self) -> None:
        """Tool should use cached search results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=[{"id": "a1", "name": "Test", "type": "number"}]
        )

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _search_analytics(ems_system_id=1, search_text="test")
            await _search_analytics(ems_system_id=1, search_text="test")

        assert mock_client.get.call_count == 1


class TestFormatDeepSearchResults:
    """Tests for _format_deep_search_results formatter."""

    def test_empty_results(self) -> None:
        """Empty results should show a message."""
        result = _format_deep_search_results([], "fuel")
        assert "No fields found" in result
        assert "fuel" in result

    def test_single_result_default_hides_id(self) -> None:
        """Single result should show name, type, path but hide ID by default."""
        _reset_result_store()
        results = [{
            "name": "Fuel Burned",
            "id": "field-123",
            "type": "number",
            "units": "kg",
            "path": "Profiles > Efficiency",
        }]
        result = _format_deep_search_results(results, "fuel")
        assert "Found 1 field(s)" in result
        assert "Fuel Burned [number (kg)]" in result
        assert "Path: Profiles > Efficiency" in result
        assert "ID:" not in result
        assert "[0]" in result
        assert "reference numbers or field names" in result

    def test_single_result_show_ids(self) -> None:
        """With show_ids=True, full ID should be displayed."""
        results = [{
            "name": "Fuel Burned",
            "id": "field-123",
            "type": "number",
            "units": "kg",
            "path": "Profiles > Efficiency",
        }]
        result = _format_deep_search_results(results, "fuel", show_ids=True)
        assert "ID: field-123" in result
        assert "get_result_id" not in result

    def test_result_without_units(self) -> None:
        """Result without units should show type only."""
        _reset_result_store()
        results = [{
            "name": "Flight ID",
            "id": "f1",
            "type": "string",
            "units": None,
            "path": "(root)",
        }]
        result = _format_deep_search_results(results, "flight")
        assert "Flight ID [string]" in result

    def test_multiple_results(self) -> None:
        """Multiple results should all be shown."""
        _reset_result_store()
        results = [
            {"name": "Fuel A", "id": "f1", "type": "number", "units": "kg", "path": "A"},
            {"name": "Fuel B", "id": "f2", "type": "number", "units": "lb", "path": "B"},
        ]
        result = _format_deep_search_results(results, "fuel")
        assert "Found 2 field(s)" in result
        assert "Fuel A" in result
        assert "Fuel B" in result

    def test_reports_search_stats(self) -> None:
        """Should include search stats when groups_visited and max_groups are provided."""
        _reset_result_store()
        results = [
            {"name": "Fuel A", "id": "f1", "type": "number", "units": "kg", "path": "A"},
        ]
        result = _format_deep_search_results(results, "fuel", groups_visited=47, max_groups=50)
        assert "Searched 47 group(s)" in result
        assert "budget: 50" in result
        assert "exhausted" not in result.lower()

    def test_reports_budget_exhausted(self) -> None:
        """Should warn when the budget was fully used."""
        _reset_result_store()
        results = [
            {"name": "Fuel A", "id": "f1", "type": "number", "units": "kg", "path": "A"},
        ]
        result = _format_deep_search_results(results, "fuel", groups_visited=50, max_groups=50)
        assert "Searched 50 group(s)" in result
        assert "Budget exhausted" in result
        assert "max_groups" in result

    def test_empty_results_with_budget_exhausted(self) -> None:
        """Empty results with exhausted budget should suggest increasing max_groups."""
        result = _format_deep_search_results([], "fuel", groups_visited=50, max_groups=50)
        assert "No fields found" in result
        assert "Budget exhausted" in result
        assert "max_groups" in result


class TestRecursiveFieldSearch:
    """Tests for _recursive_field_search helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_finds_field_at_root(self) -> None:
        """Should find fields at the root level."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]",
            "name": "Root",
            "fields": [
                {"id": "f1", "name": "Fuel Burned", "type": "number", "units": "kg"},
                {"id": "f2", "name": "Duration", "type": "number"},
            ],
            "groups": [],
        })

        results, groups_visited = await _recursive_field_search(
            mock_client, 1, "db", "fuel", max_depth=5, max_results=10, max_groups=50,
        )
        assert len(results) == 1
        assert results[0]["name"] == "Fuel Burned"
        assert results[0]["path"] == "(root)"
        assert groups_visited == 1

    @pytest.mark.asyncio
    async def test_finds_field_in_nested_group(self) -> None:
        """Should find fields in nested groups with correct path."""
        mock_client = MagicMock()

        def mock_get(path: str, **kwargs: Any) -> Any:
            if "groupId=g1" in path:
                return {
                    "id": "g1", "name": "Profiles",
                    "fields": [{"id": "f1", "name": "Fuel Burn Rate", "type": "number"}],
                    "groups": [],
                }
            return {
                "id": "[none]", "name": "Root",
                "fields": [],
                "groups": [{"id": "g1", "name": "Profiles"}],
            }

        mock_client.get = AsyncMock(side_effect=mock_get)

        results, groups_visited = await _recursive_field_search(
            mock_client, 1, "db", "fuel", max_depth=5, max_results=10, max_groups=50,
        )
        assert len(results) == 1
        assert results[0]["name"] == "Fuel Burn Rate"
        assert "Profiles" in results[0]["path"]
        assert groups_visited == 2

    @pytest.mark.asyncio
    async def test_respects_max_results(self) -> None:
        """Should stop after finding max_results matches."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [
                {"id": f"f{i}", "name": f"Field {i}", "type": "string"}
                for i in range(20)
            ],
            "groups": [],
        })

        results, _ = await _recursive_field_search(
            mock_client, 1, "db", "Field", max_depth=5, max_results=5, max_groups=50,
        )
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_respects_max_depth(self) -> None:
        """Should not traverse beyond max_depth."""
        mock_client = MagicMock()
        call_count = 0

        def mock_get(path: str, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Every group has one subgroup but no matching fields
            gid = f"g{call_count}"
            return {
                "id": gid, "name": f"Group {call_count}",
                "fields": [],
                "groups": [{"id": f"g{call_count + 1}", "name": f"Group {call_count + 1}"}],
            }

        mock_client.get = AsyncMock(side_effect=mock_get)

        results, groups_visited = await _recursive_field_search(
            mock_client, 1, "db", "nonexistent", max_depth=3, max_results=10, max_groups=50,
        )
        assert len(results) == 0
        # Should have made at most max_depth+1 API calls (root + 3 levels)
        assert call_count <= 4
        assert groups_visited == call_count

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self) -> None:
        """Field name matching should be case-insensitive."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [{"id": "f1", "name": "FUEL BURNED", "type": "number"}],
            "groups": [],
        })

        results, _ = await _recursive_field_search(
            mock_client, 1, "db", "fuel burned", max_depth=5, max_results=10, max_groups=50,
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_handles_api_errors_gracefully(self) -> None:
        """Should skip groups that return API errors."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        call_count = 0

        def mock_get(path: str, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "id": "[none]", "name": "Root",
                    "fields": [],
                    "groups": [
                        {"id": "g1", "name": "Good"},
                        {"id": "g2", "name": "Bad"},
                    ],
                }
            if "groupId=g1" in path:
                return {
                    "id": "g1", "name": "Good",
                    "fields": [{"id": "f1", "name": "Target Field", "type": "number"}],
                    "groups": [],
                }
            raise EMSAPIError("Server error", status_code=500)

        mock_client.get = AsyncMock(side_effect=mock_get)

        results, groups_visited = await _recursive_field_search(
            mock_client, 1, "db", "target", max_depth=5, max_results=10, max_groups=50,
        )
        assert len(results) == 1
        assert results[0]["name"] == "Target Field"
        assert groups_visited == 3  # root + good + bad (error still counts)

    @pytest.mark.asyncio
    async def test_respects_max_groups(self) -> None:
        """Should stop after max_groups API calls even if queue is not empty."""
        mock_client = MagicMock()

        def mock_get(path: str, **kwargs: Any) -> Any:
            # Every group has 3 subgroups but no matching fields
            return {
                "id": "g", "name": "Group",
                "fields": [],
                "groups": [
                    {"id": f"sub-a-{id(path)}", "name": "Sub A"},
                    {"id": f"sub-b-{id(path)}", "name": "Sub B"},
                    {"id": f"sub-c-{id(path)}", "name": "Sub C"},
                ],
            }

        mock_client.get = AsyncMock(side_effect=mock_get)

        results, groups_visited = await _recursive_field_search(
            mock_client, 1, "db", "nonexistent",
            max_depth=10, max_results=10, max_groups=5,
        )
        assert len(results) == 0
        assert groups_visited == 5

    @pytest.mark.asyncio
    async def test_prioritizes_relevant_groups(self) -> None:
        """Groups with search-term words in name should be visited first."""
        mock_client = MagicMock()
        visit_order: list[str] = []

        def mock_get(path: str, **kwargs: Any) -> Any:
            if "groupId=" not in path:
                # Root: two groups, "Other" listed first, "Flight Info" second
                return {
                    "id": "[none]", "name": "Root",
                    "fields": [],
                    "groups": [
                        {"id": "other", "name": "Other Stuff"},
                        {"id": "flight", "name": "Flight Information"},
                    ],
                }
            if "groupId=flight" in path:
                visit_order.append("flight")
                return {
                    "id": "flight", "name": "Flight Information",
                    "fields": [{"id": "f1", "name": "Flight Number", "type": "string"}],
                    "groups": [],
                }
            if "groupId=other" in path:
                visit_order.append("other")
                return {
                    "id": "other", "name": "Other Stuff",
                    "fields": [],
                    "groups": [],
                }
            return {"id": "x", "name": "X", "fields": [], "groups": []}

        mock_client.get = AsyncMock(side_effect=mock_get)

        results, _ = await _recursive_field_search(
            mock_client, 1, "db", "Flight Number",
            max_depth=5, max_results=10, max_groups=50,
        )
        assert len(results) == 1
        assert results[0]["name"] == "Flight Number"
        # "Flight Information" should have been visited before "Other Stuff"
        assert visit_order.index("flight") < visit_order.index("other")


class TestFindFieldsDeep:
    """Tests for find_fields tool in deep mode (formerly search_fields_deep)."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache, database_cache
        await field_cache.clear()
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_deep_requires_search_text(self) -> None:
        """Deep mode should require search_text."""
        result = await _find_fields(
            ems_system_id=1, database_id="[db]", mode="deep",
        )
        assert "Error" in result
        assert "search_text" in result

    @pytest.mark.asyncio
    async def test_empty_search_text_rejected(self) -> None:
        """Tool should reject empty search text."""
        result = await _find_fields(
            ems_system_id=1, database_id="[db]", mode="deep", search_text="",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_entity_type_group_rejected(self) -> None:
        """Tool should reject entity-type-group database IDs."""
        result = await _find_fields(
            ems_system_id=1,
            database_id="[ems-core][entity-type-group][foqa]",
            mode="deep",
            search_text="fuel",
        )
        assert "Error" in result
        assert "GROUP ID" in result

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Tool should return formatted results."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [
                {"id": "f1", "name": "Fuel Burned in Cruise", "type": "number", "units": "kg"},
            ],
            "groups": [],
        })

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[db]",
                mode="deep", search_text="fuel",
            )

        assert "Found 1 field(s)" in result
        assert "Fuel Burned in Cruise" in result
        assert "number (kg)" in result

    @pytest.mark.asyncio
    async def test_no_results(self) -> None:
        """Tool should show no-results message."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [],
            "groups": [],
        })

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1, database_id="[db]",
                mode="deep", search_text="nonexistent",
            )

        assert "No fields found" in result

    @pytest.mark.asyncio
    async def test_max_depth_clamped(self) -> None:
        """Max depth should be clamped to 10."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root", "fields": [], "groups": [],
        })

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            # Should not error with max_depth > 10
            result = await _find_fields(
                ems_system_id=1, database_id="[db]",
                mode="deep", search_text="test",
                max_depth=100,
            )
        assert "No fields found" in result

    @pytest.mark.asyncio
    async def test_works_on_entity_type_database(self) -> None:
        """Tool should work on entity-type databases (the main use case)."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [{"id": "f1", "name": "Target", "type": "number"}],
            "groups": [],
        })

        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _find_fields(
                ems_system_id=1,
                database_id="[ems-core][entity-type][foqa-flights]",
                mode="deep", search_text="target",
            )

        assert "Found 1 field(s)" in result
        assert "Target" in result


class TestResultStore:
    """Tests for the result reference store."""

    def test_store_and_retrieve(self) -> None:
        """Basic store/retrieve cycle."""
        _reset_result_store()
        ref = _store_result("Test Field", "field-abc-123")
        entry = _get_stored_result(ref)
        assert entry is not None
        assert entry["name"] == "Test Field"
        assert entry["id"] == "field-abc-123"
        assert entry["type"] == "field"

    def test_store_analytic_type(self) -> None:
        """Analytic results should have type='analytic'."""
        _reset_result_store()
        ref = _store_result("Airspeed", "H4sIAAAA...", result_type="analytic")
        entry = _get_stored_result(ref)
        assert entry is not None
        assert entry["type"] == "analytic"

    def test_store_field_type_default(self) -> None:
        """Default result_type should be 'field'."""
        _reset_result_store()
        ref = _store_result("Flight Date", "field-123")
        entry = _get_stored_result(ref)
        assert entry is not None
        assert entry["type"] == "field"

    def test_sequential_refs(self) -> None:
        """Reference numbers should be sequential."""
        _reset_result_store()
        r0 = _store_result("A", "id-a")
        r1 = _store_result("B", "id-b")
        r2 = _store_result("C", "id-c")
        assert r0 == 0
        assert r1 == 1
        assert r2 == 2

    def test_invalid_ref_returns_none(self) -> None:
        """Looking up a non-existent ref returns None."""
        _reset_result_store()
        assert _get_stored_result(999) is None

    def test_evicts_oldest_at_capacity(self) -> None:
        """Oldest entries should be evicted when store exceeds max size."""
        _reset_result_store()
        import ems_mcp.tools.discovery as disc
        old_max = disc._STORE_MAX_SIZE
        try:
            disc._STORE_MAX_SIZE = 5
            refs = []
            for i in range(8):
                refs.append(_store_result(f"F{i}", f"id-{i}"))
            # Oldest 3 refs (0, 1, 2) should be evicted
            assert _get_stored_result(refs[0]) is None
            assert _get_stored_result(refs[1]) is None
            assert _get_stored_result(refs[2]) is None
            # Newest 5 refs (3, 4, 5, 6, 7) should still exist
            for i in range(3, 8):
                assert _get_stored_result(refs[i]) is not None
        finally:
            disc._STORE_MAX_SIZE = old_max

    def test_accumulates_across_searches(self) -> None:
        """Results from multiple formatter calls should coexist."""
        _reset_result_store()
        fields1 = [{"id": "f1", "name": "Alpha", "type": "string"}]
        fields2 = [{"id": "f2", "name": "Beta", "type": "number"}]
        _format_field_search_results(fields1)
        _format_field_search_results(fields2)
        # Both should be retrievable
        e0 = _get_stored_result(0)
        e1 = _get_stored_result(1)
        assert e0 is not None and e0["name"] == "Alpha"
        assert e1 is not None and e1["name"] == "Beta"

    def test_formatters_populate_store(self) -> None:
        """Calling formatters with show_ids=False should populate the store."""
        _reset_result_store()
        fields = [
            {"id": "field-xyz", "name": "Altitude", "type": "number", "units": "ft"},
        ]
        result = _format_field_search_results(fields)
        assert "[0]" in result
        entry = _get_stored_result(0)
        assert entry is not None
        assert entry["id"] == "field-xyz"
        assert entry["name"] == "Altitude"

    def test_analytics_formatter_populates_store(self) -> None:
        """Analytics formatter should populate the store with type='analytic'."""
        _reset_result_store()
        analytics = [
            {"id": "H4sIAAAA...", "name": "Airspeed", "type": "number", "units": "knots"},
        ]
        result = _format_analytics_search_results(analytics)
        assert "[0]" in result
        entry = _get_stored_result(0)
        assert entry is not None
        assert entry["id"] == "H4sIAAAA..."
        assert entry["name"] == "Airspeed"
        assert entry["type"] == "analytic"

    def test_deep_search_formatter_populates_store(self) -> None:
        """Deep search formatter should populate the store."""
        _reset_result_store()
        results = [{
            "name": "Fuel Burned",
            "id": "field-123",
            "type": "number",
            "units": "kg",
            "path": "Profiles",
        }]
        result = _format_deep_search_results(results, "fuel")
        assert "[0]" in result
        entry = _get_stored_result(0)
        assert entry is not None
        assert entry["id"] == "field-123"

    def test_show_ids_true_skips_store(self) -> None:
        """show_ids=True should not populate the store."""
        _reset_result_store()
        fields = [{"id": "f1", "name": "Test", "type": "string"}]
        _format_field_search_results(fields, show_ids=True)
        assert _get_stored_result(0) is None


class TestGetResultId:
    """Tests for get_result_id tool."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Should return correct IDs for valid refs."""
        _reset_result_store()
        _store_result("Flight Number", "[-hub-][field][flight-num]")
        _store_result("Duration", "[-hub-][field][duration]")
        result = await _get_result_id(result_numbers=[0, 1])
        assert "[0] Flight Number (field)" in result
        assert "ID: [-hub-][field][flight-num]" in result
        assert "[1] Duration (field)" in result
        assert "ID: [-hub-][field][duration]" in result

    @pytest.mark.asyncio
    async def test_shows_analytic_type(self) -> None:
        """Should show (analytic) type label for analytic refs."""
        _reset_result_store()
        _store_result("Airspeed", "H4sIAAAA...", result_type="analytic")
        result = await _get_result_id(result_numbers=[0])
        assert "[0] Airspeed (analytic)" in result

    @pytest.mark.asyncio
    async def test_invalid_ref(self) -> None:
        """Should report not-found refs."""
        _reset_result_store()
        result = await _get_result_id(result_numbers=[999])
        assert "Not found" in result
        assert "999" in result

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid(self) -> None:
        """Should handle mix of valid and invalid refs."""
        _reset_result_store()
        _store_result("Good", "id-good")
        result = await _get_result_id(result_numbers=[0, 42])
        assert "[0] Good" in result
        assert "ID: id-good" in result
        assert "Not found" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        """Should return error for empty list."""
        result = await _get_result_id(result_numbers=[])
        assert "Error" in result


class TestResolveFieldId:
    """Tests for _resolve_field_id helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear caches before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()
        _reset_result_store()

    @pytest.mark.asyncio
    async def test_integer_ref_from_store(self) -> None:
        """Integer reference should look up in result store."""
        _store_result("Flight Date", "[-hub-][field][date]")
        result = await _resolve_field_id(0, ems_system_id=1, database_id="[db]")
        assert result == "[-hub-][field][date]"

    @pytest.mark.asyncio
    async def test_digit_string_ref_from_store(self) -> None:
        """Digit string should look up in result store."""
        _store_result("Flight Date", "[-hub-][field][date]")
        result = await _resolve_field_id("0", ems_system_id=1, database_id="[db]")
        assert result == "[-hub-][field][date]"

    @pytest.mark.asyncio
    async def test_invalid_ref_raises(self) -> None:
        """Invalid reference number should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await _resolve_field_id(999, ems_system_id=1, database_id="[db]")

    @pytest.mark.asyncio
    async def test_bracket_id_passthrough(self) -> None:
        """Bracket-encoded IDs should pass through unchanged."""
        result = await _resolve_field_id(
            "[-hub-][field][test]", ems_system_id=1, database_id="[db]"
        )
        assert result == "[-hub-][field][test]"

    @pytest.mark.asyncio
    async def test_name_exact_match(self) -> None:
        """Exact name match should resolve correctly."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "field-1", "name": "Flight Date"},
            {"id": "field-2", "name": "Flight Date (UTC)"},
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_field_id(
                "Flight Date", ems_system_id=1, database_id="[db]"
            )
        assert result == "field-1"

    @pytest.mark.asyncio
    async def test_name_single_result(self) -> None:
        """Single API result should be used even without exact match."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "field-1", "name": "Takeoff Airport Name"},
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_field_id(
                "Takeoff Airport", ems_system_id=1, database_id="[db]"
            )
        assert result == "field-1"

    @pytest.mark.asyncio
    async def test_name_not_found(self) -> None:
        """Missing field should raise ValueError."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Field not found"):
                await _resolve_field_id(
                    "Nonexistent", ems_system_id=1, database_id="[db]"
                )

    @pytest.mark.asyncio
    async def test_name_ambiguous(self) -> None:
        """Ambiguous name should raise ValueError."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "f1", "name": "Altitude (Baro)"},
            {"id": "f2", "name": "Altitude (GPS)"},
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Ambiguous"):
                await _resolve_field_id(
                    "Altitude", ems_system_id=1, database_id="[db]"
                )

    @pytest.mark.asyncio
    async def test_name_cached(self) -> None:
        """Resolved names should be cached."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "field-1", "name": "Flight Date"},
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _resolve_field_id("Flight Date", ems_system_id=1, database_id="[db]")
            await _resolve_field_id("Flight Date", ems_system_id=1, database_id="[db]")
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_analytic_ref_rejected(self) -> None:
        """Analytic references should be rejected with helpful error."""
        _store_result("Airspeed", "H4sIAAAA...", result_type="analytic")
        with pytest.raises(ValueError, match="analytic parameter"):
            await _resolve_field_id(0, ems_system_id=1, database_id="[db]")

    @pytest.mark.asyncio
    async def test_analytic_ref_error_mentions_query_flight_analytics(self) -> None:
        """Analytic ref rejection should mention query_flight_analytics."""
        _store_result("Altitude (Baro)", "H4sIAAAA...", result_type="analytic")
        with pytest.raises(ValueError, match="query_flight_analytics"):
            await _resolve_field_id(0, ems_system_id=1, database_id="[db]")

    @pytest.mark.asyncio
    async def test_field_ref_still_works(self) -> None:
        """Field references (default type) should still resolve normally."""
        _store_result("Flight Date", "[-hub-][field][date]")
        result = await _resolve_field_id(0, ems_system_id=1, database_id="[db]")
        assert result == "[-hub-][field][date]"

    @pytest.mark.asyncio
    async def test_entity_type_db_uses_bfs(self) -> None:
        """Entity-type databases should use BFS instead of field search API."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [
                {"id": "field-1", "name": "Flight Record", "type": "number"},
            ],
            "groups": [],
        })
        entity_db = "[ems-core][entity-type][foqa-flights]"
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_field_id(
                "Flight Record", ems_system_id=1, database_id=entity_db,
            )
        assert result == "field-1"
        # Should have called the field-groups API (BFS), not the field search API
        call_path = mock_client.get.call_args[0][0]
        assert "field-groups" in call_path
        assert "/fields?" not in call_path

    @pytest.mark.asyncio
    async def test_entity_type_db_not_found(self) -> None:
        """Entity-type BFS fallback should raise if field not found."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "fields": [],
            "groups": [],
        })
        entity_db = "[ems-core][entity-type][foqa-flights]"
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Field not found"):
                await _resolve_field_id(
                    "Nonexistent", ems_system_id=1, database_id=entity_db,
                )

    @pytest.mark.asyncio
    async def test_non_entity_type_db_uses_search_api(self) -> None:
        """Non-entity-type databases should use the field search API."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "field-1", "name": "Flight Date"},
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_field_id(
                "Flight Date", ems_system_id=1, database_id="[regular-db]",
            )
        assert result == "field-1"
        # Should have called the field search API, not field-groups
        call_path = mock_client.get.call_args[0][0]
        assert "/fields" in call_path
        assert "field-groups" not in call_path


class TestResolveDatabaseId:
    """Tests for _resolve_database_id helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear caches before each test."""
        from ems_mcp.cache import database_cache
        await database_cache.clear()

    @pytest.mark.asyncio
    async def test_bracket_id_passthrough(self) -> None:
        """Bracket-encoded IDs should pass through unchanged."""
        result = await _resolve_database_id(
            "[ems-core][entity-type][foqa-flights]", ems_system_id=1
        )
        assert result == "[ems-core][entity-type][foqa-flights]"

    @pytest.mark.asyncio
    async def test_name_resolved(self) -> None:
        """Database name should resolve to its ID."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=[
            # Root database groups
            {
                "id": "[none]", "name": "Root",
                "databases": [
                    {"id": "[ems-core][entity-type][foqa-flights]", "name": "FDW Flights"},
                ],
                "groups": [],
            },
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_database_id("FDW Flights", ems_system_id=1)
        assert result == "[ems-core][entity-type][foqa-flights]"

    @pytest.mark.asyncio
    async def test_name_case_insensitive(self) -> None:
        """Name lookup should be case-insensitive."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "databases": [
                {"id": "[db-id]", "name": "FDW Flights"},
            ],
            "groups": [],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_database_id("fdw flights", ems_system_id=1)
        assert result == "[db-id]"

    @pytest.mark.asyncio
    async def test_name_not_found(self) -> None:
        """Unknown name should raise ValueError."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "databases": [
                {"id": "[db-id]", "name": "FDW Flights"},
            ],
            "groups": [],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Database not found"):
                await _resolve_database_id("Nonexistent DB", ems_system_id=1)

    @pytest.mark.asyncio
    async def test_name_cached(self) -> None:
        """Database name map should be cached."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[none]", "name": "Root",
            "databases": [
                {"id": "[db-id]", "name": "FDW Flights"},
            ],
            "groups": [],
        })
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            await _resolve_database_id("FDW Flights", ems_system_id=1)
            await _resolve_database_id("FDW Flights", ems_system_id=1)
        # API should only be called once (result is cached)
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_searches_subgroups(self) -> None:
        """Should find databases in subgroups one level deep."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=[
            # Root: no databases, one group
            {
                "id": "[none]", "name": "Root",
                "databases": [],
                "groups": [{"id": "g1", "name": "Profiles"}],
            },
            # Subgroup: one database
            {
                "id": "g1", "name": "Profiles",
                "databases": [
                    {"id": "[profile-db]", "pluralName": "APM Events"},
                ],
                "groups": [],
            },
        ])
        with patch("ems_mcp.tools.discovery.get_client", return_value=mock_client):
            result = await _resolve_database_id("APM Events", ems_system_id=1)
        assert result == "[profile-db]"

    @pytest.mark.asyncio
    async def test_empty_ref_raises(self) -> None:
        """Empty database reference should raise ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await _resolve_database_id("", ems_system_id=1)
