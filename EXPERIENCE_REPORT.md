# EMS MCP Experience Report

This report summarizes the experience of using the `ems-mcp` server to explore the Qantas EMS database.

## Overview
The exploration involved navigating hierarchical databases, discovering flight information fields, searching for time-series analytics, and executing data queries. While core navigation functions were reliable, several technical hurdles hindered efficient data retrieval.

## What Worked (Successes)
*   **System Discovery:** `list_ems_systems` successfully identified the "Qantas-EMS" system.
*   **Database Navigation:** `list_databases` effectively traversed the hierarchy from root to specific databases like "FDW Flights" and "ODW Flights."
*   **Analytics Discovery:** `search_analytics` worked well, providing a wide range of time-series IDs (e.g., Altitude, TCAS rates) with descriptive metadata.
*   **Asset Listing:** `list_airports`, `list_fleets`, and `list_flight_phases` provided valuable context about the available reference data.
*   **Successful Querying:** Once a valid field ID was identified (via codebase inspection), `query_database` successfully retrieved records (e.g., Flight Record IDs 1-5).

## What Didn't Work (Challenges & Bugs)
*   **HTTP Method Error in `search_fields`:** This tool consistently failed with a `405 Method Not Allowed` error (`The requested resource does not support http method 'GET'`). This was the most significant blocker, as searching by name is the primary way to find opaque field IDs.
*   **ID Truncation:** `list_fields` truncated field IDs (e.g., `ID: [-hub-][field][[[ems-core][entity-type][foqa-fligh...`). Since these IDs are opaque and must be provided exactly to `query_database` or `get_field_info`, truncation made the discovered fields unusable without external reference.
*   **Bug in `ping_system`:** Calling `ping_system` resulted in a Python exception: `Error calling tool 'ping_system': 'bool' object has no attribute 'get'`.
*   **Opaque Field ID Logic:** The requirement for long, bracketed strings (e.g., `[-hub-][field][[[ems-core][entity-type][foqa-flights]][[ems-core][base-field][flight.uid]]]`) makes manual "guessing" impossible, heightening the impact of the search and truncation issues.

## Key Discoveries
*   **Metadata Importance:** The EMS API relies heavily on discovery. You cannot construct queries without first extracting the specific, system-generated bracketed IDs for every field.
*   **Codebase as Documentation:** The most reliable way to find working field IDs during this session was searching the existing R package (`Rems2`) tests and documentation rather than using the MCP discovery tools themselves.

## Recommendations for Improvement
1.  **Fix `search_fields`:** Investigate why the MCP server is attempting a `GET` request if the underlying EMS API requires a different method, or fix the routing.
2.  **Remove ID Truncation:** Modify the formatting logic in `discovery.py` to return full field IDs. While they are long, they are essential for the next steps in the workflow.
3.  **Fix `ping_system`:** Correct the response handling in `assets.py` to avoid the `'bool' object has no attribute 'get'` error.
4.  **Add ID Search to `list_fields`:** Optionally allow `list_fields` to return a raw JSON format or a "copyable" version of the IDs to avoid manual transcription errors.
