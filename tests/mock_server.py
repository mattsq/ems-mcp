"""Mock EMS API server for testing.

Provides a FastAPI-based mock server that simulates EMS API responses
for unit and integration testing without requiring real credentials.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock EMS API Server")

# Mock data
MOCK_SYSTEMS = [
    {"id": 1, "name": "Production", "description": "Main production EMS system"},
    {"id": 2, "name": "Test", "description": "Testing environment"},
]

VALID_CREDENTIALS = {
    "testuser": "testpass",
    "admin": "adminpass",
}


@app.post("/api/token")
async def token(
    grant_type: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
) -> dict[str, Any]:
    """Mock OAuth token endpoint."""
    if grant_type != "password":
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_grant_type", "error_description": "Only password grant is supported"},
        )

    if username not in VALID_CREDENTIALS or VALID_CREDENTIALS[username] != password:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_grant",
                "error_description": "The user name or password is incorrect.",
            },
        )

    return {
        "access_token": f"mock_token_{username}_{datetime.now(timezone.utc).timestamp()}",
        "token_type": "bearer",
        "expires_in": 1799,
    }


@app.get("/api/v2/ems-systems")
async def list_ems_systems() -> list[dict[str, Any]]:
    """Mock EMS systems list endpoint."""
    return MOCK_SYSTEMS


@app.get("/api/v2/ems-systems/{system_id}")
async def get_ems_system(system_id: int) -> dict[str, Any]:
    """Mock single EMS system endpoint."""
    for system in MOCK_SYSTEMS:
        if system["id"] == system_id:
            return system
    raise HTTPException(status_code=404, detail={"message": "EMS system not found"})


@app.get("/api/v2/ems-systems/{system_id}/ping")
async def ping_system(system_id: int) -> dict[str, Any]:
    """Mock EMS system ping endpoint."""
    for system in MOCK_SYSTEMS:
        if system["id"] == system_id:
            return {
                "serverTime": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
            }
    raise HTTPException(status_code=404, detail={"message": "EMS system not found"})


@app.get("/api/v2/ems-systems/{system_id}/database-groups")
async def list_database_groups(system_id: int) -> dict[str, Any]:
    """Mock database groups endpoint."""
    return {
        "groups": [
            {"id": "fdw", "name": "Flight Data Warehouse"},
            {"id": "events", "name": "Events"},
        ],
        "databases": [
            {"id": "ems-core", "name": "FDW Flights", "pluralName": "Flights"},
        ],
    }


# Error simulation endpoints for testing
@app.get("/api/v2/test/rate-limit")
async def test_rate_limit() -> JSONResponse:
    """Endpoint that always returns 429 for testing rate limit handling."""
    return JSONResponse(
        status_code=429,
        content={"message": "Rate limit exceeded"},
        headers={"Retry-After": "5"},
    )


@app.get("/api/v2/test/server-error")
async def test_server_error() -> JSONResponse:
    """Endpoint that always returns 500 for testing server error handling."""
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error", "unexpected": True},
    )


@app.get("/api/v2/test/timeout")
async def test_timeout() -> dict[str, str]:
    """Endpoint that simulates a timeout."""
    import asyncio

    await asyncio.sleep(300)  # Will be interrupted by client timeout
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
