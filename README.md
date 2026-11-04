# EMS MCP Server

An MCP (Model Context Protocol) server that provides LLM access to the EMS (Event Management System) API for flight data analytics.

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** package manager

## Installation

```bash
git clone <repo-url>
cd ems-mcp

# Create virtual environment
uv venv

# Activate virtual environment
# Windows (cmd):
.venv\Scripts\activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install the package
uv pip install -e .
```

This creates an `ems-mcp` executable inside the virtual environment:
- **Windows:** `.venv\Scripts\ems-mcp.exe`
- **macOS / Linux:** `.venv/bin/ems-mcp`

## Configuration

### Credentials (Windows Credential Manager)

Your EMS username and password are stored in Windows Credential Manager
(or the OS keychain on macOS/Linux) rather than in MCP config files. Store them once:

```bash
.venv\Scripts\ems-mcp.exe credentials set      # prompts for username and password
.venv\Scripts\ems-mcp.exe credentials show     # shows the stored username
.venv\Scripts\ems-mcp.exe credentials delete   # removes the stored credential
```

This creates a Generic Credential named `ems-mcp`, visible under
*Control Panel > Credential Manager > Windows Credentials*. To update your
password, run `credentials set` again.

### Environment variables

| Variable | Description |
|----------|-------------|
| `EMS_BASE_URL` | **Required.** EMS server URL (e.g. `https://your-ems-server.com`) -- do **not** include `/api` |
| `EMS_KEYRING_SERVICE` | Optional. Credential Manager entry name (default `ems-mcp`); use different names for multiple EMS servers |
| `EMS_USERNAME` / `EMS_PASSWORD` | Optional overrides for the stored credential (e.g. for CI). If only `EMS_USERNAME` is set, the stored password for that username is used |

### Claude Code (CLI)

Create a `.mcp.json` file in the project root:

```json
{
  "mcpServers": {
    "ems-mcp": {
      "command": "C:\\absolute\\path\\to\\ems-mcp\\.venv\\Scripts\\ems-mcp.exe",
      "args": [],
      "env": {
        "EMS_BASE_URL": "https://your-ems-server.com"
      }
    }
  }
}
```

Claude Code reads `.mcp.json` automatically when you open the project directory.

### Claude Desktop

Edit `claude_desktop_config.json`:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the server to the `mcpServers` block:

```json
{
  "mcpServers": {
    "ems-mcp": {
      "command": "C:\\absolute\\path\\to\\ems-mcp\\.venv\\Scripts\\ems-mcp.exe",
      "args": [],
      "env": {
        "EMS_BASE_URL": "https://your-ems-server.com"
      }
    }
  }
}
```

On macOS/Linux, use the Unix-style path to the executable (e.g. `/home/user/ems-mcp/.venv/bin/ems-mcp`).

Restart Claude Desktop after saving changes.

### Gemini CLI

Create `.gemini/settings.json` in the project directory:

```json
{
  "mcpServers": {
    "ems-mcp": {
      "command": "C:\\absolute\\path\\to\\ems-mcp\\.venv\\Scripts\\ems-mcp.exe",
      "args": [],
      "env": {
        "EMS_BASE_URL": "https://your-ems-server.com"
      }
    }
  }
}
```

## Available Tools

### Discovery
- `list_ems_systems` -- List available EMS systems (start here)
- `list_databases` -- Navigate the database hierarchy
- `find_fields` -- Find fields by keyword (`mode="search"`; runs as `mode="deep"` automatically on entity-type databases such as FDW Flights), browse the field group tree (`mode="browse"`), or BFS-traverse a database (`mode="deep"`); returns numbered `[N]` references usable directly in other tools. Field names elsewhere must match exactly -- near-misses return candidate `[N]` refs instead of being substituted
- `get_field_info` -- Get field metadata and discrete value mappings
- `search_analytics` -- Search for time-series analytics by name (altitude, airspeed, etc.)
- `get_result_id` -- (Deprecated) Resolve `[N]` references to full opaque IDs; no longer needed in the standard workflow

### Querying
- `query_database` -- Query flight records with filters, sorting, and aggregation
- `query_flight_analytics` -- Get time-series data for specific flights

### Assets
- `get_assets` -- Get reference data: `asset_type` of `fleets`, `aircraft` (optionally filtered by `fleet_id`), `airports`, or `flight_phases`
- `ping_system` -- Check whether an EMS system is online

## Resources

The server also exposes MCP resources for stable reference data:

- `ems://workflow-guide` -- Discovery-to-query workflow guide
- `ems://systems` -- List of available EMS systems (cached)
- `ems://systems/{system_id}/fleets` -- Fleet catalog for a system (cached)
- `ems://systems/{system_id}/airports` -- Airport reference data (cached)
- `ems://databases/common-fields` -- Index of databases with curated field vocabularies
- `ems://databases/{database_name}/common-fields` -- Curated common fields for a named database (e.g. `FDW Flights`)

## Prompts

Reusable templates that pre-encode multi-step EMS workflows:

- `analyze_flights` -- Discovery -> query -> analytics for a tail number / date range
- `compare_flights` -- Side-by-side time-series comparison between two flight IDs
- `search_flight_parameters` -- Discover available fields by keyword, with entity-database support

## Development

```bash
uv pip install -e ".[dev]"
pytest tests/
```

## Troubleshooting

**401 Unauthorized** -- Check the stored credential with `ems-mcp credentials show` (re-run `credentials set` if your password changed), make sure stale `EMS_USERNAME`/`EMS_PASSWORD` variables are not overriding it, and confirm the account has API access.

**Connection errors** -- Verify `EMS_BASE_URL` does **not** include a `/api` suffix. It should be just the server URL (e.g. `https://your-ems-server.com`).

**Server not found by MCP client** -- Make sure the path to the `ems-mcp` executable in your config is an absolute path and that the virtual environment has been created (`uv venv && uv pip install -e .`).
