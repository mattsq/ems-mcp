"""Asset tools for EMS MCP server.

Provides access to reference data: fleets, aircraft, airports, and flight phases.
"""

import logging
from typing import Any, Literal

from ems_mcp.api.client import EMSAPIError, EMSNotFoundError
from ems_mcp.server import get_client, mcp

logger = logging.getLogger(__name__)


def _format_fleets(fleets: list[dict[str, Any]]) -> str:
    """Format fleets list for display."""
    if not fleets:
        return "No fleets found."

    lines = [f"Found {len(fleets)} fleet(s):"]
    for f in fleets:
        name = f.get("name", "Unknown")
        fleet_id = f.get("id", "?")
        desc = f.get("description", "")
        if desc:
            lines.append(f"  - {name} (ID: {fleet_id}): {desc}")
        else:
            lines.append(f"  - {name} (ID: {fleet_id})")
    return "\n".join(lines)


def _format_aircraft(aircraft: list[dict[str, Any]]) -> str:
    """Format aircraft list for display."""
    if not aircraft:
        return "No aircraft found."

    lines = [f"Found {len(aircraft)} aircraft:"]
    for a in aircraft:
        name = a.get("name", "Unknown")
        aircraft_id = a.get("id", "?")
        fleet_name = a.get("fleetName", "Unknown")
        lines.append(f"  - {name} (ID: {aircraft_id}) [Fleet: {fleet_name}]")
    return "\n".join(lines)


def _format_flight_phases(phases: list[dict[str, Any]]) -> str:
    """Format flight phases list for display."""
    if not phases:
        return "No flight phases found."

    lines = [f"Found {len(phases)} flight phase(s):"]
    for p in phases:
        name = p.get("name", "Unknown")
        phase_id = p.get("id", "?")
        desc = p.get("description", "")
        if desc:
            lines.append(f"  - {name} (ID: {phase_id}): {desc}")
        else:
            lines.append(f"  - {name} (ID: {phase_id})")
    return "\n".join(lines)


def _format_airports(airports: list[dict[str, Any]]) -> str:
    """Format airports list for display."""
    if not airports:
        return "No airports found."

    lines = [f"Found {len(airports)} airport(s):"]
    for a in airports:
        icao = a.get("codeIcao", "????")
        iata = a.get("codeIata")
        name = a.get("name", "Unknown")
        city = a.get("city", "")
        country = a.get("country", "")

        location = ", ".join(part for part in (city, country) if part)
        id_str = f" (ID: {a.get('id', '?')})"

        codes = f"{icao}/{iata}" if iata else icao

        line = f"  - {codes}: {name}"
        if location:
            line += f" [{location}]"
        line += id_str
        lines.append(line)
    return "\n".join(lines)


@mcp.tool
async def get_assets(
    ems_system_id: int,
    asset_type: Literal["fleets", "aircraft", "airports", "flight_phases"],
    fleet_id: int | None = None,
) -> str:
    """Get reference data: fleets, aircraft, airports, or flight phases.

    Args:
        ems_system_id: EMS system ID (from list_ems_systems).
        asset_type: Type of assets to retrieve.
        fleet_id: Filter aircraft by fleet ID (only for asset_type="aircraft").

    Returns:
        Formatted list of the requested asset type.
    """
    client = get_client()

    try:
        if asset_type == "fleets":
            path = f"/api/v2/ems-systems/{ems_system_id}/assets/fleets"
            data = await client.get(path)
            return _format_fleets(data)

        elif asset_type == "aircraft":
            path = f"/api/v2/ems-systems/{ems_system_id}/assets/aircraft"
            params: dict[str, Any] = {}
            if fleet_id is not None:
                params["fleetId"] = fleet_id
            data = await client.get(path, params=params)
            return _format_aircraft(data)

        elif asset_type == "airports":
            path = f"/api/v2/ems-systems/{ems_system_id}/assets/airports"
            data = await client.get(path)
            return _format_airports(data)

        elif asset_type == "flight_phases":
            path = f"/api/v2/ems-systems/{ems_system_id}/assets/flight-phases"
            data = await client.get(path)
            return _format_flight_phases(data)

        else:
            return (
                f"Error: Unknown asset_type '{asset_type}'. "
                "Valid types: fleets, aircraft, airports, flight_phases."
            )

    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found."
    except EMSAPIError as e:
        return f"Error getting {asset_type}: {e.message}"


@mcp.tool
async def ping_system(ems_system_id: int) -> str:
    """Check if an EMS system is online and responsive.

    Args:
        ems_system_id: EMS system ID.

    Returns:
        System status.
    """
    client = get_client()
    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/ping"
        response = await client.get(path)
        # Ping response can be a boolean, a string, or a dict with a message
        if isinstance(response, bool):
            status = "ONLINE" if response else "OFFLINE"
            return f"EMS System {ems_system_id} is {status}."
        elif isinstance(response, str):
            return f"EMS System {ems_system_id} is ONLINE. Response: {response}"
        elif isinstance(response, dict):
            message = response.get("message", "System is accessible")
            return f"EMS System {ems_system_id} is ONLINE. {message}"
        else:
            return f"EMS System {ems_system_id} is ONLINE."
    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found."
    except EMSAPIError as e:
        return f"EMS System {ems_system_id} is OFFLINE or unreachable: {e.message}"
