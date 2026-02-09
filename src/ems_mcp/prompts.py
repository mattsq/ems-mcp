"""MCP Prompts for EMS MCP server.

Prompts provide reusable templates for common multi-step workflows,
pre-encoding expert knowledge about the discovery -> query pipeline.
"""

from fastmcp.prompts import Message

from ems_mcp.server import mcp


@mcp.prompt()
def analyze_flights(
    tail_number: str = "",
    date_range: str = "",
    parameters: str = "Altitude, Airspeed",
) -> list[Message]:
    """Analyze flight data for a specific aircraft. Guides through the full
    discovery -> query -> analytics workflow.

    Args:
        tail_number: Aircraft tail number (e.g. "VH-VXZ"). Leave empty to query all.
        date_range: Date range for flights (e.g. "2024-01-01 to 2024-01-31").
        parameters: Comma-separated analytics to retrieve (e.g. "Altitude, Airspeed").
    """
    filter_instructions = ""
    if tail_number:
        filter_instructions += f"\n- Filter by tail number: {tail_number}"
    if date_range:
        filter_instructions += f"\n- Filter by date range: {date_range}"

    return [Message(
        role="user",
        content=f"""\
Analyze flight data with these specifications:
{filter_instructions if filter_instructions else "- No specific filters (query recent flights)"}
- Retrieve time-series parameters: {parameters}

Follow these steps:
1. Call list_ems_systems to find the system ID
2. Call list_databases to find the FDW Flights database
3. Use find_fields to discover the field IDs for: Flight Record ID, \
{'Tail Number, ' if tail_number else ''}{'Flight Date, ' if date_range else ''}and any other relevant fields
4. Use get_result_id to get the full field IDs
5. Query flight records with query_database using the discovered fields\
{' and appropriate filters' if filter_instructions else ''}
6. Use query_flight_analytics with the flight IDs and analytics: {parameters}
7. Summarize the results
""",
    )]


@mcp.prompt()
def compare_flights(
    flight_id_1: str = "",
    flight_id_2: str = "",
    parameters: str = "Altitude, Airspeed",
) -> list[Message]:
    """Compare time-series analytics between two flights.

    Args:
        flight_id_1: First flight record ID. Leave empty to search.
        flight_id_2: Second flight record ID. Leave empty to search.
        parameters: Comma-separated analytics to compare.
    """
    if flight_id_1 and flight_id_2:
        query_step = f"Use flight IDs {flight_id_1} and {flight_id_2}."
    else:
        query_step = (
            "First query flight records with query_database to find flight IDs for "
            "the flights you want to compare."
        )

    return [Message(
        role="user",
        content=f"""\
Compare time-series analytics between two flights:

{query_step}

1. If flight IDs are not provided, use list_ems_systems, list_databases, and \
find_fields to discover the FDW Flights database and field IDs, then \
query_database to find flights
2. Call query_flight_analytics with both flight IDs and these analytics: {parameters}
3. Compare the two flights' data side by side
4. Highlight any notable differences in the parameters
""",
    )]


@mcp.prompt()
def search_flight_parameters(
    search_term: str = "fuel",
    database_type: str = "standard",
) -> list[Message]:
    """Search for available flight parameters/fields in the database.

    Args:
        search_term: What to search for (e.g. "fuel", "engine", "altitude").
        database_type: "standard" for normal databases, "entity" for entity-type.
    """
    mode = "search" if database_type == "standard" else "deep"
    extra = ""
    if database_type == "entity":
        extra = (
            " Note: This is an entity-type database, so use mode='deep' for BFS "
            "traversal of field groups."
        )

    return [Message(
        role="user",
        content=f"""\
Search for flight parameters related to "{search_term}" in the EMS database.{extra}

1. Call list_ems_systems to find the system ID
2. Call list_databases to find the FDW Flights database ID
3. Use find_fields with mode="{mode}" and search_text="{search_term}" to \
discover available fields
4. For any interesting fields, call get_field_info to see detailed metadata \
including units and discrete value mappings
5. Summarize what parameters are available related to "{search_term}"
""",
    )]
