import streamlit as st

from core.context import AppContext
from core.plugin_base import PluginBase
from plugins.cloud_flow import ui


class CloudFlowPlugin(PluginBase):
    name = "Cloud Flow"
    icon = "⚡"
    description = (
        "Browse execution history for solution-aware Cloud Flows. "
        "Search by flow name or date range via the Dataverse API, "
        "with optional Power Automate API for richer run details."
    )
    version = "1.0.0"
    release_date = "2026-09-01"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Power Automate", "Flow", "Automation"]
    requires_graph = False

    default_config = {
        "detail_fields": [
            "Operation",
            "Fetch XML",
            "Update Action",
            "Update Payload",
        ]
    }

    def render(self, ctx: AppContext) -> None:
        if not ctx.org_url:
            st.warning(
                "No Dataverse environment URL configured. "
                "Please set one before using this plugin."
            )
            return

        try:
            ui.render(ctx, plugin=self)
        except Exception as exc:
            st.error(f"Cloud Flow plugin encountered an error: {exc}")
            with st.expander("Error details"):
                st.exception(exc)
