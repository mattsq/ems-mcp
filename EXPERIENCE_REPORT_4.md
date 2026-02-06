# Experience Report #4: Road-Testing ems-mcp for Flight Data Analytics

## Overview
This report details a road-test of the `ems-mcp` server, conducted from the perspective of a QA engineer. The objective was to evaluate the reliability, discoverability, and efficiency of the MCP when performing exploratory data analysis on the Qantas EMS system.

## Session Log Summary

### Task 1: Database Discovery and Hierarchy Navigation
*   **Action**: Navigated from the root EMS system to the `FDW Flights` database.
*   **Result**: Successfully explored nested field groups such as `Flight Information`, `Aircraft Information`, and `Navigation Information`.
*   **Observation**: The `search_fields` tool is restricted for entity-type databases, which forced a manual, multi-step traversal of nested field groups to find specific measurements.

### Task 2: Fleet Data Retrieval
*   **Action**: Identified the correct Airframe ID for the **DHC-8-400 (Q400)** and queried flight counts for January 2026.
*   **Result**: **6,009** flights identified.
*   **Validation**: Cross-referenced with the 737-800 (7,765 flights) and A380-800 (381 flights) to verify data scale and query accuracy.

### Task 3: Complex Measurement and Statistical Analysis
*   **Action**: Located the **P6: Fuel Burned during Cruise (all engines) (kg)** measurement within the APM Profile hierarchy.
*   **Process**:
    1.  Navigated: `Profiles` -> `Efficiency` -> `Standard` -> `P6: Fuel Ops Measurements`.
    2.  Drilled down through: `Measured Items` -> `Cruise` -> `Measurements` -> `Fuel` -> `Engines` -> `Actual` -> `Metric`.
    3.  Retrieved fuel burn data for all 6,009 Q400 flights.
*   **Result**: Calculated an average cruise fuel burn of **473.5 kg**.

## Challenges and Roadblocks
1.  **Search Limitations**: Finding a specific measurement (e.g., cruise fuel burn) required 8+ consecutive hierarchy navigation calls because global field search was unavailable for the target database.
2.  **Manual ID Mapping**: Filtering by descriptive names (like "DHC-8-400") requires a prerequisite `get_field_info` call to retrieve the internal integer code (31). This adds friction to the query workflow.
3.  **Local Processing Overhead**: Calculating basic statistics (averages, counts) currently requires downloading thousands of rows into the LLM context. This is token-intensive and potentially hits context limits for larger fleets.

## Recommendations for Improvement

### 1. Implement Server-Side Aggregations
*   **Recommendation**: Add a `query_database_aggregate` tool supporting `AVG`, `SUM`, `COUNT`, `MIN`, and `MAX`.
*   **Benefit**: Drastically reduces token usage and latency for statistical inquiries.

### 2. Universal/Recursive Field Search
*   **Recommendation**: Enable `search_fields` for entity-type databases or implement a recursive search tool.
*   **Benefit**: Allows users to find "Fuel Burn" or "Altitude" without knowing the exact subgroup nesting.

### 3. Filter Resolution by Display Name
*   **Recommendation**: Enhance `query_database` to resolve string labels for discrete fields internally.
*   **Benefit**: Simplifies the user experience (e.g., `Takeoff Airport = "YSSY"` instead of code `361`).

### 4. Schema "Breadcrumbs" or Shortcuts
*   **Recommendation**: Provide a tool to retrieve a flattened list of "Frequently Used Fields" or a high-level schema overview.
*   **Benefit**: Reduces the "blind crawl" through deep metadata hierarchies.

## Final Verdict
The `ems-mcp` is **stable and functionally robust**. It correctly exposes the deep capabilities of the EMS API. However, it is currently "chatty"—requiring many round-trips for discovery. By moving aggregation and ID resolution to the server side, it would become an exceptionally efficient tool for data science and operational reporting.
