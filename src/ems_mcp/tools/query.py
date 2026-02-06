"""Query tools for EMS MCP server.

These tools enable LLMs to query flight records from the Flight Data Warehouse
and retrieve time-series analytics data for individual flights.
"""

import logging
import re
from typing import Any, Literal, NotRequired, TypedDict

from ems_mcp.api.client import EMSAPIError, EMSNotFoundError
from ems_mcp.cache import field_cache, make_cache_key
from ems_mcp.server import get_client, mcp

logger = logging.getLogger(__name__)

# Valid filter operators for query_database
VALID_OPERATORS = frozenset(
    {
        "equal",
        "notEqual",
        "greaterThan",
        "greaterThanOrEqual",
        "lessThan",
        "lessThanOrEqual",
        "in",
        "isNull",
        "isNotNull",
        "like",
        "between",
    }
)

# Operators that take no value argument
UNARY_OPERATORS = frozenset({"isNull", "isNotNull"})

# Pattern for bracket-encoded analytic IDs: starts with [-hub-] or has [...][...] pattern
_BRACKET_ID_PATTERN = re.compile(r"^\[-hub-\]|^\[.+?\]\[.+?\]")


def _is_analytic_id(value: str) -> bool:
    """Check if a string looks like a raw analytic ID rather than a human-readable name.

    Recognizes two formats used by the EMS API:
    - Bracket-encoded: starts with ``[-hub-]`` or has ``[...][...]`` pattern
    - Compressed: starts with ``H4sIA`` (base64-encoded gzip)

    Args:
        value: The string to check.

    Returns:
        True if the string looks like a raw analytic ID.
    """
    if not value or not value.strip():
        return False
    value = value.strip()
    if value.startswith("H4sIA"):
        return True
    if _BRACKET_ID_PATTERN.match(value):
        return True
    return False


async def _resolve_analytics(
    names_or_ids: list[str],
    ems_system_id: int,
) -> list[tuple[str, str]]:
    """Resolve analytic names or IDs to (display_name, analytic_id) pairs.

    For raw IDs (bracket-encoded or compressed), passes them through as-is.
    For human-readable names, searches the analytics API and matches by name.

    Args:
        names_or_ids: List of analytic names or raw IDs.
        ems_system_id: The EMS system ID for API lookups.

    Returns:
        List of (display_name, analytic_id) tuples in the same order.

    Raises:
        ValueError: If a name cannot be resolved (not found or ambiguous).
    """
    client = get_client()
    results: list[tuple[str, str]] = []

    for item in names_or_ids:
        item = item.strip()
        if _is_analytic_id(item):
            results.append((item, item))
            continue

        # Check cache first
        cache_key = make_cache_key("analytic_resolve", ems_system_id, item.lower())
        cached = await field_cache.get(cache_key)
        if cached is not None:
            results.append(cached)
            continue

        # Search the analytics API
        path = f"/api/v2/ems-systems/{ems_system_id}/analytics"
        params = {"text": item}
        search_results = await client.get(path, params=params)

        if not search_results:
            raise ValueError(
                f"Analytic not found: '{item}'. "
                "Use search_analytics to find valid analytic names."
            )

        # Try exact name match (case-insensitive)
        exact_matches = [
            a for a in search_results
            if a.get("name", "").lower() == item.lower()
        ]
        if len(exact_matches) == 1:
            pair = (exact_matches[0]["name"], exact_matches[0]["id"])
            await field_cache.set(cache_key, pair)
            results.append(pair)
            continue

        # If only one result total, use it
        if len(search_results) == 1:
            pair = (search_results[0]["name"], search_results[0]["id"])
            await field_cache.set(cache_key, pair)
            results.append(pair)
            continue

        # Multiple matches with no exact match - ambiguous
        match_names = [a.get("name", "?") for a in search_results[:5]]
        raise ValueError(
            f"Ambiguous analytic name: '{item}'. "
            f"Multiple matches found: {', '.join(match_names)}"
            f"{'...' if len(search_results) > 5 else ''}. "
            "Use a more specific name or use search_analytics to find the exact name."
        )

    return results


def _format_analytic_header(analytic_id: str) -> str:
    """Format a raw analytic ID for use as a column header.

    Truncates long bracket-encoded IDs to the last meaningful segment.

    Args:
        analytic_id: The raw analytic ID string.

    Returns:
        A shorter display string suitable for a column header.
    """
    if analytic_id.startswith("H4sIA"):
        return analytic_id[:12] + "..."
    # For bracket IDs, try to extract the last bracket segment
    segments = re.findall(r"\[([^\]]+)\]", analytic_id)
    if segments:
        return segments[-1]
    return analytic_id


VALID_AGGREGATES = frozenset({"avg", "count", "max", "min", "stdev", "sum", "var"})


class QueryField(TypedDict):
    """A field to include in query results."""

    field_id: str
    alias: NotRequired[str]
    aggregate: NotRequired[
        Literal["avg", "count", "max", "min", "stdev", "sum", "var"]
    ]


class QueryFilter(TypedDict):
    """A filter condition for a database query."""

    field_id: str
    operator: Literal[
        "equal",
        "notEqual",
        "greaterThan",
        "greaterThanOrEqual",
        "lessThan",
        "lessThanOrEqual",
        "in",
        "isNull",
        "isNotNull",
        "like",
        "between",
    ]
    value: NotRequired[object]


class QueryOrderBy(TypedDict):
    """Sort order for query results."""

    field_id: str
    direction: NotRequired[Literal["asc", "desc"]]


async def _get_field_metadata(
    ems_system_id: int,
    database_id: str,
    field_id: str,
) -> dict[str, Any]:
    """Fetch raw field metadata from the API, with caching.

    Uses the same cache key pattern as the ``get_field_info`` MCP tool so
    cache entries are shared between the two code paths.

    Args:
        ems_system_id: The EMS system ID.
        database_id: The database ID.
        field_id: The field ID.

    Returns:
        Raw field metadata dict from the API.
    """
    import urllib.parse

    cache_key = make_cache_key("field_info", ems_system_id, database_id, field_id)
    cached = await field_cache.get(cache_key)
    if cached is not None:
        return cached

    client = get_client()
    encoded_field_id = urllib.parse.quote(field_id, safe="")
    path = (
        f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}"
        f"/fields/{encoded_field_id}"
    )
    field_meta = await client.get(path)
    await field_cache.set(cache_key, field_meta)
    return field_meta


async def _resolve_discrete_filter_value(
    value: object,
    field_id: str,
    ems_system_id: int,
    database_id: str,
) -> object:
    """Resolve a string filter value to its numeric code for discrete fields.

    If the value is not a string, it is returned as-is. If the field is not
    discrete, the string is returned as-is. Otherwise, the field's discrete
    value mappings are looked up and the label is matched case-insensitively.

    Args:
        value: The filter value (may be string, int, etc.).
        field_id: The field ID being filtered on.
        ems_system_id: The EMS system ID.
        database_id: The database ID.

    Returns:
        The resolved numeric code, or the original value if not applicable.

    Raises:
        ValueError: If the string label is not found in discrete values.
    """
    if not isinstance(value, str):
        return value

    try:
        meta = await _get_field_metadata(ems_system_id, database_id, field_id)
    except EMSAPIError:
        # If we can't fetch metadata, pass the value through and let the API
        # return its own error.
        return value

    field_type = meta.get("type", "")
    if field_type != "discrete":
        return value

    discrete_values = meta.get("discreteValues")
    if not discrete_values:
        return value

    # Normalize to list of {value, label} dicts
    if isinstance(discrete_values, dict):
        entries = [{"value": k, "label": v} for k, v in discrete_values.items()]
    else:
        entries = discrete_values

    # Case-insensitive label lookup
    value_lower = value.lower()
    for dv in entries:
        label = str(dv.get("label", ""))
        if label.lower() == value_lower:
            raw_code = dv.get("value")
            # Discrete codes may be stored as string-encoded ints
            if isinstance(raw_code, str):
                try:
                    return int(raw_code)
                except ValueError:
                    return raw_code
            return raw_code

    # Not found - build helpful error
    sample_labels = [str(dv.get("label", "?")) for dv in entries[:10]]
    suffix = f" (and {len(entries) - 10} more)" if len(entries) > 10 else ""
    raise ValueError(
        f"Discrete value '{value}' not found for field '{field_id}'. "
        f"Available values include: {', '.join(sample_labels)}{suffix}. "
        "Use get_field_info to see all discrete values."
    )


async def _resolve_filters(
    filters: list[QueryFilter],
    ems_system_id: int,
    database_id: str,
) -> list[QueryFilter]:
    """Resolve string values in filters to numeric codes for discrete fields.

    Processes ``equal``, ``notEqual``, and ``in`` operators. Other operators
    are passed through unchanged.

    Args:
        filters: The original filter list.
        ems_system_id: The EMS system ID.
        database_id: The database ID.

    Returns:
        A new filter list with string values resolved where applicable.
    """
    resolved: list[QueryFilter] = []
    for f in filters:
        op = f["operator"]
        if op in ("equal", "notEqual"):
            value = f.get("value")
            new_value = await _resolve_discrete_filter_value(
                value, f["field_id"], ems_system_id, database_id,
            )
            new_filter: QueryFilter = {"field_id": f["field_id"], "operator": op}
            new_filter["value"] = new_value
            resolved.append(new_filter)
        elif op == "in":
            value = f.get("value")
            if isinstance(value, (list, tuple)):
                new_list = []
                for item in value:
                    new_list.append(
                        await _resolve_discrete_filter_value(
                            item, f["field_id"], ems_system_id, database_id,
                        )
                    )
                new_filter = {"field_id": f["field_id"], "operator": op}
                new_filter["value"] = new_list
                resolved.append(new_filter)
            else:
                resolved.append(f)
        else:
            resolved.append(f)
    return resolved


def _build_single_filter(f: QueryFilter) -> dict[str, Any]:
    """Translate a flat QueryFilter into the nested EMS API filter structure.

    Args:
        f: A flat filter specification.

    Returns:
        Nested EMS API filter dict.

    Raises:
        ValueError: If the filter specification is invalid.
    """
    operator = f["operator"]
    field_id = f["field_id"]
    field_arg: dict[str, Any] = {"type": "field", "value": field_id}

    if operator in UNARY_OPERATORS:
        return {"operator": operator, "args": [field_arg]}

    value = f.get("value")

    if operator == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"'between' filter requires a list of [min, max], got: {value!r}")
        return {
            "operator": "betweenInclusive",
            "args": [
                field_arg,
                {"type": "constant", "value": value[0]},
                {"type": "constant", "value": value[1]},
            ],
        }

    if operator == "in":
        if not isinstance(value, (list, tuple)) or len(value) == 0:
            raise ValueError(f"'in' filter requires a non-empty list, got: {value!r}")
        args: list[dict[str, Any]] = [field_arg]
        for v in value:
            args.append({"type": "constant", "value": v})
        return {"operator": "in", "args": args}

    # Standard binary operators: equal, notEqual, greaterThan, etc.
    return {
        "operator": operator,
        "args": [field_arg, {"type": "constant", "value": value}],
    }


def _build_query_body(
    fields: list[QueryField],
    filters: list[QueryFilter] | None,
    order_by: list[QueryOrderBy] | None,
    limit: int,
    fmt: str,
) -> dict[str, Any]:
    """Build the EMS API query request body.

    Args:
        fields: Fields to select.
        filters: Optional filter conditions.
        order_by: Optional sort order.
        limit: Maximum rows to return.
        fmt: Value format ("display" or "raw").

    Returns:
        EMS API query request body dict.
    """
    # Build select array
    select: list[dict[str, Any]] = []
    for f in fields:
        entry: dict[str, Any] = {"fieldId": f["field_id"]}
        if "alias" in f and f["alias"]:
            entry["alias"] = f["alias"]
        if "aggregate" in f and f["aggregate"]:
            entry["aggregate"] = f["aggregate"]
        select.append(entry)

    # Map format to API value
    api_format = "none" if fmt == "raw" else "display"

    body: dict[str, Any] = {
        "select": select,
        "format": api_format,
        "top": limit,
    }

    # Build filter
    if filters:
        built_filters = [_build_single_filter(f) for f in filters]
        if len(built_filters) == 1:
            body["filter"] = built_filters[0]
        else:
            body["filter"] = {
                "operator": "and",
                "args": [{"type": "filter", "value": bf} for bf in built_filters],
            }

    # Build orderBy
    if order_by:
        body["orderBy"] = []
        for ob in order_by:
            entry_ob: dict[str, Any] = {"fieldId": ob["field_id"]}
            direction = ob.get("direction", "asc")
            entry_ob["order"] = "desc" if direction == "desc" else "asc"
            body["orderBy"].append(entry_ob)

    return body


def _build_analytics_body(
    analytics: list[str],
    start_offset: float | None,
    end_offset: float | None,
    sample_rate: float,
) -> dict[str, Any]:
    """Build the EMS API analytics query request body.

    Args:
        analytics: Analytic IDs to query.
        start_offset: Start time in seconds from flight start.
        end_offset: End time in seconds from flight start.
        sample_rate: Samples per second.

    Returns:
        EMS API analytics query request body dict.
    """
    body: dict[str, Any] = {
        "select": [{"analyticId": aid} for aid in analytics],
    }

    if start_offset is not None:
        body["start"] = start_offset
    if end_offset is not None:
        body["end"] = end_offset

    if start_offset is not None and end_offset is not None:
        size = int((end_offset - start_offset) * sample_rate)
        if size > 0:
            body["size"] = size
    else:
        # Always include a size to ensure the API returns data even without
        # explicit time range boundaries
        body["size"] = 5000

    return body


def _format_query_results(
    response: dict[str, Any],
    fields: list[QueryField],
) -> str:
    """Format database query results as a fixed-width text table.

    Args:
        response: EMS API query response.
        fields: The fields that were queried (for alias support).

    Returns:
        Formatted table string.
    """
    rows = response.get("rows", [])
    headers_raw = response.get("header", [])

    if not rows:
        return "Query returned 0 rows."

    # Build column names: use alias if provided, otherwise header from response
    col_names: list[str] = []
    for i, h in enumerate(headers_raw):
        if i < len(fields) and "alias" in fields[i] and fields[i]["alias"]:
            col_names.append(fields[i]["alias"])
        else:
            col_names.append(h.get("name", f"Column {i}") if isinstance(h, dict) else str(h))

    # Convert cell values to strings, handling None/NULL
    str_rows: list[list[str]] = []
    for row in rows:
        str_row: list[str] = []
        for cell in row:
            if cell is None:
                str_row.append("NULL")
            else:
                s = str(cell)
                if len(s) > 40:
                    s = s[:37] + "..."
                str_row.append(s)
        str_rows.append(str_row)

    # Calculate column widths (cap at 40)
    col_widths: list[int] = []
    for i, name in enumerate(col_names):
        max_w = min(len(name), 40)
        for row in str_rows:
            if i < len(row):
                max_w = max(max_w, min(len(row[i]), 40))
        col_widths.append(max_w)

    # Build table
    lines: list[str] = []

    # Header
    header_line = " | ".join(name.ljust(col_widths[i]) for i, name in enumerate(col_names))
    lines.append(header_line)

    # Separator
    sep_line = "-+-".join("-" * w for w in col_widths)
    lines.append(sep_line)

    # Data rows
    for row in str_rows:
        cells: list[str] = []
        for i, w in enumerate(col_widths):
            val = row[i] if i < len(row) else ""
            cells.append(val.ljust(w))
        lines.append(" | ".join(cells))

    lines.append(f"\n({len(rows)} row(s) returned)")

    return "\n".join(lines)


def _format_analytics_results(
    results: list[dict[str, Any]],
    max_rows_per_flight: int = 200,
    analytic_names: list[str] | None = None,
) -> str:
    """Format analytics query results as per-flight time-series tables.

    The EMS API returns analytics in the format:
        {"offsets": [0, 1, 2, ...], "results": [{"analyticId": "...", "values": [...]}, ...]}

    Args:
        results: List of per-flight result dicts with flight_id, data or error.
        max_rows_per_flight: Maximum display rows per flight.
        analytic_names: Optional display names for analytics columns. When
            provided, these are used as column headers instead of raw analytic IDs.

    Returns:
        Formatted multi-flight analytics output.
    """
    if not results:
        return "No analytics results."

    sections: list[str] = []
    error_count = 0

    for r in results:
        flight_id = r.get("flight_id", "?")
        section_lines: list[str] = [f"=== Flight {flight_id} ==="]

        if "error" in r:
            section_lines.append(f"Error: {r['error']}")
            error_count += 1
            sections.append("\n".join(section_lines))
            continue

        data = r.get("data", {})
        offsets = data.get("offsets", [])
        analytic_results = data.get("results", [])

        if not offsets:
            section_lines.append("No data returned.")
            sections.append("\n".join(section_lines))
            continue

        # Column names: Offset + display names (or formatted IDs as fallback)
        col_names = ["Offset"]
        for i, ar in enumerate(analytic_results):
            if analytic_names and i < len(analytic_names):
                col_names.append(analytic_names[i])
            else:
                raw_id = str(ar.get("analyticId", f"Analytic_{i}"))
                col_names.append(_format_analytic_header(raw_id))

        # Build rows: each row is [offset, value1, value2, ...]
        data_rows: list[list[Any]] = []
        for i, offset in enumerate(offsets):
            row: list[Any] = [offset]
            for ar in analytic_results:
                values = ar.get("values", [])
                row.append(values[i] if i < len(values) else None)
            data_rows.append(row)

        # Check for suspicious all-zero data (possible invalid flight ID)
        total_rows = len(data_rows)
        if total_rows >= 100 and analytic_results:
            all_zero = True
            for ar in analytic_results:
                values = ar.get("values", [])
                if any(v != 0.0 and v is not None for v in values):
                    all_zero = False
                    break
            if all_zero:
                section_lines.append(
                    "WARNING: All analytic values are 0.0. This may indicate "
                    "an invalid flight ID. Verify the flight ID using query_database."
                )

        # Convert to string rows
        display_rows = data_rows[:max_rows_per_flight]
        str_rows: list[list[str]] = []
        for row in display_rows:
            str_row: list[str] = []
            for cell in row:
                if cell is None:
                    str_row.append("NULL")
                else:
                    str_row.append(str(cell))
            str_rows.append(str_row)

        # Calculate column widths
        col_widths: list[int] = [len(n) for n in col_names]
        for row in str_rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(cell))

        # Cap widths at 40
        col_widths = [min(w, 40) for w in col_widths]

        # Header
        header_line = " | ".join(col_names[i].rjust(col_widths[i]) for i in range(len(col_names)))
        section_lines.append(header_line)

        sep_line = "-+-".join("-" * w for w in col_widths)
        section_lines.append(sep_line)

        # Data rows (right-aligned for numeric data)
        for row in str_rows:
            cells: list[str] = []
            for i, w in enumerate(col_widths):
                val = row[i] if i < len(row) else ""
                cells.append(val.rjust(w))
            section_lines.append(" | ".join(cells))

        if total_rows > max_rows_per_flight:
            section_lines.append(
                f"... ({total_rows - max_rows_per_flight} more rows, " f"{total_rows} total)"
            )
        else:
            section_lines.append(f"({total_rows} row(s))")

        sections.append("\n".join(section_lines))

    output = "\n\n".join(sections)

    if error_count > 0:
        output += f"\n\n({error_count} flight(s) had errors)"

    return output


@mcp.tool
async def query_database(
    ems_system_id: int,
    database_id: str,
    fields: list[QueryField],
    filters: list[QueryFilter] | None = None,
    order_by: list[QueryOrderBy] | None = None,
    limit: int = 100,
    format: str = "display",
) -> str:
    """Execute a database query to retrieve flight records.

    Query the Flight Data Warehouse or other databases. Field IDs must be
    discovered first using search_fields or list_fields.

    Supports server-side aggregation: add an "aggregate" key to any field
    (avg, count, max, min, stdev, sum, var). Fields without an aggregate
    act as implicit GROUP BY columns. This avoids downloading thousands of
    rows for statistical summaries.

    Discrete field filters accept string labels (e.g. "DHC-8-400") which
    are automatically resolved to numeric codes via the field metadata API.

    Args:
        ems_system_id: The EMS system ID (from list_ems_systems).
        database_id: Database ID (typically FDW Flights, from list_databases).
        fields: Fields to retrieve. Each must have a field_id and optional alias
            and optional aggregate (avg, count, max, min, stdev, sum, var).
        filters: Optional filter conditions combined with AND. Each has field_id,
            operator (equal, notEqual, greaterThan, greaterThanOrEqual, lessThan,
            lessThanOrEqual, in, isNull, isNotNull, like, between), and value.
            String values for discrete fields are auto-resolved to numeric codes.
        order_by: Optional sort order. Each has field_id and optional direction (asc/desc).
        limit: Maximum rows to return (1-10000, default: 100).
        format: Value format - 'display' for human-readable labels (default),
            'raw' for numeric codes.

    Returns:
        Query results as a formatted text table with column headers and row count.
    """
    # Validate inputs
    if not fields:
        return "Error: At least one field is required. Use search_fields to find field IDs."

    if limit < 1 or limit > 10000:
        return "Error: limit must be between 1 and 10000."

    if format not in ("display", "raw"):
        return "Error: format must be 'display' or 'raw'."

    # Validate aggregate values
    for f in fields:
        agg = f.get("aggregate")
        if agg and agg not in VALID_AGGREGATES:
            return (
                f"Error: Invalid aggregate '{agg}'. "
                f"Valid aggregates: {', '.join(sorted(VALID_AGGREGATES))}"
            )

    # Validate filter operators
    if filters:
        for f in filters:
            if f["operator"] not in VALID_OPERATORS:
                return (
                    f"Error: Invalid filter operator '{f['operator']}'. "
                    f"Valid operators: {', '.join(sorted(VALID_OPERATORS))}"
                )

    # Resolve discrete filter values (string labels -> numeric codes)
    if filters:
        try:
            filters = await _resolve_filters(filters, ems_system_id, database_id)
        except ValueError as e:
            return f"Error resolving filter value: {e}"

    # Build query body
    try:
        body = _build_query_body(fields, filters, order_by, limit, format)
    except ValueError as e:
        return f"Error building query: {e}"

    client = get_client()
    path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/query"

    try:
        response = await client.post(path, json=body)
        return _format_query_results(response, fields)
    except EMSNotFoundError:
        return (
            f"Error: Database or system not found. "
            f"Verify ems_system_id={ems_system_id} and database_id='{database_id}'. "
            "Use list_databases to find valid database IDs."
        )
    except EMSAPIError as e:
        if e.status_code == 400:
            return (
                f"Error: Bad query request - {e.message}. "
                "Check that field IDs are valid (use search_fields) and "
                "filter values match field types (use get_field_info for discrete mappings)."
            )
        return f"Error executing query: {e.message}"


@mcp.tool
async def query_flight_analytics(
    ems_system_id: int,
    flight_ids: list[int],
    analytics: list[str],
    start_offset: float | None = None,
    end_offset: float | None = None,
    sample_rate: float = 1.0,
) -> str:
    """Query time-series analytics data for one or more flights.

    Retrieves time-series data (altitude, airspeed, etc.) for specific flights.
    Flight IDs come from query_database. Analytics can be specified as either
    human-readable names (e.g. "Airspeed") or raw analytic IDs from search_analytics.
    When names are used, they are resolved automatically and displayed as column headers.

    Args:
        ems_system_id: The EMS system ID (from list_ems_systems).
        flight_ids: Flight record IDs to query (max 10, from query_database).
        analytics: Analytic names or IDs to retrieve (max 20). Accepts human-readable
            names like "Airspeed" or raw IDs from search_analytics.
        start_offset: Optional start time in seconds from flight start.
        end_offset: Optional end time in seconds from flight start.
        sample_rate: Samples per second (default: 1.0, must be > 0).

    Returns:
        Time-series data formatted as per-flight tables with offset and analytic columns.
    """
    # Validate inputs
    if not flight_ids:
        return "Error: At least one flight_id is required. Use query_database to find flight IDs."

    if len(flight_ids) > 10:
        return "Error: Maximum 10 flight IDs per request to prevent timeouts."

    if not analytics:
        return (
            "Error: At least one analytic is required. Use search_analytics to find "
            "analytic names, or pass human-readable names like 'Airspeed'."
        )

    if len(analytics) > 20:
        return "Error: Maximum 20 analytics per request to prevent timeouts."

    if sample_rate <= 0:
        return "Error: sample_rate must be greater than 0."

    if start_offset is not None and end_offset is not None and start_offset >= end_offset:
        return "Error: start_offset must be less than end_offset."

    # Resolve analytic names to IDs
    try:
        resolved = await _resolve_analytics(analytics, ems_system_id)
    except ValueError as e:
        return f"Error resolving analytics: {e}"
    except EMSAPIError as e:
        return f"Error searching analytics API: {e.message}"

    display_names = [name for name, _ in resolved]
    analytic_ids = [aid for _, aid in resolved]

    body = _build_analytics_body(analytic_ids, start_offset, end_offset, sample_rate)
    client = get_client()

    results: list[dict[str, Any]] = []

    for fid in flight_ids:
        path = f"/api/v2/ems-systems/{ems_system_id}/flights/{fid}/analytics/query"
        try:
            data = await client.post(path, json=body)
            results.append({"flight_id": fid, "data": data})
        except EMSNotFoundError:
            results.append(
                {
                    "flight_id": fid,
                    "error": f"Flight {fid} not found in EMS system {ems_system_id}.",
                }
            )
        except EMSAPIError as e:
            results.append(
                {
                    "flight_id": fid,
                    "error": f"API error: {e.message}",
                }
            )

    # If all flights failed, mention it prominently
    if all("error" in r for r in results):
        formatted = _format_analytics_results(results, analytic_names=display_names)
        return (
            f"All {len(flight_ids)} flight(s) failed. "
            "Verify flight IDs (from query_database) and analytic IDs (from search_analytics).\n\n"
            + formatted
        )

    return _format_analytics_results(results, analytic_names=display_names)
