"""
MCP Tools for Audit History Explorer plugin.
Exposes Dataverse record audit histories, attribute change logs, and audit-enabled entity discovery.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.audit_history.client import DataverseClient

logger = logging.getLogger("mcp_audit_history")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register audit history tools into MCP server."""

    @app.tool()
    def get_record_audit_history(
        entity_set_name: str,
        record_id: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Fetch field-level change audit history for a specific Dataverse record (who changed what, when, old and new values).

        Args:
            entity_set_name: Entity set name (e.g. 'contacts', 'accounts', 'incidents').
            record_id: GUID of the record to audit.
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            res = client.get_audit_history(entity_set_name=entity_set_name, record_id=record_id)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_record_audit_history")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def list_audit_enabled_entities(
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """List all Dataverse tables and entities that have change auditing enabled or are custom entities.

        Args:
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            res = client.get_entities()
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in list_audit_enabled_entities")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def search_audit_records(
        entity_set_name: str,
        search_field: Optional[str] = None,
        search_value: Optional[str] = None,
        top: int = 20,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Search for records in an entity set by field and value to locate record GUIDs for audit inspection.

        Args:
            entity_set_name: Entity set name (e.g. 'contacts', 'accounts').
            search_field: Optional field logical name to filter on (e.g. 'emailaddress1', 'name').
            search_value: Optional value to match.
            top: Maximum number of records to return (default 20).
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            filters = {search_field: search_value} if search_field and search_value else None
            res = client.search_records(entity_set_name=entity_set_name, filters=filters, top=top)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in search_audit_records")
            return json.dumps({"error": str(exc)}, indent=2)

