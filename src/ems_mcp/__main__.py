"""Entry point for running the EMS MCP server.

This module allows running the server with:
    python -m ems_mcp
"""

from ems_mcp.server import run


def main() -> None:
    """Main entry point for the EMS MCP server."""
    run()


if __name__ == "__main__":
    main()
