"""FastMCP server for EMS API access.

This module defines the MCP server that exposes EMS API functionality
as tools for LLM assistants like Claude.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from ems_mcp.api.client import EMSClient
from ems_mcp.config import get_settings

# Configure logging based on settings
try:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level, logging.INFO)
except Exception:
    log_level = logging.INFO

logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage the lifecycle of the EMS MCP server.

    Initializes the EMS API client on startup and cleans up on shutdown.

    Args:
        app: The FastMCP application instance.

    Yields:
        A context dict with the initialized client.
    """
    logger.info("Starting EMS MCP server...")

    # Initialize the EMS client
    settings = get_settings()
    client = EMSClient(settings=settings)
    await client._initialize()
    EMSClient.set_instance(client)

    logger.info("EMS MCP server ready (base URL: %s)", settings.base_url)

    try:
        yield {"client": client}
    finally:
        logger.info("Shutting down EMS MCP server...")
        await client._cleanup()
        EMSClient.clear_instance()
        logger.info("EMS MCP server stopped")


# Create the FastMCP server instance
mcp = FastMCP(
    name="ems-mcp",
    version="0.2.0",
    instructions="""\
EMS flight data analytics server. Follow this workflow:

DISCOVERY (required before querying):
1. list_ems_systems -> get system ID (usually 1)
2. list_databases -> find database names (e.g. "FDW Flights"). Each \
database is annotated [searchable] or [entity-type] so you know which \
find_fields mode to use before calling it.
3. find_fields(mode="search") -> find fields by keyword. ALWAYS pass a \
list to search_text to look up ALL needed terms in one call -- this \
collapses N round-trips into one and is dramatically more token-efficient \
(e.g. search_text=["fuel burn", "tail number", "takeoff airport", \
"flight date", "aircraft type"]). Each term is cached individually.
4. Discrete value mappings: pass include_field_info=True to find_fields \
to get mappings inline, or call get_field_info separately on specific fields.

EVENT QUESTIONS (hard landing, tail strike, GPWS, unstable approach, \
exceedance, runway excursion, etc.):
Event names are DISCRETE VALUES inside each APM profile's Event Type \
field, not field names. find_fields will NOT find them. Two-step workflow:
  a. list_event_profiles(ems_system_id) -- one cheap call, returns every \
APM event profile (P14, P40, P600, P796, ...) with its description.
  b. find_event_types(ems_system_id, query, profiles=[...]) -- REQUIRED \
non-empty list of profile codes. Pick profiles whose descriptions \
plausibly match the user's term; broaden only if you get 0 matches. \
Returns [N]-referenced event values you can use as Event Type filters \
in query_database.

FIELD REFERENCES:
- find_fields returns numbered [N] references. Use these directly in \
query_database, get_field_info, etc. -- no need to retrieve raw IDs.
- You can also pass field names (e.g. "Takeoff Airport Name") and \
database names (e.g. "FDW Flights") -- they are resolved automatically.

OUTPUT FORMATS:
- query_database: output_format 'table' (default), 'csv', or 'json'.
- query_flight_analytics: output_format 'csv' (default, compact for \
time-series), 'table', or 'json'.

QUERYING:
- query_database: SQL-like queries on flight records. Supports filters, \
aggregation (avg/count/max/min/sum), and sorting. Best-practice data \
quality filters (Takeoff Valid, Landing Valid, Processing State, etc.) \
are applied automatically -- set apply_best_practice_filters=False only \
when you explicitly want to include invalid/unprocessed records.
- query_flight_analytics: Time-series data (altitude, airspeed, etc.) \
for specific flights. Accepts human-readable analytic names.

KEY RULES:
- Discrete fields use numeric codes internally. Pass string labels in \
filters -- they are auto-resolved. Use get_field_info or \
include_field_info=True to verify available values.
- Entity-type databases: list_databases flags these as [entity-type]. \
Use find_fields(mode="browse") to navigate or mode="deep" for BFS. \
mode="search" does NOT work on entity-type databases.
- get_assets returns reference data (fleets, aircraft, airports, flight phases).
- Use search_analytics to find time-series parameter names before querying.

QUERY BEST PRACTICES (applied automatically by query_database):
- FDW Flights queries: Takeoff Valid == true AND Landing Valid == true.
- Profile fields ("P{N}: ..."): Processing State == "Succeeded".
- Profile event fields: also False Positive == "Not a False Positive".
- Fleet vs Fleet Group vs Airline Fleet Group: state which one you used. \
"Fleet" is the recorder type; FDR_* values are FDR downloads, where you \
should also add Duplicate Detection (Master) == "Not a Duplicate".
- Use suggest_query_filters only when you want to inspect or adjust \
suggestions before the query; query_database applies them automatically.
""",
    lifespan=lifespan,
)


def get_client() -> EMSClient:
    """Get the EMS API client instance.

    This is a convenience function for tools to access the client.

    Returns:
        The initialized EMSClient instance.

    Raises:
        RuntimeError: If the server hasn't been started.
    """
    return EMSClient.get_instance()


def run() -> None:
    """Run the MCP server.

    This is the main entry point for starting the server.
    Uses stdio transport by default.
    """
    mcp.run()


# Import tools and resources to register them with the mcp instance
# This must happen after mcp is created
import ems_mcp.tools.assets  # noqa: E402, F401
import ems_mcp.tools.discovery  # noqa: E402, F401
import ems_mcp.tools.query  # noqa: E402, F401
import ems_mcp.prompts  # noqa: E402, F401
import ems_mcp.resources  # noqa: E402, F401
