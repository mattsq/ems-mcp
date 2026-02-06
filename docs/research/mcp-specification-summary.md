# MCP Specification Summary

Research notes on the Model Context Protocol specification (revision 2025-06-18).

## Core Concepts

### Architecture

MCP follows a client-server architecture:
- **MCP Host**: AI application (Claude Desktop, Claude Code, VS Code, etc.)
- **MCP Client**: Component in host that maintains connection to a server
- **MCP Server**: Program providing context to clients

Key insight: One host can have multiple clients, each connected to different servers.

### Protocol Layers

1. **Data Layer**: JSON-RPC 2.0 based protocol
   - Lifecycle management (initialize, negotiate, shutdown)
   - Primitives: Tools, Resources, Prompts
   - Notifications for real-time updates

2. **Transport Layer**: Communication mechanisms
   - **stdio**: Standard I/O for local processes (most common)
   - **Streamable HTTP**: HTTP POST + SSE for remote servers

### Primitives

**Tools** (Model-controlled):
- Executable functions LLMs can invoke
- Have name, description, inputSchema
- Return content (text, images, resources)
- Should have human confirmation for sensitive operations

**Resources** (Application-driven):
- Data sources providing context
- Identified by URIs
- Can be subscribed for updates
- Types: text (with mimeType) or binary (blob)

**Prompts** (User-controlled):
- Reusable templates for LLM interactions
- Include system prompts, examples
- Not commonly used for API wrappers

## Tool Specification

### Definition Structure

```json
{
  "name": "tool_name",
  "title": "Human-Readable Title",
  "description": "What this tool does",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param1": {"type": "string", "description": "..."},
      "param2": {"type": "integer"}
    },
    "required": ["param1"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {...}
  }
}
```

### Tool Results

```json
{
  "content": [
    {"type": "text", "text": "Result message"},
    {"type": "image", "data": "base64...", "mimeType": "image/png"},
    {"type": "resource", "resource": {...}}
  ],
  "isError": false
}
```

### Error Handling

Two types:
1. **Protocol errors**: JSON-RPC errors (-32602, -32603, etc.)
2. **Tool execution errors**: Return result with `isError: true`

## Capabilities

Declared during initialization:

```json
{
  "capabilities": {
    "tools": {
      "listChanged": true  // Server notifies when tool list changes
    },
    "resources": {
      "subscribe": true,   // Clients can subscribe to resource changes
      "listChanged": true
    }
  }
}
```

## Security Best Practices

### Authentication
- OAuth 2.1 recommended for HTTP transport
- Servers are OAuth 2.0 Resource Servers (June 2025 spec)
- Avoid static tokens in production
- Use short-lived tokens with PKCE

### Input Validation
- Validate all tool inputs against schema
- Implement proper access controls
- Rate limit tool invocations
- Sanitize outputs

### Client Responsibilities
- Prompt for user confirmation on sensitive operations
- Show tool inputs before calling (prevent data exfiltration)
- Validate tool results before passing to LLM
- Implement timeouts

### Session Security
- Don't use sessions for authentication
- Use secure, non-deterministic session IDs
- Bind sessions to user-specific information

## Best Practices for API Wrappers

### Tool Design
1. **RPC-style over REST-style**: Tools are actions, not resources
2. **Clear naming**: `query_database` not `database_query`
3. **Descriptive errors**: Include next steps in error messages
4. **Reasonable defaults**: Don't require all parameters

### Tool Organization
- Group related tools logically
- Discovery tools should be cheap and fast
- Action tools may be expensive, add limits
- Consider caching for stable data

### Error Messages
```json
{
  "content": [
    {
      "type": "text",
      "text": "Error: Field 'invalid_field' not found.\n\nSuggestions:\n- Use search_fields('altitude') to find field IDs\n- Field IDs are long strings, not simple names"
    }
  ],
  "isError": true
}
```

## Python SDK (FastMCP)

### Server Creation

```python
from fastmcp import FastMCP

mcp = FastMCP(name="My Server")

@mcp.tool
def my_tool(param1: str, param2: int = 10) -> str:
    """Tool description for LLM.

    Args:
        param1: First parameter description
        param2: Optional second parameter
    """
    return f"Result: {param1}, {param2}"

if __name__ == "__main__":
    mcp.run()
```

### Resource Definition

```python
@mcp.resource("resource://config")
def get_config() -> dict:
    """Application configuration."""
    return {"version": "1.0"}

@mcp.resource("greetings://{name}")
def personalized_greeting(name: str) -> str:
    """Personalized greeting resource."""
    return f"Hello, {name}!"
```

### Transport Options

```python
# stdio (default, for local use)
mcp.run(transport="stdio")

# HTTP/SSE (for remote deployment)
mcp.run(transport="sse", host="0.0.0.0", port=8000)
```

### Testing with Inspector

```bash
fastmcp dev src/server.py
# Opens http://127.0.0.1:6274
```

## References

- [MCP Specification](https://modelcontextprotocol.io/specification/2025-06-18)
- [MCP Architecture](https://modelcontextprotocol.io/docs/concepts/architecture)
- [MCP Tools](https://modelcontextprotocol.io/docs/concepts/tools)
- [MCP Resources](https://modelcontextprotocol.io/docs/concepts/resources)
- [FastMCP Documentation](https://gofastmcp.com)
- [Python MCP SDK](https://modelcontextprotocol.github.io/python-sdk/)
- [MCP Security Best Practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices)
