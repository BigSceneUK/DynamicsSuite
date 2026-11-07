"""
MCP Tools for Power Pages Web API Auditor plugin.
Exposes Web API configuration analysis, field exposure diagnostics, and deep table contexts.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.portal_webapi_auditor.client import PortalWebApiAuditorClient

logger = logging.getLogger("mcp_portal_webapi_auditor")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register portal webapi auditor tools into MCP server."""

    @app.tool()
    def audit_table_portal_context(
        table_name: str,
        website_id: Optional[str] = None,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Deep analysis of how a Dataverse table is utilized across Power Pages (forms, lists, site settings, Web API occurrences).

        Args:
            table_name: Logical name of table (e.g. 'contact', 'account', 'incident').
            website_id: Optional website GUID.
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = PortalWebApiAuditorClient(env.url, token)
            res = client.get_table_deep_context(table_name=table_name, website_id=website_id)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in audit_table_portal_context")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def get_portal_websites_list(
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """List all provisioned Power Pages websites and portals in the environment with IDs and URLs.

        Args:
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = PortalWebApiAuditorClient(env.url, token)
            res = client.get_websites()
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_portal_websites_list")
            return json.dumps({"error": str(exc)}, indent=2)

