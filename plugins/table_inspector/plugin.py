from core.plugin_base import PluginBase
from core.context import AppContext
from .ui import render_ui

class TableInspectorPlugin(PluginBase):
    name = "Table Inspector"
    icon = "🔍"
    description = "Inspect table attributes, metadata, form placement, and data usage statistics."
    version = "1.1.0"
    release_date = "2026-09-08"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Metadata", "Dataverse", "Schema"]

    def render(self, ctx: AppContext) -> None:
        """
        Renders the Table Inspector UI.
        """
        render_ui(ctx)
