from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider

logger = logging.getLogger("mcp_plugin_discovery")

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "plugins_config.json"


def _is_plugin_enabled(plugin_id: str) -> bool:
    """Check if the plugin is enabled in plugins_config.json."""
    if not CONFIG_FILE.is_file():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and plugin_id in cfg:
            return bool(cfg[plugin_id].get("enabled", True))
    except Exception as exc:
        logger.warning("Could not read %s: %s", CONFIG_FILE, exc)
    return True


def load_dynamic_plugin_tools(
    app: MCPServer,
    auth_provider: DynamicsAuthProvider,
) -> List[Dict[str, Any]]:
    """
    Scan the plugins directory for any plugin defining mcp_tools.py
    and automatically register its AI tools into the MCP Server.

    Supported plugin convention:
        plugins/<plugin_name>/mcp_tools.py

    The module can define either:
        1. def register_mcp_tools(app: MCPServer, auth_provider: DynamicsAuthProvider):
               @app.tool()
               def my_custom_tool(...): ...
        or
        2. def register_tools(app: MCPServer, auth_provider: DynamicsAuthProvider): ...
    """
    if not PLUGINS_DIR.is_dir():
        return []

    discovered_summary: List[Dict[str, Any]] = []

    for item in sorted(PLUGINS_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith((".", "_")):
            continue

        plugin_id = item.name
        tools_file = item / "mcp_tools.py"
        if not tools_file.is_file():
            continue

        if not _is_plugin_enabled(plugin_id):
            logger.info("Skipping disabled plugin '%s' for MCP tools", plugin_id)
            continue

        # Count registered tools before import
        existing_tools = set(app._tool_manager._tools.keys()) if hasattr(app._tool_manager, "_tools") else set()

        try:
            module_name = f"plugins.{plugin_id}.mcp_tools"
            spec = importlib.util.spec_from_file_location(module_name, tools_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                registered_fn = getattr(mod, "register_mcp_tools", None) or getattr(mod, "register_tools", None)
                if callable(registered_fn):
                    registered_fn(app, auth_provider)
                else:
                    logger.warning(
                        "Plugin '%s' has mcp_tools.py but no register_mcp_tools(app, auth) function found.",
                        plugin_id,
                    )

                # Determine newly added tools
                new_tools = set(app._tool_manager._tools.keys()) - existing_tools
                if new_tools:
                    discovered_summary.append({
                        "plugin_id": plugin_id,
                        "tools": sorted(list(new_tools)),
                    })
                    logger.info("Auto-registered %d MCP tools from plugin '%s': %s", len(new_tools), plugin_id, list(new_tools))
        except Exception as exc:
            logger.exception("Failed to load dynamic MCP tools from plugin '%s': %s", plugin_id, exc)

    return discovered_summary

