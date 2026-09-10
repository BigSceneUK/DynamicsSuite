import streamlit as st

from core.plugin_base import PluginBase
from core.plugin_registry import PluginRegistry


def _has_config(instance) -> bool:
    """Return True if the plugin overrides render_config() (not just inherits the no-op)."""
    return type(instance).render_config is not PluginBase.render_config


def render(registry: PluginRegistry) -> None:
    """Render the Plugin Manager admin page."""

    st.title("⚙️ Plugin Manager")
    st.markdown(
        "Enable or disable plugins below. Changes take effect immediately and are "
        "saved to `plugins_config.json` so they persist across restarts."
    )
    st.divider()

    entries = registry.all_entries()

    if not entries:
        st.info(
            "No plugins found. Place a plugin folder under `plugins/` and restart the app."
        )
        return

    # Track pending changes
    pending: dict[str, bool] = {}

    for entry in entries:
        col_icon, col_info, col_toggle = st.columns([0.5, 6, 1.5])

        if entry.load_error:
            with col_icon:
                st.markdown("❌")
            with col_info:
                st.markdown(f"**{entry.plugin_id}** — *failed to load*")
                st.caption(entry.load_error)
            with col_toggle:
                st.toggle(
                    "Enable",
                    value=False,
                    disabled=True,
                    key=f"toggle_{entry.plugin_id}",
                    label_visibility="collapsed",
                )
        else:
            p = entry.instance
            with col_icon:
                st.markdown(p.icon)
            with col_info:
                st.markdown(f"**{p.name}** `v{p.version}`")
                st.caption(p.description)
            with col_toggle:
                new_val = st.toggle(
                    "Enable",
                    value=entry.enabled,
                    key=f"toggle_{entry.plugin_id}",
                    label_visibility="collapsed",
                )
                if new_val != entry.enabled:
                    pending[entry.plugin_id] = new_val

            # Show Configure expander only for plugins that override render_config()
            if _has_config(p):
                with st.expander(f"🔧 Configure {p.name}", expanded=False):
                    try:
                        p.render_config()
                    except Exception as exc:
                        st.error(f"Configuration UI error: {exc}")
                        st.exception(exc)

        st.divider()

    if pending:
        for plugin_id, enabled in pending.items():
            registry.set_enabled(plugin_id, enabled)
        registry.save_config()
        action = "enabled" if list(pending.values())[0] else "disabled"
        names = ", ".join(pending.keys())
        st.success(f"Saved — {names} {action}.")
        st.rerun()

