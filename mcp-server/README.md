# KNX ETS Bridge -- MCP Server

MCP server that exposes KNX ETS6 project operations as tools for LLMs.
Built with [FastMCP v3](https://gofastmcp.com/) (`from fastmcp import FastMCP`).
It forwards each tool call as a newline-delimited JSON request over IPC
to the ETS6 AddIn (running inside ETS on Windows), then returns the response.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
cd mcp-server
uv sync --all-extras
```

## Running

```bash
# Production (Windows, connects to ETS6 AddIn via named pipe):
KNX_BRIDGE_TRANSPORT=pipe uv run python -m knx_ets_mcp.server

# Development / testing (any OS, in-memory mock AddIn):
KNX_BRIDGE_TRANSPORT=mock uv run python -m knx_ets_mcp.server

# Via the FastMCP CLI (alternative):
KNX_BRIDGE_TRANSPORT=mock uv run fastmcp run src/knx_ets_mcp/server.py:mcp
```

The server communicates over **stdio** (the standard MCP transport for
Claude Desktop, Claude Code, etc.).

### Claude Desktop configuration

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "knx-ets-bridge": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-server", "python", "-m", "knx_ets_mcp.server"],
      "env": {
        "KNX_BRIDGE_TRANSPORT": "mock"
      }
    }
  }
}
```

Remove the `env` block (or set to `pipe`) when running against a real
ETS6 instance on Windows.

## Tests

```bash
uv run pytest -v
```

All tests run against `MockTransport` via FastMCP's in-memory `Client`,
exercising the full MCP tool layer end-to-end -- no Windows or ETS6
required.

## Tool list

| Tool | Protocol method | Type |
|---|---|---|
| `knx_project_info` | `project.info` | read |
| `knx_list_devices` | `devices.list` | read |
| `knx_list_group_addresses` | `ga.list` | read |
| `knx_list_comobjects` | `comobjects.list` | read |
| `knx_create_group_address` | `ga.create` | mutation |
| `knx_add_device` | `device.addFromCatalog` | mutation |
| `knx_link` | `link.create` | mutation |
| `knx_unlink` | `link.delete` | mutation |
| `knx_set_parameter` | `param.set` | mutation |
| `knx_program_device` | `device.program` | programming |
| `knx_job_status` | `job.status` | programming |
| `knx_job_cancel` | `job.cancel` | programming |

## Idempotency

Every mutation tool call includes an `idempotencyKey` in the protocol
request.  The key is a **fresh UUID4** generated per operation.  If the
client layer retries the same operation after a transport failure, it
reuses the same key so the AddIn deduplicates the retry correctly.

Two separate tool calls with identical arguments get **different** keys
and execute independently.  This ensures sequences like
link -> unlink -> link work correctly (the second link is not
deduplicated against the first).

## Architecture

```
LLM  --(MCP/stdio)-->  MCPServer  --(client)-->  Transport  --(JSON/pipe)-->  ETS6 AddIn
                        server.py                 client.py                   (C#, in ETS)
                                                  |
                                                  +-- MockTransport (tests)
                                                  +-- NamedPipeTransport (prod)
```

## Transport selection

Set `KNX_BRIDGE_TRANSPORT` environment variable:

| Value | Transport | Platform |
|---|---|---|
| `pipe` (default) | `NamedPipeTransport` | Windows only |
| `mock` | `MockTransport` | Any (in-memory fake) |
