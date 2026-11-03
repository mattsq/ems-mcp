"""Entry point for running the EMS MCP server.

This module allows running the server with:
    python -m ems_mcp

Credentials can be managed in the OS credential store with:
    python -m ems_mcp credentials set|show|delete [--service NAME]
"""

import argparse
import getpass
import os
import sys

from ems_mcp.credentials import (
    DEFAULT_SERVICE_NAME,
    CredentialStoreError,
    delete_credentials,
    load_credentials,
    store_credentials,
)


def _credentials_command(action: str, service: str) -> int:
    """Run a credentials subcommand.

    Args:
        action: One of "set", "show", "delete".
        service: Credential store service (target) name.

    Returns:
        Process exit code.
    """
    try:
        if action == "set":
            username = input("EMS username: ").strip()
            password = getpass.getpass("EMS password: ")
            if not username or not password:
                print("Username and password are both required.", file=sys.stderr)
                return 1
            store_credentials(username, password, service)
            print(f"Stored credentials for {username} in credential store as '{service}'.")
        elif action == "show":
            credentials = load_credentials(service)
            if credentials is None:
                print(f"No credentials stored for '{service}'.")
                return 1
            print(f"'{service}': username {credentials[0]} (password stored)")
        elif action == "delete":
            if delete_credentials(service):
                print(f"Deleted credentials for '{service}'.")
            else:
                print(f"No credentials stored for '{service}'.")
    except CredentialStoreError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def main() -> None:
    """Main entry point for the EMS MCP server."""
    parser = argparse.ArgumentParser(prog="ems-mcp", description="EMS API MCP server")
    subparsers = parser.add_subparsers(dest="command")
    creds = subparsers.add_parser(
        "credentials", help="Manage EMS credentials in the OS credential store"
    )
    creds.add_argument("action", choices=["set", "show", "delete"])
    creds.add_argument(
        "--service",
        default=os.environ.get("EMS_KEYRING_SERVICE", DEFAULT_SERVICE_NAME),
        help=f"Credential store service name (default: {DEFAULT_SERVICE_NAME})",
    )
    args = parser.parse_args()

    if args.command == "credentials":
        sys.exit(_credentials_command(args.action, args.service))

    from ems_mcp.server import run

    run()


if __name__ == "__main__":
    main()
