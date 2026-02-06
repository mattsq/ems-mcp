# EMS MCP Experience Report 3
**Date:** February 6, 2026
**Subject:** Road-Testing and Quality Assurance of the ems-mcp

## 1. Regression Testing & Stability Improvements
Several issues noted in previous reports appear to have been addressed, though some remain persistent.
- **`ping_system` (Fixed):** Now correctly returns system status without Python attribute errors.
- **`get_field_info` (Fixed/Stable):** Successfully retrieved discrete value mappings for fields like "Tail Number" without `slice` errors.
- **`query_flight_analytics` (Functional):** Unlike previous reports, I was able to retrieve actual time-series data (e.g., Airspeed) for Flight ID 1.

## 2. Persistent Critical Bug: `search_fields` (HTTP 405)
The `search_fields` tool remains the most significant hurdle for discovery.
- **Error:** Consistently returns `HTTP 405 Method Not Allowed`.
- **Impact:** Users cannot find specific fields (like "takeoff date") by name. They must instead navigate the entire hierarchical tree via `list_fields`, which is time-consuming and error-prone.

## 3. Silent Failure in `query_flight_analytics`
A new critical data integrity issue was discovered during "boundary testing."
- **Issue:** Querying analytics for a non-existent Flight ID (e.g., `999999`) does not return an error. Instead, it returns a 5000-row table filled with `0.0` values.
- **Risk:** This is a major QA concern. An analyst might assume a flight has zeroed-out data (e.g., zero airspeed for 60 seconds) instead of realizing the flight record itself is invalid. The MCP should return an error or "No data found" for invalid IDs.

## 4. Metadata Display Issues ("The Unknowns")
Many asset-listing tools fail to display human-readable names.
- **Observation:** `list_fleets`, `list_aircraft`, and `list_flight_phases` frequently prefix items with `Unknown (ID: X)`.
- **Details:** The actual names are often present in the description field (e.g., `QFA - 737-800`), suggesting the MCP is looking for a "name" property in the API response that is either missing or differently named (e.g., `label` or `description`).

## 5. Analytic Header Readability
The output of `query_flight_analytics` uses raw Base64 analytic IDs as column headers.
- **Critique:** Without the ability to use aliases (as in `query_database`), the results are very difficult to read. A user must manually keep track of which long ID corresponds to "Altitude" vs "Airspeed".

## Recommendations for the Next Sprint
1.  **Prioritize `search_fields`**: Fix the HTTP method routing for the field search endpoint.
2.  **Validate Flight IDs**: Update `query_flight_analytics` to return an error for non-existent flight IDs rather than zero-filled tables.
3.  **Refine Metadata Mapping**: Update the asset listing logic to use descriptions or labels when the primary "name" field is null.
4.  **Friendly Analytic Headers**: Automatically use the analytic's name in the headers of `query_flight_analytics` results to improve interpretability.

---
**QA Status:** Improved stability in core queries, but discovery tools and data validation for edge cases (invalid IDs) require immediate attention.
