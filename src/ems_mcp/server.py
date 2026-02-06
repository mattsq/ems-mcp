"""FastMCP server for EMS API access.

This module defines the MCP server that exposes EMS API functionality
as tools for LLM assistants like Claude.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from ems_mcp.api.client import EMSClient
from ems_mcp.config import get_settings

# Configure logging based on settings
try:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level, logging.INFO)
except Exception:
    log_level = logging.INFO

logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage the lifecycle of the EMS MCP server.

    Initializes the EMS API client on startup and cleans up on shutdown.

    Args:
        app: The FastMCP application instance.

    Yields:
        A context dict with the initialized client.
    """
    logger.info("Starting EMS MCP server...")

    # Initialize the EMS client
    settings = get_settings()
    client = EMSClient(settings=settings)
    await client._initialize()
    EMSClient.set_instance(client)

    logger.info("EMS MCP server ready (base URL: %s)", settings.base_url)

    try:
        yield {"client": client}
    finally:
        logger.info("Shutting down EMS MCP server...")
        await client._cleanup()
        EMSClient.clear_instance()
        logger.info("EMS MCP server stopped")


# Create the FastMCP server instance
mcp = FastMCP(
    name="ems-mcp",
    version="0.1.0",
    instructions=(
        "MCP server providing LLM access to the EMS (Engine Monitoring System) API "
        "for flight data analytics. Enables discovery of EMS systems, databases, and fields; "
        "querying flight records; and retrieving time-series analytics data."
    ),
    lifespan=lifespan,
)


def get_client() -> EMSClient:
    """Get the EMS API client instance.

    This is a convenience function for tools to access the client.

    Returns:
        The initialized EMSClient instance.

    Raises:
        RuntimeError: If the server hasn't been started.
    """
    return EMSClient.get_instance()


def run() -> None:
    """Run the MCP server.

    This is the main entry point for starting the server.
    Uses stdio transport by default.
    """
    mcp.run()


# Import tools to register them with the mcp instance
# This must happen after mcp is created
import ems_mcp.tools.assets  # noqa: E402, F401
import ems_mcp.tools.discovery  # noqa: E402, F401
import ems_mcp.tools.query  # noqa: E402, F401
