"""
MCP Tools for Cloud Flow plugin.
Exposes Power Automate cloud flow cataloging, execution run histories, and status diagnostics.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from plugins.cloud_flow.client import DataverseClient

logger = logging.getLogger("mcp_cloud_flow")


def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    """Register cloud flow tools into MCP server."""

    @app.tool()
    def list_cloud_flows(
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """List all solution-aware Power Automate Cloud Flows in the Dataverse environment, including their IDs, display names, state, and modification dates.

        Args:
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            flows = client.get_modern_flows()
            return json.dumps(flows, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in list_cloud_flows")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def get_cloud_flow_runs(
        workflow_id: str,
        top: int = 50,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Fetch recent execution runs for a specific Power Automate Cloud Flow, including start time, end time, status, duration, and trigger name.

        Args:
            workflow_id: GUID of the Cloud Flow (workflow entity).
            top: Maximum number of recent runs to retrieve (default 50).
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            runs = client.get_flow_runs(workflow_id=workflow_id, top=top)
            return json.dumps(runs, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_cloud_flow_runs")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def search_cloud_flows(
        search_text: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Search for solution-aware Cloud Flows by matching their name or description against search text.

        Args:
            search_text: Text snippet or keyword to search for in flow names and descriptions.
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            all_flows = client.get_modern_flows()
            lower = search_text.lower()
            matches = [
                f for f in all_flows
                if lower in (f.get("name") or "").lower() or lower in (f.get("description") or "").lower()
            ]
            return json.dumps(matches, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in search_cloud_flows")
            return json.dumps({"error": str(exc)}, indent=2)

    @app.tool()
    def get_cloud_flow_sessions_by_date(
        start_datetime: str,
        end_datetime: str,
        top: int = 100,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> str:
        """Fetch Cloud Flow execution sessions across all flows within an ISO datetime window (e.g. '2026-11-01T00:00:00Z' to '2026-11-07T23:59:59Z').

        Args:
            start_datetime: Start datetime in ISO format.
            end_datetime: End datetime in ISO format.
            top: Maximum number of flow sessions to return (default 100).
            environment: Optional environment name.
            account: Optional account username or type.
        """
        try:
            env = auth.get_environment(environment)
            token = auth.get_token(environment, account=account)
            client = DataverseClient(env.url, token)
            res = client.get_sessions_by_date(start_dt=start_datetime, end_dt=end_datetime, top=top)
            return json.dumps(res, indent=2, default=str)
        except Exception as exc:
            logger.exception("Error in get_cloud_flow_sessions_by_date")
            return json.dumps({"error": str(exc)}, indent=2)

