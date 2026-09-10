from core.plugin_base import PluginBase
from core.context import AppContext
from .ui import render_ui

class RecordTransporterPlugin(PluginBase):
    name = "Record Transporter"
    icon = "🚚"
    description = "Transfer records between different environments, resolving missing lookup dependencies interactively."
    version = "1.0.0"
    release_date = "2026-09-01"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Migration", "Dataverse", "Transporter"]

    def render(self, ctx: AppContext) -> None:
        """
        Renders the Record Transporter UI.
        """
        render_ui(ctx)
