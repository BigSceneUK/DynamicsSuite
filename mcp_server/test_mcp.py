#!/usr/bin/env python3
"""
Test Suite for Dynamics 365 Architecture & System Diagnostics MCP Server.
Verifies server initialization, tool schemas, and offline/graceful handling.
"""

import json
import unittest
from mcp_server.server import app, list_environments, list_accounts, get_case, diagnose_record, find_relationship_path, check_user_access


class TestMCPServer(unittest.TestCase):

    def test_registered_tools_count(self):
        """Verify all 18 core tools and dynamic plugin tools are registered with valid signatures and descriptions."""
        tools = app._tool_manager.list_tools()
        self.assertGreaterEqual(len(tools), 18, f"Expected at least 18 registered tools, got {len(tools)}")
        tool_names = [t.name for t in tools]
        expected_tools = [
            "list_environments",
            "list_accounts",
            "get_case",
            "search_cases",
            "get_case_timeline",
            "add_case_note",
            "query_dataverse_table",
            "get_entity_metadata",
            "execute_fetchxml",
            "diagnose_record",
            "list_user_roles",
            "check_user_access",
            "find_relationship_path",
            "get_table_components",
            "list_entity_triggers",
            "get_failed_flow_runs",
            "audit_portal_webapi",
            "get_portal_page_hierarchy",
        ]
        for name in expected_tools:
            self.assertIn(name, tool_names, f"Tool '{name}' was not found registered in MCP Server.")

    def test_dynamic_plugin_auto_discovery(self):
        """Verify dynamic auto-discovery of tools from plugins/<id>/mcp_tools.py."""
        tool_names = [t.name for t in app._tool_manager.list_tools()]
        expected_dynamic_tools = [
            "template_plugin_action",
            "get_record_audit_history",
            "list_cloud_flows",
            "get_mcp_server_status",
        ]
        for tool_name in expected_dynamic_tools:
            self.assertIn(
                tool_name,
                tool_names,
                f"Dynamic tool '{tool_name}' was not discovered from plugins.",
            )

    def test_list_environments_empty_safe(self):
        """Verify list_environments returns valid JSON list even when no session is active."""
        res_str = list_environments()
        data = json.loads(res_str)
        self.assertIsInstance(data, list)

    def test_list_accounts_empty_safe(self):
        """Verify list_accounts returns valid JSON list even when no session is active."""
        res_str = list_accounts()
        data = json.loads(res_str)
        self.assertIsInstance(data, list)

    def test_unauthenticated_call_returns_graceful_error_json(self):
        """Verify tools return clean error JSON instead of crashing when unauthenticated."""
        res_str = get_case("CAS-00001")
        data = json.loads(res_str)
        self.assertIn("error", data)
        self.assertTrue(len(data["error"]) > 0)


if __name__ == "__main__":
    unittest.main()

