"""
MCP Tools for Record Transporter plugin.
Exposes record comparison, target existence checks, and transport preparation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.record_transporter.client import RecordTransporterClient

logger = logging.getLogger("mcp_record_transporter")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register record transporter tools into MCP server."""

    @app.tool()
    def get_record_transport_details(
        entity_set_name: str,
        record_id: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Fetch raw and formatted payload of a record to prepare or verify transport across environments.

        Args:
            entity_set_name: Entity set name (e.g. 'contacts', 'accounts').
            record_id: GUID of the record.
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = RecordTransporterClient(env.url, token)
            res = client.fetch_record_details(entity_set_name=entity_set_name, record_id=record_id)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_record_transport_details")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def check_record_exists_in_env(
        entity_set_name: str,
        record_id: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Check whether a specific record GUID exists in the target Dataverse environment.

        Args:
            entity_set_name: Entity set name (e.g. 'contacts', 'accounts').
            record_id: GUID of the record.
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = RecordTransporterClient(env.url, token)
            exists = client.check_record_exists(entity_set_name=entity_set_name, record_id=record_id)
            return json.dumps({
                "entity_set_name": entity_set_name,
                "record_id": record_id,
                "exists": exists,
                "environment": env.name,
            }, indent=2)
        except Exception as exc:
            logger.exception("Error in check_record_exists_in_env")
            return json.dumps({"error": str(exc)}, indent=2)

