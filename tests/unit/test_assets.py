"""Unit tests for EMS MCP asset tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ems_mcp.tools.assets import (
    _format_aircraft,
    _format_airports,
    _format_flight_phases,
    _format_fleets,
    get_assets,
    ping_system,
)

# Access the underlying functions from the FastMCP FunctionTool wrappers
_get_assets = get_assets.fn
_ping_system = ping_system.fn


class TestAssetFormatters:
    """Tests for asset formatting functions."""

    def test_format_fleets_empty(self) -> None:
        result = _format_fleets([])
        assert result == "No fleets found."

    def test_format_fleets_multiple(self) -> None:
        fleets = [
            {"id": 1, "name": "B737", "description": "Boeing 737"},
            {"id": 2, "name": "A320"},
        ]
        result = _format_fleets(fleets)
        assert "Found 2 fleet(s):" in result
        assert "B737 (ID: 1): Boeing 737" in result
        assert "A320 (ID: 2)" in result

    def test_format_aircraft_empty(self) -> None:
        result = _format_aircraft([])
        assert result == "No aircraft found."

    def test_format_aircraft_multiple(self) -> None:
        aircraft = [
            {"id": 101, "name": "VH-VXZ", "fleetName": "B737"},
            {"id": 102, "name": "VH-VYA", "fleetName": "B737"},
        ]
        result = _format_aircraft(aircraft)
        assert "Found 2 aircraft:" in result
        assert "VH-VXZ (ID: 101) [Fleet: B737]" in result

    def test_format_flight_phases_empty(self) -> None:
        result = _format_flight_phases([])
        assert result == "No flight phases found."

    def test_format_flight_phases_multiple(self) -> None:
        phases = [
            {"id": 1, "name": "Takeoff", "description": "Takeoff roll"},
            {"id": 2, "name": "Climb"},
        ]
        result = _format_flight_phases(phases)
        assert "Found 2 flight phase(s):" in result
        assert "Takeoff (ID: 1): Takeoff roll" in result
        assert "Climb (ID: 2)" in result

    def test_format_airports_empty(self) -> None:
        result = _format_airports([])
        assert result == "No airports found."

    def test_format_airports_multiple(self) -> None:
        airports = [
            {
                "id": 1,
                "codeIcao": "YSSY",
                "codeIata": "SYD",
                "name": "Sydney Airport",
                "city": "Sydney",
                "country": "Australia",
            },
            {
                "id": 2,
                "codeIcao": "KSFO",
                "name": "San Francisco",
                "city": "San Francisco",
            },
        ]
        result = _format_airports(airports)
        assert "Found 2 airport(s):" in result
        assert "YSSY/SYD: Sydney Airport [Sydney, Australia] (ID: 1)" in result
        assert "KSFO: San Francisco [San Francisco] (ID: 2)" in result

    def test_format_airports_no_city_or_country(self) -> None:
        airports = [{"id": 3, "codeIcao": "XXXX", "name": "Unknown Airport"}]
        result = _format_airports(airports)
        # No location bracket when city and country are empty
        assert "XXXX: Unknown Airport (ID: 3)" in result
        assert "[" not in result.split("\n")[-1]


class TestGetAssets:
    """Tests for get_assets consolidated tool."""

    @pytest.mark.asyncio
    async def test_get_fleets(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[{"id": 1, "name": "Fleet 1"}])

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=1, asset_type="fleets")

        assert "Fleet 1" in result
        mock_client.get.assert_called_once_with("/api/v2/ems-systems/1/assets/fleets")

    @pytest.mark.asyncio
    async def test_get_aircraft_with_fleet_filter(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[{"id": 1, "name": "AC1", "fleetName": "F1"}])

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(
                ems_system_id=1, asset_type="aircraft", fleet_id=10
            )

        assert "AC1" in result
        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/assets/aircraft", params={"fleetId": 10}
        )

    @pytest.mark.asyncio
    async def test_get_aircraft_no_fleet_filter(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[{"id": 1, "name": "AC1", "fleetName": "F1"}])

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=1, asset_type="aircraft")

        assert "AC1" in result
        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/assets/aircraft", params={}
        )

    @pytest.mark.asyncio
    async def test_get_flight_phases(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[{"id": 1, "name": "Phase 1"}])

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=1, asset_type="flight_phases")

        assert "Phase 1" in result
        mock_client.get.assert_called_once_with("/api/v2/ems-systems/1/assets/flight-phases")

    @pytest.mark.asyncio
    async def test_get_airports(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=[{"id": 1, "codeIcao": "YSSY"}])

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=1, asset_type="airports")

        assert "YSSY" in result
        mock_client.get.assert_called_once_with(
            "/api/v2/ems-systems/1/assets/airports"
        )

    @pytest.mark.asyncio
    async def test_not_found_error(self) -> None:
        from ems_mcp.api.client import EMSNotFoundError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSNotFoundError("Not found"))

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=999, asset_type="fleets")

        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_api_error(self) -> None:
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSAPIError("Server error"))

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _get_assets(ems_system_id=1, asset_type="airports")

        assert "Error" in result
        assert "Server error" in result


class TestPingSystem:
    """Tests for ping_system tool."""

    @pytest.mark.asyncio
    async def test_ping_system_bool_true(self) -> None:
        """Ping returning boolean true should show ONLINE."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=True)

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _ping_system(ems_system_id=1)

        assert "ONLINE" in result
        mock_client.get.assert_called_once_with("/api/v2/ems-systems/1/ping")

    @pytest.mark.asyncio
    async def test_ping_system_bool_false(self) -> None:
        """Ping returning boolean false should show OFFLINE."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=False)

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _ping_system(ems_system_id=1)

        assert "OFFLINE" in result

    @pytest.mark.asyncio
    async def test_ping_system_dict_response(self) -> None:
        """Ping returning a dict should extract message."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value={"message": "All systems go"})

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _ping_system(ems_system_id=1)

        assert "ONLINE" in result
        assert "All systems go" in result

    @pytest.mark.asyncio
    async def test_ping_system_string_response(self) -> None:
        """Ping returning a string should show it."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value="2024-02-05T12:00:00Z")

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _ping_system(ems_system_id=1)

        assert "ONLINE" in result
        assert "2024-02-05T12:00:00Z" in result

    @pytest.mark.asyncio
    async def test_ping_system_api_error(self) -> None:
        """Ping should handle API errors gracefully."""
        from ems_mcp.api.client import EMSAPIError

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=EMSAPIError("Server error", status_code=500))

        with patch("ems_mcp.tools.assets.get_client", return_value=mock_client):
            result = await _ping_system(ems_system_id=1)

        assert "OFFLINE" in result
        assert "Server error" in result
