"""
update_banner.py — Startup & In-App Plugin Update Notification Banner for DynamicsSuite.

Checks for available plugin updates on startup (throttled to avoid network overhead)
and renders a clean, non-intrusive notification banner at the top of the application.
Provides:
  - 1-click update directly from the banner
  - Direct navigation to Plugin Manager
  - Session-level dismissal
"""

from __future__ import annotations

import logging
import time
from typing import List, Tuple
import streamlit as st

import config
from core.plugin_registry import PluginRegistry
from core.plugin_store import (
    PluginStatus,
    StorePlugin,
    fetch_catalog,
    get_plugin_status,
    install_or_update_plugin,
)

_CACHE_TTL_SECONDS = 1800  # Re-check every 30 minutes at most
_SESSION_UPDATES_KEY = "_ds_available_plugin_updates"
_SESSION_TIMESTAMP_KEY = "_ds_last_update_check_ts"
_SESSION_DISMISSED_KEY = "_ds_update_banner_dismissed"


def get_available_updates(
    registry: PluginRegistry,
    force: bool = False,
) -> List[Tuple[StorePlugin, str]]:
    """
    Check for available plugin updates against the remote/local catalog.
    Returns a list of (StorePlugin, installed_version) tuples.
    Caches results in session state to prevent repeated network calls on every rerun.
    """
    now = time.time()
    last_check = st.session_state.get(_SESSION_TIMESTAMP_KEY, 0)

    # Return cached results if within TTL and not forcing refresh
    if not force and (now - last_check < _CACHE_TTL_SECONDS) and _SESSION_UPDATES_KEY in st.session_state:
        return st.session_state[_SESSION_UPDATES_KEY]

    catalog_url = st.session_state.get("store_catalog_url") or getattr(config, "DEFAULT_CATALOG_URL", "")
    try:
        catalog_plugins, _ = fetch_catalog(catalog_url)
    except Exception as exc:
        logging.warning("Startup update check failed to fetch catalog: %s", exc)
        st.session_state[_SESSION_UPDATES_KEY] = []
        st.session_state[_SESSION_TIMESTAMP_KEY] = now
        return []

    available_updates: List[Tuple[StorePlugin, str]] = []
    for store_plugin in catalog_plugins:
        try:
            status, installed_ver = get_plugin_status(store_plugin, registry)
            if status == PluginStatus.UPDATE_AVAILABLE and installed_ver:
                available_updates.append((store_plugin, installed_ver))
        except Exception as exc:
            logging.debug("Error checking status for %s: %s", store_plugin.id, exc)

    st.session_state[_SESSION_UPDATES_KEY] = available_updates
    st.session_state[_SESSION_TIMESTAMP_KEY] = now
    return available_updates


def clear_update_cache() -> None:
    """Clear cached update check results."""
    st.session_state.pop(_SESSION_UPDATES_KEY, None)
    st.session_state.pop(_SESSION_TIMESTAMP_KEY, None)


def render(registry: PluginRegistry) -> None:
    """
    Render the startup/in-app plugin update notification banner if any installed
    plugin has a newer version available in the catalog.
    """
    if st.session_state.get(_SESSION_DISMISSED_KEY, False):
        return

    updates = get_available_updates(registry)
    if not updates:
        return

    num_updates = len(updates)
    title_text = f"🔔 **{num_updates} Plugin Update{'s' if num_updates > 1 else ''} Available**"

    with st.container(border=True):
        col_content, col_actions = st.columns([7, 3])

        with col_content:
            st.markdown(title_text)
            update_descriptions = []
            for p, installed_v in updates:
                update_descriptions.append(
                    f"• {p.icon} **{p.name}**: `{installed_v}` ➔ `v{p.version}`"
                )
            st.markdown("\n".join(update_descriptions))

        with col_actions:
            btn_update_label = "⚡ Update All" if num_updates > 1 else "⚡ Update Now"
            if st.button(btn_update_label, key="ds_banner_update_now_btn", type="primary", use_container_width=True):
                with st.spinner("Downloading and applying plugin updates..."):
                    errors = []
                    success_count = 0
                    for store_plugin, _ in updates:
                        ok, msg = install_or_update_plugin(store_plugin, registry)
                        if ok:
                            success_count += 1
                        else:
                            errors.append(f"{store_plugin.name}: {msg}")

                    clear_update_cache()

                    if errors:
                        st.error("Some updates encountered errors:\n" + "\n".join(errors))
                    else:
                        st.toast(
                            f"🎉 Successfully updated {success_count} plugin{'s' if success_count > 1 else ''}!",
                            icon="✅",
                        )
                        st.rerun()

            c_sub1, c_sub2 = st.columns(2)
            with c_sub1:
                if st.button("🏪 Store", key="ds_banner_store_btn", help="Open Plugin Manager", use_container_width=True):
                    st.session_state["selected_plugin_id"] = "__plugin_manager__"
                    st.rerun()
            with c_sub2:
                if st.button("✕ Dismiss", key="ds_banner_dismiss_btn", help="Dismiss update notification for this session", use_container_width=True):
                    st.session_state[_SESSION_DISMISSED_KEY] = True
                    st.rerun()
