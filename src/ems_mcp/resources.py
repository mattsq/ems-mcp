"""MCP Resources for EMS MCP server.

Resources provide reference data that LLMs can read without executing a tool call.
These are ideal for stable data like system lists, fleet catalogs, and workflow guides.
"""

import json
import logging
from typing import Any

from ems_mcp.api.client import EMSAPIError
from ems_mcp.cache import asset_cache, make_cache_key
from ems_mcp.server import get_client, mcp

logger = logging.getLogger(__name__)


@mcp.resource("ems://workflow-guide")
def workflow_guide() -> str:
    """Discovery-to-query workflow guide for EMS flight data analysis."""
    return """\
EMS Flight Data Analysis Workflow
==================================

Step 1: Find your EMS system
  Tool: list_ems_systems
  -> Returns system IDs (usually ID: 1)

Step 2: Find the database
  Tool: list_databases(ems_system_id=1)
  -> Look for "FDW Flights" (Flight Data Warehouse)
  -> Navigate groups if needed with group_id parameter

Step 3: Discover fields
  Tool: find_fields(ems_system_id=1, database_id="...", mode="search", search_text="...")
  -> Returns numbered references [N]
  -> Use get_result_id([N]) to get the full opaque field ID

  For entity-type databases that don't support search:
  Tool: find_fields(..., mode="deep", search_text="...")
  -> BFS traversal, slower but works on all databases

  To browse the field hierarchy:
  Tool: find_fields(..., mode="browse")
  -> Navigate with group_id parameter

Step 4: Check discrete field values (if filtering)
  Tool: get_field_info(ems_system_id=1, database_id="...", field_id="...")
  -> Shows numeric code-to-label mappings
  -> String labels in filters are auto-resolved to codes

Step 5: Query flight records
  Tool: query_database(ems_system_id=1, database_id="...", fields=[...], filters=[...])
  -> Returns tabular data with flight record IDs

Step 6: Get time-series analytics (optional)
  Tool: search_analytics(ems_system_id=1, search_text="altitude")
  -> Find available analytics names

  Tool: query_flight_analytics(ems_system_id=1, flight_ids=[...], analytics=["Altitude", "Airspeed"])
  -> Returns time-series data per flight
  -> Accepts human-readable names (resolved automatically)

Tips:
- Field IDs are opaque bracket-encoded strings. Never guess them.
- Use aggregation (avg/sum/count) in query_database to avoid large result sets.
- get_assets retrieves reference data (fleets, aircraft, airports, flight phases).
- ping_system checks if a system is online.
"""


@mcp.resource("ems://systems")
async def systems_resource() -> str:
    """List of available EMS systems (cached)."""
    cache_key = make_cache_key("resource", "systems")
    cached = await asset_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = get_client()
        systems = await client.get("/api/v2/ems-systems")
        result = json.dumps(systems, indent=2)
        await asset_cache.set(cache_key, result)
        return result
    except EMSAPIError as e:
        return f"Error fetching systems: {e.message}"
    except RuntimeError:
        return "Server not initialized. Use list_ems_systems tool instead."


@mcp.resource("ems://systems/{system_id}/fleets")
async def fleets_resource(system_id: int) -> str:
    """Fleet catalog for an EMS system (cached)."""
    cache_key = make_cache_key("resource", "fleets", system_id)
    cached = await asset_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = get_client()
        fleets = await client.get(f"/api/v2/ems-systems/{system_id}/assets/fleets")
        result = json.dumps(fleets, indent=2)
        await asset_cache.set(cache_key, result)
        return result
    except EMSAPIError as e:
        return f"Error fetching fleets: {e.message}"
    except RuntimeError:
        return "Server not initialized. Use get_assets tool instead."


@mcp.resource("ems://systems/{system_id}/airports")
async def airports_resource(system_id: int) -> str:
    """Airport reference data for an EMS system (cached)."""
    cache_key = make_cache_key("resource", "airports", system_id)
    cached = await asset_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = get_client()
        airports = await client.get(f"/api/v2/ems-systems/{system_id}/assets/airports")
        result = json.dumps(airports, indent=2)
        await asset_cache.set(cache_key, result)
        return result
    except EMSAPIError as e:
        return f"Error fetching airports: {e.message}"
    except RuntimeError:
        return "Server not initialized. Use get_assets tool instead."
