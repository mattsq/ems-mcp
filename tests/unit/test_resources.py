"""Unit tests for MCP resources."""

import json

from ems_mcp.resources import (
    common_fields_index_resource,
    common_fields_resource,
)

# FastMCP wraps decorated functions; access the originals via .fn
_common_fields = common_fields_resource.fn
_common_fields_index = common_fields_index_resource.fn


class TestCommonFieldsResource:
    """Tests for the curated common-fields MCP resource."""

    def test_known_database_returns_fields(self) -> None:
        result = json.loads(_common_fields("FDW Flights"))
        assert result["database"] == "FDW Flights"
        assert isinstance(result["fields"], list)
        names = [f["name"] for f in result["fields"]]
        assert "Flight Date" in names
        assert "Tail Number" in names

    def test_lookup_is_case_insensitive(self) -> None:
        result = json.loads(_common_fields("fdw flights"))
        assert result["database"] == "FDW Flights"

    def test_url_encoded_name(self) -> None:
        result = json.loads(_common_fields("FDW%20Flights"))
        assert result["database"] == "FDW Flights"

    def test_unknown_database_returns_helpful_error(self) -> None:
        result = json.loads(_common_fields("Made Up DB"))
        assert "error" in result
        assert "FDW Flights" in result["databases_with_curated_lists"]
        assert "find_fields" in result["hint"]

    def test_index_lists_available_databases(self) -> None:
        result = json.loads(_common_fields_index())
        assert "FDW Flights" in result["databases"]
        assert "APM Events" in result["databases"]
        assert "_description" not in result["databases"]

    def test_discrete_fields_flagged(self) -> None:
        """Discrete fields must be flagged so the agent knows to fetch codes."""
        result = json.loads(_common_fields("FDW Flights"))
        tail = next(f for f in result["fields"] if f["name"] == "Tail Number")
        assert tail["discrete"] is True
        flight_date = next(f for f in result["fields"] if f["name"] == "Flight Date")
        assert flight_date["discrete"] is False
