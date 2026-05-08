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
2. list_databases -> find database names (e.g. "FDW Flights")
3. find_fields(mode="search") -> find fields by keyword. Pass a list to \
search_text to look up many terms in parallel in a single tool call \
(e.g. search_text=["fuel burn", "tail", "takeoff airport"]).
4. get_field_info -> check discrete value codes for filtering. Or skip \
this by calling find_fields(..., include_field_info=True) which fetches \
discrete value mappings inline.

FIELD REFERENCES:
- find_fields returns numbered [N] references. Use these directly in \
query_database, get_field_info, etc. -- no need to retrieve raw IDs.
- You can also pass field names (e.g. "Takeoff Airport Name") and \
database names (e.g. "FDW Flights") -- they are resolved automatically.

OUTPUT FORMATS:
- query_database and query_flight_analytics accept output_format: \
'table' (default), 'csv' (compact), or 'json' (structured).

QUERYING:
- query_database: SQL-like queries on flight records. Supports filters, \
aggregation (avg/count/max/min/sum), and sorting.
- query_flight_analytics: Time-series data (altitude, airspeed, etc.) \
for specific flights. Accepts human-readable analytic names.

KEY RULES:
- Discrete fields use numeric codes internally. Use get_field_info to see \
code-to-label mappings, or pass string labels in filters (auto-resolved).
- Entity-type databases don't support field search. Use \
find_fields(mode="deep") for BFS traversal, or mode="browse" to navigate.
- get_assets returns reference data (fleets, aircraft, airports, flight phases).
- Use search_analytics to find time-series parameter names before querying.

QUERY BEST PRACTICES (apply unless the user opts out):
- Profile fields (names matching "P{N}: ..."): add Processing State == \
"Succeeded". For event fields (path contains "Event Information"): also add \
False Positive == "Not a False Positive".
- FDW Flights queries: add Takeoff Valid == true AND Landing Valid == true.
- Fleet vs Fleet Group vs Airline Fleet Group: state which one you used. \
"Fleet" is the recorder type; FDR_* values are FDR downloads, where you \
should also add Duplicate Detection (Master) == "Not a Duplicate".
- Call suggest_query_filters before query_database to get rules resolved \
for your specific query.
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
