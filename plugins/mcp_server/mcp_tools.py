"""
MCP Tools for MCP Server plugin.
Exposes MCP runtime server diagnostics, port binding, and active in-memory session status.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.mcp_server.service import get_embedded_mcp_server

logger = logging.getLogger("mcp_server_plugin_tools")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register MCP server diagnostic tools."""

    @app.tool()
    def get_mcp_server_status() -> str:
        """Fetch the real-time runtime status, port, uptime, storage security mode, and active session details of the embedded DynamicsSuite MCP server."""
        try:
            server = get_embedded_mcp_server()
            status = server.get_status()
            return json.dumps(status, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_mcp_server_status")
            return json.dumps({"error": str(exc)}, indent=2)

