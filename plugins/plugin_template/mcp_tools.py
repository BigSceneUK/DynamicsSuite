"""
Template MCP Tools definition for DynamicsSuite plugins.

Drop this file (mcp_tools.py) into any plugin folder (e.g. plugins/<plugin_name>/mcp_tools.py).
The MCP server will automatically discover and register all tools declared here on startup!
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """
    Hook called automatically by mcp_server.plugin_discovery.
    Define and decorate your plugin's AI tools using @app.tool().
    """

    @app.tool()
    def template_plugin_action(
        target_entity: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Example AI tool from Plugin Template demonstrating dynamic discovery.

        Args:
            target_entity: Logical name of target entity (e.g. 'account', 'contact').
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            # You can access tokens or API clients via auth.get_token(environment, account=account)
            return json.dumps({
                "status": "success",
                "message": f"Plugin template action executed dynamically on {target_entity}",
                "plugin": "plugin_template",
            }, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, indent=2)

