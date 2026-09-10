import streamlit as st

from core.context import AppContext
from core.plugin_base import PluginBase
from plugins.portal_webapi_auditor import client as pwa_client
from plugins.portal_webapi_auditor import ui


class PortalWebApiAuditorPlugin(PluginBase):
    name = "Power Page API Wildcard Auditor"
    icon = "🛡️"
    description = (
        "Audit Power Pages Web API site settings for deprecated '*' wildcards, "
        "scan portal JS/HTML for table & field usage, drill down into calling components, "
        "and 1-click update field whitelists."
    )
    version = "1.1.0"
    release_date = "2026-09-08"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Security", "Power Pages", "Web API"]
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

        cached_client = st.session_state.get("pwa_client_instance")
        if cached_client is not None and getattr(cached_client, "org_url", None) == ctx.org_url:
            client = cached_client
            client.headers["Authorization"] = f"Bearer {token}"
        else:
            client = pwa_client.PortalWebApiAuditorClient(ctx.org_url, token)
            st.session_state["pwa_client_instance"] = client

        try:
            ui.render_page(ctx, client)
        except Exception as exc:
            st.error(f"Power Page API Wildcard Auditor encountered an error: {exc}")
            with st.expander("Error details"):
                st.exception(exc)
