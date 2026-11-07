# MCP Server Plugin for DynamicsSuite

Runs a local **Model Context Protocol (MCP)** service with **in-memory session security (Zero Disk Storage)**.

## Highlights
- **Zero Disk Cache**: No `.msal_token_cache.json` or `.dynamics_session.json` saved to disk.
- **Configurable Port**: Run on any port (default: `58999` for DynamicsSuite, `58998` for DynamicsSuite_mo).
- **Dual Transport Support**:
  - Direct **SSE / HTTP** (`http://127.0.0.1:<port>/sse`) for Claude Desktop, Cursor, and Antigravity.
  - In-memory **IPC Token Endpoint** (`http://127.0.0.1:<port>/api/token`) for Stdio MCP clients.
- **Automatic Lifecycle**: As soon as you log out or close DynamicsSuite, all in-memory tokens vanish from RAM.

