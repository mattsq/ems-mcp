"""Query tools for EMS MCP server.

These tools enable LLMs to query flight records from the Flight Data Warehouse
and retrieve time-series analytics data for individual flights.
"""

import csv
import io
import json
import logging
import re
from typing import Any, Literal, NotRequired, TypedDict

from fastmcp import Context

from ems_mcp.api.client import EMSAPIError, EMSNotFoundError
from ems_mcp.cache import field_cache, make_cache_key
from ems_mcp.server import get_client, mcp
from ems_mcp.tools.discovery import (
    _get_stored_result,
    _lookup_stored_field,
    _parse_ref_number,
    _rank_by_name,
    _resolve_database_id,
    _resolve_field_id,
    _result_store,
)

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

        # [N] reference (or bare digits) from search_analytics /
        # find_physical_params. Physical-parameter names cannot be resolved via
        # the global analytics search (they are flight-scoped), so the [N] ref
        # is the only way to carry them into a query -- resolve it from the
        # store before anything else.
        ref_num = _parse_ref_number(item)
        if ref_num is not None:
            entry = _get_stored_result(ref_num)
            if entry is not None and entry.get("type") == "analytic":
                results.append((entry.get("name", item), str(entry["id"])))
                continue
            if entry is not None:
                raise ValueError(
                    f"Reference [{ref_num}] ('{entry.get('name')}') is a "
                    f"{entry.get('type')}, not an analytic. Use search_analytics "
                    "or find_physical_params to get an analytic reference."
                )
            raise ValueError(
                f"Reference [{ref_num}] is not a known analytic. Use "
                "search_analytics or find_physical_params to get a valid "
                "reference."
            )

        if _is_analytic_id(item):
            results.append((item, item))
            continue

        # A name already returned by search_analytics / find_physical_params
        # this session. Physical-parameter names are flight-scoped and absent
        # from the global search, so this is the only way they resolve by name.
        stored = [
            e for e in _result_store.values()
            if e.get("type") == "analytic"
            and e.get("ems_system_id") == ems_system_id
            and str(e.get("name", "")).lower() == item.lower()
        ]
        if len({str(e["id"]) for e in stored}) == 1:
            results.append((stored[0]["name"], str(stored[0]["id"])))
            continue

        # Check cache first. The cache stores [name, id] as a list because the
        # SQLite cache backend serializes via JSON (which has no tuple type);
        # convert back to a tuple here so the in-memory return shape stays
        # consistent with the type annotation.
        cache_key = make_cache_key("analytic_resolve", ems_system_id, item.lower())
        cached = await field_cache.get(cache_key)
        if cached is not None:
            results.append(tuple(cached))
            continue

        # Search the analytics API
        path = f"/api/v2/ems-systems/{ems_system_id}/analytics"
        params = {"text": item}
        search_results = await client.get(path, params=params)

        if not search_results:
            raise ValueError(
                f"Analytic not found: '{item}'. Use search_analytics to find valid "
                "analytic names. Raw recorder parameters are not in the global "
                "search -- use find_physical_params(flight_id=...) and pass its [N]."
            )

        # Only an exact (case-insensitive) name match is used automatically;
        # a lone fuzzy hit could be a different parameter entirely.
        exact_matches = [
            a for a in search_results
            if a.get("name", "").lower() == item.lower()
        ]
        if len(exact_matches) == 1:
            pair = (exact_matches[0]["name"], exact_matches[0]["id"])
            await field_cache.set(cache_key, list(pair))
            results.append(pair)
            continue

        ranked = _rank_by_name(search_results, item)
        match_names = [a.get("name", "?") for a in ranked[:8]]
        raise ValueError(
            f"Analytic name '{item}' is not an exact match. Closest names: "
            f"{'; '.join(match_names)}{' ...' if len(ranked) > 8 else ''}. "
            "Pass one of these exactly, or an [N] from search_analytics. Raw "
            "recorder parameters need find_physical_params(flight_id=...) instead."
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

    field_id: str | int
    alias: NotRequired[str]
    aggregate: NotRequired[
        Literal["avg", "count", "max", "min", "stdev", "sum", "var"]
    ]


class QueryFilter(TypedDict):
    """A filter condition for a database query."""

    field_id: str | int
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

    field_id: str | int
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

    # Not found - build helpful error. Prefer labels containing the input
    # (e.g. "a321" -> every A321 fleet) over the first N alphabetically.
    field_name = meta.get("name") or field_id
    labels = [str(dv.get("label", "?")) for dv in entries]
    partial = [lab for lab in labels if value_lower in lab.lower()]
    if partial:
        shown = partial[:15]
        more = f" (and {len(partial) - 15} more)" if len(partial) > 15 else ""
        detail = f"Values containing '{value}': {'; '.join(shown)}{more}."
    else:
        more = f" (and {len(labels) - 10} more)" if len(labels) > 10 else ""
        detail = f"Available values include: {'; '.join(labels[:10])}{more}."
    raise ValueError(
        f"Discrete value '{value}' is not an exact label of field '{field_name}'. "
        f"{detail} Labels must match exactly (case-insensitive); use "
        "get_field_info to see all values, or operator 'in' for several."
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
    # Build select array with emspy-compatible structure:
    # - Every select entry gets "aggregate" (defaults to "none")
    # - Non-aggregated fields go into a top-level "groupBy" array
    has_aggregate = any(f.get("aggregate") for f in fields)
    select: list[dict[str, Any]] = []
    group_by: list[dict[str, str]] = []
    for f in fields:
        entry: dict[str, Any] = {
            "fieldId": f["field_id"],
            "aggregate": f.get("aggregate") or "none",
        }
        if "alias" in f and f["alias"]:
            entry["alias"] = f["alias"]
        select.append(entry)
        # Non-aggregated fields become groupBy entries
        if has_aggregate and not f.get("aggregate"):
            group_by.append({"fieldId": f["field_id"]})

    # Map format to API value
    api_format = "none" if fmt == "raw" else "display"

    body: dict[str, Any] = {
        "select": select,
        "format": api_format,
        "top": limit,
    }
    if group_by:
        body["groupBy"] = group_by

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


# Upper bound on samples requested per flight. EMS spaces ``size`` samples
# evenly across the requested window.
_MAX_SAMPLES: int = 5000

# Rows shown per flight before the output is truncated.
_MAX_DISPLAY_ROWS: int = 200


def _sample_count(span_seconds: float, sample_rate: float) -> int:
    """Number of samples for a window, at least 1 and at most ``_MAX_SAMPLES``."""
    return min(_MAX_SAMPLES, max(1, int(span_seconds * sample_rate)))


def _build_analytics_body(
    analytics: list[str],
    start_offset: float | None,
    end_offset: float | None,
    sample_rate: float,
    size: int | None = None,
) -> dict[str, Any]:
    """Build the EMS API analytics query request body.

    Args:
        analytics: Analytic IDs to query.
        start_offset: Start time in seconds from flight start.
        end_offset: End time in seconds from flight start.
        sample_rate: Samples per second.
        size: Explicit sample count (e.g. computed from the flight's actual
            span when an offset was omitted). When None, derived from the
            offsets, or ``_MAX_SAMPLES`` if either offset is missing.

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

    if size is None:
        if start_offset is not None and end_offset is not None:
            size = _sample_count(end_offset - start_offset, sample_rate)
        else:
            size = _MAX_SAMPLES
    body["size"] = size

    return body


async def _probe_flight_span(
    client: Any,
    path: str,
    analytic_id: str,
    start_offset: float | None,
    end_offset: float | None,
) -> tuple[float, float] | None:
    """Find the actual [start, end] offsets of a flight window.

    Asking EMS for 2 evenly spaced samples returns the window's first and
    last offsets, which is how a missing start/end offset is filled in so
    ``sample_rate`` can be honoured for whole-flight requests.
    """
    body: dict[str, Any] = {"select": [{"analyticId": analytic_id}], "size": 2}
    if start_offset is not None:
        body["start"] = start_offset
    if end_offset is not None:
        body["end"] = end_offset
    try:
        data = await client.post(path, json=body)
    except EMSAPIError:
        return None
    offsets = data.get("offsets") or []
    if len(offsets) < 2:
        return None
    return float(offsets[0]), float(offsets[-1])


def _fmt_offset(value: Any) -> Any:
    """Round a time offset to 0.01 s for display."""
    if isinstance(value, float):
        return round(value, 2)
    return value


def _fmt_value(value: Any) -> Any:
    """Trim float noise to 6 significant figures for display."""
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return float(f"{value:.6g}")
    return value


def _truncation_note(offsets: list[Any], shown: int) -> str:
    """Explain a truncated time series and how to get the part that's missing."""
    total = len(offsets)
    first, last_shown, last = offsets[0], offsets[shown - 1], offsets[-1]
    span = float(last) - float(first)
    hint = ""
    if span > 0:
        hint = (
            f" Lower sample_rate (about {shown / span:.3g} gives ~{shown} rows over "
            "this window) or narrow start_offset/end_offset to see the rest."
        )
    return (
        f"Showing {shown} of {total} samples: offsets {_fmt_offset(first)}-"
        f"{_fmt_offset(last_shown)} s of {_fmt_offset(first)}-{_fmt_offset(last)} s.{hint}"
    )


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

    col_names = _extract_column_names(headers_raw, fields)

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


def _analytics_columns(
    analytic_results: list[dict[str, Any]],
    analytic_names: list[str] | None,
) -> list[str]:
    """Column names for analytics output: display names, else shortened IDs."""
    cols: list[str] = []
    for i, ar in enumerate(analytic_results):
        if analytic_names and i < len(analytic_names):
            cols.append(analytic_names[i])
        else:
            raw_id = str(ar.get("analyticId", f"Analytic_{i}"))
            cols.append(_format_analytic_header(raw_id))
    return cols


def _all_zero(analytic_results: list[dict[str, Any]], total_rows: int) -> bool:
    """True when a long series is entirely 0.0/None (often a bad flight ID)."""
    if total_rows < 100 or not analytic_results:
        return False
    return all(
        all(v == 0.0 or v is None for v in ar.get("values", []))
        for ar in analytic_results
    )


_ALL_ZERO_WARNING = (
    "WARNING: All analytic values are 0.0. This may indicate an invalid "
    "flight ID. Verify the flight ID using query_database."
)


def _format_analytics_results(
    results: list[dict[str, Any]],
    max_rows_per_flight: int = _MAX_DISPLAY_ROWS,
    analytic_names: list[str] | None = None,
) -> str:
    """Format analytics query results as per-flight time-series tables.

    The EMS API returns analytics in the format:
        {"offsets": [0, 1, 2, ...], "results": [{"analyticId": "...", "values": [...]}, ...]}

    Args:
        results: List of per-flight result dicts with flight_id, data or
            error, and optional ``notes`` (informational lines).
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
        section_lines.extend(f"Note: {n}" for n in r.get("notes", []))

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

        col_names = ["Offset"] + _analytics_columns(analytic_results, analytic_names)
        total_rows = len(offsets)
        if _all_zero(analytic_results, total_rows):
            section_lines.append(_ALL_ZERO_WARNING)

        shown = min(total_rows, max_rows_per_flight)
        str_rows: list[list[str]] = []
        for i in range(shown):
            row = [str(_fmt_offset(offsets[i]))]
            for ar in analytic_results:
                values = ar.get("values", [])
                v = values[i] if i < len(values) else None
                row.append("NULL" if v is None else str(_fmt_value(v)))
            str_rows.append(row)

        col_widths = [len(n) for n in col_names]
        for row in str_rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(cell))
        col_widths = [min(w, 40) for w in col_widths]

        section_lines.append(
            " | ".join(col_names[i].rjust(col_widths[i]) for i in range(len(col_names)))
        )
        section_lines.append("-+-".join("-" * w for w in col_widths))
        for row in str_rows:
            cells = [
                (row[i] if i < len(row) else "").rjust(w)
                for i, w in enumerate(col_widths)
            ]
            section_lines.append(" | ".join(cells))

        if total_rows > shown:
            section_lines.append(_truncation_note(offsets, shown))
        else:
            section_lines.append(f"({total_rows} row(s))")

        sections.append("\n".join(section_lines))

    output = "\n\n".join(sections)

    if error_count > 0:
        output += f"\n\n({error_count} flight(s) had errors)"

    return output


def _extract_column_names(
    headers_raw: list[Any],
    fields: list[QueryField],
) -> list[str]:
    """Extract column names from response headers, using aliases where available.

    Args:
        headers_raw: Raw header list from the API response.
        fields: The fields that were queried (for alias support).

    Returns:
        List of column name strings.
    """
    col_names: list[str] = []
    for i, h in enumerate(headers_raw):
        if i < len(fields) and "alias" in fields[i] and fields[i]["alias"]:
            col_names.append(fields[i]["alias"])
        else:
            col_names.append(
                h.get("name", f"Column {i}") if isinstance(h, dict) else str(h)
            )
    return col_names


def _format_query_results_csv(
    response: dict[str, Any],
    fields: list[QueryField],
) -> str:
    """Format database query results as CSV.

    Args:
        response: EMS API query response.
        fields: The fields that were queried (for alias support).

    Returns:
        CSV-formatted string.
    """
    rows = response.get("rows", [])
    headers_raw = response.get("header", [])

    if not rows:
        return "Query returned 0 rows."

    col_names = _extract_column_names(headers_raw, fields)

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])

    output.write(f"\n({len(rows)} row(s) returned)")
    return output.getvalue()


def _format_query_results_json(
    response: dict[str, Any],
    fields: list[QueryField],
) -> str:
    """Format database query results as compact JSON.

    Args:
        response: EMS API query response.
        fields: The fields that were queried (for alias support).

    Returns:
        JSON-formatted string with columns, rows, and row_count.
    """
    rows = response.get("rows", [])
    headers_raw = response.get("header", [])

    if not rows:
        return '{"columns":[],"rows":[],"row_count":0}'

    col_names = _extract_column_names(headers_raw, fields)

    row_dicts = []
    for row in rows:
        row_dict: dict[str, Any] = {}
        for i, col in enumerate(col_names):
            row_dict[col] = row[i] if i < len(row) else None
        row_dicts.append(row_dict)

    result = {
        "columns": col_names,
        "rows": row_dicts,
        "row_count": len(rows),
    }
    return json.dumps(result, separators=(",", ":"))


def _format_analytics_results_csv(
    results: list[dict[str, Any]],
    max_rows_per_flight: int = _MAX_DISPLAY_ROWS,
    analytic_names: list[str] | None = None,
) -> str:
    """Format analytics results as CSV with per-flight comment headers.

    Args:
        results: List of per-flight result dicts.
        max_rows_per_flight: Maximum display rows per flight.
        analytic_names: Optional display names for analytics columns.

    Returns:
        CSV-formatted string.
    """
    if not results:
        return "No analytics results."

    output = io.StringIO()
    # "\n" (not csv's default "\r\n") so data rows match the comment lines.
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")

    for r in results:
        flight_id = r.get("flight_id", "?")
        output.write(f"# Flight {flight_id}\n")
        for note in r.get("notes", []):
            output.write(f"# Note: {note}\n")

        if "error" in r:
            output.write(f"# Error: {r['error']}\n")
            continue

        data = r.get("data", {})
        offsets = data.get("offsets", [])
        analytic_results = data.get("results", [])

        if not offsets:
            output.write("# No data returned.\n")
            continue

        writer.writerow(["Offset"] + _analytics_columns(analytic_results, analytic_names))

        total_rows = len(offsets)
        if _all_zero(analytic_results, total_rows):
            output.write(f"# {_ALL_ZERO_WARNING}\n")

        shown = min(total_rows, max_rows_per_flight)
        for i in range(shown):
            row: list[Any] = [_fmt_offset(offsets[i])]
            for ar in analytic_results:
                values = ar.get("values", [])
                v = values[i] if i < len(values) else None
                row.append("" if v is None else _fmt_value(v))
            writer.writerow(row)

        if total_rows > shown:
            output.write(f"# {_truncation_note(offsets, shown)}\n")
        else:
            output.write(f"# ({total_rows} row(s))\n")

    return output.getvalue()


def _format_analytics_results_json(
    results: list[dict[str, Any]],
    max_rows_per_flight: int = _MAX_DISPLAY_ROWS,
    analytic_names: list[str] | None = None,
) -> str:
    """Format analytics results as compact JSON.

    Args:
        results: List of per-flight result dicts.
        max_rows_per_flight: Maximum display rows per flight.
        analytic_names: Optional display names for analytics columns.

    Returns:
        JSON-formatted string with flights and warnings.
    """
    if not results:
        return '{"flights":[],"warnings":[]}'

    flights_out: list[dict[str, Any]] = []
    warnings: list[str] = []

    for r in results:
        flight_id = r.get("flight_id", "?")
        warnings.extend(f"Flight {flight_id}: {n}" for n in r.get("notes", []))

        if "error" in r:
            flights_out.append({"flight_id": flight_id, "error": r["error"]})
            continue

        data = r.get("data", {})
        offsets = data.get("offsets", [])
        analytic_results = data.get("results", [])

        if not offsets:
            flights_out.append({"flight_id": flight_id, "rows": [], "row_count": 0})
            continue

        col_names = _analytics_columns(analytic_results, analytic_names)
        total_rows = len(offsets)
        if _all_zero(analytic_results, total_rows):
            warnings.append(f"Flight {flight_id}: {_ALL_ZERO_WARNING}")

        shown = min(total_rows, max_rows_per_flight)
        row_dicts = []
        for i in range(shown):
            row_dict: dict[str, Any] = {"Offset": _fmt_offset(offsets[i])}
            for j, ar in enumerate(analytic_results):
                values = ar.get("values", [])
                col = col_names[j] if j < len(col_names) else f"Analytic_{j}"
                row_dict[col] = _fmt_value(values[i]) if i < len(values) else None
            row_dicts.append(row_dict)

        flight_out: dict[str, Any] = {
            "flight_id": flight_id,
            "rows": row_dicts,
            "row_count": total_rows,
        }
        if total_rows > shown:
            flight_out["truncated"] = _truncation_note(offsets, shown)
        flights_out.append(flight_out)

    result = {"flights": flights_out, "warnings": warnings}
    return json.dumps(result, separators=(",", ":"))


# Day/month/year-style dates ("02/11/2026", "2-11-26"). EMS parses these as
# US month/day, which silently swaps day and month for Australian input.
_NON_ISO_DATE = re.compile(r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(\s|T|$)")


def _describe_filter(f: Any) -> str:
    """Short human-readable form of a filter, e.g. "Takeoff Valid = True"."""
    op_symbols = {
        "equal": "=", "notEqual": "!=", "greaterThan": ">",
        "greaterThanOrEqual": ">=", "lessThan": "<", "lessThanOrEqual": "<=",
    }
    op = f.get("operator", "equal")
    symbol = op_symbols.get(op, op)
    if op in UNARY_OPERATORS:
        return f"{f.get('field_id')} {op}"
    return f"{f.get('field_id')} {symbol} {f.get('value')}"


# The "[N]" display form of a reference (as opposed to a bare number).
_BRACKETED_REF = re.compile(r"^\s*\[\d+\]\s*$")


def _event_value_entry(ref: Any) -> dict[str, Any] | None:
    """Stored entry for an event-value [N] ref from find_event_types, else None."""
    ref_num = _parse_ref_number(ref)
    if ref_num is None:
        return None
    entry = _get_stored_result(ref_num)
    if entry is not None and entry.get("type") == "event-value":
        return entry
    return None


def _event_code(entry: dict[str, Any]) -> Any:
    """Numeric Event Type code stored on an event-value entry."""
    code = entry["id"]
    try:
        return int(code)
    except (TypeError, ValueError):
        return code


def _normalize_filters(
    filters: list[QueryFilter],
    ems_system_id: int,
    database_id: str,
) -> list[QueryFilter]:
    """Validate and normalise filters before field resolution.

    * Event-value [N] refs (from find_event_types) expand: as ``field_id``
      they become the profile's Event Type field, and as values -- only in
      the explicit "[N]" string form -- they become the event's numeric
      code. Value refs must come from the database being queried.
    * ``in`` with a scalar value is wrapped into a one-item list.
    * Non-ISO dates are rejected, because EMS would read 02/11/2026 as
      11 Feb rather than 2 Nov.

    Raises:
        ValueError: On a non-ISO date or an event ref from another database.
    """
    normalized: list[QueryFilter] = []
    for f in filters:
        new_f: dict[str, Any] = dict(f)
        op = new_f["operator"]

        field_entry = _event_value_entry(new_f.get("field_id"))
        if field_entry is not None:
            if field_entry.get("database_id") != database_id:
                raise ValueError(
                    f"event reference {new_f['field_id']} ('{field_entry['name']}') belongs "
                    "to a different profile database. Query the database shown next "
                    "to it in find_event_types (database_id=\"[N]\")."
                )
            if not field_entry.get("event_type_field_id"):
                raise ValueError(
                    f"event reference {new_f['field_id']} has no Event Type field "
                    "recorded; re-run find_event_types."
                )
            new_f["field_id"] = field_entry["event_type_field_id"]
            if "value" not in new_f or new_f["value"] is None:
                new_f["value"] = (
                    [_event_code(field_entry)] if op == "in" else _event_code(field_entry)
                )

        value = new_f.get("value")
        if op == "in" and value is not None and not isinstance(value, (list, tuple)):
            value = [value]

        def _convert(v: Any) -> Any:
            # Only the explicit "[N]" form counts as an event ref in a value:
            # a bare "1" is an ordinary literal (e.g. Flight Number == "1")
            # and must never be swapped for whatever ref [1] happens to be.
            is_bracketed = isinstance(v, str) and _BRACKETED_REF.match(v) is not None
            entry = _event_value_entry(v) if is_bracketed else None
            if entry is not None:
                # A code is only meaningful within its own profile's Event Type
                # field; the same number can be a different event elsewhere.
                if entry.get("ems_system_id") != ems_system_id or (
                    entry.get("database_id") != database_id
                ):
                    raise ValueError(
                        f"event reference {v} ('{entry['name']}') belongs to a "
                        "different profile database. Use event refs from the "
                        "profile you are querying (see find_event_types)."
                    )
                if field_entry is not None and (
                    entry.get("event_type_field_id") != field_entry.get("event_type_field_id")
                ):
                    raise ValueError(
                        f"event reference {v} ('{entry['name']}') is for a different "
                        f"Event Type field than {f.get('field_id')}."
                    )
                return _event_code(entry)
            if isinstance(v, str) and _NON_ISO_DATE.match(v):
                raise ValueError(
                    f"ambiguous date {v!r}. Use ISO format YYYY-MM-DD (optionally "
                    "with a time, e.g. 2026-11-02T06:00:00) -- EMS reads slash dates "
                    "as month/day."
                )
            return v

        if isinstance(value, (list, tuple)):
            value = [_convert(v) for v in value]
        elif value is not None:
            value = _convert(value)
        if "value" in new_f or value is not None:
            new_f["value"] = value
        normalized.append(new_f)  # type: ignore[arg-type]
    return normalized


@mcp.tool
async def query_database(
    ems_system_id: int,
    database_id: str,
    fields: list[QueryField],
    filters: list[QueryFilter] | None = None,
    order_by: list[QueryOrderBy] | None = None,
    limit: int = 100,
    format: str = "display",
    output_format: str = "table",
    apply_best_practice_filters: bool = True,
    ctx: Context | None = None,
) -> str:
    """Query flight records from a database.

    Accepts field names (e.g. "Flight Date"), [N] reference numbers from
    find_fields, or raw bracket-encoded IDs. Database names (e.g. "FDW Flights")
    are also resolved automatically.

    Supports aggregation (avg/count/max/min/stdev/sum/var) and discrete filter
    auto-resolution (string labels resolved to numeric codes automatically).

    Best-practice filters are applied automatically (apply_best_practice_filters=True
    by default): FDW Flights gets Takeoff Valid == true AND Landing Valid == true;
    profile fields get Processing State == "Succeeded"; profile event fields also
    get False Positive == "Not a False Positive". User-provided filters on the
    same field always take precedence over auto-suggested ones. The filters
    actually applied are listed at the end of the result. Set
    apply_best_practice_filters=False to disable (e.g. when explicitly
    querying invalid flights).

    Field names must match exactly; a near-miss returns the closest fields
    as [N] refs rather than silently querying a different field.

    Args:
        ems_system_id: EMS system ID.
        database_id: Database name (e.g. "FDW Flights"), ID, or an event-profile
            [N] ref from list_event_profiles / find_event_types.
        fields: Fields to retrieve. Each has field_id (exact name, [N] ref, or
            bracket ID), optional alias, optional aggregate.
        filters: Filter conditions (AND-combined). Each has field_id, operator
            (equal/notEqual/greaterThan/lessThan/between/in/like/isNull/etc.), value.
            Dates must be ISO (e.g. "2026-11-02"); 'between' takes [min, max];
            'in' takes a list. An event [N] ref from find_event_types can be the
            field_id (expands to Event Type == that event).
        order_by: Sort order. Each has field_id, optional direction (asc/desc).
        limit: Max rows (1-10000, default: 100).
        format: 'display' (human-readable, default) or 'raw' (numeric codes).
        output_format: 'table' (default), 'csv' (compact), or 'json' (structured).
        apply_best_practice_filters: Auto-apply EMS data quality filters (default:
            True). User filters always override auto-suggested ones.

    Returns:
        Results in the requested output format.
    """
    # Validate inputs
    if not fields:
        return "Error: At least one field is required. Use find_fields to discover field IDs."

    if limit < 1 or limit > 10000:
        return "Error: limit must be between 1 and 10000."

    if format not in ("display", "raw"):
        return "Error: format must be 'display' or 'raw'."

    if output_format not in ("table", "csv", "json"):
        return "Error: output_format must be 'table', 'csv', or 'json'."

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

    # Resolve database name -> ID
    try:
        database_id = await _resolve_database_id(database_id, ems_system_id)
    except ValueError as e:
        return f"Error resolving database: {e}"

    if filters:
        try:
            filters = _normalize_filters(filters, ems_system_id, database_id)
        except ValueError as e:
            return f"Error in filters: {e}"

    # Auto-apply best-practice filters unless the caller opted out.
    # We do this before field resolution so the original [N] references and
    # field names are still available for result-store lookups.
    bp_count = 0
    if apply_best_practice_filters:
        field_refs = [f["field_id"] for f in fields]
        bp_bundle, _ = _compute_best_practice_filter_bundle(
            ems_system_id, database_id, field_refs, filters
        )
        if bp_bundle:
            # Prepend best-practice filters; user filters follow so they
            # always override auto-suggested ones for the same field.
            bp_count = len(bp_bundle)
            filters = bp_bundle + list(filters or [])  # type: ignore[assignment]
    auto_filter_labels = [
        _describe_filter(f) for f in (filters or [])[:bp_count]
    ]

    # Resolve field references -> opaque IDs
    try:
        resolved_fields: list[QueryField] = []
        for f in fields:
            resolved_id = await _resolve_field_id(
                f["field_id"], ems_system_id, database_id
            )
            resolved_fields.append({**f, "field_id": resolved_id})
        fields = resolved_fields
    except (ValueError, EMSAPIError) as e:
        return f"Error resolving field: {e}"

    # Resolve field references in filters
    if filters:
        try:
            resolved_filters: list[QueryFilter] = []
            for flt in filters:
                resolved_id = await _resolve_field_id(
                    flt["field_id"], ems_system_id, database_id
                )
                resolved_filters.append({**flt, "field_id": resolved_id})
        except (ValueError, EMSAPIError) as e:
            return f"Error resolving filter field: {e}"
        # The name-based skip in the bundle cannot see a user filter given as
        # an [N] ref or raw ID for the same field, so drop any auto filter
        # whose resolved field the user also filtered on -- user choice wins.
        user_ids = {f["field_id"] for f in resolved_filters[bp_count:]}
        kept_auto = [
            (label, flt)
            for label, flt in zip(auto_filter_labels, resolved_filters[:bp_count], strict=True)
            if flt["field_id"] not in user_ids
        ]
        auto_filter_labels = [label for label, _ in kept_auto]
        filters = [flt for _, flt in kept_auto] + resolved_filters[bp_count:]

    # Resolve field references in order_by
    if order_by:
        try:
            resolved_order: list[QueryOrderBy] = []
            for ob in order_by:
                resolved_id = await _resolve_field_id(
                    ob["field_id"], ems_system_id, database_id
                )
                resolved_order.append({**ob, "field_id": resolved_id})
            order_by = resolved_order
        except (ValueError, EMSAPIError) as e:
            return f"Error resolving order_by field: {e}"

    # Resolve discrete filter values (string labels -> numeric codes)
    if filters:
        if ctx:
            await ctx.report_progress(1, 3, "Resolving filter values...")
        try:
            filters = await _resolve_filters(filters, ems_system_id, database_id)
        except ValueError as e:
            return f"Error resolving filter value: {e}"

    # Build query body
    if ctx:
        await ctx.report_progress(2, 3, "Executing query...")
    try:
        body = _build_query_body(fields, filters, order_by, limit, format)
    except ValueError as e:
        return f"Error building query: {e}"

    client = get_client()
    path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/query"

    try:
        if ctx:
            await ctx.info(
                f"Querying database {database_id} with {len(fields)} field(s), "
                f"limit={limit}",
                logger_name="ems_mcp.query",
            )
        response = await client.post(path, json=body)
        if ctx:
            row_count = len(response.get("rows", []))
            await ctx.report_progress(3, 3, f"Formatting {row_count} rows...")

        if output_format == "json":
            payload = json.loads(_format_query_results_json(response, fields))
            if auto_filter_labels:
                payload["auto_filters"] = auto_filter_labels
            return json.dumps(payload, separators=(",", ":"))
        if output_format == "csv":
            formatted = _format_query_results_csv(response, fields)
        else:
            formatted = _format_query_results(response, fields)
        if auto_filter_labels:
            formatted += (
                f"\n(Auto-applied data-quality filters: {'; '.join(auto_filter_labels)}. "
                "Pass apply_best_practice_filters=False to include excluded records.)"
            )
        return formatted
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
                "Check that field IDs are valid (use find_fields) and "
                "filter values match field types (use get_field_info for discrete mappings)."
            )
        return f"Error executing query: {e.message}"


def _has_no_values(data: dict[str, Any]) -> bool:
    """True when an analytics response has no offsets or only None values."""
    if not data.get("offsets"):
        return True
    return all(
        all(v is None for v in ar.get("values", []))
        for ar in data.get("results", [])
    )


@mcp.tool
async def query_flight_analytics(
    ems_system_id: int,
    flight_ids: list[int],
    analytics: list[str],
    start_offset: float | None = None,
    end_offset: float | None = None,
    sample_rate: float = 1.0,
    output_format: str = "csv",
    ctx: Context | None = None,
) -> str:
    """Get time-series data (altitude, airspeed, etc.) for specific flights.

    Flight IDs come from query_database ('Flight Record' field). Analytics are
    given as exact names (e.g. "Pressure Altitude (ft)"), [N] references from
    search_analytics / find_physical_params, or raw IDs. Names must match
    exactly -- generic names like "Altitude" return the closest candidates
    instead of guessing. Raw recorder (physical) parameters only resolve via
    their [N] from find_physical_params.

    Without start_offset/end_offset the whole flight is sampled at
    sample_rate (capped at 5000 samples). At most 200 rows per flight are
    displayed; the output says which offsets were shown and how to get the
    rest.

    Args:
        ems_system_id: EMS system ID.
        flight_ids: Flight record IDs (max 10, from query_database).
        analytics: Analytic names, [N] refs, or IDs (max 20), e.g.
            ["Pressure Altitude (ft)", "Airspeed (Calibrated; 1 or only) (knots)"].
        start_offset: Start time in seconds from flight start (optional).
        end_offset: End time in seconds from flight start (optional).
        sample_rate: Samples per second (default: 1.0). For a whole-flight
            overview, ~0.05 gives about one sample every 20 s.
        output_format: 'csv' (default, compact), 'table' (human-readable), or
            'json' (structured).

    Returns:
        Per-flight time-series data in the requested output format.
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

    if output_format not in ("table", "csv", "json"):
        return "Error: output_format must be 'table', 'csv', or 'json'."

    if start_offset is not None and end_offset is not None and start_offset >= end_offset:
        return "Error: start_offset must be less than end_offset."

    total_steps = len(flight_ids) + 1  # 1 for resolution, N for flights

    # Resolve analytic names to IDs
    if ctx:
        await ctx.report_progress(0, total_steps, "Resolving analytic names...")
    try:
        resolved = await _resolve_analytics(analytics, ems_system_id)
    except ValueError as e:
        return f"Error resolving analytics: {e}"
    except EMSAPIError as e:
        return f"Error searching analytics API: {e.message}"

    display_names = [name for name, _ in resolved]
    analytic_ids = [aid for _, aid in resolved]

    # Physical-parameter refs remember the flight(s) they were discovered on;
    # used below to explain empty results on a flight with a different frame.
    ref_origins: list[tuple[str, list[int]]] = []
    for item in analytics:
        ref_num = _parse_ref_number(item)
        entry = _get_stored_result(ref_num) if ref_num is not None else None
        if entry is not None and entry.get("flight_ids"):
            ref_origins.append((f"[{ref_num}]", list(entry["flight_ids"])))

    client = get_client()

    results: list[dict[str, Any]] = []

    for i, fid in enumerate(flight_ids):
        if ctx:
            await ctx.report_progress(
                i + 1, total_steps,
                f"Querying flight {fid} ({i + 1}/{len(flight_ids)})...",
            )
        path = f"/api/v2/ems-systems/{ems_system_id}/flights/{fid}/analytics/query"
        notes: list[str] = []
        size: int | None = None
        if start_offset is None or end_offset is None:
            # Without both offsets EMS spreads ``size`` samples over the whole
            # window, so derive size from the real span to honour sample_rate.
            span = await _probe_flight_span(
                client, path, analytic_ids[0], start_offset, end_offset,
            )
            if span is not None:
                wanted = max(1, int((span[1] - span[0]) * sample_rate))
                size = _sample_count(span[1] - span[0], sample_rate)
                if wanted > size and span[1] > span[0]:
                    notes.append(
                        f"sample_rate {sample_rate} over {span[1] - span[0]:.0f} s would need "
                        f"{wanted} samples; capped at {size} "
                        f"(~{size / (span[1] - span[0]):.3g} samples/s)."
                    )
        body = _build_analytics_body(
            analytic_ids, start_offset, end_offset, sample_rate, size=size,
        )
        try:
            data = await client.post(path, json=body)
            entry_out: dict[str, Any] = {"flight_id": fid, "data": data}
        except EMSNotFoundError:
            entry_out = {
                "flight_id": fid,
                "error": (
                    f"Flight {fid} not found in EMS system {ems_system_id}. Get flight "
                    "record IDs from query_database on 'FDW Flights'."
                ),
            }
        except EMSAPIError as e:
            entry_out = {
                "flight_id": fid,
                "error": (
                    f"API error: {e.message} -- if this flight ID did not come from "
                    "query_database it may not exist (EMS reports unknown flights as "
                    "access or query failures)."
                ),
            }
        if "data" in entry_out and _has_no_values(entry_out["data"]):
            for ref, origin in ref_origins:
                if fid not in origin:
                    notes.append(
                        f"{ref} was discovered on flight {origin[0]}; it may not exist "
                        f"in this flight's recorder frame. Run "
                        f"find_physical_params(flight_id={fid}) to find its equivalent."
                    )
        if notes:
            entry_out["notes"] = notes
        results.append(entry_out)

    if ctx:
        await ctx.report_progress(total_steps, total_steps, "Formatting results...")

    # Select formatter
    if output_format == "csv":
        formatter = _format_analytics_results_csv
    elif output_format == "json":
        formatter = _format_analytics_results_json
    else:
        formatter = _format_analytics_results

    # If all flights failed, mention it prominently
    if all("error" in r for r in results):
        formatted = formatter(results, analytic_names=display_names)
        return (
            f"Error: all {len(flight_ids)} flight(s) failed. "
            "Verify flight IDs (from query_database) and analytic IDs (from search_analytics).\n\n"
            + formatted
        )

    return formatter(results, analytic_names=display_names)


# ---------------------------------------------------------------------------
# Query best-practice suggestions
# ---------------------------------------------------------------------------
#
# These constants encode generalizable EMS conventions for query filters
# that callers should apply by default. They are surfaced to the agent via
# the suggest_query_filters tool below and the QUERY BEST PRACTICES block
# in the server instructions. Override semantics: rules are skipped when
# the caller has already filtered the same field, so an explicit user
# choice (e.g. Takeoff Valid == false) is never overruled.

# Profile fields are named "P{N}: ..." in EMS deployments. Path-based
# detection (a field whose path contains "Profiles/") is preferred when
# available; the regex is the fallback when only a name is known.
_PROFILE_NAME_PREFIX = re.compile(r"^P(\d+):\s*", re.IGNORECASE)

# Canonical label spellings. Discrete-value resolution is case-insensitive,
# but we emit canonical casing so the agent's narrative reads correctly.
_PROCESSING_STATE_OK = "Succeeded"
_FALSE_POSITIVE_OK = "Not a False Positive"
_DUPLICATE_DETECTION_OK = "Not a Duplicate"

# Fleet-like fields the agent should disambiguate explicitly.
_FLEET_LIKE_FIELDS = {"Fleet", "Fleet Group", "Airline Fleet Group", "Airframe Group"}


def _profile_number(name: str) -> str | None:
    """Extract the profile prefix (e.g. ``"P437"``) from a field name.

    Returns the matched prefix (without trailing colon/space) or None.
    """
    if not name:
        return None
    m = _PROFILE_NAME_PREFIX.match(name)
    if m is None:
        return None
    return f"P{m.group(1)}"


def _is_profile_event_field(name: str | None, path: str | None) -> bool:
    """True if a field belongs to a profile's Event Information group.

    Path is authoritative; a field whose path contains ``Event Information``
    under a ``Profiles`` subtree is an event field. Falls back to a
    name-based heuristic only when path is unknown.
    """
    if path:
        path_lower = path.lower()
        if "event information" in path_lower and "profiles" in path_lower:
            return True
        return False
    # No path info -- can't disambiguate event vs measurement reliably from
    # the name alone, so default to False (rule B is suppressed without
    # path evidence; rule A still fires from the name regex).
    return False


def _is_profile_field(name: str | None, path: str | None) -> bool:
    """True if a field appears to belong to an EMS profile.

    Uses path when available (path contains ``Profiles/``); falls back to
    the ``^P\\d+:`` name regex otherwise.
    """
    if path and "profiles" in path.lower():
        return True
    if name and _profile_number(name) is not None:
        return True
    return False


def _existing_filter_field_names(
    filters: list[QueryFilter] | None,
    ems_system_id: int,
    database_id: str,
) -> set[str]:
    """Best-effort: collect canonical names of fields already filtered.

    Used by rules to avoid re-suggesting filters the user already set.
    Names are looked up from the result store when the filter references a
    [N] number; passed-through name strings are taken as-is.
    """
    if not filters:
        return set()
    names: set[str] = set()
    for f in filters:
        ref = f.get("field_id")
        if ref is None:
            continue
        entry = _lookup_stored_field(ref, ems_system_id, database_id)
        if entry is not None and entry.get("name"):
            names.add(str(entry["name"]))
        elif isinstance(ref, str) and not ref.startswith("["):
            names.add(ref)
    return names


def _filter_targets_fleet_with_fdr(
    filters: list[QueryFilter] | None,
    ems_system_id: int,
    database_id: str,
) -> bool:
    """True if any filter targets the ``Fleet`` field with an ``FDR_*`` value.

    Detects rule E: when filtering by Fleet on a recorder type whose label
    starts ``FDR_``, the same flight may have a separate QAR record and
    should be deduped via Duplicate Detection (Master).
    """
    if not filters:
        return False
    for f in filters:
        ref = f.get("field_id")
        if ref is None:
            continue
        # Identify the field as "Fleet" -- check the literal name first,
        # then fall back to a stored entry's name.
        is_fleet = False
        if isinstance(ref, str) and ref.lower() == "fleet":
            is_fleet = True
        else:
            entry = _lookup_stored_field(ref, ems_system_id, database_id)
            if entry is not None and str(entry.get("name", "")).lower() == "fleet":
                is_fleet = True
        if not is_fleet:
            continue

        value = f.get("value")
        candidates: list[Any] = []
        if isinstance(value, (list, tuple)):
            candidates.extend(value)
        else:
            candidates.append(value)
        for v in candidates:
            if isinstance(v, str) and v.upper().startswith("FDR_"):
                return True
    return False


def _format_suggestion_block(
    rule_id: str,
    rule_title: str,
    rationale: str,
    suggestions: list[dict[str, Any]],
) -> str:
    """Render one rule's suggestions in the canonical text format."""
    lines = [f"[{rule_id}] {rule_title}", f"  {rationale}"]
    for s in suggestions:
        operator = s.get("operator", "equal")
        value = s.get("value")
        value_repr = repr(value) if not isinstance(value, bool) else str(value)
        lines.append(
            f"  - field_id: {s['field_id']!r}, operator: {operator!r}, "
            f"value: {value_repr}"
        )
    return "\n".join(lines)


def _compute_best_practice_filter_bundle(
    ems_system_id: int,
    resolved_db: str,
    fields: list[str | int],
    existing_filters: list[QueryFilter] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compute best-practice filter bundle for the given fields.

    Returns a tuple of (filter_bundle, informational_sections) where
    filter_bundle is a list of filter dicts ready for query_database and
    informational_sections is a list of formatted strings for rules that
    produce text notes rather than concrete filters (e.g. Rule D).

    This is the shared core used by both suggest_query_filters (which
    formats the bundle as human-readable text) and query_database (which
    applies the bundle automatically).
    """
    # Gather (name, path) tuples for each queried field.
    field_infos: list[tuple[str | None, str | None]] = []
    for ref in fields:
        entry = _lookup_stored_field(ref, ems_system_id, resolved_db)
        if entry is not None:
            field_infos.append((entry.get("name"), entry.get("path")))
        elif isinstance(ref, str) and ref.strip() and not ref.strip().startswith("["):
            field_infos.append((ref.strip(), None))
        else:
            field_infos.append((None, None))

    # Identify already-filtered field names so we can suppress duplicate suggestions.
    filtered_names = _existing_filter_field_names(
        existing_filters, ems_system_id, resolved_db
    )
    filtered_names_lower = {n.lower() for n in filtered_names}

    bundle: list[dict[str, Any]] = []
    info_sections: list[str] = []

    # --- Rule A: Profile measurement -- Processing State == Succeeded ---
    # --- Rule B: Profile event -- also False Positive == Not a False Positive ---
    profiles_in_query: list[str] = []
    profiles_with_event_field: set[str] = set()
    seen_profile: set[str] = set()
    for name, path in field_infos:
        if not _is_profile_field(name, path):
            continue
        prof = _profile_number(name) if name else None
        if not prof:
            continue
        if prof not in seen_profile:
            seen_profile.add(prof)
            profiles_in_query.append(prof)
        if _is_profile_event_field(name, path):
            profiles_with_event_field.add(prof)

    rule_a_suggestions: list[dict[str, Any]] = []
    rule_b_suggestions: list[dict[str, Any]] = []
    for prof in profiles_in_query:
        ps_field = f"{prof}: Processing State"
        if ps_field.lower() not in filtered_names_lower:
            rule_a_suggestions.append({
                "field_id": ps_field,
                "operator": "equal",
                "value": _PROCESSING_STATE_OK,
            })
        if prof in profiles_with_event_field:
            fp_field = f"{prof}: False Positive"
            if fp_field.lower() not in filtered_names_lower:
                rule_b_suggestions.append({
                    "field_id": fp_field,
                    "operator": "equal",
                    "value": _FALSE_POSITIVE_OK,
                })

    if rule_a_suggestions:
        info_sections.append(_format_suggestion_block(
            "A", "Profile Processing State",
            "Restrict to flights where the profile finished processing "
            "successfully.",
            rule_a_suggestions,
        ))
        bundle.extend(rule_a_suggestions)
    if rule_b_suggestions:
        info_sections.append(_format_suggestion_block(
            "B", "Profile event False Positive",
            "Exclude events that reviewers flagged as false positives.",
            rule_b_suggestions,
        ))
        bundle.extend(rule_b_suggestions)

    # --- Rule C: FDW Flights validity ---
    is_fdw_flights = "foqa-flights" in resolved_db.lower()
    rule_c_suggestions: list[dict[str, Any]] = []
    if is_fdw_flights:
        for fname in ("Takeoff Valid", "Landing Valid"):
            if fname.lower() not in filtered_names_lower:
                rule_c_suggestions.append({
                    "field_id": fname,
                    "operator": "equal",
                    "value": True,
                })
    if rule_c_suggestions:
        info_sections.append(_format_suggestion_block(
            "C", "FDW Flights validity",
            "Exclude flights without valid takeoff/landing detection so "
            "aggregates are not skewed by partial records.",
            rule_c_suggestions,
        ))
        bundle.extend(rule_c_suggestions)

    # --- Rule D: Fleet ambiguity (informational only) ---
    referenced_fleet_like: list[str] = []
    for name, _ in field_infos:
        if name and name in _FLEET_LIKE_FIELDS:
            if name not in referenced_fleet_like:
                referenced_fleet_like.append(name)
    if existing_filters:
        for f in existing_filters:
            ref = f.get("field_id")
            candidate_name: str | None = None
            if isinstance(ref, str) and not ref.startswith("[") and not ref.isdigit():
                candidate_name = ref
            else:
                entry = _lookup_stored_field(ref, ems_system_id, resolved_db) if ref is not None else None
                if entry is not None:
                    candidate_name = entry.get("name")
            if candidate_name in _FLEET_LIKE_FIELDS and candidate_name not in referenced_fleet_like:
                referenced_fleet_like.append(candidate_name)

    if referenced_fleet_like:
        info_sections.append(
            "[D] Fleet field disambiguation\n"
            f"  Your query references: {', '.join(referenced_fleet_like)}.\n"
            "  These fields are NOT interchangeable -- be explicit in your\n"
            "  narrative which one was used:\n"
            '    - "Fleet": flight-recorder type (FDR_* are FDR downloads).\n'
            '    - "Fleet Group": aircraft model (e.g. "737-800", "A330-200").\n'
            '    - "Airline Fleet Group": operator-specific code (e.g. "QFB738").\n'
            '    - "Airframe Group": airframe-level grouping.'
        )

    # --- Rule E: FDR duplicate detection ---
    rule_e_suggestions: list[dict[str, Any]] = []
    if _filter_targets_fleet_with_fdr(existing_filters, ems_system_id, resolved_db):
        if "duplicate detection (master)" not in filtered_names_lower:
            rule_e_suggestions.append({
                "field_id": "Duplicate Detection (Master)",
                "operator": "equal",
                "value": _DUPLICATE_DETECTION_OK,
            })
    if rule_e_suggestions:
        info_sections.append(_format_suggestion_block(
            "E", "FDR duplicate detection",
            "Filter targets a Fleet recorder starting FDR_, where the same "
            "flight may also have a QAR record. Deduplicate via Duplicate "
            "Detection (Master).",
            rule_e_suggestions,
        ))
        bundle.extend(rule_e_suggestions)

    return bundle, info_sections


@mcp.tool
async def suggest_query_filters(
    ems_system_id: int,
    database_id: str,
    fields: list[str | int],
    existing_filters: list[QueryFilter] | None = None,
    ctx: Context | None = None,
) -> str:
    """Suggest best-practice filters for a planned query_database call.

    Returns canonical EMS query conventions resolved for the specific
    fields you intend to query: profile Processing State == "Succeeded",
    profile event False Positive == "Not a False Positive", FDW Flights
    Takeoff Valid / Landing Valid == true, and FDR-fleet duplicate
    detection. Suggestions skip any field already covered by
    ``existing_filters`` so explicit user choices are never overruled.

    Field references accept the same forms as query_database (name, [N]
    reference, bracket-encoded ID). Path-based profile detection requires
    fields that were discovered via find_fields(mode='deep') in this
    session; otherwise falls back to name-pattern heuristics ("P{N}: ...").

    Note: query_database calls this automatically when
    apply_best_practice_filters=True (the default). Use this tool
    explicitly only when you want to inspect or adjust the suggestions
    before passing them to query_database.

    Args:
        ems_system_id: EMS system ID.
        database_id: Database ID or name (e.g. "FDW Flights").
        fields: Field references planned for the query (names, [N], or IDs).
        existing_filters: Filters already on the query, so the tool can
            skip re-suggesting them. Same shape as query_database.filters.

    Returns:
        Formatted text listing applicable suggestions per rule, ending
        with a ready-to-paste filter block; or a single line stating no
        suggestions apply.
    """
    if not fields:
        return (
            "Error: At least one field reference is required. Pass the same "
            "field references you plan to give to query_database."
        )

    # Resolve database -- needed to identify FDW Flights and to scope store lookups.
    try:
        resolved_db = await _resolve_database_id(database_id, ems_system_id)
    except ValueError as e:
        return f"Error resolving database: {e}"

    bundle, sections = _compute_best_practice_filter_bundle(
        ems_system_id, resolved_db, fields, existing_filters
    )

    if not sections:
        return "No best-practice filter suggestions for this query."

    output = ["Best-practice filter suggestions:\n"]
    output.append("\n\n".join(sections))
    if bundle:
        output.append(
            "\n\nReady-to-use filters block (paste into query_database "
            "as ``filters=...``, with your existing filters):\n"
            + repr(bundle)
        )
    output.append(
        "\n\nApply these unless the user explicitly asked you to skip them. "
        "Field names auto-resolve in query_database; canonical discrete labels "
        "(e.g. \"Succeeded\") resolve to numeric codes automatically."
    )
    return "".join(output)
