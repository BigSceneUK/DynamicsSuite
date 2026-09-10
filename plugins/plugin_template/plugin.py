"""
plugin_template plugin for DynamicsSuite.
Reference starter template for developing custom DynamicsSuite plugins.
"""

from __future__ import annotations

import streamlit as st
from core.context import AppContext
from core.plugin_base import PluginBase


class PluginTemplatePlugin(PluginBase):
    name = "Plugin Development Project Template"
    icon = "🧩"
    description = "Starter template and reference implementation for building custom DynamicsSuite plugins."
    version = "1.0.0"
    release_date = "2026-09-09"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Template", "Development", "Dataverse", "SDK"]
    requires_graph = False

    def on_load(self) -> None:
        """Lifecycle hook: Called once when the plugin is loaded into the app."""
        pass

    def on_unload(self) -> None:
        """Lifecycle hook: Called once when the plugin is unloaded or switched."""
        pass

    def render(self, ctx: AppContext) -> None:
        """Render the plugin UI.

        Args:
            ctx: AppContext containing authenticated identity, tenant, org_url,
                 and auth token provider.
        """
        st.header(f"{self.icon} {self.name}")
        st.caption(self.description)

        # 1. Environment Connection Gate
        if not ctx.org_url:
            st.warning("⚠️ Please connect to a Dataverse environment from the sidebar first.")
            return

        st.success(f"Connected Environment: **{ctx.org_url}**")

        tabs = st.tabs(["🚀 Getting Started", "⚙️ Action Sandbox", "📚 Architecture Guide"])

        with tabs[0]:
            st.subheader("Plugin Architecture & Context")
            st.markdown(
                """
                Each plugin in DynamicsSuite:
                - Subclasses `PluginBase` and implements `render(self, ctx: AppContext)`
                - Accesses the shared authentication context via `ctx`
                - Can acquire tokens silently without prompting the user again:
                """
            )
            st.code(
                """# Example: Acquiring a Dataverse Web API Bearer Token
token = ctx.auth.get_token(ctx.org_url)

# Example: Acquiring a Microsoft Graph API Token
graph_token = ctx.auth.get_token("https://graph.microsoft.com")
                """,
                language="python",
            )
            st.info(
                f"**Current User**: {ctx.user_display_name or 'Anonymous'} "
                f"(`{ctx.user_email or 'Not logged in'}`)"
            )

        with tabs[1]:
            st.subheader("Interactive Sandbox")
            st.write("Use this area to test Streamlit inputs, tables, and Dataverse calls.")

            col1, col2 = st.columns([3, 1])
            with col1:
                sample_input = st.text_input("Sample Parameter", value="accounts")
            with col2:
                st.write("")
                st.write("")
                run_btn = st.button("Execute", type="primary")

            if run_btn:
                with st.spinner(f"Simulating operation on '{sample_input}'..."):
                    st.success(f"Operation completed for `{sample_input}` on `{ctx.org_url}`!")

        with tabs[2]:
            st.subheader("Best Practices for DynamicsSuite Plugins")
            st.markdown(
                """
                1. **State Isolation**: Prefix all `st.session_state` keys with your plugin ID (e.g. `st.session_state['myplugin_data']`).
                2. **Modular Architecture**: For complex tools, split into:
                   - `plugin.py`: Class definition and UI entry point
                   - `ui.py`: Streamlit rendering functions and tabs
                   - `client.py`: Dataverse Web API / OData query logic
                3. **CLI Management**:
                   - Validate: `python3 scripts/manage_plugin.py validate plugin_template`
                   - Bump Version: `python3 scripts/manage_plugin.py bump plugin_template patch`
                   - Build & Package: `python3 scripts/manage_plugin.py build`
                """
            )
