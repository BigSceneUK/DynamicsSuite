"""
MCP Tools for Table & Schema Inspector plugin.
Exposes entity attribute mappings, form placements, and record count statistics.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.table_inspector.client import TableInspectorClient

logger = logging.getLogger("mcp_table_inspector")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register table inspector tools into MCP server."""

    @app.tool()
    def get_table_attributes_and_forms(
        logical_name: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Fetch all attributes for an entity along with which Model-Driven forms contain each attribute.

        Args:
            logical_name: Entity logical name (e.g. 'account', 'contact', 'incident').
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = TableInspectorClient(env.url, token)
            attrs = client.get_attributes(logical_name=logical_name)
            forms_map = client.get_forms_containing_attributes(logical_name=logical_name)
            return json.dumps({
                "entity": logical_name,
                "total_attributes": len(attrs),
                "attributes": attrs,
                "forms_containing_attributes": forms_map,
            }, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_table_attributes_and_forms")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def get_table_total_record_count(
        logical_name: str,
        entity_set_name: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Get the total record count for a table using FetchXML aggregate counting.

        Args:
            logical_name: Entity logical name (e.g. 'incident').
            entity_set_name: Entity set name (e.g. 'incidents').
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = TableInspectorClient(env.url, token)
            count = client.get_table_total_count(logical_name=logical_name, entity_set_name=entity_set_name)
            return json.dumps({
                "entity": logical_name,
                "entity_set_name": entity_set_name,
                "total_records": count,
                "environment": env.name,
            }, indent=2)
        except Exception as exc:
            logger.exception("Error in get_table_total_record_count")
            return json.dumps({"error": str(exc)}, indent=2)

