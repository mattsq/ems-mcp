"""Unit tests for EMS MCP query tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_mcp.tools.query import (
    _build_analytics_body,
    _build_query_body,
    _build_single_filter,
    _format_analytic_header,
    _format_analytics_results,
    _format_query_results,
    _get_field_metadata,
    _is_analytic_id,
    _resolve_analytics,
    _resolve_discrete_filter_value,
    _resolve_filters,
    query_database,
    query_flight_analytics,
)

# Access the underlying functions from the FastMCP FunctionTool wrappers
_query_database = query_database.fn
_query_flight_analytics = query_flight_analytics.fn


class TestBuildSingleFilter:
    """Tests for _build_single_filter helper."""

    def test_equal_operator(self) -> None:
        """Equal filter should produce field + constant args."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "equal", "value": 42,
        })
        assert result["operator"] == "equal"
        assert result["args"][0] == {"type": "field", "value": "f1"}
        assert result["args"][1] == {"type": "constant", "value": 42}

    def test_not_equal_operator(self) -> None:
        """notEqual filter."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "notEqual", "value": "abc",
        })
        assert result["operator"] == "notEqual"
        assert result["args"][1] == {"type": "constant", "value": "abc"}

    def test_greater_than_operator(self) -> None:
        """greaterThan filter."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "greaterThan", "value": 100,
        })
        assert result["operator"] == "greaterThan"
        assert len(result["args"]) == 2

    def test_less_than_or_equal_operator(self) -> None:
        """lessThanOrEqual filter."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "lessThanOrEqual", "value": 50.5,
        })
        assert result["operator"] == "lessThanOrEqual"
        assert result["args"][1]["value"] == 50.5

    def test_like_operator(self) -> None:
        """like filter."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "like", "value": "%test%",
        })
        assert result["operator"] == "like"
        assert result["args"][1]["value"] == "%test%"

    def test_is_null_operator(self) -> None:
        """isNull is unary - no value arg."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "isNull",
        })
        assert result["operator"] == "isNull"
        assert len(result["args"]) == 1
        assert result["args"][0] == {"type": "field", "value": "f1"}

    def test_is_not_null_operator(self) -> None:
        """isNotNull is unary - no value arg."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "isNotNull",
        })
        assert result["operator"] == "isNotNull"
        assert len(result["args"]) == 1

    def test_between_operator(self) -> None:
        """between maps to betweenInclusive with 3 args."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "between", "value": [10, 20],
        })
        assert result["operator"] == "betweenInclusive"
        assert len(result["args"]) == 3
        assert result["args"][0] == {"type": "field", "value": "f1"}
        assert result["args"][1] == {"type": "constant", "value": 10}
        assert result["args"][2] == {"type": "constant", "value": 20}

    def test_between_invalid_value_not_list(self) -> None:
        """between with non-list value should raise."""
        with pytest.raises(ValueError, match="between"):
            _build_single_filter({
                "field_id": "f1", "operator": "between", "value": 42,
            })

    def test_between_invalid_value_wrong_length(self) -> None:
        """between with wrong-length list should raise."""
        with pytest.raises(ValueError, match="between"):
            _build_single_filter({
                "field_id": "f1", "operator": "between", "value": [1, 2, 3],
            })

    def test_in_operator(self) -> None:
        """in spreads values as individual constant args."""
        result = _build_single_filter({
            "field_id": "f1", "operator": "in", "value": [1, 2, 3],
        })
        assert result["operator"] == "in"
        assert len(result["args"]) == 4  # 1 field + 3 constants
        assert result["args"][0] == {"type": "field", "value": "f1"}
        assert result["args"][1] == {"type": "constant", "value": 1}
        assert result["args"][2] == {"type": "constant", "value": 2}
        assert result["args"][3] == {"type": "constant", "value": 3}

    def test_in_invalid_value_empty_list(self) -> None:
        """in with empty list should raise."""
        with pytest.raises(ValueError, match="in"):
            _build_single_filter({
                "field_id": "f1", "operator": "in", "value": [],
            })


class TestBuildQueryBody:
    """Tests for _build_query_body helper."""

    def test_minimal_query(self) -> None:
        """Minimal query with just fields."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=None,
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["select"] == [{"fieldId": "f1"}]
        assert body["format"] == "display"
        assert body["top"] == 100
        assert "filter" not in body
        assert "orderBy" not in body

    def test_query_with_alias(self) -> None:
        """Fields with aliases should include them."""
        body = _build_query_body(
            fields=[{"field_id": "f1", "alias": "Flight Date"}],
            filters=None,
            order_by=None,
            limit=50,
            fmt="display",
        )
        assert body["select"] == [{"fieldId": "f1", "alias": "Flight Date"}]

    def test_raw_format_maps_to_none(self) -> None:
        """Raw format should map to 'none' in API."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=None,
            order_by=None,
            limit=100,
            fmt="raw",
        )
        assert body["format"] == "none"

    def test_single_filter(self) -> None:
        """Single filter should not be wrapped in AND."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=[{"field_id": "f2", "operator": "equal", "value": 1}],
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["filter"]["operator"] == "equal"

    def test_multiple_filters_wrapped_in_and(self) -> None:
        """Multiple filters should be wrapped in AND."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=[
                {"field_id": "f2", "operator": "equal", "value": 1},
                {"field_id": "f3", "operator": "greaterThan", "value": 100},
            ],
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["filter"]["operator"] == "and"
        assert len(body["filter"]["args"]) == 2
        assert body["filter"]["args"][0]["type"] == "filter"
        assert body["filter"]["args"][1]["type"] == "filter"

    def test_order_by(self) -> None:
        """Order by should map direction."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=None,
            order_by=[
                {"field_id": "f1", "direction": "desc"},
                {"field_id": "f2"},
            ],
            limit=100,
            fmt="display",
        )
        assert len(body["orderBy"]) == 2
        assert body["orderBy"][0] == {"fieldId": "f1", "order": "desc"}
        assert body["orderBy"][1] == {"fieldId": "f2", "order": "asc"}

    def test_limit_maps_to_top(self) -> None:
        """Limit should map to top in body."""
        body = _build_query_body(
            fields=[{"field_id": "f1"}],
            filters=None,
            order_by=None,
            limit=500,
            fmt="display",
        )
        assert body["top"] == 500


class TestFormatQueryResults:
    """Tests for _format_query_results formatter."""

    def test_empty_results(self) -> None:
        """Empty results should show 0 rows message."""
        result = _format_query_results({"rows": [], "header": []}, [])
        assert "0 rows" in result

    def test_single_row(self) -> None:
        """Single row formatting."""
        response = {
            "header": [{"name": "Name"}, {"name": "Value"}],
            "rows": [["Alice", 42]],
        }
        fields: list[QueryField] = [{"field_id": "f1"}, {"field_id": "f2"}]
        result = _format_query_results(response, fields)
        assert "Name" in result
        assert "Value" in result
        assert "Alice" in result
        assert "42" in result
        assert "1 row(s)" in result

    def test_multiple_rows(self) -> None:
        """Multiple rows with proper table format."""
        response = {
            "header": [{"name": "A"}, {"name": "B"}],
            "rows": [["x", 1], ["y", 2], ["z", 3]],
        }
        fields: list[QueryField] = [{"field_id": "f1"}, {"field_id": "f2"}]
        result = _format_query_results(response, fields)
        assert "3 row(s)" in result
        assert " | " in result
        assert "-+-" in result

    def test_null_handling(self) -> None:
        """None values should display as NULL."""
        response = {
            "header": [{"name": "Col"}],
            "rows": [[None], ["value"]],
        }
        fields: list[QueryField] = [{"field_id": "f1"}]
        result = _format_query_results(response, fields)
        assert "NULL" in result

    def test_long_value_truncation(self) -> None:
        """Values longer than 40 chars should be truncated."""
        long_val = "x" * 50
        response = {
            "header": [{"name": "Col"}],
            "rows": [[long_val]],
        }
        fields: list[QueryField] = [{"field_id": "f1"}]
        result = _format_query_results(response, fields)
        assert "..." in result
        assert long_val not in result

    def test_alias_as_column_name(self) -> None:
        """Alias should be used as column name instead of header."""
        response = {
            "header": [{"name": "[-hub-][field][long-id]"}],
            "rows": [["test"]],
        }
        fields: list[QueryField] = [{"field_id": "f1", "alias": "My Column"}]
        result = _format_query_results(response, fields)
        assert "My Column" in result
        # Original header should not appear
        assert "[-hub-]" not in result

    def test_string_headers(self) -> None:
        """Handle headers that are plain strings instead of dicts."""
        response = {
            "header": ["Name", "Value"],
            "rows": [["test", 123]],
        }
        fields: list[QueryField] = [{"field_id": "f1"}, {"field_id": "f2"}]
        result = _format_query_results(response, fields)
        assert "Name" in result
        assert "Value" in result


class TestFormatAnalyticsResults:
    """Tests for _format_analytics_results formatter."""

    def test_single_flight(self) -> None:
        """Single flight with data (real API format: offsets + results)."""
        results = [{
            "flight_id": 12345,
            "data": {
                "offsets": [0.0, 1.0],
                "results": [
                    {"analyticId": "Altitude", "values": [1000.0, 1100.0]},
                    {"analyticId": "Airspeed", "values": [250.0, 255.0]},
                ],
            },
        }]
        result = _format_analytics_results(results)
        assert "=== Flight 12345 ===" in result
        assert "Altitude" in result
        assert "Airspeed" in result
        assert "2 row(s)" in result

    def test_multi_flight(self) -> None:
        """Multiple flights."""
        results = [
            {
                "flight_id": 100,
                "data": {
                    "offsets": [0.0],
                    "results": [{"analyticId": "Alt", "values": [500.0]}],
                },
            },
            {
                "flight_id": 200,
                "data": {
                    "offsets": [0.0],
                    "results": [{"analyticId": "Alt", "values": [600.0]}],
                },
            },
        ]
        result = _format_analytics_results(results)
        assert "=== Flight 100 ===" in result
        assert "=== Flight 200 ===" in result

    def test_errors_only(self) -> None:
        """All flights with errors."""
        results = [
            {"flight_id": 100, "error": "Not found"},
            {"flight_id": 200, "error": "Timeout"},
        ]
        result = _format_analytics_results(results)
        assert "=== Flight 100 ===" in result
        assert "Error: Not found" in result
        assert "=== Flight 200 ===" in result
        assert "Error: Timeout" in result
        assert "2 flight(s) had errors" in result

    def test_large_result_truncation(self) -> None:
        """Large results should be truncated at max_rows_per_flight."""
        results = [{
            "flight_id": 100,
            "data": {
                "offsets": [float(i) for i in range(500)],
                "results": [{"analyticId": "Alt", "values": [float(i * 100) for i in range(500)]}],
            },
        }]
        result = _format_analytics_results(results, max_rows_per_flight=200)
        assert "300 more rows" in result
        assert "500 total" in result

    def test_empty_data(self) -> None:
        """Flight with no data (empty offsets)."""
        results = [{
            "flight_id": 100,
            "data": {"offsets": [], "results": []},
        }]
        result = _format_analytics_results(results)
        assert "No data returned" in result

    def test_empty_results_list(self) -> None:
        """Empty results list."""
        result = _format_analytics_results([])
        assert "No analytics results" in result


class TestQueryDatabase:
    """Tests for query_database tool."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Tool should execute query and return formatted table."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "header": [{"name": "Flight Date"}, {"name": "Duration"}],
            "rows": [["2024-01-15", "3.5h"], ["2024-01-16", "2.1h"]],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="ems-core",
                fields=[{"field_id": "f1"}, {"field_id": "f2"}],
            )

        assert "Flight Date" in result
        assert "2024-01-15" in result
        assert "2 row(s)" in result
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/v2/ems-systems/1/databases/ems-core/query"

    @pytest.mark.asyncio
    async def test_with_filters(self) -> None:
        """Tool should include filters in query body."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "header": [{"name": "Col"}],
            "rows": [["test"]],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "f1"}],
                filters=[{"field_id": "f2", "operator": "equal", "value": 42}],
            )

        call_body = mock_client.post.call_args[1]["json"]
        assert "filter" in call_body
        assert call_body["filter"]["operator"] == "equal"

    @pytest.mark.asyncio
    async def test_empty_fields_validation(self) -> None:
        """Tool should reject empty fields list."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[],
        )
        assert "Error" in result
        assert "field" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_limit_too_high(self) -> None:
        """Tool should reject limit > 10000."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[{"field_id": "f1"}],
            limit=20000,
        )
        assert "Error" in result
        assert "limit" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_limit_zero(self) -> None:
        """Tool should reject limit < 1."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[{"field_id": "f1"}],
            limit=0,
        )
        assert "Error" in result
        assert "limit" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_format(self) -> None:
        """Tool should reject invalid format."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[{"field_id": "f1"}],
            format="json",
        )
        assert "Error" in result
        assert "format" in result.lower()

    @pytest.mark.asyncio
    async def test_not_found_error(self) -> None:
        """Tool should handle 404 errors."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=999,
                database_id="invalid",
                fields=[{"field_id": "f1"}],
            )

        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_bad_request_error(self) -> None:
        """Tool should handle 400 errors with helpful message."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=EMSAPIError("Invalid field ID", status_code=400)
        )

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "bad-id"}],
            )

        assert "Error" in result
        assert "search_fields" in result
        assert "get_field_info" in result

    @pytest.mark.asyncio
    async def test_invalid_filter_operator(self) -> None:
        """Tool should reject unknown filter operators."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[{"field_id": "f1"}],
            filters=[{"field_id": "f2", "operator": "contains", "value": "x"}],
        )
        assert "Error" in result
        assert "Invalid filter operator" in result

    @pytest.mark.asyncio
    async def test_bad_between_filter(self) -> None:
        """Tool should handle ValueError from bad between filter."""
        mock_client = MagicMock()

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "f1"}],
                filters=[{"field_id": "f2", "operator": "between", "value": 42}],
            )

        assert "Error building query" in result


class TestQueryFlightAnalytics:
    """Tests for query_flight_analytics tool."""

    @pytest.mark.asyncio
    async def test_single_flight_success(self) -> None:
        """Tool should return analytics for a single flight."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "offsets": [0.0, 1.0],
            "results": [
                {"analyticId": "[-hub-][a1]", "values": [1000.0, 1100.0]},
                {"analyticId": "[-hub-][a2]", "values": [250.0, 255.0]},
            ],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[12345],
                analytics=["[-hub-][a1]", "[-hub-][a2]"],
            )

        assert "=== Flight 12345 ===" in result
        assert "a1" in result
        assert "a2" in result
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/flights/12345/analytics/query" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_multi_flight_success(self) -> None:
        """Tool should query multiple flights sequentially."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "offsets": [0.0],
            "results": [{"analyticId": "[-hub-][alt]", "values": [500.0]}],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100, 200],
                analytics=["[-hub-][alt]"],
            )

        assert "=== Flight 100 ===" in result
        assert "=== Flight 200 ===" in result
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_too_many_flights(self) -> None:
        """Tool should reject more than 10 flights."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=list(range(15)),
            analytics=["a1"],
        )
        assert "Error" in result
        assert "10" in result

    @pytest.mark.asyncio
    async def test_too_many_analytics(self) -> None:
        """Tool should reject more than 20 analytics."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=[1],
            analytics=[f"a{i}" for i in range(25)],
        )
        assert "Error" in result
        assert "20" in result

    @pytest.mark.asyncio
    async def test_empty_flight_ids(self) -> None:
        """Tool should reject empty flight_ids."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=[],
            analytics=["a1"],
        )
        assert "Error" in result
        assert "flight_id" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_analytics(self) -> None:
        """Tool should reject empty analytics."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=[1],
            analytics=[],
        )
        assert "Error" in result
        assert "analytic" in result.lower()

    @pytest.mark.asyncio
    async def test_partial_failure(self) -> None:
        """Tool should handle partial failures (some flights succeed, some fail)."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        # First call succeeds, second fails
        mock_client.post = AsyncMock(side_effect=[
            {
                "offsets": [0.0],
                "results": [{"analyticId": "[-hub-][alt]", "values": [500.0]}],
            },
            EMSNotFoundError("Not found"),
        ])

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100, 200],
                analytics=["[-hub-][alt]"],
            )

        assert "=== Flight 100 ===" in result
        assert "=== Flight 200 ===" in result
        assert "Error" in result  # Flight 200 error
        # Should not have the "all failed" prefix
        assert "All" not in result.split("\n")[0]

    @pytest.mark.asyncio
    async def test_all_flights_fail(self) -> None:
        """Tool should indicate when all flights fail."""
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100, 200],
                analytics=["[-hub-][alt]"],
            )

        assert "All 2 flight(s) failed" in result

    @pytest.mark.asyncio
    async def test_time_range_validation(self) -> None:
        """Tool should reject start >= end."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=[1],
            analytics=["a1"],
            start_offset=100.0,
            end_offset=50.0,
        )
        assert "Error" in result
        assert "start_offset" in result

    @pytest.mark.asyncio
    async def test_invalid_sample_rate(self) -> None:
        """Tool should reject non-positive sample_rate."""
        result = await _query_flight_analytics(
            ems_system_id=1,
            flight_ids=[1],
            analytics=["a1"],
            sample_rate=0,
        )
        assert "Error" in result
        assert "sample_rate" in result

    @pytest.mark.asyncio
    async def test_time_range_in_body(self) -> None:
        """Tool should include time range and size in body."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "offsets": [0.0],
            "results": [{"analyticId": "[-hub-][alt]", "values": [500.0]}],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100],
                analytics=["[-hub-][alt]"],
                start_offset=10.0,
                end_offset=60.0,
                sample_rate=2.0,
            )

        call_body = mock_client.post.call_args[1]["json"]
        assert call_body["start"] == 10.0
        assert call_body["end"] == 60.0
        assert call_body["size"] == 100  # (60-10) * 2.0


class TestBuildAnalyticsBody:
    """Tests for _build_analytics_body helper."""

    def test_minimal_body(self) -> None:
        """Minimal body with just analytics should include default size."""
        body = _build_analytics_body(["a1", "a2"], None, None, 1.0)
        assert body["select"] == [{"analyticId": "a1"}, {"analyticId": "a2"}]
        assert "start" not in body
        assert "end" not in body
        assert body["size"] == 5000

    def test_with_time_range(self) -> None:
        """Body with start and end offsets."""
        body = _build_analytics_body(["a1"], 10.0, 60.0, 1.0)
        assert body["start"] == 10.0
        assert body["end"] == 60.0
        assert body["size"] == 50  # (60-10) * 1.0

    def test_with_sample_rate(self) -> None:
        """Body with custom sample rate."""
        body = _build_analytics_body(["a1"], 0.0, 100.0, 0.5)
        assert body["size"] == 50  # 100 * 0.5

    def test_start_only(self) -> None:
        """Body with start but no end should include default size."""
        body = _build_analytics_body(["a1"], 10.0, None, 1.0)
        assert body["start"] == 10.0
        assert "end" not in body
        assert body["size"] == 5000


class TestIsAnalyticId:
    """Tests for _is_analytic_id helper."""

    def test_bracket_id_hub_prefix(self) -> None:
        """Bracket IDs starting with [-hub-] should be recognized."""
        assert _is_analytic_id("[-hub-][field][some-long-path]") is True

    def test_bracket_id_double_bracket(self) -> None:
        """Bracket IDs with [...][...] pattern should be recognized."""
        assert _is_analytic_id("[ems-core][entity-type][foqa-flights]") is True

    def test_compressed_id(self) -> None:
        """Compressed IDs starting with H4sIA should be recognized."""
        assert _is_analytic_id("H4sIAAAAAAAAA6tWKkktLlGyUNJRSizIL0pVsgIAJmUYWxUAAAA=") is True

    def test_human_readable_name(self) -> None:
        """Human-readable names should not be recognized as IDs."""
        assert _is_analytic_id("Airspeed") is False
        assert _is_analytic_id("Altitude (Baro-Corrected)") is False
        assert _is_analytic_id("Heading") is False

    def test_empty_string(self) -> None:
        """Empty strings should not be recognized as IDs."""
        assert _is_analytic_id("") is False
        assert _is_analytic_id("   ") is False

    def test_partial_bracket(self) -> None:
        """Partial bracket patterns should not match."""
        assert _is_analytic_id("[single-bracket]") is False

    def test_h4s_prefix_but_not_h4sia(self) -> None:
        """H4s prefix without IA should not match."""
        assert _is_analytic_id("H4sFoo") is False


class TestFormatAnalyticHeader:
    """Tests for _format_analytic_header helper."""

    def test_compressed_id_truncated(self) -> None:
        """Compressed IDs should be truncated."""
        result = _format_analytic_header("H4sIAAAAAAAAA6tWKkktLlGyUNJRSizIL0pVsgIAJmUYWxUAAAA=")
        assert result == "H4sIAAAAAAAA..."
        assert len(result) == 15

    def test_bracket_id_last_segment(self) -> None:
        """Bracket IDs should show the last segment."""
        result = _format_analytic_header("[-hub-][field][airspeed-computed]")
        assert result == "airspeed-computed"

    def test_plain_id_passthrough(self) -> None:
        """Non-bracket, non-compressed IDs should pass through."""
        result = _format_analytic_header("Altitude")
        assert result == "Altitude"


class TestResolveAnalytics:
    """Tests for _resolve_analytics helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_raw_id_passthrough(self) -> None:
        """Raw analytic IDs should pass through without API calls."""
        mock_client = MagicMock()
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_analytics(
                ["[-hub-][field][altitude]", "H4sIAAAA..."],
                ems_system_id=1,
            )
        assert result == [
            ("[-hub-][field][altitude]", "[-hub-][field][altitude]"),
            ("H4sIAAAA...", "H4sIAAAA..."),
        ]
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_name_exact_match(self) -> None:
        """Exact name match should resolve to the matching analytic."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "id-1", "name": "Airspeed"},
            {"id": "id-2", "name": "Airspeed (Computed)"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_analytics(["Airspeed"], ems_system_id=1)
        assert result == [("Airspeed", "id-1")]

    @pytest.mark.asyncio
    async def test_name_single_result(self) -> None:
        """Single search result should be used even without exact name match."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "id-1", "name": "Baro-Corrected Altitude"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_analytics(["Altitude"], ems_system_id=1)
        assert result == [("Baro-Corrected Altitude", "id-1")]

    @pytest.mark.asyncio
    async def test_name_not_found(self) -> None:
        """Missing analytic should raise ValueError."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Analytic not found"):
                await _resolve_analytics(["Nonexistent"], ems_system_id=1)

    @pytest.mark.asyncio
    async def test_name_ambiguous(self) -> None:
        """Ambiguous name (multiple matches, no exact) should raise ValueError."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "id-1", "name": "Altitude (Baro)"},
            {"id": "id-2", "name": "Altitude (GPS)"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Ambiguous"):
                await _resolve_analytics(["Altitude"], ems_system_id=1)

    @pytest.mark.asyncio
    async def test_name_cached(self) -> None:
        """Resolved names should be cached."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "id-1", "name": "Airspeed"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            await _resolve_analytics(["Airspeed"], ems_system_id=1)
            await _resolve_analytics(["Airspeed"], ems_system_id=1)
        # API should only be called once
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_names_and_ids(self) -> None:
        """Mix of names and raw IDs should resolve correctly."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "resolved-id", "name": "Airspeed"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_analytics(
                ["[-hub-][field][alt]", "Airspeed"],
                ems_system_id=1,
            )
        assert result == [
            ("[-hub-][field][alt]", "[-hub-][field][alt]"),
            ("Airspeed", "resolved-id"),
        ]

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self) -> None:
        """Exact match should be case-insensitive."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[
            {"id": "id-1", "name": "Airspeed"},
            {"id": "id-2", "name": "Airspeed (Computed)"},
        ])
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_analytics(["airspeed"], ems_system_id=1)
        assert result == [("Airspeed", "id-1")]


class TestFormatAnalyticsResultsWithNames:
    """Tests for _format_analytics_results with analytic_names parameter."""

    def test_named_columns(self) -> None:
        """Analytic names should be used as column headers."""
        results = [{
            "flight_id": 100,
            "data": {
                "offsets": [0.0, 1.0],
                "results": [
                    {"analyticId": "[-hub-][field][long-id-1]", "values": [1000.0, 1100.0]},
                    {"analyticId": "[-hub-][field][long-id-2]", "values": [250.0, 255.0]},
                ],
            },
        }]
        result = _format_analytics_results(
            results, analytic_names=["Altitude", "Airspeed"]
        )
        assert "Altitude" in result
        assert "Airspeed" in result
        # Raw IDs should not appear as headers
        assert "long-id-1" not in result
        assert "long-id-2" not in result

    def test_fallback_without_names(self) -> None:
        """Without names, formatted IDs should be used as headers."""
        results = [{
            "flight_id": 100,
            "data": {
                "offsets": [0.0],
                "results": [
                    {"analyticId": "[-hub-][field][airspeed]", "values": [250.0]},
                ],
            },
        }]
        result = _format_analytics_results(results, analytic_names=None)
        # Should use the last bracket segment as header
        assert "airspeed" in result

    def test_all_zero_warning(self) -> None:
        """All-zero data with 100+ rows should trigger a warning."""
        results = [{
            "flight_id": 999999,
            "data": {
                "offsets": [float(i) for i in range(150)],
                "results": [
                    {"analyticId": "Alt", "values": [0.0] * 150},
                    {"analyticId": "Spd", "values": [0.0] * 150},
                ],
            },
        }]
        result = _format_analytics_results(results)
        assert "WARNING" in result
        assert "invalid flight ID" in result

    def test_no_warning_for_real_data(self) -> None:
        """Non-zero data should not trigger a warning."""
        results = [{
            "flight_id": 100,
            "data": {
                "offsets": [float(i) for i in range(150)],
                "results": [
                    {"analyticId": "Alt", "values": [float(i * 100) for i in range(150)]},
                ],
            },
        }]
        result = _format_analytics_results(results)
        assert "WARNING" not in result

    def test_no_warning_for_small_zero_data(self) -> None:
        """All-zero data with fewer than 100 rows should not trigger a warning."""
        results = [{
            "flight_id": 100,
            "data": {
                "offsets": [float(i) for i in range(50)],
                "results": [
                    {"analyticId": "Alt", "values": [0.0] * 50},
                ],
            },
        }]
        result = _format_analytics_results(results)
        assert "WARNING" not in result


class TestQueryFlightAnalyticsNameResolution:
    """Tests for query_flight_analytics with name resolution."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_name_resolution_in_tool(self) -> None:
        """Tool should resolve names and use them as column headers."""
        mock_client = MagicMock()
        # First call: analytics search for "Airspeed"
        # Second call: analytics query for flight
        mock_client.get = AsyncMock(return_value=[
            {"id": "resolved-airspeed-id", "name": "Airspeed"},
        ])
        mock_client.post = AsyncMock(return_value={
            "offsets": [0.0, 1.0],
            "results": [
                {"analyticId": "resolved-airspeed-id", "values": [250.0, 255.0]},
            ],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100],
                analytics=["Airspeed"],
            )

        assert "Airspeed" in result
        assert "250.0" in result

    @pytest.mark.asyncio
    async def test_resolution_error_in_tool(self) -> None:
        """Tool should return helpful error when name resolution fails."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[])

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100],
                analytics=["NonexistentAnalytic"],
            )

        assert "Error resolving analytics" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_raw_id_still_works(self) -> None:
        """Raw analytic IDs should still work without API search calls."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "offsets": [0.0],
            "results": [
                {"analyticId": "[-hub-][field][alt]", "values": [1000.0]},
            ],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_flight_analytics(
                ems_system_id=1,
                flight_ids=[100],
                analytics=["[-hub-][field][alt]"],
            )

        assert "=== Flight 100 ===" in result
        # No search calls should have been made
        mock_client.get.assert_not_called()


class TestBuildQueryBodyAggregation:
    """Tests for aggregation support in _build_query_body."""

    def test_field_with_aggregate(self) -> None:
        """Field with aggregate should include it in select."""
        body = _build_query_body(
            fields=[{"field_id": "f1", "aggregate": "avg"}],
            filters=None,
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["select"] == [{"fieldId": "f1", "aggregate": "avg"}]

    def test_mixed_aggregate_and_plain(self) -> None:
        """Mix of aggregated and non-aggregated fields (implicit GROUP BY)."""
        body = _build_query_body(
            fields=[
                {"field_id": "group_field"},
                {"field_id": "value_field", "aggregate": "sum"},
                {"field_id": "count_field", "aggregate": "count"},
            ],
            filters=None,
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["select"][0] == {"fieldId": "group_field"}
        assert body["select"][1] == {"fieldId": "value_field", "aggregate": "sum"}
        assert body["select"][2] == {"fieldId": "count_field", "aggregate": "count"}

    def test_aggregate_with_alias(self) -> None:
        """Field with both aggregate and alias."""
        body = _build_query_body(
            fields=[{"field_id": "f1", "aggregate": "avg", "alias": "Avg Duration"}],
            filters=None,
            order_by=None,
            limit=100,
            fmt="display",
        )
        assert body["select"] == [
            {"fieldId": "f1", "aggregate": "avg", "alias": "Avg Duration"}
        ]

    def test_all_aggregate_types(self) -> None:
        """All supported aggregate types should pass through."""
        for agg in ("avg", "count", "max", "min", "stdev", "sum", "var"):
            body = _build_query_body(
                fields=[{"field_id": "f1", "aggregate": agg}],
                filters=None,
                order_by=None,
                limit=100,
                fmt="display",
            )
            assert body["select"][0]["aggregate"] == agg


class TestQueryDatabaseAggregation:
    """Tests for aggregation in the query_database tool."""

    @pytest.mark.asyncio
    async def test_invalid_aggregate_rejected(self) -> None:
        """Tool should reject invalid aggregate values."""
        result = await _query_database(
            ems_system_id=1,
            database_id="db",
            fields=[{"field_id": "f1", "aggregate": "median"}],
        )
        assert "Error" in result
        assert "Invalid aggregate" in result

    @pytest.mark.asyncio
    async def test_aggregate_in_api_call(self) -> None:
        """Tool should pass aggregate through to the API."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value={
            "header": [{"name": "Avg Duration"}],
            "rows": [[3.5]],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "f1", "aggregate": "avg", "alias": "Avg Duration"}],
            )

        assert "3.5" in result
        call_body = mock_client.post.call_args[1]["json"]
        assert call_body["select"][0]["aggregate"] == "avg"


class TestResolveDiscreteFilterValue:
    """Tests for _resolve_discrete_filter_value helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_non_string_passthrough(self) -> None:
        """Non-string values should pass through unchanged."""
        result = await _resolve_discrete_filter_value(
            42, "field1", ems_system_id=1, database_id="db",
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_non_discrete_field_passthrough(self) -> None:
        """String values for non-discrete fields should pass through."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Notes", "type": "string",
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_discrete_filter_value(
                "some text", "f1", ems_system_id=1, database_id="db",
            )
        assert result == "some text"

    @pytest.mark.asyncio
    async def test_discrete_list_format_resolved(self) -> None:
        """String label should resolve to numeric code (list format)."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Fleet", "type": "discrete",
            "discreteValues": [
                {"value": 10, "label": "Boeing 737"},
                {"value": 31, "label": "DHC-8-400"},
                {"value": 42, "label": "Airbus A320"},
            ],
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_discrete_filter_value(
                "DHC-8-400", "f1", ems_system_id=1, database_id="db",
            )
        assert result == 31

    @pytest.mark.asyncio
    async def test_discrete_dict_format_resolved(self) -> None:
        """String label should resolve to numeric code (dict format)."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Airport", "type": "discrete",
            "discreteValues": {
                "676": "YPKA",
                "411": "YPKG",
                "123": "YSSY",
            },
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_discrete_filter_value(
                "YSSY", "f1", ems_system_id=1, database_id="db",
            )
        assert result == 123

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self) -> None:
        """Label matching should be case-insensitive."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Status", "type": "discrete",
            "discreteValues": [
                {"value": 1, "label": "Active"},
                {"value": 2, "label": "Inactive"},
            ],
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_discrete_filter_value(
                "active", "f1", ems_system_id=1, database_id="db",
            )
        assert result == 1

    @pytest.mark.asyncio
    async def test_label_not_found_raises(self) -> None:
        """Missing label should raise ValueError with available values."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Status", "type": "discrete",
            "discreteValues": [
                {"value": 1, "label": "Active"},
                {"value": 2, "label": "Inactive"},
            ],
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="not found"):
                await _resolve_discrete_filter_value(
                    "Unknown", "f1", ems_system_id=1, database_id="db",
                )

    @pytest.mark.asyncio
    async def test_api_error_passthrough(self) -> None:
        """If field metadata fetch fails, value should pass through."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSAPIError("Server error"))
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_discrete_filter_value(
                "SomeLabel", "f1", ems_system_id=1, database_id="db",
            )
        assert result == "SomeLabel"


class TestResolveFilters:
    """Tests for _resolve_filters helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_equal_filter_resolved(self) -> None:
        """Equal filter with string value should be resolved."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Fleet", "type": "discrete",
            "discreteValues": [{"value": 31, "label": "DHC-8-400"}],
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_filters(
                [{"field_id": "f1", "operator": "equal", "value": "DHC-8-400"}],
                ems_system_id=1, database_id="db",
            )
        assert result[0]["value"] == 31

    @pytest.mark.asyncio
    async def test_in_filter_values_resolved(self) -> None:
        """In filter with string values should resolve each one."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Status", "type": "discrete",
            "discreteValues": [
                {"value": 1, "label": "Active"},
                {"value": 2, "label": "Inactive"},
            ],
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_filters(
                [{"field_id": "f1", "operator": "in", "value": ["Active", "Inactive"]}],
                ems_system_id=1, database_id="db",
            )
        assert result[0]["value"] == [1, 2]

    @pytest.mark.asyncio
    async def test_non_resolvable_operators_passthrough(self) -> None:
        """Operators like greaterThan, between should pass through unchanged."""
        filters = [
            {"field_id": "f1", "operator": "greaterThan", "value": 100},
            {"field_id": "f2", "operator": "isNull"},
            {"field_id": "f3", "operator": "between", "value": [10, 20]},
        ]
        # No mock client needed since these operators don't trigger resolution
        result = await _resolve_filters(filters, ems_system_id=1, database_id="db")
        assert result == filters

    @pytest.mark.asyncio
    async def test_numeric_value_not_resolved(self) -> None:
        """Numeric values in equal filter should pass through without API call."""
        mock_client = MagicMock()
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _resolve_filters(
                [{"field_id": "f1", "operator": "equal", "value": 42}],
                ems_system_id=1, database_id="db",
            )
        assert result[0]["value"] == 42
        mock_client.get.assert_not_called()


class TestQueryDatabaseDiscreteResolution:
    """Tests for discrete value resolution in query_database tool."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_string_filter_auto_resolved(self) -> None:
        """String filter values should be auto-resolved before query."""
        mock_client = MagicMock()
        # get call for field metadata
        mock_client.get = AsyncMock(return_value={
            "id": "f2", "name": "Fleet", "type": "discrete",
            "discreteValues": [{"value": 31, "label": "DHC-8-400"}],
        })
        # post call for query
        mock_client.post = AsyncMock(return_value={
            "header": [{"name": "Col"}],
            "rows": [["test"]],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "f1"}],
                filters=[{"field_id": "f2", "operator": "equal", "value": "DHC-8-400"}],
            )

        assert "test" in result
        # The filter in the API call should have numeric value, not string
        call_body = mock_client.post.call_args[1]["json"]
        filter_value = call_body["filter"]["args"][1]["value"]
        assert filter_value == 31

    @pytest.mark.asyncio
    async def test_resolution_error_returns_message(self) -> None:
        """Failed resolution should return helpful error message."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f2", "name": "Fleet", "type": "discrete",
            "discreteValues": [{"value": 31, "label": "DHC-8-400"}],
        })

        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result = await _query_database(
                ems_system_id=1,
                database_id="db",
                fields=[{"field_id": "f1"}],
                filters=[{"field_id": "f2", "operator": "equal", "value": "NonexistentFleet"}],
            )

        assert "Error resolving filter value" in result
        assert "not found" in result.lower()


class TestGetFieldMetadata:
    """Tests for _get_field_metadata helper."""

    @pytest.fixture(autouse=True)
    async def clear_cache(self) -> None:
        """Clear field cache before each test."""
        from ems_mcp.cache import field_cache
        await field_cache.clear()

    @pytest.mark.asyncio
    async def test_fetches_and_caches(self) -> None:
        """Should fetch metadata and cache it."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "f1", "name": "Test", "type": "string",
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            result1 = await _get_field_metadata(1, "db", "f1")
            result2 = await _get_field_metadata(1, "db", "f1")
        assert result1["name"] == "Test"
        assert result1 == result2
        # Only one API call due to caching
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_url_encodes_field_id(self) -> None:
        """Should URL-encode field IDs with special characters."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={
            "id": "[-hub-][field][test]", "name": "Test", "type": "string",
        })
        with patch("ems_mcp.tools.query.get_client", return_value=mock_client):
            await _get_field_metadata(1, "db", "[-hub-][field][test]")
        call_path = mock_client.get.call_args[0][0]
        assert "%5B" in call_path
