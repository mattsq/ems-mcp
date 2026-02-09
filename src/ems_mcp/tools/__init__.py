"""EMS MCP tools for discovery, querying, and asset management.

This package contains MCP tools organized by category:
- discovery: Tools for discovering systems, databases, fields, and analytics
- query: Tools for querying database records and time-series data
- assets: Tools for accessing reference data (fleets, aircraft, airports, etc.)
"""

from ems_mcp.tools.assets import (
    get_assets,
    ping_system,
)
from ems_mcp.tools.discovery import (
    find_fields,
    get_field_info,
    get_result_id,
    list_databases,
    list_ems_systems,
    search_analytics,
)
from ems_mcp.tools.query import (
    query_database,
    query_flight_analytics,
)

__all__ = [
    "list_ems_systems",
    "list_databases",
    "find_fields",
    "get_field_info",
    "get_result_id",
    "search_analytics",
    "query_database",
    "query_flight_analytics",
    "get_assets",
    "ping_system",
]
