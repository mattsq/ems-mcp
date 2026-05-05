"""Discovery tools for EMS MCP server.

These tools enable LLMs to discover EMS systems, databases, fields, and analytics.
Discovery must be performed before querying data, as field and analytic IDs are
opaque strings that cannot be constructed manually.
"""

import asyncio
import logging
import urllib.parse
from collections import deque
from typing import Any, Literal

from ems_mcp.api.client import EMSAPIError, EMSNotFoundError
from ems_mcp.cache import database_cache, field_cache, make_cache_key
from ems_mcp.server import get_client, mcp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result reference store
# ---------------------------------------------------------------------------
# Discovery tools assign numbered references [N] to search results instead of
# displaying full opaque IDs. The agent can later call get_result_id([N, ...])
# to retrieve the actual IDs for the specific results it needs.

_result_store: dict[int, dict[str, Any]] = {}
_next_ref: int = 0
_STORE_MAX_SIZE: int = 500


def _store_result(
    name: str,
    result_id: str,
    result_type: str = "field",
    ems_system_id: int | None = None,
    database_id: str | None = None,
) -> int:
    """Store a result and return its reference number.

    Entries accumulate across searches so the agent can reference results
    from any prior search within the session. When the store exceeds
    ``_STORE_MAX_SIZE``, the oldest entries are evicted.

    Args:
        name: Human-readable name of the result.
        result_id: The full opaque ID string.
        result_type: Type of result: ``"field"`` or ``"analytic"``.
        ems_system_id: EMS system the result belongs to. Used to scope
            name-based lookups; analytic entries only need this.
        database_id: Database the result belongs to (fields only). Used to
            prevent cross-database name collisions in lookup.

    Returns:
        The reference number assigned to this result.
    """
    global _next_ref  # noqa: PLW0603

    ref = _next_ref
    _next_ref += 1
    _result_store[ref] = {
        "name": name,
        "id": result_id,
        "type": result_type,
        "ems_system_id": ems_system_id,
        "database_id": database_id,
    }

    # Evict oldest entries when over capacity
    if len(_result_store) > _STORE_MAX_SIZE:
        oldest_keys = sorted(_result_store.keys())[: len(_result_store) - _STORE_MAX_SIZE]
        for k in oldest_keys:
            del _result_store[k]

    return ref


def _get_stored_result(ref: int) -> dict[str, Any] | None:
    """Look up a stored result by reference number.

    Args:
        ref: The reference number returned by ``_store_result``.

    Returns:
        Dict with ``name`` and ``id`` keys, or ``None`` if not found.
    """
    return _result_store.get(ref)


def _find_stored_field_by_name(
    name: str,
    ems_system_id: int,
    database_id: str,
) -> str | None:
    """Find a stored field result by case-insensitive name + DB scope match.

    Only matches entries whose stored ``type`` is ``"field"`` AND that were
    stored against the same ``(ems_system_id, database_id)`` pair. Entries
    stored without scope (legacy behavior) never match -- they would risk
    returning IDs from a different database.

    Args:
        name: The field name to look up.
        ems_system_id: EMS system the lookup is scoped to.
        database_id: Database the lookup is scoped to.

    Returns:
        The stored field ID if exactly one match is found, or None.
    """
    name_lower = name.lower()
    matches: list[str] = [
        str(entry["id"])
        for entry in _result_store.values()
        if entry.get("type") == "field"
        and entry.get("name", "").lower() == name_lower
        and entry.get("ems_system_id") == ems_system_id
        and entry.get("database_id") == database_id
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _reset_result_store() -> None:
    """Reset the result store (for testing only)."""
    global _next_ref  # noqa: PLW0603
    _result_store.clear()
    _next_ref = 0


async def _resolve_field_id(
    field_ref: str | int,
    ems_system_id: int,
    database_id: str,
) -> str:
    """Resolve a field reference to an opaque field ID.

    Resolution order:
    1. Integer or digit string -> look up in result store
    2. Bracket-encoded string (starts with ``[``) -> pass through
    3. Human-readable name -> search field API, exact or single match

    Args:
        field_ref: A result store reference number, bracket-encoded ID, or
            human-readable field name.
        ems_system_id: The EMS system ID for API lookups.
        database_id: The database ID for API lookups.

    Returns:
        The resolved opaque field ID string.

    Raises:
        ValueError: If the reference cannot be resolved.
    """
    # 1. Integer or digit string -> result store lookup
    if isinstance(field_ref, int) or (isinstance(field_ref, str) and field_ref.strip().isdigit()):
        ref_num = int(field_ref) if isinstance(field_ref, str) else field_ref
        entry = _get_stored_result(ref_num)
        if entry is not None:
            if entry.get("type") == "analytic":
                raise ValueError(
                    f"Reference [{ref_num}] ('{entry['name']}') is an analytic parameter, "
                    "not a database field. Use it with query_flight_analytics, "
                    "or use find_fields to find database field references."
                )
            return entry["id"]
        raise ValueError(
            f"Reference [{ref_num}] not found in result store. "
            "Re-run find_fields to get fresh references."
        )

    if not isinstance(field_ref, str) or not field_ref.strip():
        raise ValueError(f"Invalid field reference: {field_ref!r}")

    field_ref = field_ref.strip()

    # 2. Bracket-encoded string -> pass through
    if field_ref.startswith("["):
        return field_ref

    # 3. Result store hit by name (scoped to this ems+db) -> avoid an API
    # round-trip when the agent passes a name that was just returned by
    # find_fields against the SAME database.
    stored_id = _find_stored_field_by_name(field_ref, ems_system_id, database_id)
    if stored_id is not None:
        return stored_id

    # 4. Human-readable name -> search via API
    cache_key = make_cache_key("field_resolve", ems_system_id, database_id, field_ref.lower())
    cached = await field_cache.get(cache_key)
    if cached is not None:
        return cached

    client = get_client()

    # Entity-type databases don't support the field search endpoint (405);
    # fall back to BFS traversal of field groups.
    if _is_entity_type_database(database_id):
        matches, _ = await _recursive_field_search(
            client, ems_system_id, database_id,
            search_text=field_ref,
            max_depth=10, max_results=50, max_groups=50,
        )
        search_results = matches
    else:
        path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/fields"
        params = {"text": field_ref}
        search_results = await client.get(path, params=params)

    if not search_results:
        raise ValueError(
            f"Field not found: '{field_ref}'. "
            "Use find_fields to discover valid field names."
        )

    # Try exact name match (case-insensitive)
    exact_matches = [
        f for f in search_results
        if f.get("name", "").lower() == field_ref.lower()
    ]
    if len(exact_matches) == 1:
        resolved_id = exact_matches[0]["id"]
        await field_cache.set(cache_key, resolved_id)
        return resolved_id

    # Single result total -> use it
    if len(search_results) == 1:
        resolved_id = search_results[0]["id"]
        await field_cache.set(cache_key, resolved_id)
        return resolved_id

    # Multiple matches with no exact match -> ambiguous
    match_names = [f.get("name", "?") for f in search_results[:5]]
    raise ValueError(
        f"Ambiguous field name: '{field_ref}'. "
        f"Multiple matches found: {', '.join(match_names)}"
        f"{'...' if len(search_results) > 5 else ''}. "
        "Use a more specific name or use find_fields to find the exact name."
    )


async def _resolve_database_id(
    database_ref: str,
    ems_system_id: int,
) -> str:
    """Resolve a database name to an opaque database ID.

    Resolution order:
    1. Bracket-encoded string (starts with ``[``) -> pass through
    2. Human-readable name -> look up via database groups API

    The first call fetches root database groups + one level deep and caches
    the full name-to-ID mapping.

    Args:
        database_ref: A bracket-encoded database ID or human-readable name.
        ems_system_id: The EMS system ID for API lookups.

    Returns:
        The resolved opaque database ID string.

    Raises:
        ValueError: If the name cannot be resolved.
    """
    if not database_ref or not database_ref.strip():
        raise ValueError("database_id cannot be empty.")

    database_ref = database_ref.strip()

    # 1. Bracket-encoded -> pass through
    if database_ref.startswith("["):
        return database_ref

    # 2. Name -> look up in cached mapping
    cache_key = make_cache_key("database_name_map", ems_system_id)
    name_map: dict[str, str] | None = await database_cache.get(cache_key)

    if name_map is None:
        # Build the name -> ID mapping from root + one level of subgroups
        client = get_client()
        name_map = {}

        try:
            root = await client.get(f"/api/v2/ems-systems/{ems_system_id}/database-groups")
        except (EMSAPIError, EMSNotFoundError) as e:
            raise ValueError(f"Failed to fetch database groups: {e}") from e

        # Collect databases at root
        for db in root.get("databases", []):
            db_id = db.get("id", "")
            for name_key in ("name", "pluralName", "singularName"):
                db_name = db.get(name_key)
                if db_name:
                    name_map[db_name.lower()] = db_id

        # Fetch one level of subgroups
        for group in root.get("groups", []):
            group_id = group.get("id")
            if not group_id:
                continue
            try:
                sub = await client.get(
                    f"/api/v2/ems-systems/{ems_system_id}/database-groups?groupId={group_id}"
                )
                for db in sub.get("databases", []):
                    db_id = db.get("id", "")
                    for name_key in ("name", "pluralName", "singularName"):
                        db_name = db.get(name_key)
                        if db_name:
                            name_map[db_name.lower()] = db_id
            except (EMSAPIError, EMSNotFoundError):
                continue

        await database_cache.set(cache_key, name_map)

    # Case-insensitive lookup
    resolved = name_map.get(database_ref.lower())
    if resolved is not None:
        return resolved

    # Not found
    available = sorted(set(name_map.keys()))[:10]
    raise ValueError(
        f"Database not found: '{database_ref}'. "
        f"Available databases include: {', '.join(available)}"
        f"{'...' if len(name_map) > 10 else ''}. "
        "Use list_databases to browse available databases."
    )


def _is_entity_type_database(database_id: str) -> bool:
    """Check if a database ID is an entity-type database.

    Entity-type databases (containing ``[entity-type]`` but not
    ``[entity-type-group]``) don't support the field search endpoint.
    Fields must be discovered via browse mode or deep search instead.

    Args:
        database_id: The database ID to check.

    Returns:
        True if this is an entity-type database.
    """
    return "[entity-type]" in database_id and "[entity-type-group]" not in database_id


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_ems_systems(systems: list[dict[str, Any]]) -> str:
    """Format EMS systems list for display."""
    if not systems:
        return "No EMS systems found."

    lines = [f"Found {len(systems)} EMS system(s):"]
    for sys in systems:
        name = sys.get("name", "Unknown")
        sys_id = sys.get("id", "?")
        desc = sys.get("description", "")
        if desc:
            lines.append(f"  - {name} (ID: {sys_id}): {desc}")
        else:
            lines.append(f"  - {name} (ID: {sys_id})")
    return "\n".join(lines)


def _format_database_group(group: dict[str, Any]) -> str:
    """Format database group response for display."""
    lines = []

    group_name = group.get("name", "Root")
    group_id = group.get("id", "[none]")
    lines.append(f"Group: {group_name} (ID: {group_id})")

    # Format databases
    databases = group.get("databases", [])
    if databases:
        lines.append(f"\nDatabases ({len(databases)}):")
        for db in databases:
            db_id = db.get("id", "?")
            # Handle both root level (name/description) and nested (pluralName/singularName)
            db_name = db.get("name") or db.get("pluralName") or db.get("singularName", "Unknown")
            desc = db.get("description", "")
            # Annotate entity-type-group IDs that require further navigation
            if "[entity-type-group]" in str(db_id):
                note = " [NOTE: this is a group ID - navigate deeper with list_databases]"
                lines.append(f"  - {db_name} (ID: {db_id}){note}")
            elif desc:
                lines.append(f"  - {db_name}: {desc}")
            else:
                lines.append(f"  - {db_name}")

    # Format subgroups
    groups = group.get("groups", [])
    if groups:
        lines.append(f"\nSubgroups ({len(groups)}):")
        for g in groups:
            g_id = g.get("id", "?")
            g_name = g.get("name", "Unknown")
            lines.append(f"  - {g_name} (ID: {g_id})")

    if not databases and not groups:
        lines.append("\n(Empty group)")

    if databases:
        lines.append(
            "\nUse database names directly in find_fields, query_database, etc."
        )

    return "\n".join(lines)


def _format_field_group(
    group: dict[str, Any],
    ems_system_id: int | None = None,
    database_id: str | None = None,
) -> str:
    """Format field group response for display."""
    lines = []

    group_name = group.get("name", "Root")
    group_id = group.get("id", "[none]")
    lines.append(f"Field Group: {group_name} (ID: {group_id})")

    # Format fields
    fields = group.get("fields", [])
    if fields:
        lines.append(f"\nFields ({len(fields)}):")
        for f in fields:
            field_id = f.get("id", "?")
            field_name = f.get("name", "Unknown")
            field_type = f.get("type", "unknown")
            ref = _store_result(
                field_name, field_id,
                ems_system_id=ems_system_id, database_id=database_id,
            )
            lines.append(f"  [{ref}] {field_name} ({field_type})")

    # Format subgroups
    groups = group.get("groups", [])
    if groups:
        lines.append(f"\nSubgroups ({len(groups)}):")
        for g in groups:
            g_id = g.get("id", "?")
            g_name = g.get("name", "Unknown")
            lines.append(f"  - {g_name} (ID: {g_id})")

    if not fields and not groups:
        lines.append("\n(Empty group)")

    return "\n".join(lines)


def _format_field_search_results(
    fields: list[dict[str, Any]],
    show_ids: bool = False,
    ems_system_id: int | None = None,
    database_id: str | None = None,
) -> str:
    """Format field search results for display.

    Args:
        fields: List of field dicts from the API.
        show_ids: If True, show full IDs inline (backward compat).
            If False (default), assign numbered refs and hide IDs.
        ems_system_id: EMS system the search ran against. Stored alongside
            each reference so subsequent name lookups stay DB-scoped.
        database_id: Database the search ran against.
    """
    if not fields:
        return "No fields found matching the search criteria."

    lines = [f"Found {len(fields)} field(s):"]
    for f in fields:
        field_id = f.get("id", "?")
        field_name = f.get("name", "Unknown")
        field_type = f.get("type", "unknown")
        units = f.get("units")
        description = f.get("description")
        discrete_values = f.get("discreteValues")

        type_str = field_type
        if units:
            type_str = f"{field_type} ({units})"

        if show_ids:
            lines.append(f"\n  {field_name} [{type_str}]")
            lines.append(f"    ID: {field_id}")
        else:
            ref = _store_result(
                field_name, field_id,
                ems_system_id=ems_system_id, database_id=database_id,
            )
            lines.append(f"\n  [{ref}] {field_name} [{type_str}]")

        if description:
            lines.append(f"    {description}")

        if discrete_values:
            lines.extend(_format_inline_discrete_values(discrete_values))

    if not show_ids:
        lines.append(
            "\nUse [N] reference numbers or field names directly in query_database."
        )

    return "\n".join(lines)


def _format_inline_discrete_values(
    discrete_values: Any,
    max_inline: int = 10,
) -> list[str]:
    """Format discrete value mappings for inline display in search results.

    Shows up to ``max_inline`` values inline; if there are more, shows the
    count and tells the agent how to retrieve them all.

    Args:
        discrete_values: Either a list of ``{"value": code, "label": label}``
            dicts or a ``{"code": "label"}`` mapping.
        max_inline: Maximum entries to show before truncating.

    Returns:
        Lines to append to the formatted output.
    """
    if isinstance(discrete_values, dict):
        values = [{"value": k, "label": v} for k, v in discrete_values.items()]
    elif isinstance(discrete_values, list):
        values = discrete_values
    else:
        return []

    if not values:
        return []

    lines = [f"    Discrete values ({len(values)}):"]
    for dv in values[:max_inline]:
        value = dv.get("value", "?") if isinstance(dv, dict) else "?"
        label = dv.get("label", "Unknown") if isinstance(dv, dict) else str(dv)
        lines.append(f"      {value}: {label}")
    if len(values) > max_inline:
        lines.append(
            f"      ... and {len(values) - max_inline} more "
            f"(use get_field_info or include_field_info=True for the full list)"
        )
    return lines


def _format_field_info(field: dict[str, Any]) -> str:
    """Format detailed field information for display."""
    lines = []

    field_name = field.get("name", "Unknown")
    field_id = field.get("id", "?")
    field_type = field.get("type", "unknown")

    lines.append(f"Field: {field_name}")
    lines.append(f"Type: {field_type}")

    units = field.get("units")
    if units:
        lines.append(f"Units: {units}")

    description = field.get("description")
    if description:
        lines.append(f"Description: {description}")

    lines.append(f"\nField ID: {field_id}")

    # Handle discrete values
    discrete_values = field.get("discreteValues")
    if discrete_values:
        # Normalize dict format {"code": "label"} to list format [{"value": code, "label": label}]
        if isinstance(discrete_values, dict):
            discrete_values = [{"value": k, "label": v} for k, v in discrete_values.items()]

        lines.append(f"\nDiscrete Values ({len(discrete_values)}):")
        # Limit display for large value sets
        display_count = min(len(discrete_values), 50)
        for dv in discrete_values[:display_count]:
            value = dv.get("value", "?")
            label = dv.get("label", "Unknown")
            lines.append(f"  {value}: {label}")
        if len(discrete_values) > display_count:
            lines.append(f"  ... and {len(discrete_values) - display_count} more values")

    return "\n".join(lines)


def _format_analytics_search_results(
    analytics: list[dict[str, Any]],
    show_ids: bool = False,
    ems_system_id: int | None = None,
) -> str:
    """Format analytics search results for display.

    Args:
        analytics: List of analytic dicts from the API.
        show_ids: If True, show full IDs inline (backward compat).
            If False (default), assign numbered refs and hide IDs.
        ems_system_id: EMS system the search ran against.
    """
    if not analytics:
        return "No analytics found matching the search criteria."

    lines = [f"Found {len(analytics)} analytic(s):"]
    for a in analytics:
        analytic_id = a.get("id", "?")
        analytic_name = a.get("name", "Unknown")
        analytic_type = a.get("type", "unknown")
        units = a.get("units")
        description = a.get("description")

        type_str = analytic_type
        if units:
            type_str = f"{analytic_type} ({units})"

        if show_ids:
            lines.append(f"\n  {analytic_name} [{type_str}]")
            if description:
                lines.append(f"    {description}")
            lines.append(f"    ID: {analytic_id}")
        else:
            ref = _store_result(
                analytic_name, analytic_id,
                result_type="analytic", ems_system_id=ems_system_id,
            )
            lines.append(f"\n  [{ref}] {analytic_name} [{type_str}]")
            if description:
                lines.append(f"    {description}")

    if not show_ids:
        lines.append(
            "\nYou can pass analytic names directly to query_flight_analytics."
        )

    return "\n".join(lines)


def _format_deep_search_results(
    results: list[dict[str, Any]],
    search_text: str,
    groups_visited: int = 0,
    max_groups: int = 0,
    show_ids: bool = False,
    ems_system_id: int | None = None,
    database_id: str | None = None,
) -> str:
    """Format recursive field search results for display.

    Args:
        results: List of matching field dicts.
        search_text: The original search text (for display).
        groups_visited: Number of field-group API calls made.
        max_groups: The max_groups budget that was configured.
        show_ids: If True, show full IDs inline (backward compat).
            If False (default), assign numbered refs and hide IDs.
        ems_system_id: EMS system the search ran against.
        database_id: Database the search ran against.

    Returns:
        Formatted search results string.
    """
    if not results:
        msg = f"No fields found matching '{search_text}' in deep search."
        if groups_visited > 0 and max_groups > 0:
            msg += f"\n(Searched {groups_visited} group(s), budget: {max_groups})"
            if groups_visited >= max_groups:
                msg += "\nBudget exhausted -- try increasing max_groups for a wider search."
        return msg

    lines = [f"Found {len(results)} field(s) matching '{search_text}':"]

    for f in results:
        field_name = f["name"]
        field_type = f["type"]
        units = f.get("units")
        path = f["path"]
        field_id = f["id"]

        type_str = field_type
        if units:
            type_str = f"{field_type} ({units})"

        if show_ids:
            lines.append(f"\n  {field_name} [{type_str}]")
            lines.append(f"    Path: {path}")
            lines.append(f"    ID: {field_id}")
        else:
            ref = _store_result(
                field_name, field_id,
                ems_system_id=ems_system_id, database_id=database_id,
            )
            lines.append(f"\n  [{ref}] {field_name} [{type_str}]")
            lines.append(f"    Path: {path}")

    if not show_ids and results:
        lines.append(
            "\nUse [N] reference numbers or field names directly in query_database."
        )

    if groups_visited > 0 and max_groups > 0:
        stats = f"\n(Searched {groups_visited} group(s), budget: {max_groups})"
        if groups_visited >= max_groups:
            stats += "\nBudget exhausted -- try increasing max_groups for a wider search."
        lines.append(stats)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_field_group(
    client: Any,
    ems_system_id: int,
    database_id: str,
    group_id: str | None,
) -> dict[str, Any]:
    """Fetch a field group from the API, with caching.

    Reuses the same cache key pattern as browse mode so cache entries
    are shared between code paths.

    Args:
        client: The EMS API client.
        ems_system_id: The EMS system ID.
        database_id: The database ID.
        group_id: The field group ID, or None for root.

    Returns:
        The field group response dict.
    """
    cache_key = make_cache_key("field_group", ems_system_id, database_id, group_id or "root")
    cached = await field_cache.get(cache_key)
    if cached is not None:
        return cached

    path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/field-groups"
    if group_id:
        path += f"?groupId={group_id}"

    group = await client.get(path)
    await field_cache.set(cache_key, group)
    return group


async def _recursive_field_search(
    client: Any,
    ems_system_id: int,
    database_id: str,
    search_text: str,
    max_depth: int,
    max_results: int,
    max_groups: int,
) -> tuple[list[dict[str, Any]], int]:
    """BFS traversal of field groups to find fields matching search text.

    Args:
        client: The EMS API client.
        ems_system_id: The EMS system ID.
        database_id: The database ID.
        search_text: Text to match against field names (case-insensitive partial).
        max_depth: Maximum depth to traverse.
        max_results: Maximum number of matching fields to return.
        max_groups: Hard cap on total field-group API calls to prevent timeouts.

    Returns:
        Tuple of (matching fields list, groups_visited count).
    """
    search_lower = search_text.lower()
    search_words = set(search_lower.split())
    matches: list[dict[str, Any]] = []
    groups_visited = 0

    # BFS queue entries: (group_id_or_None, depth, path_parts)
    queue: deque[tuple[str | None, int, list[str]]] = deque()
    queue.append((None, 0, []))

    while queue and len(matches) < max_results:
        if groups_visited >= max_groups:
            break

        group_id, depth, path_parts = queue.popleft()

        if depth > max_depth:
            continue

        try:
            group = await _fetch_field_group(client, ems_system_id, database_id, group_id)
            groups_visited += 1
        except (EMSAPIError, EMSNotFoundError):
            groups_visited += 1
            continue

        group_name = group.get("name", "")
        current_path = path_parts + [group_name] if group_name and depth > 0 else path_parts

        # Check fields at this level
        for field in group.get("fields", []):
            if len(matches) >= max_results:
                break
            field_name = field.get("name", "")
            if search_lower in field_name.lower():
                matches.append({
                    "name": field_name,
                    "id": field.get("id", ""),
                    "type": field.get("type", "unknown"),
                    "units": field.get("units"),
                    "path": " > ".join(current_path) if current_path else "(root)",
                })

        # Enqueue subgroups with relevance prioritization
        if depth < max_depth:
            for sub in group.get("groups", []):
                sub_id = sub.get("id")
                if sub_id:
                    sub_name_lower = sub.get("name", "").lower()
                    entry = (sub_id, depth + 1, current_path)
                    # Prioritize groups whose name contains a search word
                    if search_words & set(sub_name_lower.split()):
                        queue.appendleft(entry)
                    else:
                        queue.append(entry)

    return matches, groups_visited


async def _do_browse_fields(
    ems_system_id: int,
    database_id: str,
    group_id: str | None,
) -> str:
    """Browse mode: navigate field group hierarchy.

    Args:
        ems_system_id: The EMS system ID.
        database_id: Database ID.
        group_id: Optional field group ID to navigate into.

    Returns:
        Formatted field group listing.
    """
    if "[entity-type-group]" in database_id:
        return (
            "Error: This appears to be a database GROUP ID, not a database ID. "
            "Use list_databases with this as group_id to navigate deeper and find "
            "actual database IDs."
        )

    client = get_client()

    cache_key = make_cache_key("field_group", ems_system_id, database_id, group_id or "root")
    cached = await field_cache.get(cache_key)
    if cached is not None:
        logger.debug("Using cached field group: %s", cache_key)
        return _format_field_group(cached, ems_system_id, database_id)

    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/field-groups"
        if group_id:
            path += f"?groupId={group_id}"

        group = await client.get(path)
        await field_cache.set(cache_key, group)
        return _format_field_group(group, ems_system_id, database_id)
    except EMSNotFoundError:
        return (
            f"Error: Field group not found. Verify database_id='{database_id}' is valid. "
            "Use list_databases to find valid database IDs."
        )
    except EMSAPIError as e:
        if e.status_code == 405:
            return (
                "Error: HTTP 405 Method Not Allowed. "
                f"This usually means database_id='{database_id}' is invalid. "
                "Make sure you are using a database ID (not a group ID). "
                "Use list_databases to find valid database IDs."
            )
        return f"Error listing fields: {e.message}"


async def _fetch_field_search(
    client: Any,
    ems_system_id: int,
    database_id: str,
    search_text: str,
) -> list[dict[str, Any]]:
    """Fetch field-search results, hitting the cache first.

    Caches the full unbounded result list keyed on the lowercased search
    term so callers can apply their own ``max_results`` slice without
    invalidating the cache.

    Args:
        client: The EMS API client.
        ems_system_id: The EMS system ID.
        database_id: The opaque database ID.
        search_text: The keyword to search for.

    Returns:
        The list of field dicts returned by the API.
    """
    cache_key = make_cache_key("field_search", ems_system_id, database_id, search_text.lower())
    cached = await field_cache.get(cache_key)
    if cached is not None:
        logger.debug("Using cached field search: %s", cache_key)
        cached_list: list[dict[str, Any]] = cached
        return cached_list

    path = f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/fields"
    fields: list[dict[str, Any]] = await client.get(path, params={"text": search_text})
    await field_cache.set(cache_key, fields)
    return fields


async def _do_search_fields(
    ems_system_id: int,
    database_id: str,
    search_text: str,
    max_results: int,
    show_ids: bool,
    include_field_info: bool = False,
) -> str:
    """Search mode: keyword search via the field search API endpoint.

    Args:
        ems_system_id: The EMS system ID.
        database_id: Database ID.
        search_text: Text to search for.
        max_results: Maximum results.
        show_ids: Show full IDs inline.
        include_field_info: If True, fan out get_field_info for each result
            in parallel and merge details (description, discreteValues) into
            the formatter input.

    Returns:
        Formatted search results.
    """
    if "[entity-type-group]" in database_id:
        return (
            "Error: This appears to be a database GROUP ID, not a database ID. "
            "Use list_databases with this as group_id to navigate deeper and find "
            "actual database IDs."
        )

    if _is_entity_type_database(database_id):
        return (
            f"Error: database_id='{database_id}' is an entity-type database, which does "
            "not support the field search endpoint. Use find_fields with mode='deep' "
            "for BFS traversal, or mode='browse' to navigate field groups."
        )

    client = get_client()

    try:
        fields = await _fetch_field_search(client, ems_system_id, database_id, search_text)
    except EMSNotFoundError:
        return (
            f"Error: Database not found. Verify database_id='{database_id}' is valid. "
            "Use list_databases to find valid database IDs."
        )
    except EMSAPIError as e:
        if e.status_code == 405:
            return (
                f"Error: Field search failed with HTTP 405 Method Not Allowed for "
                f"database_id='{database_id}'. This may be an entity-type database "
                "that doesn't support field search, or the database ID may be invalid. "
                "Try find_fields with mode='deep' or mode='browse', "
                "or use list_databases to verify the database ID."
            )
        return f"Error searching fields: {e.message}"

    sliced = fields[:max_results]
    if include_field_info:
        sliced = await _enrich_with_field_info(client, ems_system_id, database_id, sliced)
    return _format_field_search_results(
        sliced, show_ids=show_ids,
        ems_system_id=ems_system_id, database_id=database_id,
    )


async def _do_search_fields_multi(
    ems_system_id: int,
    database_id: str,
    search_terms: list[str],
    max_results: int,
    show_ids: bool,
    include_field_info: bool = False,
) -> str:
    """Search mode: run multiple keyword searches concurrently.

    Each term hits its own cache entry so that subsequent single-term
    calls still hit cache. Results are grouped by term in the output.

    Args:
        ems_system_id: The EMS system ID.
        database_id: Database ID.
        search_terms: Non-empty list of keywords.
        max_results: Maximum results per term.
        show_ids: Show full IDs inline.
        include_field_info: If True, fan out get_field_info for each
            result in parallel and merge details into the formatter input.

    Returns:
        Formatted search results, one block per term.
    """
    if "[entity-type-group]" in database_id:
        return (
            "Error: This appears to be a database GROUP ID, not a database ID. "
            "Use list_databases with this as group_id to navigate deeper and find "
            "actual database IDs."
        )

    if _is_entity_type_database(database_id):
        return (
            f"Error: database_id='{database_id}' is an entity-type database, which does "
            "not support the field search endpoint. Use find_fields with mode='deep' "
            "for BFS traversal, or mode='browse' to navigate field groups."
        )

    cleaned: list[str] = []
    seen: set[str] = set()
    for term in search_terms:
        if not isinstance(term, str):
            continue
        stripped = term.strip()
        if not stripped:
            continue
        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(stripped)

    if not cleaned:
        return "Error: search_text list contained no usable search terms."

    client = get_client()

    results = await asyncio.gather(
        *(_fetch_field_search(client, ems_system_id, database_id, term) for term in cleaned),
        return_exceptions=True,
    )

    blocks: list[str] = []
    for term, outcome in zip(cleaned, results, strict=True):
        block_lines = [f'Search: "{term}"']

        if isinstance(outcome, EMSNotFoundError):
            block_lines.append(
                f"  Error: Database not found. Verify database_id='{database_id}'."
            )
            blocks.append("\n".join(block_lines))
            continue
        if isinstance(outcome, EMSAPIError):
            if outcome.status_code == 405:
                block_lines.append(
                    "  Error: HTTP 405 Method Not Allowed. "
                    "Try mode='deep' or mode='browse'."
                )
            else:
                block_lines.append(f"  Error: {outcome.message}")
            blocks.append("\n".join(block_lines))
            continue
        if isinstance(outcome, BaseException):
            block_lines.append(f"  Error: {outcome}")
            blocks.append("\n".join(block_lines))
            continue

        sliced = outcome[:max_results]
        if include_field_info:
            sliced = await _enrich_with_field_info(
                client, ems_system_id, database_id, sliced
            )
        block_lines.append(
            _format_field_search_results(
                sliced, show_ids=show_ids,
                ems_system_id=ems_system_id, database_id=database_id,
            )
        )
        blocks.append("\n".join(block_lines))

    header = (
        f"Multi-term search across {len(cleaned)} term(s) "
        f"in database '{database_id}':\n"
    )
    return header + "\n\n".join(blocks)


async def _enrich_with_field_info(
    client: Any,
    ems_system_id: int,
    database_id: str,
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fan out get_field_info for each field and merge results.

    Each ``get_field_info`` call is independently cached, so repeated
    enrichment is free.

    Args:
        client: The EMS API client.
        ems_system_id: The EMS system ID.
        database_id: The opaque database ID.
        fields: The field dicts to enrich. Each must have an ``id`` key.

    Returns:
        A new list of field dicts with ``description`` and ``discreteValues``
        merged in where available.
    """
    if not fields:
        return fields

    async def _fetch_one(field: dict[str, Any]) -> dict[str, Any]:
        field_id = field.get("id", "")
        if not field_id:
            return field

        cache_key = make_cache_key("field_info", ems_system_id, database_id, field_id)
        info = await field_cache.get(cache_key)
        if info is None:
            try:
                encoded = urllib.parse.quote(field_id, safe="")
                path = (
                    f"/api/v2/ems-systems/{ems_system_id}/databases/"
                    f"{database_id}/fields/{encoded}"
                )
                info = await client.get(path)
                await field_cache.set(cache_key, info)
            except (EMSAPIError, EMSNotFoundError):
                return field

        merged = dict(field)
        if "description" in info and "description" not in merged:
            merged["description"] = info["description"]
        if "discreteValues" in info and "discreteValues" not in merged:
            merged["discreteValues"] = info["discreteValues"]
        return merged

    return list(await asyncio.gather(*(_fetch_one(f) for f in fields)))


async def _do_deep_search_fields(
    ems_system_id: int,
    database_id: str,
    search_text: str,
    max_results: int,
    max_depth: int,
    max_groups: int,
    show_ids: bool,
) -> str:
    """Deep mode: BFS traversal of field group hierarchy.

    Args:
        ems_system_id: The EMS system ID.
        database_id: Database ID.
        search_text: Text to search for.
        max_results: Maximum matching fields to return.
        max_depth: Maximum traversal depth.
        max_groups: Maximum API calls.
        show_ids: Show full IDs inline.

    Returns:
        Formatted deep search results.
    """
    if not search_text or not search_text.strip():
        return "Error: search_text cannot be empty."

    max_depth = min(max(1, max_depth), 10)
    max_results = min(max(1, max_results), 50)
    max_groups = min(max(1, max_groups), 200)

    if "[entity-type-group]" in database_id:
        return (
            "Error: This appears to be a database GROUP ID, not a database ID. "
            "Use list_databases with this as group_id to navigate deeper and find "
            "actual database IDs."
        )

    client = get_client()

    try:
        results, groups_visited = await _recursive_field_search(
            client, ems_system_id, database_id, search_text.strip(),
            max_depth, max_results, max_groups,
        )
        return _format_deep_search_results(
            results, search_text.strip(), groups_visited, max_groups,
            show_ids=show_ids,
            ems_system_id=ems_system_id, database_id=database_id,
        )
    except EMSNotFoundError:
        return (
            f"Error: Database not found. Verify database_id='{database_id}' is valid. "
            "Use list_databases to find valid database IDs."
        )
    except EMSAPIError as e:
        return f"Error during deep field search: {e.message}"


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool
async def list_ems_systems() -> str:
    """List available EMS systems. Start here to get system IDs for all other tools.

    Returns:
        EMS systems with IDs, names, and descriptions.
    """
    client = get_client()

    try:
        systems = await client.get("/api/v2/ems-systems")
        return _format_ems_systems(systems)
    except EMSAPIError as e:
        return f"Error listing EMS systems: {e.message}"


@mcp.tool
async def list_databases(
    ems_system_id: int,
    group_id: str | None = None,
) -> str:
    """Navigate the database hierarchy. Call without group_id for root level.

    The "FDW Flights" database (Flight Data Warehouse) contains flight records.

    Args:
        ems_system_id: EMS system ID (from list_ems_systems).
        group_id: Group ID to navigate into (omit for root).

    Returns:
        Databases and subgroups at the specified level.
    """
    client = get_client()

    cache_key = make_cache_key("database_group", ems_system_id, group_id or "root")
    cached = await database_cache.get(cache_key)
    if cached is not None:
        logger.debug("Using cached database group: %s", cache_key)
        return _format_database_group(cached)

    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/database-groups"
        if group_id:
            path += f"?groupId={group_id}"

        group = await client.get(path)
        await database_cache.set(cache_key, group)
        return _format_database_group(group)
    except EMSNotFoundError:
        return f"Error: Database group not found. Verify ems_system_id={ems_system_id} is valid."
    except EMSAPIError as e:
        return f"Error listing databases: {e.message}"


@mcp.tool
async def find_fields(
    ems_system_id: int,
    database_id: str,
    mode: Literal["search", "browse", "deep"] = "search",
    search_text: str | list[str] | None = None,
    group_id: str | None = None,
    max_results: int = 50,
    max_depth: int = 5,
    max_groups: int = 50,
    show_ids: bool = False,
    include_field_info: bool = False,
) -> str:
    """Find fields in a database. Three modes available:

    - search: Fast keyword search (default). Requires search_text.
      Does NOT work on entity-type databases.
    - browse: Navigate field group hierarchy. Use group_id to drill down.
    - deep: BFS traversal across all field groups. Requires search_text.
      Works on ALL databases including entity-type. Slower (multiple API calls).

    Results show numbered references [N] that can be used directly in
    query_database, get_field_info, etc. Field names also work.

    Pass a list to search_text to run multiple keyword searches in parallel
    (e.g. ["fuel burn", "tail number", "takeoff airport"]) -- this collapses
    N round-trips into one tool call. Each term is cached individually.

    Args:
        ems_system_id: EMS system ID.
        database_id: Database ID or name (e.g. "FDW Flights").
        mode: "search" (fast keyword), "browse" (navigate groups), or "deep" (BFS).
        search_text: Search keyword, or a list of keywords for parallel
            multi-term search. Required for search and deep modes.
            Multi-term lists are only supported in search mode.
        group_id: Field group ID to navigate into (browse mode only).
        max_results: Maximum results per term (search/deep modes, default: 50).
        max_depth: Maximum traversal depth (deep mode, default: 5, max: 10).
        max_groups: Maximum API calls (deep mode, default: 50, max: 200).
        show_ids: If True, show full IDs inline instead of numbered references.
        include_field_info: If True (search mode only), fetch full field
            metadata in parallel for every result so descriptions and
            discrete-value mappings appear inline. Eliminates a follow-up
            get_field_info call per discrete field.

    Returns:
        Fields with names, types, and IDs (or numbered references).
    """
    # Resolve database name -> ID
    try:
        database_id = await _resolve_database_id(database_id, ems_system_id)
    except ValueError as e:
        return f"Error resolving database: {e}"

    if mode == "browse":
        return await _do_browse_fields(ems_system_id, database_id, group_id)
    elif mode == "deep":
        if not search_text:
            return "Error: search_text is required for deep mode."
        if isinstance(search_text, list):
            return (
                "Error: deep mode does not support multi-term search. "
                "Pass a single string to search_text, or call again per term."
            )
        return await _do_deep_search_fields(
            ems_system_id, database_id, search_text,
            max_results, max_depth, max_groups, show_ids,
        )
    else:  # mode == "search" (default)
        if not search_text:
            return "Error: search_text is required for search mode."
        if isinstance(search_text, list):
            return await _do_search_fields_multi(
                ems_system_id, database_id, search_text,
                max_results, show_ids, include_field_info,
            )
        return await _do_search_fields(
            ems_system_id, database_id, search_text,
            max_results, show_ids, include_field_info,
        )


@mcp.tool
async def get_field_info(
    ems_system_id: int,
    database_id: str,
    field_id: str | int,
) -> str:
    """Get field metadata including type, units, and discrete value mappings.

    Essential for discrete fields: shows numeric code-to-label mappings needed
    for filtering. String labels in filters are auto-resolved, but use this to
    verify available values.

    Args:
        ems_system_id: EMS system ID.
        database_id: Database ID or name (e.g. "FDW Flights").
        field_id: Field reference: [N] number from find_fields, field name
            (e.g. "Takeoff Airport Name"), or bracket-encoded ID.

    Returns:
        Field details with discrete value mappings if applicable.
    """
    # Resolve database name -> ID
    try:
        database_id = await _resolve_database_id(database_id, ems_system_id)
    except ValueError as e:
        return f"Error resolving database: {e}"

    # Resolve field reference -> opaque ID
    try:
        field_id = await _resolve_field_id(field_id, ems_system_id, database_id)
    except (ValueError, EMSAPIError) as e:
        return f"Error resolving field: {e}"

    client = get_client()

    cache_key = make_cache_key("field_info", ems_system_id, database_id, field_id)
    cached = await field_cache.get(cache_key)
    if cached is not None:
        logger.debug("Using cached field info: %s", cache_key)
        return _format_field_info(cached)

    try:
        encoded_field_id = urllib.parse.quote(field_id, safe="")
        path = (
            f"/api/v2/ems-systems/{ems_system_id}/databases/{database_id}/fields/{encoded_field_id}"
        )

        field = await client.get(path)
        await field_cache.set(cache_key, field)
        return _format_field_info(field)
    except EMSNotFoundError:
        return (
            "Error: Field not found. Verify field_id is correct. "
            "Use find_fields to find valid field IDs."
        )
    except EMSAPIError as e:
        return f"Error getting field info: {e.message}"


@mcp.tool
async def search_analytics(
    ems_system_id: int,
    search_text: str,
    group_id: str | None = None,
    max_results: int = 50,
    show_ids: bool = False,
) -> str:
    """Search for time-series analytics by name (altitude, airspeed, etc.).

    You can pass analytic names directly to query_flight_analytics -- raw IDs
    are not needed. Use this tool to discover available analytic names.

    Args:
        ems_system_id: EMS system ID.
        search_text: Keyword to search for in analytic names.
        group_id: Optional analytic group ID to narrow search.
        max_results: Maximum results (default: 50).
        show_ids: If True, show full IDs inline.

    Returns:
        Matching analytics with names, types, units, and descriptions.
    """
    client = get_client()

    cache_key = make_cache_key(
        "analytics_search", ems_system_id, search_text.lower(), group_id or "all"
    )
    cached = await field_cache.get(cache_key)
    if cached is not None:
        logger.debug("Using cached analytics search: %s", cache_key)
        return _format_analytics_search_results(
            cached[:max_results], show_ids=show_ids,
            ems_system_id=ems_system_id,
        )

    try:
        path = f"/api/v2/ems-systems/{ems_system_id}/analytics"
        params: dict[str, str] = {"text": search_text}
        if group_id:
            params["groupId"] = group_id

        analytics = await client.get(path, params=params)
        await field_cache.set(cache_key, analytics)
        return _format_analytics_search_results(
            analytics[:max_results], show_ids=show_ids,
            ems_system_id=ems_system_id,
        )
    except EMSNotFoundError:
        return f"Error: EMS system {ems_system_id} not found. Use list_ems_systems to find valid system IDs."
    except EMSAPIError as e:
        return f"Error searching analytics: {e.message}"


@mcp.tool
async def get_result_id(
    result_numbers: list[int],
) -> str:
    """DEPRECATED: query_database and get_field_info now accept [N] reference
    numbers and field names directly. This tool is no longer needed in the
    standard workflow.

    Retrieve full opaque IDs for numbered [N] references from search results.

    Args:
        result_numbers: Reference numbers from search results (e.g., [1, 3, 5]).

    Returns:
        The name and full ID for each requested result.
    """
    if not result_numbers:
        return "Error: result_numbers cannot be empty."

    lines: list[str] = []
    not_found: list[int] = []

    for ref in result_numbers:
        entry = _get_stored_result(ref)
        if entry is not None:
            type_label = f" ({entry['type']})" if entry.get("type") else ""
            lines.append(f"[{ref}] {entry['name']}{type_label}")
            lines.append(f"  ID: {entry['id']}")
        else:
            not_found.append(ref)

    if not_found:
        lines.append(
            f"\nNot found: {not_found}. These may have been evicted or never existed. "
            "Re-run the search to get fresh references."
        )

    return "\n".join(lines) if lines else "No results found for the given reference numbers."


# ---------------------------------------------------------------------------
# Legacy aliases for backward compatibility in imports/tests
# ---------------------------------------------------------------------------
# These are the old tools that have been consolidated into find_fields.
# They still work as standalone functions but are NOT registered as MCP tools.

async def _legacy_list_fields(
    ems_system_id: int,
    database_id: str,
    group_id: str | None = None,
) -> str:
    """Legacy wrapper: browse fields (now find_fields mode=browse)."""
    return await _do_browse_fields(ems_system_id, database_id, group_id)


async def _legacy_search_fields(
    ems_system_id: int,
    database_id: str,
    search_text: str,
    max_results: int = 50,
    show_ids: bool = False,
) -> str:
    """Legacy wrapper: search fields (now find_fields mode=search)."""
    return await _do_search_fields(
        ems_system_id, database_id, search_text, max_results, show_ids,
    )


async def _legacy_search_fields_deep(
    ems_system_id: int,
    database_id: str,
    search_text: str,
    max_depth: int = 5,
    max_results: int = 20,
    max_groups: int = 50,
    show_ids: bool = False,
) -> str:
    """Legacy wrapper: deep search fields (now find_fields mode=deep)."""
    return await _do_deep_search_fields(
        ems_system_id, database_id, search_text,
        max_results, max_depth, max_groups, show_ids,
    )
