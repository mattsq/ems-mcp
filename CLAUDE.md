# EMS API MCP Server

An MCP (Model Context Protocol) server that provides LLM access to the EMS (Engine Monitoring System) API for flight data analytics.

## Project Overview

This MCP server wraps the EMS REST API, allowing AI assistants like Claude to:
- Discover EMS systems, databases, and fields
- Query flight records from the Flight Data Warehouse
- Retrieve time-series analytics data for flights
- Access asset information (fleets, aircraft, airports)

The server follows MCP specification 2025-06-18 and uses FastMCP for Python implementation.

## Quick Start

```bash
# Create virtual environment
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
uv pip install -e .

# Set credentials
export EMS_BASE_URL="https://your-ems-server.com"
export EMS_USERNAME="your-username"
export EMS_PASSWORD="your-password"

# Run server (stdio transport)
python -m ems_mcp

# Or run with MCP Inspector for testing
fastmcp dev src/ems_mcp/server.py
```

## Development Guidelines

### Code Style

- **Python 3.11+** required
- **Type hints** on all functions
- **Async/await** for all API calls
- **Docstrings** in Google format
- **Black** for formatting, **ruff** for linting

### Project Structure

```
ems-mcp/
    src/
        ems_mcp/
            __init__.py
            server.py         # FastMCP server definition
            tools/
                discovery.py  # list_ems_systems, list_databases, etc.
                query.py      # query_database, query_flight_analytics
                assets.py     # list_fleets, list_aircraft, etc.
            api/
                client.py     # EMS API HTTP client
                auth.py       # OAuth token management
                models.py     # Pydantic models for API types
            cache.py          # In-memory caching layer
            config.py         # Configuration management
    tests/
        unit/
        integration/
        mock_server.py        # Mock EMS API for testing
    docs/
        design.md             # Architecture and design document
        research/             # MCP and EMS research notes
    pyproject.toml
    CLAUDE.md                 # This file
```

### Key Files

| File | Purpose |
|------|---------|
| `src/ems_mcp/server.py` | Main FastMCP server with tool decorators |
| `src/ems_mcp/api/client.py` | HTTP client for EMS API with retry logic |
| `src/ems_mcp/api/auth.py` | OAuth 2.0 token acquisition and refresh |
| `src/ems_mcp/tools/discovery.py` | Discovery tools (list_databases, search_fields) |
| `src/ems_mcp/tools/query.py` | Query tools (query_database, query_flight_analytics) |
| `docs/design.md` | Comprehensive design document with all tool schemas |

### Adding a New Tool

1. **Define the tool function** in the appropriate module:

```python
# src/ems_mcp/tools/discovery.py
from ems_mcp.server import mcp
from ems_mcp.api.client import EMSClient

@mcp.tool
async def my_new_tool(
    ems_system_id: int,
    some_param: str
) -> str:
    """Short description of what this tool does.

    Args:
        ems_system_id: The EMS system ID to query
        some_param: Description of this parameter

    Returns:
        Description of the return value
    """
    client = await EMSClient.get_instance()
    result = await client.some_api_call(ems_system_id, some_param)
    return format_result(result)
```

2. **FastMCP automatically:**
   - Extracts the function name as tool name
   - Uses the docstring for description
   - Generates JSON Schema from type hints
   - Handles MCP protocol formatting

3. **Add tests** in `tests/unit/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_my_new_tool_validates_input():
    with pytest.raises(ValidationError):
        await my_new_tool(ems_system_id="invalid")  # Should be int
```

4. **Update documentation** if the tool is user-facing.

### Authentication Flow

The server uses OAuth 2.0 password grant:

```
1. On first API call, check for valid cached token
2. If no token or expired, call POST /api/token with credentials
3. Cache token with expiry time (1799 seconds typical)
4. Refresh 60 seconds before expiry to prevent mid-request failures
5. Include token in Authorization: Bearer header for all requests
```

Credentials come from environment variables only:
- `EMS_USERNAME`: Username or email
- `EMS_PASSWORD`: Password
- `EMS_BASE_URL`: API server URL (e.g., https://ems.example.com)

### Error Handling

All tools should:
1. Return descriptive error messages
2. Set `isError: true` on tool result for failures
3. Suggest next steps when possible

```python
try:
    result = await client.query(...)
except FieldNotFoundError as e:
    return {
        "content": [{"type": "text", "text": f"Field not found: {e.field_id}. Use search_fields to find valid field IDs."}],
        "isError": True
    }
```

### Testing

```bash
# Run unit tests
pytest tests/unit/

# Run integration tests (requires credentials)
EMS_USERNAME=user EMS_PASSWORD=pass pytest tests/integration/

# Run with coverage
pytest --cov=ems_mcp --cov-report=html

# Type checking
mypy src/ems_mcp/
```

### Common Tasks

#### Debugging API Calls

Enable debug logging to see HTTP requests:
```bash
EMS_LOG_LEVEL=DEBUG python -m ems_mcp
```

#### Testing with MCP Inspector

```bash
fastmcp dev src/ems_mcp/server.py
# Opens browser at http://127.0.0.1:6274
# Use Tools tab to test individual tools
```

#### Updating Token Handling

Token management is in `src/ems_mcp/api/auth.py`:
- `TokenManager.get_token()` - Get valid token (refreshing if needed)
- `TokenManager._request_token()` - Actually call /api/token
- `TokenManager._is_token_valid()` - Check expiry with buffer

#### Adding Field Caching

Field IDs are stable and can be cached. Add to `src/ems_mcp/cache.py`:
```python
@cache(ttl=3600)
async def get_field_info(db_id: str, field_id: str) -> FieldInfo:
    ...
```

## Architecture Notes

### Why FastMCP?

- **High-level API**: Decorators handle MCP protocol complexity
- **Auto-generated schemas**: Type hints become JSON Schema
- **Built-in testing**: MCP Inspector for interactive debugging
- **Python-native**: async/await, type hints, Pydantic integration
- **Active maintenance**: Part of official MCP ecosystem

### Tool Design Principles

1. **Discovery before action**: Users must discover field IDs before querying
2. **Reasonable defaults**: Limit results, use display format by default
3. **Descriptive errors**: Tell users what went wrong and how to fix it
4. **Cacheable where possible**: Asset data changes infrequently

### EMS API Characteristics

- **Field IDs are opaque**: Cannot be constructed, must be discovered
- **Discrete fields use numeric codes**: Filters require codes, not labels
- **Queries can be expensive**: Always support limits
- **Token expires in ~30 minutes**: Must handle refresh

### Data Flow

```
User Request (natural language)
        |
        v
Claude (LLM) interprets request
        |
        v
MCP Tool Call (JSON-RPC)
        |
        v
EMS MCP Server validates input
        |
        v
EMS API HTTP Request
        |
        v
Response parsing & formatting
        |
        v
MCP Tool Result (JSON-RPC)
        |
        v
Claude formats response
        |
        v
User sees result
```

## Reference Documentation

- **MCP Specification**: https://modelcontextprotocol.io/specification/2025-06-18
- **FastMCP Documentation**: https://gofastmcp.com
- **Design Document**: `docs/design.md`
- **EMS API Docs**: `Rems2/api_docs/` (extracted from EMS developer portal)

## Common Patterns

### Typical Query Workflow

1. `list_ems_systems` - Find available systems
2. `list_databases` - Navigate to find FDW Flights database
3. `search_fields` - Find field IDs for desired columns
4. `get_field_info` - Get discrete value mappings if needed
5. `query_database` - Execute query with discovered field IDs

### Typical Analytics Workflow

1. `query_database` - Get flight record IDs for flights of interest
2. `search_analytics` - Find analytic IDs (e.g., "Altitude", "Airspeed")
3. `query_flight_analytics` - Get time-series data for specific flights

## Troubleshooting

### "401 Unauthorized" errors
- Check `EMS_USERNAME` and `EMS_PASSWORD` are set correctly
- Verify the user has API access in EMS admin console
- Check if password has special characters that need escaping

### "Field not found" errors
- Field IDs must be discovered via `list_fields` or `search_fields`
- Field IDs are system-specific; don't copy from other systems
- Field IDs are long bracket-encoded strings, not simple names

### "Discrete value mismatch" errors
- Discrete field filters need numeric codes, not string labels
- Use `get_field_info` to see the value-to-label mapping
- When filtering on "VH-ABC", first find its numeric code (e.g., 407)

### Query timeouts
- Reduce `limit` parameter
- Add filters to reduce result set
- For time-series, use `start_offset` and `end_offset`

### Rate limiting
- EMS API may return 429 if overloaded
- Server has built-in retry with backoff
- Consider caching repeated queries

---

## Implementation Progress

### Phase 1: Core Infrastructure - COMPLETED

Established foundational infrastructure: project setup, authentication, HTTP client with retry logic, and server skeleton.

**Files Created:** 19 files including config, auth, client, cache, and server modules.
**Test Coverage:** 66 unit tests, 90% coverage.

### Phase 2: Discovery Tools - COMPLETED

Implemented all 6 discovery tools for EMS systems, databases, fields, and analytics.

**Tools Implemented:**
1. `list_ems_systems` - GET /api/v2/ems-systems
2. `list_databases` - GET /api/v2/ems-systems/{id}/database-groups
3. `list_fields` - GET /api/v2/ems-systems/{id}/databases/{db}/field-groups
4. `search_fields` - GET /api/v2/ems-systems/{id}/databases/{db}/fields
5. `get_field_info` - GET /api/v2/ems-systems/{id}/databases/{db}/fields/{field}
6. `search_analytics` - GET /api/v2/ems-systems/{id}/analytics

**Files Created:**
- `src/ems_mcp/tools/discovery.py` - 460 lines implementing all discovery tools
- `tests/unit/test_discovery.py` - 35 unit tests for discovery tools

**Key Features:**
- Human-readable output formatting with truncation for long IDs
- In-memory caching with TTL for repeated queries
- Proper URL encoding for field IDs with special characters
- Comprehensive error handling with helpful suggestions
- Support for hierarchical navigation (database groups, field groups)

**Test Coverage:** 101 unit tests total, 90% coverage.

### Phase 3: Query Tools - COMPLETED

Implemented tools for querying database records and time-series analytics.

**Tools Implemented:**
1. `query_database` - POST /api/v2/ems-systems/{id}/databases/{db}/query
2. `query_flight_analytics` - POST /api/v2/ems-systems/{id}/flights/{fid}/analytics/query

**Files Created:**
- `src/ems_mcp/tools/query.py` - Implementation of query tools and formatters
- `tests/unit/test_query.py` - Unit tests for query tools

**Key Features:**
- Support for complex filters (equal, notEqual, between, in, like, isNull, etc.)
- Automatic wrapping of multiple filters in 'and' operator
- Result formatting as fixed-width text tables
- Support for field aliasing in results
- Multi-flight analytics retrieval with per-flight tables

### Phase 4: Asset Tools & Utilities - COMPLETED

Implemented tools for accessing reference data (fleets, aircraft, airports, phases) and system health.

**Tools Implemented:**
1. `list_fleets` - GET /api/v2/ems-systems/{id}/assets/fleets
2. `list_aircraft` - GET /api/v2/ems-systems/{id}/assets/aircraft
3. `list_airports` - GET /api/v2/ems-systems/{id}/assets/airports
4. `list_flight_phases` - GET /api/v2/ems-systems/{id}/assets/flight-phases
5. `ping_system` - GET /api/v2/ems-systems/{id}/ping

**Files Created:**
- `src/ems_mcp/tools/assets.py` - Implementation of asset and utility tools
- `tests/unit/test_assets.py` - Unit tests for asset tools

**Key Features:**
- Comprehensive coverage of EMS reference data
- Helpful formatting of airport codes (ICAO/IATA) and locations
- Support for filtering aircraft by fleet
- Simple health check utility with multi-format response handling
