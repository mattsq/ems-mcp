"""MCP Resources for EMS MCP server.

Resources provide reference data that LLMs can read without executing a tool call.
These are ideal for stable data like system lists, fleet catalogs, and workflow guides.
"""

import json
import logging
import urllib.parse
from importlib import resources
from typing import Any

from ems_mcp.api.client import EMSAPIError
from ems_mcp.cache import asset_cache, make_cache_key
from ems_mcp.server import get_client, mcp

logger = logging.getLogger(__name__)

_COMMON_FIELDS_RESOURCE = ("ems_mcp", "data/common_fields.json")
_common_fields_cache: dict[str, Any] | None = None
_logged_load_error: bool = False


def _load_common_fields() -> dict[str, Any]:
    """Load and cache the curated common-fields catalog.

    Uses ``importlib.resources`` so the JSON file resolves correctly when
    the package is installed from a wheel or imported from a zip. On a
    successful load the catalog is memoised for the lifetime of the
    process; on failure (missing file, parse error, I/O error) we log
    once and return an empty dict so callers degrade to their normal
    "no curated list for this database" output. The failure is *not*
    memoised, so a transiently missing file (e.g. installed late) is
    picked up on the next access without restarting the server.
    """
    global _common_fields_cache, _logged_load_error  # noqa: PLW0603
    if _common_fields_cache is not None:
        return _common_fields_cache

    package, name = _COMMON_FIELDS_RESOURCE
    try:
        raw = resources.files(package).joinpath(name).read_text(encoding="utf-8")
        _common_fields_cache = json.loads(raw)
        _logged_load_error = False
        return _common_fields_cache
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        if not _logged_load_error:
            logger.warning(
                "Could not load common-fields catalog at %s/%s (%s: %s); "
                "common-fields resources will return empty results.",
                package, name, type(e).__name__, e,
            )
            _logged_load_error = True
        return {}


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
  Resource: ems://databases/{database_name}/common-fields
  -> Curated vocabulary of typical fields for FDW Flights, APM Events, etc.
  -> Read first to know what to search for, then resolve via find_fields.

  Tool: find_fields(ems_system_id=1, database_id="...", mode="search", search_text="...")
  -> Returns numbered references [N]
  -> Pass a list to search_text (e.g. ["fuel burn", "tail", "takeoff"]) to
     resolve many terms in one parallel tool call.
  -> include_field_info=True merges discrete-value mappings inline.

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


@mcp.resource("ems://databases/{database_name}/common-fields")
def common_fields_resource(database_name: str) -> str:
    """Curated vocabulary of typical fields for a named database.

    Reads the shipped catalog at ``data/common_fields.json`` and returns
    the entry for the requested database (URL-decoded). Field names listed
    here are advisory: deployment-specific names exist, so always resolve
    them to actual IDs with ``find_fields(search_text=[...])`` before use.
    """
    catalog = _load_common_fields()
    decoded = urllib.parse.unquote(database_name)

    for key in catalog:
        if key.lower() == decoded.lower():
            entry = {
                "database": key,
                "fields": catalog[key],
                "usage": catalog.get("_usage", ""),
            }
            return json.dumps(entry, indent=2)

    available = [k for k in catalog if not k.startswith("_")]
    return json.dumps(
        {
            "error": f"No curated field list for database '{decoded}'.",
            "databases_with_curated_lists": available,
            "hint": "Use find_fields(mode='search', search_text=[...]) to discover fields directly.",
        },
        indent=2,
    )


@mcp.resource("ems://databases/common-fields")
def common_fields_index_resource() -> str:
    """Index of databases that have a curated common-fields list."""
    catalog = _load_common_fields()
    return json.dumps(
        {
            "description": catalog.get("_description", ""),
            "usage": catalog.get("_usage", ""),
            "databases": [k for k in catalog if not k.startswith("_")],
        },
        indent=2,
    )


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
