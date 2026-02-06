"""EMS MCP Server - LLM access to the EMS API for flight data analytics.

This package provides an MCP (Model Context Protocol) server that wraps
the EMS REST API, allowing AI assistants like Claude to query flight data,
discover analytics, and interact with EMS databases.

Example:
    # Run the server
    python -m ems_mcp

    # Or use the entry point
    ems-mcp
"""

from ems_mcp.api.auth import AuthenticationError, TokenManager
from ems_mcp.api.client import (
    EMSAPIError,
    EMSAuthorizationError,
    EMSClient,
    EMSNotFoundError,
    EMSRateLimitError,
    EMSServerError,
)
from ems_mcp.config import EMSSettings, get_settings
from ems_mcp.server import get_client, mcp, run
from ems_mcp.tools import (
    get_field_info,
    list_databases,
    list_ems_systems,
    list_fields,
    search_analytics,
    search_fields,
)

__version__ = "0.1.0"

__all__ = [
    # Server
    "mcp",
    "run",
    "get_client",
    # Client
    "EMSClient",
    # Auth
    "TokenManager",
    # Settings
    "EMSSettings",
    "get_settings",
    # Exceptions
    "AuthenticationError",
    "EMSAPIError",
    "EMSAuthorizationError",
    "EMSNotFoundError",
    "EMSRateLimitError",
    "EMSServerError",
    # Discovery Tools
    "list_ems_systems",
    "list_databases",
    "list_fields",
    "search_fields",
    "get_field_info",
    "search_analytics",
]
