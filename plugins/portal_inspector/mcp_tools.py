"""
MCP Tools for Portal Inspector plugin.
Exposes Power Pages website structures, Liquid templates, and portal page hierarchies.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.portal_inspector.client import DataverseClient

logger = logging.getLogger("mcp_portal_inspector")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register portal inspector tools into MCP server."""

    @app.tool()
    def list_portal_pages(
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """List all published Power Pages web pages with IDs, names, and partial URLs.

        Args:
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            res = client.list_all_webpages()
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in list_portal_pages")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def search_portal_code(
        search_text: str,
        match_mode: str = "Contains",
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Search across all Power Pages Web Templates, Content Snippets, and Page JavaScript for specific Liquid tags, variables, or functions.

        Args:
            search_text: Text snippet or code token to search for.
            match_mode: Matching mode ('Contains', 'Exact', 'Word').
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            res = client.search_portal_content(search_text=search_text, match_mode=match_mode)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in search_portal_code")
            return json.dumps({"error": str(exc)}, indent=2)

