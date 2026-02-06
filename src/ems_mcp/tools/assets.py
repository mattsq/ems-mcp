"""Asset tools for EMS MCP server.

These tools enable LLMs to retrieve reference data about fleets, aircraft,
flight phases, and airports from the EMS system.
"""

import logging
from typing import Any

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
async def list_fleets(ems_system_id: int) -> str:
    """List aircraft fleets available in the EMS system.

    Fleets are groups of aircraft that share common characteristics.

    Args:
        ems_system_id: The EMS system ID (from list_ems_systems).

    Returns:
        Formatted list of fleets with their IDs and descriptions.
    """
    client = get_client()
    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/assets/fleets"
        fleets = await client.get(path)
        return _format_fleets(fleets)
    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found."
    except EMSAPIError as e:
        return f"Error listing fleets: {e.message}"


@mcp.tool
async def list_aircraft(
    ems_system_id: int,
    fleet_id: int | None = None,
) -> str:
    """List aircraft in the EMS system.

    Returns aircraft identifiers (tail numbers) and their associated fleets.

    Args:
        ems_system_id: The EMS system ID.
        fleet_id: Optional: filter to specific fleet ID.

    Returns:
        Formatted list of aircraft with their IDs and fleets.
    """
    client = get_client()
    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/assets/aircraft"
        params = {}
        if fleet_id is not None:
            params["fleetId"] = fleet_id

        aircraft = await client.get(path, params=params)
        return _format_aircraft(aircraft)
    except EMSNotFoundError:
        return "Error: EMS system or fleet not found."
    except EMSAPIError as e:
        return f"Error listing aircraft: {e.message}"


@mcp.tool
async def list_flight_phases(ems_system_id: int) -> str:
    """List flight phases used in the EMS system.

    Flight phases divide flights into logical segments (Takeoff, Climb, Cruise, etc.).

    Args:
        ems_system_id: The EMS system ID.

    Returns:
        Formatted list of flight phases with their IDs and descriptions.
    """
    client = get_client()
    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/assets/flight-phases"
        phases = await client.get(path)
        return _format_flight_phases(phases)
    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found."
    except EMSAPIError as e:
        return f"Error listing flight phases: {e.message}"


@mcp.tool
async def list_airports(ems_system_id: int) -> str:
    """List airports known to the EMS system.

    Returns airport names, codes (ICAO/IATA), and locations.

    Args:
        ems_system_id: The EMS system ID.

    Returns:
        Formatted list of airports.
    """
    client = get_client()
    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/assets/airports"
        airports = await client.get(path)
        return _format_airports(airports)
    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found."
    except EMSAPIError as e:
        return f"Error listing airports: {e.message}"


@mcp.tool
async def ping_system(ems_system_id: int) -> str:
    """Check if an EMS system is online and responsive.

    Args:
        ems_system_id: The EMS system ID.

    Returns:
        System status and server response timestamp.
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
