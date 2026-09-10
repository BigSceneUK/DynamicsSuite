from core.plugin_base import PluginBase
from core.context import AppContext
import streamlit as st

class SolutionPackagerPlugin(PluginBase):
    name = "Solution Packager UI"
    icon = "📦"
    description = "Easily extract, inspect, and package Dataverse solution zip files directly from the browser."
    version = "1.0.0"
    release_date = "2026-08-28"
    author = "Community"
    released_by = "Community"
    tags = ["Solutions", "DevOps", "ALM"]

    def render(self, ctx: AppContext) -> None:
        st.title("📦 Solution Packager UI")
        st.info("Solution Packager tool successfully installed and loaded from the Tools Library!")
