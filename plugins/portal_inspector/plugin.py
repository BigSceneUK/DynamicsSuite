import streamlit as st

from core.context import AppContext
from core.plugin_base import PluginBase
from plugins.portal_inspector import client as pi_client
from plugins.portal_inspector import ui


class PortalInspectorPlugin(PluginBase):
    name = "Portal Inspector"
    icon = "🌐"
    description = (
        "Explore component hierarchies, forms, and templates in Power Pages portals "
        "using the Page Structure Explorer."
    )
    version = "1.1.0"
    release_date = "2026-09-08"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Power Pages", "Portals", "Metadata"]
    requires_graph = False

    def render(self, ctx: AppContext) -> None:
        if not ctx.org_url:
            st.warning(
                "No Dataverse environment URL configured. Please set one in the sidebar."
            )
            return
        try:
            token = ctx.auth.get_token(ctx.org_url)
        except Exception as exc:
            st.error(f"Could not acquire a Dataverse token: {exc}")
            st.info("Please sign out and sign in again.")
            return
        client = pi_client.DataverseClient(ctx.org_url, token)
        try:
            ui.render_page(ctx, client)
        except Exception as exc:
            st.error(f"Portal Inspector encountered an error: {exc}")
            with st.expander("Error details"):
                st.exception(exc)
