# EMS MCP Experience Report 2
**Date:** February 5, 2026
**Subject:** Critical Evaluation of EMS MCP Functionality

## 1. Internal Server Errors (Python Tracebacks)
The `get_field_info` tool appears to have a significant bug when retrieving metadata for certain fields.
- **Error:** `Error calling tool 'get_field_info': slice(None, 3, None)` and `slice(None, 50, None)`.
- **Observation:** These look like internal Python `slice` object errors, likely occurring during data processing or mapping within the MCP server itself. This prevents the LLM from understanding discrete value mappings (e.g., what "1" means for a specific flight phase or flag).

## 2. Tool Routing / Database ID Ambiguity
The `search_fields` tool consistently failed with an **HTTP 405 Method Not Allowed** when using Database IDs provided by `list_databases`.
- **Database ID used:** `[ems-core][entity-type][foqa-flights]`
- **Error Message:** Claimed the ID was invalid or a "group ID", despite `list_databases` explicitly listing it as a "Database".
- **Critique:** Either the MCP is misidentifying IDs in `list_databases`, or the `search_fields` endpoint is incorrectly configured to handle the standard FDW database identifiers.

## 3. Suspected Data Retrieval Silent Failures
The `query_flight_analytics` tool returned "No data returned" for every attempt across multiple flights and time offsets.
- **Context:** Flight `4379991` was confirmed to have `1.1` hours of data and `Flight Data Exists = True`.
- **Offsets tried:** `0-10`, `1000-1010`, `0-120`, `0-600`.
- **Critique:** While it's possible the specific analytics requested weren't available, the lack of data for standard parameters like `ALTITUDE (FT)` or `Airspeed` across different flights suggests a potential bug in how the MCP constructs the underlying API request or parses the time-series response.

## 4. UX and ID Complexity
The identifiers used by the MCP are extremely verbose and nested (e.g., `[-hub-][field][[[ems-core][entity-type][foqa-flights]][[ems-core][base-field][flight.uid]]]`).
- **Issue:** While these may reflect the underlying EMS API, they are prone to truncation or interpretation errors when handled by an LLM.
- **Suggestion:** The MCP could benefit from a "Short ID" mapping or a more flattened identifier structure to improve reliability in multi-step tool chains.

## 5. Metadata Visibility
The current implementation makes it difficult to discover the "unit" or "type" of a field without calling `get_field_info`, which as noted above, is currently unstable. This creates a "blind spot" when trying to filter database queries (e.g., needing to know if a value should be a string or a numeric code).

---
**Conclusion:** The EMS MCP provides powerful access to the flight data warehouse but currently suffers from stability issues in metadata retrieval and potential routing bugs for field searching.
