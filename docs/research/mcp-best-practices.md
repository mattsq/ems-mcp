# MCP Implementation Best Practices

Compiled research on best practices for building MCP servers, particularly for API wrappers.

## Tool Design Principles

### 1. Action-Oriented Design (RPC over REST)

MCP tools should be designed as Remote Procedure Calls, not RESTful resources:

**Good (RPC-style):**
- `query_database` - Execute a query
- `search_fields` - Find fields by name
- `get_flight_analytics` - Retrieve time-series data

**Avoid (REST-style):**
- `database_read` - Unclear what action
- `fields` - Is this list, search, or get?
- `flights/{id}/analytics` - URL-like naming

### 2. Discovery Before Action

For APIs with complex IDs (like EMS), require discovery:

```
1. User asks to query flight data
2. LLM calls list_databases to find FDW Flights
3. LLM calls search_fields("flight date") to get field ID
4. LLM calls query_database with discovered field IDs
```

This prevents errors from guessing IDs and teaches the LLM the API structure.

### 3. Reasonable Defaults

```python
@mcp.tool
def query_database(
    ems_system_id: int,
    database_id: str,
    fields: list[str],
    filters: list[dict] = None,     # Optional
    limit: int = 100,                # Sensible default
    format: str = "display"          # User-friendly default
) -> str:
    ...
```

### 4. Self-Documenting Errors

```python
# Bad error
raise ValueError("Invalid field")

# Good error
return {
    "content": [{
        "type": "text",
        "text": (
            "Error: Field 'altitude' not found in database.\n\n"
            "Suggestions:\n"
            "1. Use search_fields('altitude') to find field IDs\n"
            "2. Field IDs are long strings like: [-hub-][field][...]\n"
            "3. The field may be in a different database"
        )
    }],
    "isError": True
}
```

## Authentication Patterns

### Environment Variables Only

```python
# Good: Environment variables
EMS_USERNAME = os.environ.get("EMS_USERNAME")
EMS_PASSWORD = os.environ.get("EMS_PASSWORD")

# Bad: Hardcoded or config file credentials
config = load_yaml("config.yaml")
password = config["password"]  # Security risk
```

### Token Management

```python
class TokenManager:
    def __init__(self):
        self._token = None
        self._expires_at = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._is_expired():
                await self._refresh_token()
            return self._token

    def _is_expired(self) -> bool:
        if not self._expires_at:
            return True
        # 60 second buffer before actual expiry
        return datetime.utcnow() >= self._expires_at - timedelta(seconds=60)
```

### Automatic Re-authentication

On 401 responses:
1. Refresh token
2. Retry request once
3. Fail if still 401

## Error Handling Patterns

### Retry with Exponential Backoff

```python
async def request_with_retry(
    method: str,
    url: str,
    **kwargs
) -> Response:
    max_retries = 3
    base_delay = 1.0

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", base_delay * (2 ** attempt)))
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                    continue

            return response

        except (ConnectionError, Timeout) as e:
            if attempt < max_retries:
                await asyncio.sleep(base_delay * (2 ** attempt))
                continue
            raise

    raise MaxRetriesExceeded()
```

### Error Classification

```python
class EMSError(Exception):
    """Base class for EMS errors."""

class AuthenticationError(EMSError):
    """401 - Token invalid or expired."""

class AuthorizationError(EMSError):
    """403 - User lacks permission."""

class NotFoundError(EMSError):
    """404 - Resource doesn't exist."""

class RateLimitError(EMSError):
    """429 - Too many requests."""

class ServerError(EMSError):
    """5xx - EMS server issue."""
```

## Caching Strategies

### Cache Stable Data

```python
from functools import lru_cache
from cachetools import TTLCache

# In-memory with TTL
field_cache = TTLCache(maxsize=10000, ttl=3600)

async def get_field_info(database_id: str, field_id: str) -> FieldInfo:
    cache_key = f"{database_id}:{field_id}"

    if cache_key in field_cache:
        return field_cache[cache_key]

    result = await api.get_field_info(database_id, field_id)
    field_cache[cache_key] = result
    return result
```

### What to Cache

| Data Type | TTL | Rationale |
|-----------|-----|-----------|
| EMS systems list | 1 hour | Rarely changes |
| Database structure | 1 hour | Stable |
| Field metadata | 1 hour | Very stable |
| Field IDs from search | 1 hour | Stable |
| Query results | Don't cache | User expects fresh data |
| Token | Until expiry | Managed separately |

### Cache Invalidation

```python
# Periodic refresh
async def background_cache_refresh():
    while True:
        await asyncio.sleep(3600)  # Every hour
        field_cache.clear()
        # Optionally pre-populate common fields
```

## Rate Limiting

### Token Bucket Implementation

```python
class RateLimiter:
    def __init__(self, rate: float, burst: int):
        self.rate = rate  # tokens per second
        self.burst = burst
        self.tokens = burst
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1
```

### Per-Tool Limits

```python
# Higher limits for cheap tools
discovery_limiter = RateLimiter(rate=10, burst=20)

# Lower limits for expensive tools
query_limiter = RateLimiter(rate=2, burst=5)

@mcp.tool
async def list_databases(...):
    await discovery_limiter.acquire()
    ...

@mcp.tool
async def query_database(...):
    await query_limiter.acquire()
    ...
```

## Input Validation

### Schema Validation

FastMCP handles JSON Schema validation automatically from type hints:

```python
from pydantic import BaseModel, Field, validator

class QueryFilter(BaseModel):
    field_id: str = Field(..., min_length=1)
    operator: str = Field(..., pattern="^(equal|greaterThan|lessThan|in)$")
    value: Any

    @validator('field_id')
    def validate_field_id_format(cls, v):
        if not v.startswith('[-hub-]'):
            raise ValueError('Field ID must start with [-hub-]')
        return v
```

### Limit Enforcement

```python
MAX_QUERY_LIMIT = 10000
MAX_ANALYTICS_PER_CALL = 20
MAX_FLIGHTS_PER_CALL = 10

@mcp.tool
def query_database(
    limit: int = Field(100, le=MAX_QUERY_LIMIT),
    ...
):
    ...
```

## Testing Patterns

### Unit Tests with Mocks

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_list_databases_returns_formatted_response():
    mock_client = AsyncMock()
    mock_client.get_databases.return_value = [
        {"id": "db1", "name": "FDW Flights"}
    ]

    with patch('ems_mcp.api.client.get_client', return_value=mock_client):
        result = await list_databases(ems_system_id=1)

    assert "FDW Flights" in result
```

### Integration Tests

```python
@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("EMS_USERNAME"),
    reason="EMS credentials not configured"
)
async def test_query_database_against_real_api():
    result = await query_database(
        ems_system_id=1,
        database_id="ems-core",
        fields=[known_field_id],
        limit=1
    )
    assert "rows" in result
```

### Mock Server

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

mock_app = FastAPI()

@mock_app.get("/api/v2/ems-systems")
def mock_list_systems():
    return [{"id": 1, "name": "Test", "description": "Mock"}]

@mock_app.post("/api/v2/ems-systems/{id}/databases/{db}/query")
def mock_query(id: int, db: str, body: dict):
    return {
        "header": [{"name": "Flight Date", "type": "datetime"}],
        "rows": [["2024-01-01T00:00:00Z"]]
    }
```

## Performance Optimization

### Connection Pooling

```python
import httpx

# Reuse HTTP client
class EMSClient:
    _instance = None

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = httpx.AsyncClient(
                timeout=120.0,
                limits=httpx.Limits(max_connections=10)
            )
        return cls._instance
```

### Parallel Requests

```python
async def get_multiple_field_info(field_ids: list[str]) -> list[FieldInfo]:
    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(get_field_info(fid))
            for fid in field_ids
        ]
    return [task.result() for task in tasks]
```

### Response Compression

```python
headers = {
    "Accept-Encoding": "gzip",
    "Authorization": f"Bearer {token}"
}
```

## Logging and Observability

### Structured Logging

```python
import structlog

logger = structlog.get_logger()

@mcp.tool
async def query_database(...):
    logger.info(
        "query_database_called",
        ems_system_id=ems_system_id,
        database_id=database_id,
        field_count=len(fields),
        filter_count=len(filters or [])
    )

    start = time.monotonic()
    result = await execute_query(...)
    duration = time.monotonic() - start

    logger.info(
        "query_database_completed",
        row_count=len(result.rows),
        duration_ms=int(duration * 1000)
    )
```

### Metrics

```python
from prometheus_client import Counter, Histogram

tool_calls = Counter(
    'mcp_tool_calls_total',
    'Total MCP tool calls',
    ['tool_name', 'status']
)

tool_duration = Histogram(
    'mcp_tool_duration_seconds',
    'MCP tool call duration',
    ['tool_name']
)
```

## Security Checklist

- [ ] Credentials from environment variables only
- [ ] No credentials in logs
- [ ] Token stored in memory only
- [ ] Input validation on all parameters
- [ ] Rate limiting implemented
- [ ] Query limits enforced
- [ ] HTTPS required for API calls
- [ ] Errors don't expose internal details
- [ ] Audit logging for all tool calls

## References

- [MCP Security Best Practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices)
- [MCP Best Practices Guide](https://modelcontextprotocol.info/docs/best-practices/)
- [FastMCP Documentation](https://gofastmcp.com)
- [Wrapping APIs with MCP](https://gun.io/ai/2025/05/wrap-existing-api-with-mcp/)
- [MCP Server Security](https://www.truefoundry.com/blog/mcp-server-security-best-practices)
