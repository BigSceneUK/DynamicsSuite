import traceback
import streamlit as st

from core.plugin_registry import PluginRegistry
from core.ui import login_page, sidebar, plugin_manager_page, plugin_header, feedback_dialog

st.set_page_config(
    page_title="Dynamics Suite",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# Plugin registry — discover once per session
# ------------------------------------------------------------------

@st.cache_resource
def _get_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.discover()
    registry.load_config()
    return registry


registry = _get_registry()

# ------------------------------------------------------------------
# Authentication gate
# ------------------------------------------------------------------

if "app_context" not in st.session_state:
    login_page.render()
    st.stop()

ctx = st.session_state["app_context"]

# ------------------------------------------------------------------
# Navigation
# ------------------------------------------------------------------

# Default to Plugin Manager on first load after login
if "selected_plugin_id" not in st.session_state:
    st.session_state["selected_plugin_id"] = "__plugin_manager__"

selected_id = sidebar.render(registry, ctx)

# ------------------------------------------------------------------
# Routing
# ------------------------------------------------------------------

if selected_id == "__plugin_manager__":
    plugin_manager_page.render(registry)
else:
    plugin = registry.get_plugin(selected_id)
    if plugin is None:
        st.error(f"Plugin '{selected_id}' is not loaded.")
    else:
        plugin_header.render(plugin, ctx)
        try:
            plugin.render(ctx)
        except Exception as exc:
            st.error(f"**{plugin.name}** encountered an unexpected error.")
            with st.expander("🔍 Technical Error Details"):
                st.exception(exc)

            tb_str = traceback.format_exc()
            if st.button("🐞 Report this Error to Support", key=f"crash_report_{selected_id}", type="primary"):
                feedback_dialog.open_feedback_dialog(
                    target_name=plugin.name,
                    version=str(getattr(plugin, "version", "1.0.0")),
                    user_email=ctx.user_email or "",
                    support_email=getattr(plugin, "support_email", "support@bigscene.uk") or "support@bigscene.uk",
                    repo_url=getattr(plugin, "repo_url", ""),
                    issues_url=getattr(plugin, "issues_url", ""),
                    error_log=tb_str,
                    default_type="🐞 Report a Bug / Error",
                    ctx=ctx,
                )

# Render inline feedback if running on environments without modal dialogs
feedback_dialog.render_fallback_feedback_if_active(ctx)
