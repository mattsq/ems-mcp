"""EMS MCP tools for discovery, querying, and asset management.

This package contains MCP tools organized by category:
- discovery: Tools for discovering systems, databases, fields, and analytics
- query: Tools for querying database records and time-series data
- assets: Tools for listing fleets, aircraft, airports, etc. (Phase 4)
"""

from ems_mcp.tools.discovery import (
    get_field_info,
    list_databases,
    list_ems_systems,
    list_fields,
    search_analytics,
    search_fields,
    search_fields_deep,
)
from ems_mcp.tools.query import (
    query_database,
    query_flight_analytics,
)

__all__ = [
    "list_ems_systems",
    "list_databases",
    "list_fields",
    "search_fields",
    "search_fields_deep",
    "get_field_info",
    "search_analytics",
    "query_database",
    "query_flight_analytics",
]
