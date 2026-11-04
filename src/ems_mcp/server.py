"""FastMCP server for EMS API access.

This module defines the MCP server that exposes EMS API functionality
as tools for LLM assistants like Claude.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

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
2. Database: for flight records use "FDW Flights" by name directly -- no \
need to browse. list_databases shows only groups at the root; databases \
(annotated [searchable] or [entity-type]) appear one level down \
(FDW Flights is under the "FDW" group).
3. find_fields -> find fields by keyword. ALWAYS pass a list to \
search_text to look up ALL needed terms in one call (e.g. \
search_text=["fuel burn", "tail number", "takeoff airport", "flight \
date"]). FDW Flights is an entity-type database, so this runs as a deep \
(tree) search automatically; the default mode is fine.
4. Discrete value mappings: pass include_field_info=True to find_fields \
to get mappings inline, or call get_field_info separately on specific fields.
5. Field names passed to other tools must match EXACTLY. A near-miss is \
never substituted silently: you get an error listing the closest fields \
as [N] refs -- pick one deliberately.

EVENT QUESTIONS (hard landing, tail strike, GPWS, unstable approach, \
exceedance, runway excursion, etc.):
Event names are DISCRETE VALUES inside each APM profile's Event Type \
field, not field names. find_fields will NOT find them. Workflow:
  a. list_event_profiles(ems_system_id [, name_filter="..."]) -- returns \
APM event profiles with an [N] ref, P-code (when the name has one) and \
description. Pass name_filter (e.g. "landing" or "strike") to narrow it.
  b. find_event_types(ems_system_id, query, profiles=[...]) -- pass P-codes \
or [N] refs from (a) ([N] works for profiles without a code). If you don't \
yet know which profile, OMIT profiles= and the tool auto-shortlists. \
Returns [N]-referenced event values plus a database [N] for each profile.
  c. query_database(database_id="[db N]", fields=[...], \
filters=[{"field_id": "[event N]", "operator": "equal"}]) -- the event \
ref expands to that profile's Event Type == code.

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
are applied automatically and listed at the end of the result -- set \
apply_best_practice_filters=False only when you explicitly want to \
include invalid/unprocessed records. Dates must be ISO (YYYY-MM-DD).
- query_flight_analytics: Time-series data for specific flights. Pass \
exact analytic names (e.g. "Pressure Altitude (ft)", "Airspeed \
(Calibrated; 1 or only) (knots)") or [N] references from search_analytics \
/ find_physical_params. Generic names like "Altitude" are ambiguous -- \
search_analytics first. Omitting start/end_offset covers the whole \
flight at sample_rate; at most 200 rows per flight are shown, so lower \
sample_rate or narrow the window for long spans.
- find_physical_params: discover PHYSICAL (raw FDR-recorded) parameters \
for a specific flight. These are NOT in search_analytics (which only \
covers global computed analytics) -- they are a property of the flight's \
frame configuration, so a flight_id is required. Use this when a parameter \
is fleet/frame-specific (raw bleed valves, individual sensor channels) or \
when search_analytics returns nothing/NULLs for a flight. Pass the [N] \
references it returns (not the names) to query_flight_analytics with the \
same flight_id; a ref from one flight may not exist on another fleet's \
frame.

KEY RULES:
- Discrete fields use numeric codes internally. Pass string labels in \
filters -- they are auto-resolved. Use get_field_info or \
include_field_info=True to verify available values.
- Entity-type databases (flagged [entity-type] by list_databases, \
including FDW Flights) have no keyword-search endpoint; find_fields runs \
mode="deep" for them automatically. mode="browse" navigates the tree.
- Tool failures are returned as errors starting with "Error"; read the \
message -- it says what to do next.
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


class ErrorResultMiddleware(Middleware):
    """Flag tool results that report a failure as MCP errors.

    Tools return their failures as plain text beginning with "Error" so the
    message stays readable and actionable. Without this, MCP clients would
    see those as successful results; re-raising them as ToolError sets
    ``isError: true`` on the tool result while keeping the same text.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        result = await call_next(context)
        content = getattr(result, "content", None) or []
        if len(content) == 1:
            text = getattr(content[0], "text", None)
            if isinstance(text, str) and text.lstrip().startswith("Error"):
                raise ToolError(text)
        return result


mcp.add_middleware(ErrorResultMiddleware())


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
