import streamlit as st

from core.context import AppContext
from core.plugin_base import PluginBase
from plugins.audit_history import client as ah_module
from plugins.audit_history import ui


class AuditHistoryPlugin(PluginBase):
    name = "Audit History Explorer"
    icon = "📜"
    description = (
        "Browse field-level Dataverse record changes — who changed what, "
        "when, and from which value to which."
    )
    version = "1.0.0"
    release_date = "2026-09-04"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Audit", "History", "Compliance"]
    requires_graph = False

    def render(self, ctx: AppContext) -> None:
        if not ctx.org_url:
            st.warning(
                "No Dataverse environment URL configured. "
                "Please set one in the sidebar before using this plugin."
            )
            return

        try:
            token = ctx.auth.get_token(ctx.org_url)
        except Exception as exc:
            st.error(f"Could not acquire a Dataverse token: {exc}")
            st.info("Please sign out and sign in again.")
            return

        client = ah_module.DataverseClient(ctx.org_url, token)
        ui.render_page(ctx, client)
