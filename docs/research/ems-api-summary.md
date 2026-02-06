# EMS API Summary

Research notes on the EMS (Engine Monitoring System) API based on documentation and the Rems2 R package.

## Overview

EMS is a large software system for flight data analysis, supporting:
- Event detection and measurement
- Exploratory analysis of historical flight data
- Time-series analytics on flight parameters

The REST API provides programmatic access to EMS data.

## Authentication

### OAuth 2.0 Password Grant

**Token Request:**
```http
POST /api/token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username={user}&password={pass}
```

**Token Response:**
```json
{
  "access_token": "JW74p1oRZHbXH2-clkSiZYlHUb2Iz3gWqlAPwAq7K...",
  "token_type": "bearer",
  "expires_in": 1799
}
```

**Token Usage:**
```http
Authorization: Bearer {access_token}
```

**Key Points:**
- Token expires in ~30 minutes (1799 seconds)
- 401 response when token expires
- Domain prefix supported: `DOMAIN\username`

## Core Concepts

### EMS Systems

Each installation is a separate "EMS system" with its own ID:
```http
GET /api/v2/ems-systems
```

Most endpoints require `{emsSystemId}` as path parameter.

### Databases

Data is organized in hierarchical databases:
- **Database Groups**: Contain databases and sub-groups
- **Databases**: Contain records and fields (e.g., "FDW Flights")
- **Field Groups**: Organize fields into categories
- **Fields**: Actual data columns

**Key Database: FDW Flights (Flight Data Warehouse)**
- Primary flight records
- Hundreds of available fields
- Common fields: Flight Date, Airports, Aircraft, Duration

### Field IDs

Field IDs are opaque, bracket-encoded strings:
```
[-hub-][field][[[ems-core][entity-type][foqa-flights]][[ems-core][base-field][flight.uid]]]
```

**Critical Rules:**
- Must be obtained from API discovery (not constructed)
- Stable across sessions (can be cached)
- Very long (use POST for queries, not GET)

### Field Types

| EMS Type | Description |
|----------|-------------|
| Boolean | TRUE/FALSE |
| DateTime | Date and time values |
| Discrete | Enumerated values (numeric codes with labels) |
| Number | Numeric (integer or float) |
| String | Text values |

### Discrete Fields

Discrete fields have a fixed set of values:
```json
{
  "discreteValues": [
    {"value": 407, "label": "VH-OQA"},
    {"value": 501, "label": "VH-OQB"}
  ]
}
```

**Important:** Filters require numeric codes, not string labels!

## Database API Endpoints

### Navigate Database Groups
```http
GET /api/v2/ems-systems/{id}/database-groups?groupId={groupId}
```

### Navigate Field Groups
```http
GET /api/v2/ems-systems/{id}/databases/{dbId}/field-groups?groupId={groupId}
```

### Search Fields
```http
GET /api/v2/ems-systems/{id}/databases/{dbId}/fields?text={searchText}
```

### Get Field Info
```http
GET /api/v2/ems-systems/{id}/databases/{dbId}/fields/{fieldId}
```

## Query API

### Synchronous Query
```http
POST /api/v2/ems-systems/{id}/databases/{dbId}/query
```

### Async Query (for large results)
```http
POST /api/v2/ems-systems/{id}/databases/{dbId}/async-query

# Fetch results in chunks:
GET /api/v2/ems-systems/{id}/databases/{dbId}/async-query/{queryId}/read/{start}/{end}

# Stop query:
DELETE /api/v2/ems-systems/{id}/databases/{dbId}/async-query/{queryId}
```

### Query Structure

```json
{
  "select": [
    {"fieldId": "field-id-1"},
    {"fieldId": "field-id-2", "aggregate": "avg"}
  ],
  "filter": {
    "operator": "and",
    "args": [
      {
        "type": "filter",
        "value": {
          "operator": "greaterThanOrEqual",
          "args": [
            {"type": "field", "value": "date-field-id"},
            {"type": "constant", "value": "2024-01-01"}
          ]
        }
      }
    ]
  },
  "orderBy": [
    {"fieldId": "field-id", "order": "desc"}
  ],
  "top": 100,
  "format": "display"
}
```

### Filter Operators

| Operator | Description |
|----------|-------------|
| equal, notEqual | Equality comparison |
| greaterThan, greaterThanOrEqual | Greater comparisons |
| lessThan, lessThanOrEqual | Less comparisons |
| in, notIn | Value in list |
| isNull, isNotNull | NULL checks |
| and, or, not | Logical operators |
| like, notLike | Pattern matching |
| betweenInclusive | Range check |
| dateRelative | Relative date filtering |

### Format Options

- `"none"`: Raw values (numeric codes for discrete fields)
- `"display"`: Formatted values (labels for discrete fields)

## Analytics API

### Search Analytics
```http
GET /api/v2/ems-systems/{id}/analytics?text={searchText}
```

### Navigate Analytic Groups
```http
GET /api/v2/ems-systems/{id}/analytic-groups?groupId={groupId}
```

### Query Time-Series
```http
POST /api/v2/ems-systems/{id}/flights/{flightId}/analytics/query
```

**Request:**
```json
{
  "select": [
    {"analyticId": "analytic-id-1"},
    {"analyticId": "analytic-id-2"}
  ],
  "start": 0,
  "end": 500,
  "size": 200
}
```

**Response:**
```json
{
  "header": [
    {"name": "time", "type": "number"},
    {"name": "Altitude", "type": "number", "units": "ft"}
  ],
  "rows": [
    [0, 847],
    [1, 852],
    [2, 863]
  ]
}
```

### Analytic IDs

Like field IDs, analytic IDs are long, opaque strings:
```
H4sIANx/4A0E/2WQy07DMBBF1/wFyqI7x6ZxlD...
```

Must be discovered via search or group navigation.

## Asset APIs

### Fleets
```http
GET /api/v2/ems-systems/{id}/assets/fleets
GET /api/v2/ems-systems/{id}/assets/fleets/{fleetId}
```

### Aircraft
```http
GET /api/v2/ems-systems/{id}/assets/aircraft
GET /api/v2/ems-systems/{id}/assets/aircraft/{aircraftId}
```

### Airports
```http
GET /api/v2/ems-systems/{id}/assets/airports
GET /api/v2/ems-systems/{id}/assets/airports/{airportId}
```

### Flight Phases
```http
GET /api/v2/ems-systems/{id}/assets/flight-phases
```

## Error Handling

### Error Response Format
```json
{
  "message": "High-level error message",
  "messageDetail": "Detailed explanation",
  "unexpected": true
}
```

### Common Status Codes

| Code | Meaning |
|------|---------|
| 400 | Invalid request (bad query structure, wrong types) |
| 401 | Token expired or invalid |
| 403 | Access denied |
| 404 | Resource not found |
| 429 | Rate limited |
| 5xx | Server error |

### OAuth Error Format
```json
{
  "error": "invalid_grant",
  "error_description": "The user name or password is incorrect."
}
```

## Custom Headers

### Request Headers

| Header | Purpose |
|--------|---------|
| `X-Adi-Application-Name` | Identify your application |
| `X-Adi-Client-Username` | Client user tracking |
| `X-Adi-Correlation-Id` | Request tracking |
| `User-Agent` | SDK identification (e.g., `ems-api-sdk python 1.0`) |
| `Accept-Encoding: gzip` | Enable response compression |

### Response Headers

| Header | Purpose |
|--------|---------|
| `X-Adi-Unique-Id` | Unique request ID for debugging |

## Performance Considerations

### Caching Opportunities

1. **EMS Systems list**: Changes rarely
2. **Database structure**: Stable
3. **Field metadata**: Very stable
4. **Analytic metadata**: Stable
5. **Asset data**: Changes infrequently

### Query Optimization

1. Use `top` to limit results
2. Add filters to reduce data transfer
3. Use async queries for large result sets
4. For time-series, specify `start`/`end` offsets

### Rate Limiting

- API may return 429 when overloaded
- Implement exponential backoff
- Consider caching to reduce API calls

## Key Gotchas

1. **Field IDs must be discovered** - Cannot guess or construct
2. **Discrete filters use codes** - Not labels
3. **Format affects results** - `display` vs `none`
4. **Analytics are per-flight** - Must loop for multiple flights
5. **Token expires** - Need refresh logic
6. **IDs are very long** - Use POST, not GET for queries
