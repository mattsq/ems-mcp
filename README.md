# EMS MCP Server

An MCP (Model Context Protocol) server that provides LLM access to the EMS (Event Monitoring System) API for flight data analytics.

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

All MCP clients need three values to connect to your EMS server:

| Variable | Description |
|----------|-------------|
| `EMS_BASE_URL` | EMS server URL (e.g. `https://your-ems-server.com`) -- do **not** include `/api` |
| `EMS_USERNAME` | Your EMS username |
| `EMS_PASSWORD` | Your EMS password |

### Claude Code (CLI)

Create a `.mcp.json` file in the project root:

```json
{
  "mcpServers": {
    "ems-mcp": {
      "command": "C:\\absolute\\path\\to\\ems-mcp\\.venv\\Scripts\\ems-mcp.exe",
      "args": [],
      "env": {
        "EMS_BASE_URL": "https://your-ems-server.com",
        "EMS_USERNAME": "your-username",
        "EMS_PASSWORD": "your-password"
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
        "EMS_BASE_URL": "https://your-ems-server.com",
        "EMS_USERNAME": "your-username",
        "EMS_PASSWORD": "your-password"
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
        "EMS_BASE_URL": "https://your-ems-server.com",
        "EMS_USERNAME": "your-username",
        "EMS_PASSWORD": "your-password"
      }
    }
  }
}
```

## Available Tools

### Discovery
- `list_ems_systems` -- List available EMS systems
- `list_databases` -- Navigate database hierarchy
- `list_fields` -- Navigate field hierarchy
- `search_fields` -- Search for field IDs by name
- `get_field_info` -- Get detailed field metadata and discrete value mappings
- `search_analytics` -- Search for time-series analytic IDs

### Querying
- `query_database` -- Query flight records with filters and sorting
- `query_flight_analytics` -- Get time-series data for specific flights

### Assets
- `list_fleets` -- List aircraft fleets
- `list_aircraft` -- List aircraft (tail numbers)
- `list_airports` -- List airports with codes and locations
- `list_flight_phases` -- List flight phase definitions
- `ping_system` -- Check system health and server time

## Development

```bash
uv pip install -e ".[dev]"
pytest tests/
```

## Troubleshooting

**401 Unauthorized** -- Check that `EMS_USERNAME` and `EMS_PASSWORD` are correct and that the account has API access.

**Connection errors** -- Verify `EMS_BASE_URL` does **not** include a `/api` suffix. It should be just the server URL (e.g. `https://your-ems-server.com`).

**Server not found by MCP client** -- Make sure the path to the `ems-mcp` executable in your config is an absolute path and that the virtual environment has been created (`uv venv && uv pip install -e .`).
