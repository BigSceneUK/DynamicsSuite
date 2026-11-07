from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import streamlit as st

from core.context import AppContext
from .service import get_embedded_mcp_server


def render_ui(ctx: AppContext, plugin_instance: Any = None) -> None:
    """Render the MCP Server Plugin user interface."""
    st.header("🤖 Model Context Protocol (MCP) Server")
    st.caption(
        "Runs an in-memory MCP and IPC server enabling AI assistants (Claude Desktop, Cursor, Antigravity) "
        "to interact with your active Microsoft Dynamics environment without writing tokens to disk."
    )

    # 1. Environment Connection Gate
    if not ctx.org_url:
        st.warning("⚠️ Please sign in and connect to a Dataverse environment from the sidebar first.")
        return

    server = get_embedded_mcp_server()

    # Load configuration
    plugin_config = {}
    if plugin_instance and hasattr(plugin_instance, "default_config"):
        plugin_config = getattr(plugin_instance, "config", {}) or plugin_instance.default_config

    default_port = plugin_config.get("port", 58999)
    auto_start = plugin_config.get("auto_start", True)

    # Auto-start on load if configured and not already running
    if auto_start and not server.is_running():
        server.start(default_port, ctx)
    elif server.is_running():
        # Keep context fresh
        server.set_context(ctx)

    status = server.get_status()
    is_running = status["is_running"]
    current_port = status["port"] if is_running else default_port

    st.divider()

    # 2. Status Banner
    col_status, col_sec = st.columns([2, 1])
    with col_status:
        if is_running:
            st.success(f"🟢 **MCP Server Running** on `http://127.0.0.1:{current_port}`")
            st.caption(f"Uptime: {status['uptime_seconds']} seconds | SSE endpoint: `http://127.0.0.1:{current_port}/sse`")
        else:
            st.error("🔴 **MCP Server Stopped**")
            st.caption("AI assistants will not be able to connect until the service is started.")

    with col_sec:
        st.info("🛡️ **Zero Disk Storage**  \nTokens held in RAM only.")

    st.markdown("---")

    # 3. Server Configuration & Controls
    st.subheader("⚙️ Configuration & Port Management")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        new_port = st.number_input(
            "Service Port",
            min_value=1024,
            max_value=65535,
            value=int(current_port),
            step=1,
            help="Port to run the local MCP HTTP/SSE & IPC service on (default: 58999).",
            key="mcp_server_port_input",
        )

    with c2:
        st.write("")
        st.write("")
        auto_start_checked = st.checkbox(
            "Auto-start on sign-in",
            value=auto_start,
            help="Automatically start the MCP server whenever you sign into DynamicsSuite.",
            key="mcp_server_autostart_toggle",
        )

    with c3:
        st.write("")
        st.write("")
        btn_cols = st.columns(3)
        with btn_cols[0]:
            if not is_running:
                if st.button("▶️ Start Server", type="primary", use_container_width=True):
                    with st.spinner("Starting server..."):
                        if server.start(int(new_port), ctx):
                            st.success(f"Started on port {new_port}")
                            time.sleep(0.3)
                            st.rerun()
                        else:
                            st.error("Failed to start server. Check if port is already in use.")
            else:
                if st.button("⏹️ Stop Server", type="secondary", use_container_width=True):
                    server.stop()
                    st.warning("Server stopped.")
                    time.sleep(0.3)
                    st.rerun()

        with btn_cols[1]:
            if is_running and st.button("🔄 Restart", use_container_width=True):
                with st.spinner("Restarting on new port..."):
                    server.stop()
                    time.sleep(0.5)
                    server.start(int(new_port), ctx)
                    st.success(f"Restarted on port {new_port}")
                    time.sleep(0.3)
                    st.rerun()

        with btn_cols[2]:
            if st.button("⚡ Test Health", use_container_width=True, disabled=not is_running):
                try:
                    start_t = time.time()
                    req = urllib.request.Request(f"http://127.0.0.1:{current_port}/api/status")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        body = json.loads(resp.read().decode())
                        elapsed_ms = int((time.time() - start_t) * 1000)
                        st.toast(f"✅ Local MCP Server healthy! Latency: {elapsed_ms}ms", icon="⚡")
                except Exception as exc:
                    st.toast(f"❌ Health check failed: {exc}", icon="⚠️")

    # 4. Active Session Summary
    st.markdown("---")
    st.subheader("📋 Active Session (In-Memory)")

    meta_cols = st.columns(4)
    with meta_cols[0]:
        st.metric("Environment", ctx.environment_name or "Dataverse")
    with meta_cols[1]:
        st.metric("Signed-in User", ctx.user_email or "Unknown")
    with meta_cols[2]:
        st.metric("Active Port", f":{current_port}" if is_running else "Inactive")
    with meta_cols[3]:
        st.metric("Token Storage", "RAM only")

    st.caption(f"Target CRM URL: `{ctx.org_url}`")

    # 5. Client Configuration Snippets
    st.markdown("---")
    st.subheader("🔌 AI Assistant Setup Guide")
    st.markdown(
        "Add this server to your AI tool's configuration. "
        "Because of **in-memory security**, the AI assistant will only have access while this DynamicsSuite tab is running."
    )

    py_executable = sys.executable
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    tab_claude, tab_cursor, tab_antigravity = st.tabs(["Claude Desktop", "Cursor", "Antigravity & Others"])

    with tab_claude:
        st.markdown(
            "Paste this into your `claude_desktop_config.json`  \n"
            "* macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`  \n"
            "* Windows: `%APPDATA%\\Claude\\claude_desktop_config.json`"
        )
        claude_sse_snippet = {
            "mcpServers": {
                "dynamics365": {
                    "url": f"http://127.0.0.1:{current_port}/sse"
                }
            }
        }
        st.code(json.dumps(claude_sse_snippet, indent=2), language="json")

        st.caption("Or, if your Claude Desktop version prefers Stdio:")
        claude_stdio_snippet = {
            "mcpServers": {
                "dynamics365": {
                    "command": py_executable,
                    "args": ["-m", "mcp_server.server", "--port", str(current_port)],
                    "cwd": workspace_dir
                }
            }
        }
        st.code(json.dumps(claude_stdio_snippet, indent=2), language="json")

    with tab_cursor:
        st.markdown("Add to `.cursor/mcp.json` in your project root:")
        cursor_snippet = {
            "mcpServers": {
                "dynamics365": {
                    "url": f"http://127.0.0.1:{current_port}/sse"
                }
            }
        }
        st.code(json.dumps(cursor_snippet, indent=2), language="json")

    with tab_antigravity:
        st.markdown("Antigravity MCP Server Configuration:")
        st.code(
            f"""# In your MCP Settings:
URL: http://127.0.0.1:{current_port}/sse
Transport: SSE
Protocol: Model Context Protocol (MCP)""",
            language="yaml",
        )

