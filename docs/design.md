# EMS API MCP Server Design Document

## Executive Summary

### Purpose
This document describes the architecture and implementation plan for an MCP (Model Context Protocol) server that provides LLM access to the EMS (Engine Monitoring System) API. The server enables AI assistants like Claude to query flight data, discover analytics, and interact with EMS databases through natural language.

### Scope
The MCP server will expose EMS API functionality as MCP tools, allowing LLMs to:
- Discover and navigate EMS systems, databases, and fields
- Execute database queries for flight information
- Query time-series analytics data for flights
- Access asset information (fleets, aircraft, airports)

### Target Users
- Claude Code and Claude Desktop users
- Any MCP-compatible AI application
- Developers building AI-powered flight analytics tools

---

## Architecture Overview

### System Architecture

```
+-------------------+        +------------------+        +-------------+
|   MCP Client      |  MCP   |   EMS MCP        |  HTTP  |   EMS API   |
|   (Claude, etc.)  | <----> |   Server         | <----> |   Server    |
+-------------------+        +------------------+        +-------------+
                                    |
                             +------+------+
                             |             |
                          Token         Field/DB
                          Cache         Cache
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **SDK** | FastMCP (Python) | High-level abstractions, automatic schema generation, active maintenance |
| **Transport** | stdio (primary), HTTP/SSE (optional) | stdio for local use, HTTP for remote deployment |
| **Async Strategy** | async/await | EMS API is I/O bound; async improves throughput |
| **Caching** | In-memory with optional persistence | Field IDs are stable; reduce API calls |
| **Authentication** | Environment variables | Follows MCP security patterns; no credentials in config |

### MCP Capabilities Declaration

```python
{
    "capabilities": {
        "tools": {
            "listChanged": false  # Tool list is static
        },
        "resources": {
            "subscribe": false,   # Resources don't change in real-time
            "listChanged": false
        }
    }
}
```

---

## Tool Specifications

### Category 1: Discovery Tools (Read-only, Low-cost)

#### 1.1 `list_ems_systems`

**Purpose:** List available EMS systems the user can access.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {},
    "required": []
}
```

**Output:** Array of EMS system objects with id, name, description.

**Example Response:**
```json
{
    "content": [
        {
            "type": "text",
            "text": "Found 2 EMS systems:\n1. Production (ID: 1) - Main production system\n2. Test (ID: 2) - Testing environment"
        }
    ]
}
```

**Error Conditions:**
- 401: Authentication failed (token expired or invalid)
- 500: EMS server unreachable

---

#### 1.2 `list_databases`

**Purpose:** Navigate database hierarchy to find available databases.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID (from list_ems_systems)"
        },
        "group_id": {
            "type": "string",
            "description": "Optional group ID to navigate into. Omit for root level."
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** Object containing databases and subgroups at the specified level.

**Usage Notes:**
- Call without group_id first to see root level
- Use returned group IDs to navigate deeper
- Common database: "FDW Flights" (Flight Data Warehouse)

---

#### 1.3 `list_fields`

**Purpose:** Navigate field hierarchy within a database.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "database_id": {
            "type": "string",
            "description": "Database ID (from list_databases)"
        },
        "group_id": {
            "type": "string",
            "description": "Optional field group ID to navigate into"
        }
    },
    "required": ["ems_system_id", "database_id"]
}
```

**Output:** Object containing fields and field groups at the specified level.

---

#### 1.4 `search_fields`

**Purpose:** Search for fields by name within a database.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "database_id": {
            "type": "string",
            "description": "Database ID"
        },
        "search_text": {
            "type": "string",
            "description": "Text to search for in field names (case-insensitive partial match)"
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum results to return (default: 50)",
            "default": 50
        }
    },
    "required": ["ems_system_id", "database_id", "search_text"]
}
```

**Output:** Array of matching fields with id, name, type, and units.

---

#### 1.5 `get_field_info`

**Purpose:** Get detailed information about a specific field, including discrete value mappings.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "database_id": {
            "type": "string",
            "description": "Database ID"
        },
        "field_id": {
            "type": "string",
            "description": "Field ID (from list_fields or search_fields)"
        }
    },
    "required": ["ems_system_id", "database_id", "field_id"]
}
```

**Output:** Detailed field information including type, units, description, and discrete value mapping if applicable.

---

#### 1.6 `search_analytics`

**Purpose:** Search for time-series analytics by name.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "search_text": {
            "type": "string",
            "description": "Text to search for in analytic names"
        },
        "group_id": {
            "type": "string",
            "description": "Optional: limit search to specific analytic group"
        }
    },
    "required": ["ems_system_id", "search_text"]
}
```

**Output:** Array of matching analytics with id, name, type, units, description.

---

### Category 2: Query Tools (Parameterized, May Be Expensive)

#### 2.1 `query_database`

**Purpose:** Execute a database query to retrieve flight records.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "database_id": {
            "type": "string",
            "description": "Database ID (typically FDW Flights)"
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "alias": {"type": "string", "description": "Optional column name for results"}
                },
                "required": ["field_id"]
            },
            "description": "Fields to retrieve"
        },
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["equal", "notEqual", "greaterThan", "greaterThanOrEqual",
                                 "lessThan", "lessThanOrEqual", "in", "isNull", "isNotNull",
                                 "like", "between"]
                    },
                    "value": {
                        "description": "Filter value (type depends on field)"
                    }
                },
                "required": ["field_id", "operator"]
            },
            "description": "Filter conditions (combined with AND)"
        },
        "order_by": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "direction": {"type": "string", "enum": ["asc", "desc"]}
                },
                "required": ["field_id"]
            },
            "description": "Sort order"
        },
        "limit": {
            "type": "integer",
            "description": "Maximum rows to return (default: 100, max: 10000)",
            "default": 100
        },
        "format": {
            "type": "string",
            "enum": ["display", "raw"],
            "description": "Value format: 'display' for labels, 'raw' for numeric codes",
            "default": "display"
        }
    },
    "required": ["ems_system_id", "database_id", "fields"]
}
```

**Output:** Query results as a formatted table or JSON array.

**Important Notes:**
- Field IDs must be discovered via `list_fields` or `search_fields`
- Discrete field filters require numeric codes (use `get_field_info` to find mappings)
- Large queries should use `limit` to avoid timeouts

---

#### 2.2 `query_flight_analytics`

**Purpose:** Query time-series data for one or more flights.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "flight_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Flight record IDs (from query_database)",
            "minItems": 1,
            "maxItems": 10
        },
        "analytics": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Analytic IDs (from search_analytics)",
            "minItems": 1,
            "maxItems": 20
        },
        "start_offset": {
            "type": "number",
            "description": "Start time in seconds from flight start"
        },
        "end_offset": {
            "type": "number",
            "description": "End time in seconds from flight start"
        },
        "sample_rate": {
            "type": "number",
            "description": "Samples per second (default: 1.0)",
            "default": 1.0
        }
    },
    "required": ["ems_system_id", "flight_ids", "analytics"]
}
```

**Output:** Time-series data with columns: flight_id, time, and each requested analytic.

**Important Notes:**
- Flight IDs must come from a database query
- Analytic IDs must be discovered via `search_analytics`
- Large time ranges may be slow; use `start_offset`/`end_offset` to limit
- Maximum 10 flights and 20 analytics per call to prevent timeouts

---

### Category 3: Asset Tools (Read-only, Cacheable)

#### 3.1 `list_fleets`

**Purpose:** List aircraft fleets available in the EMS system.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** Array of fleet objects with id, name, description.

---

#### 3.2 `list_aircraft`

**Purpose:** List aircraft in the EMS system.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "fleet_id": {
            "type": "integer",
            "description": "Optional: filter to specific fleet"
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** Array of aircraft objects with id, tail number, fleet, aircraft type.

---

#### 3.3 `list_airports`

**Purpose:** List airports known to the EMS system.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        },
        "visited_only": {
            "type": "boolean",
            "description": "Only show airports with flight data (default: true)",
            "default": true
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** Array of airport objects with id, ICAO code, name, location.

---

#### 3.4 `list_flight_phases`

**Purpose:** List flight phases used in the EMS system.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** Array of flight phase objects with id, name, description.

---

### Category 4: Utility Tools

#### 4.1 `ping_system`

**Purpose:** Check if an EMS system is online and responsive.

**Input Schema:**
```json
{
    "type": "object",
    "properties": {
        "ems_system_id": {
            "type": "integer",
            "description": "EMS system ID"
        }
    },
    "required": ["ems_system_id"]
}
```

**Output:** System status (online/offline) and server timestamp.

---

## Resource Specifications

### Static Resources

The MCP server exposes system information as resources that can be pre-fetched by clients:

#### `ems://systems`
**URI:** `ems://systems`
**Description:** List of available EMS systems
**MIME Type:** `application/json`

#### `ems://systems/{id}/databases`
**URI Template:** `ems://systems/{systemId}/databases`
**Description:** Database catalog for an EMS system
**MIME Type:** `application/json`

### Resource Annotations

Resources include standard MCP annotations:
```json
{
    "audience": ["assistant"],
    "priority": 0.5,
    "lastModified": "2024-01-01T00:00:00Z"
}
```

---

## Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `EMS_BASE_URL` | Yes | Base URL of EMS API server | - |
| `EMS_USERNAME` | Yes | Username for authentication | - |
| `EMS_PASSWORD` | Yes | Password for authentication | - |
| `EMS_DEFAULT_SYSTEM` | No | Default EMS system ID | First available |
| `EMS_CACHE_TTL` | No | Cache time-to-live in seconds | 3600 |
| `EMS_REQUEST_TIMEOUT` | No | Request timeout in seconds | 120 |
| `EMS_LOG_LEVEL` | No | Logging level | INFO |

### Configuration File (Optional)

For non-sensitive settings, a YAML config file is supported:

**Location:** `~/.config/ems-mcp/config.yaml` or `./ems-mcp.yaml`

```yaml
# EMS MCP Server Configuration
server:
  name: "ems-mcp"
  version: "1.0.0"

defaults:
  system_id: 1
  database_id: "ems-core"
  query_limit: 100

cache:
  enabled: true
  ttl_seconds: 3600
  max_entries: 10000

logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

rate_limiting:
  enabled: true
  requests_per_minute: 60
  burst_limit: 10
```

---

## Security Considerations

### Credential Handling

1. **Environment Variables Only**: Credentials are read exclusively from environment variables
2. **No Credential Storage**: Server never writes credentials to disk
3. **Token Caching**: OAuth tokens cached in memory only, cleared on exit
4. **Token Refresh**: Automatic refresh ~60 seconds before expiry

### Input Validation

1. **Schema Validation**: All tool inputs validated against JSON Schema
2. **Field ID Validation**: Field IDs verified to match expected format
3. **Query Limits**: Hard limits on result sizes (max 10,000 rows)
4. **String Sanitization**: SQL-like patterns in filters are escaped

### Rate Limiting

1. **Per-Tool Limits**: Expensive tools (queries) have lower limits
2. **Token Bucket**: 60 requests/minute with burst of 10
3. **Retry-After Headers**: Respect EMS API rate limit responses
4. **Backoff Strategy**: Exponential backoff on 429 responses

### Audit Logging

```python
# Log format for security auditing
{
    "timestamp": "2024-01-01T12:00:00Z",
    "tool": "query_database",
    "user": "from_client",  # If available from MCP client
    "ems_system": 1,
    "database": "ems-core",
    "fields_requested": 5,
    "filters_count": 2,
    "result_rows": 100,
    "duration_ms": 1523
}
```

---

## Error Handling Strategy

### Error Categories

| Category | HTTP Status | MCP Handling | Retry |
|----------|-------------|--------------|-------|
| Authentication | 401 | Re-authenticate, retry once | Yes (1x) |
| Authorization | 403 | Return error to user | No |
| Not Found | 404 | Return descriptive error | No |
| Rate Limited | 429 | Wait and retry with backoff | Yes (3x) |
| Client Error | 4xx | Return validation error | No |
| Server Error | 5xx | Retry with backoff | Yes (3x) |
| Timeout | - | Retry with increased timeout | Yes (2x) |
| Network | - | Retry with backoff | Yes (3x) |

### Error Response Format

```json
{
    "content": [
        {
            "type": "text",
            "text": "Error querying database: The field 'Invalid Field' was not found. Use search_fields to find valid field IDs."
        }
    ],
    "isError": true
}
```

### Retry Strategy

```python
RETRY_CONFIG = {
    "max_retries": 3,
    "base_delay": 1.0,  # seconds
    "max_delay": 30.0,  # seconds
    "exponential_base": 2,
    "jitter": True
}
```

---

## Testing Strategy

### Unit Tests

```python
# Test tool input validation
def test_query_database_validates_required_fields():
    with pytest.raises(ValidationError):
        query_database(ems_system_id=1)  # Missing database_id and fields

# Test filter building
def test_filter_builds_correct_structure():
    filter_spec = {"field_id": "abc", "operator": "equal", "value": 123}
    result = build_filter(filter_spec)
    assert result["operator"] == "equal"
    assert result["args"][1]["value"] == 123
```

### Integration Tests

```python
# Test against real API (skipped without credentials)
@pytest.mark.integration
def test_list_ems_systems_returns_systems():
    result = list_ems_systems()
    assert len(result) > 0
    assert "id" in result[0]
    assert "name" in result[0]
```

### Mock Server

A mock EMS API server is provided for testing without credentials:

```python
# tests/mock_ems_server.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/api/v2/ems-systems")
def mock_list_systems():
    return [
        {"id": 1, "name": "Test System", "description": "Mock EMS"}
    ]
```

### MCP Inspector Testing

```bash
# Start server and open inspector
fastmcp dev src/ems_mcp/server.py

# Inspector available at http://127.0.0.1:6274
```

---

## Implementation Roadmap

### Phase 1: Core Infrastructure (Week 1)
- [ ] Project setup with FastMCP
- [ ] EMS API client with authentication
- [ ] Token caching and refresh
- [ ] Basic error handling

### Phase 2: Discovery Tools (Week 2)
- [ ] `list_ems_systems`
- [ ] `list_databases`
- [ ] `list_fields` and `search_fields`
- [ ] `get_field_info`
- [ ] `search_analytics`

### Phase 3: Query Tools (Week 3)
- [ ] `query_database` with filter support
- [ ] `query_flight_analytics`
- [ ] Result formatting and pagination

### Phase 4: Asset Tools & Polish (Week 4)
- [ ] `list_fleets`, `list_aircraft`, `list_airports`
- [ ] `list_flight_phases`
- [ ] `ping_system`
- [ ] Comprehensive testing
- [ ] Documentation

### Phase 5: Advanced Features (Future)
- [ ] Async query support for large datasets
- [ ] Resource subscriptions for live data
- [ ] Caching persistence
- [ ] HTTP/SSE transport for remote deployment

---

## Appendix A: EMS API Reference

### Authentication Flow

```
POST /api/token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username={user}&password={pass}

Response:
{
    "access_token": "...",
    "token_type": "bearer",
    "expires_in": 1799
}
```

### Common Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v2/ems-systems` | GET | List EMS systems |
| `/api/v2/ems-systems/{id}/ping` | GET | Health check |
| `/api/v2/ems-systems/{id}/database-groups` | GET | Database catalog |
| `/api/v2/ems-systems/{id}/databases/{db}/query` | POST | Execute query |
| `/api/v2/ems-systems/{id}/analytics` | GET | Search analytics |
| `/api/v2/ems-systems/{id}/flights/{fid}/analytics/query` | POST | Time-series query |

### Field ID Format

Field IDs are bracket-encoded strings:
```
[-hub-][field][[[ems-core][entity-type][foqa-flights]][[ems-core][base-field][flight.uid]]]
```

These must be obtained from API discovery, not constructed manually.

---

## Appendix B: MCP Protocol Reference

### Initialization Sequence

```json
// Client -> Server
{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "claude-code", "version": "1.0"}
}}

// Server -> Client
{"jsonrpc": "2.0", "id": 1, "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "ems-mcp", "version": "1.0"}
}}

// Client -> Server
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

### Tool Call Flow

```json
// Client -> Server
{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
    "name": "list_ems_systems",
    "arguments": {}
}}

// Server -> Client
{"jsonrpc": "2.0", "id": 2, "result": {
    "content": [{"type": "text", "text": "..."}]
}}
```

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2024-02-04 | Claude | Initial design document |
