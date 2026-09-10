from abc import ABC, abstractmethod
from core.context import AppContext


class PluginBase(ABC):
    """
    Abstract base class for all DynamicsSuite plugins.

    Every plugin must:
    1. Subclass PluginBase
    2. Set class attributes: name, icon, description (version optional)
    3. Implement render(ctx)

    The plugin is auto-discovered by PluginRegistry when placed in plugins/<folder>/plugin.py.
    The plugin author must NOT manage authentication — use ctx.auth.get_token(resource) instead.
    """

    # --- Class-level metadata (override in subclass) ---
    name: str = "Unnamed Plugin"
    icon: str = "🔌"
    description: str = "No description provided."
    version: str = "1.0.0"
    release_date: str = "2026-09-08"
    author: str = "DynamicsSuite Team"
    released_by: str = "DynamicsSuite Team"
    tags: list[str] = []

    # Optional community & support links
    repo_url: str = ""
    issues_url: str = ""
    doc_url: str = ""
    support_email: str = "support@bigscene.uk"

    # If True, the core shell verifies a Graph token exists before calling render().
    requires_graph: bool = False

    # Default configuration dictionary (override in subclass)
    default_config: dict = {}

    # --- Lifecycle methods (optional overrides) ---

    def on_load(self) -> None:
        """Called once when the plugin is enabled in the admin panel."""

    def on_unload(self) -> None:
        """Called once when the plugin is disabled in the admin panel."""

    def on_config_updated(self, config: dict) -> None:
        """Called when this plugin's configuration is modified and saved."""

    def get_config(self) -> dict:
        """Return the active configuration for this plugin (merged with default_config)."""
        if getattr(self, "_registry", None) is not None and getattr(self, "_plugin_id", None):
            return self._registry.get_plugin_config(self._plugin_id)
        return dict(self.default_config)

    def save_config(self, config: dict) -> None:
        """Save the updated configuration for this plugin."""
        if getattr(self, "_registry", None) is not None and getattr(self, "_plugin_id", None):
            self._registry.save_plugin_config(self._plugin_id, config)

    def get_example_config(self) -> dict:
        """Return example configuration from config.example.json or default_config."""
        if getattr(self, "_registry", None) is not None and getattr(self, "_plugin_id", None):
            return self._registry.get_example_config(self._plugin_id)
        return dict(self.default_config)

    def render_config(self) -> None:
        """
        Optional: render a Streamlit configuration UI for this plugin.

        Shown as an expandable "Configure" section inside the Plugin Manager.
        Override in your plugin subclass to expose per-plugin settings.
        If not overridden the Configure section is hidden.
        """

    # --- Required implementation ---

    @abstractmethod
    def render(self, ctx: AppContext) -> None:
        """
        Render the full Streamlit UI for this plugin.

        Parameters
        ----------
        ctx : AppContext
            Shared auth context. Use ctx.auth.get_token(resource) to get bearer tokens.
            Use ctx.user_email, ctx.org_url etc. for user/environment info.
        """
