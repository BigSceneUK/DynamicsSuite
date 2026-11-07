from __future__ import annotations

from core.context import AppContext
from core.plugin_base import PluginBase
from .ui import render_ui
from .service import get_embedded_mcp_server


class MCPServerPlugin(PluginBase):
    name: str = "MCP Server"
    icon: str = "🤖"
    description: str = "Runs local Model Context Protocol (MCP) server for AI assistants with in-memory session security."
    version: str = "1.1.0"
    release_date: str = "2026-11-07"
    author: str = "DynamicsSuite Team"
    released_by: str = "DynamicsSuite Team"
    tags: list[str] = ["MCP", "AI", "Assistant", "Claude", "Cursor", "Antigravity", "Security"]
    requires_graph: bool = False

    default_config: dict = {
        "port": 58999,
        "auto_start": True,
    }

    def on_load(self) -> None:
        """Called when plugin is enabled."""
        pass

    def on_unload(self) -> None:
        """Called when plugin is disabled — stops the running background server."""
        try:
            server = get_embedded_mcp_server()
            if server.is_running():
                server.stop()
        except Exception:
            pass

    def render(self, ctx: AppContext) -> None:
        """Render the MCP Server Plugin UI."""
        render_ui(ctx, self)

