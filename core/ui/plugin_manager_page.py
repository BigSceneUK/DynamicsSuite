from __future__ import annotations

import html
import json
from pathlib import Path
import streamlit as st
import bleach

import config
from core.plugin_base import PluginBase
from core.plugin_registry import PluginRegistry
from core.plugin_store import (
    PluginStatus,
    StorePlugin,
    fetch_catalog,
    get_plugin_status,
    install_or_update_plugin,
    uninstall_plugin,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"

# HTML tags and attributes that are safe to render in plugin READMEs.
# Covers standard Markdown-generated output only — no script/style/form tags.
_SAFE_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "s", "del",
    "code", "pre", "blockquote",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "details", "summary",
    "div", "span",
]
_SAFE_ATTRS = {
    "a": ["href", "title", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["align"],
    "td": ["align"],
    "*": ["class", "style"],
}


def _safe_markdown(text: str) -> str:
    """
    Sanitize HTML embedded in markdown text before rendering.

    Strips disallowed tags and attributes (e.g. <script>, event handlers)
    while preserving standard markdown-generated HTML elements.
    Used for remote catalog README content to prevent XSS if the catalog
    server is ever compromised.
    """
    if not text:
        return text
    return bleach.clean(text, tags=_SAFE_TAGS, attributes=_SAFE_ATTRS, strip=True)




def _get_local_readme(plugin_id: str) -> str:
    readme_path = _PLUGINS_DIR / plugin_id / "README.md"
    if readme_path.exists():
        try:
            return readme_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


if hasattr(st, "dialog"):
    @st.dialog("📖 Plugin Guide & Documentation", width="large")
    def _show_guide_modal(title: str, icon: str, version: str, author: str, release_date: str, readme_md: str, repo_url: str = "", issues_url: str = ""):
        st.subheader(f"{icon} {title}")
        st.caption(f"v{version} • by {author} • Released {release_date}")
        if repo_url or issues_url:
            links = []
            if repo_url:
                links.append(f"[🐙 View on GitHub]({repo_url})")
            if issues_url:
                links.append(f"[🐞 Report Issue]({issues_url})")
            st.markdown(" • ".join(links))
        st.markdown("---")
        if readme_md:
            st.markdown(_safe_markdown(readme_md), unsafe_allow_html=True)
        else:
            st.info("No README.md documentation provided for this plugin.")
        if st.button("Close", key=f"dlg_close_{title}", use_container_width=True):
            st.rerun()
else:
    def _show_guide_modal(*args, **kwargs):
        pass


_VIEW_KEY = "pm_view_mode"  # "card" | "list"


def render(registry: PluginRegistry) -> None:
    """
    Render the Plugin Manager landing page.
    Provides two tabs:
      1. Installed Tools (Manage and launch local plugins)
      2. Tools Library (Store) (Browse, download, install and update plugins from the online catalog)
    """
    st.title("🔷 Plugin Manager")
    st.markdown("Discover, install, and manage tools for your Dynamics & Power Platform environment.")

    tab_installed, tab_store = st.tabs(["🔌 Installed Tools", "🏪 Tools Library (Store)"])

    with tab_installed:
        _render_installed_tab(registry)

    with tab_store:
        _render_store_tab(registry)

    _render_guide_inline_fallback()


def _render_guide_inline_fallback() -> None:
    guide_info = st.session_state.get("viewing_guide_plugin")
    if not guide_info:
        return
    st.markdown("---")
    st.subheader(f"📖 {guide_info.get('icon', '🔌')} {guide_info.get('title', 'Plugin Guide')}")
    st.caption(f"v{guide_info.get('ver', '1.0.0')} • by {guide_info.get('author', 'DynamicsSuite Team')} • Released {guide_info.get('release_date', '')}")
    repo = guide_info.get("repo_url")
    issues = guide_info.get("issues_url")
    if repo or issues:
        links = []
        if repo:
            links.append(f"[🐙 View on GitHub]({repo})")
        if issues:
            links.append(f"[🐞 Report Issue]({issues})")
        st.markdown(" • ".join(links))
    st.markdown("---")
    st.markdown(_safe_markdown(guide_info.get("readme", "No documentation provided.")))
    st.markdown("---")
    if st.button("✖ Close Guide", key="close_guide_inline_btn", use_container_width=True):
        del st.session_state["viewing_guide_plugin"]
        st.rerun()


# ==============================================================================
# Installed Tools Tab
# ==============================================================================

def _render_installed_tab(registry: PluginRegistry) -> None:
    # Header row: view toggle
    col_info, col_toggle = st.columns([8, 2])
    with col_info:
        st.caption("Manage plugins currently installed in your local DynamicsSuite environment.")
    with col_toggle:
        view_mode = st.segmented_control(
            "View",
            options=["🃏 Card", "📋 List"],
            default="🃏 Card",
            key=_VIEW_KEY,
            label_visibility="collapsed",
        )

    st.divider()

    entries = registry.all_entries()

    if not entries:
        st.info(
            "No plugins installed yet. Head to the **Tools Library (Store)** tab to download tools!"
        )
        return

    # Filter bar: Search query + Status filter
    col_search, col_filter = st.columns([7, 3])
    with col_search:
        search_query = st.text_input(
            "Search installed plugins",
            placeholder="🔍 Search plugins by name, ID, or description...",
            key="pm_search_query",
            label_visibility="collapsed",
        )
    with col_filter:
        status_filter = (
            st.segmented_control(
                "Status",
                options=["All", "Enabled", "Disabled"],
                default="All",
                key="pm_status_filter",
                label_visibility="collapsed",
            )
            or "All"
        )

    filtered_entries = [
        e for e in entries if _matches_filter(e, search_query, status_filter)
    ]

    is_filtered = bool(search_query.strip()) or status_filter != "All"
    if is_filtered:
        col_count, col_clear = st.columns([8.5, 1.5])
        with col_count:
            st.caption(f"Showing **{len(filtered_entries)}** of **{len(entries)}** plugins")
        with col_clear:
            st.button(
                "✖ Clear",
                key="pm_clear_filters",
                on_click=_clear_filters,
                use_container_width=True,
            )

    if not filtered_entries:
        st.info("No plugins match your filter criteria.")
        _render_configure_dialog(registry)
        return

    if view_mode == "🃏 Card":
        _render_card_view(registry, filtered_entries)
    else:
        _render_list_view(registry, filtered_entries)


def _clear_filters() -> None:
    st.session_state["pm_search_query"] = ""
    st.session_state["pm_status_filter"] = "All"


def _matches_filter(entry, query: str, status_filter: str) -> bool:
    if status_filter == "Enabled" and not entry.enabled:
        return False
    if status_filter == "Disabled" and entry.enabled:
        return False

    if not query:
        return True

    q = query.lower().strip()
    p = entry.instance
    name = (p.name if p else "").lower()
    desc = (p.description if p else "").lower()
    pid = entry.plugin_id.lower()
    err = (entry.load_error or "").lower()
    deps = " ".join(getattr(entry, "missing_deps", []) or []).lower()

    terms = q.split()
    searchable_text = f"{name} {desc} {pid} {err} {deps}"
    return all(term in searchable_text for term in terms)


def _plugin_status_badge(enabled: bool, load_error: str | None, missing_deps: list[str] = None) -> str:
    if missing_deps:
        return '<span style="background:#d35400;color:white;padding:2px 8px;border-radius:12px;font-size:11px">Missing Dependencies</span>'
    if load_error:
        return '<span style="background:#c0392b;color:white;padding:2px 8px;border-radius:12px;font-size:11px">Error</span>'
    if enabled:
        return '<span style="background:#1a7f37;color:white;padding:2px 8px;border-radius:12px;font-size:11px">Enabled</span>'
    return '<span style="background:#6c757d;color:white;padding:2px 8px;border-radius:12px;font-size:11px">Disabled</span>'


def _render_card_view(registry, entries) -> None:
    cols = st.columns(3, gap="medium")
    for i, entry in enumerate(entries):
        with cols[i % 3]:
            p = entry.instance
            missing_deps = getattr(entry, "missing_deps", [])
            badge = _plugin_status_badge(entry.enabled, entry.load_error, missing_deps)

            if missing_deps:
                safe_pid = html.escape(entry.plugin_id)
                deps_list_str = html.escape(", ".join(missing_deps))
                st.markdown(
                    f"""
                    <div style="
                        border:1px solid #d35400;border-radius:12px;
                        padding:20px;height:220px;
                        background:#fffcf9;opacity:0.95;
                        display:flex;flex-direction:column;">
                        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                            <span style="font-size:32px">⚠️</span>
                            <span style="font-weight:600;font-size:15px;color:#1a1a1a">{safe_pid}</span>
                        </div>
                        <div style="font-size:12px;color:#d35400;margin-bottom:4px;font-weight:600;">Missing Packages:</div>
                        <div style="font-size:12px;color:#e67e22;flex:1;overflow:hidden">{deps_list_str}</div>
                        <div style="margin-top:auto">{badge}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                if st.button(
                    "📥 Install Dependencies",
                    key=f"install_deps_card_{entry.plugin_id}",
                    use_container_width=True,
                ):
                    with st.spinner("Installing dependencies..."):
                        if registry.install_dependencies(entry.plugin_id):
                            st.success(f"Installed dependencies for {entry.plugin_id}!")
                            st.rerun()
                        else:
                            st.error(f"Failed to install dependencies: {entry.load_error}")
                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
                continue

            if entry.load_error:
                safe_pid = html.escape(entry.plugin_id)
                safe_err = html.escape(entry.load_error[:80])
                st.markdown(
                    f"""
                    <div style="
                        border:1px solid #e0e0e0;border-radius:12px;
                        padding:20px;height:220px;
                        background:#fafafa;opacity:0.7;
                        display:flex;flex-direction:column;">
                        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                            <span style="font-size:32px">❌</span>
                            <span style="font-weight:600;font-size:15px;color:#1a1a1a">{safe_pid}</span>
                        </div>
                        <div style="font-size:12px;color:#c0392b;flex:1;overflow:hidden">{safe_err}…</div>
                        <div style="margin-top:auto">{badge}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                continue

            safe_name = html.escape(p.name)
            safe_desc = html.escape(p.description)
            safe_ver = html.escape(str(p.version))
            st.markdown(
                f"""
                <div style="
                    border:1px solid #dde3ea;border-radius:12px;
                    padding:20px;height:220px;
                    background:white;box-shadow:0 1px 4px rgba(0,0,0,0.07);
                    display:flex;flex-direction:column;">
                    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                        <span style="font-size:36px">{p.icon}</span>
                        <span style="font-weight:700;font-size:16px;color:#1a1a1a">{safe_name}</span>
                    </div>
                    <div style="font-size:12px;color:#666;margin-bottom:4px">v{safe_ver}</div>
                    <div style="font-size:13px;color:#444;flex:1;line-height:1.4;overflow:hidden">{safe_desc}</div>
                    <div style="margin-top:auto;padding-top:10px">{badge}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 1])
            with btn_col1:
                use_disabled = not entry.enabled or bool(entry.load_error) or bool(missing_deps)
                if st.button(
                    "▶  Use",
                    key=f"use_card_{entry.plugin_id}",
                    use_container_width=True,
                    type="primary",
                    disabled=use_disabled,
                ):
                    st.session_state["selected_plugin_id"] = entry.plugin_id
                    st.rerun()
            with btn_col2:
                cfg_disabled = bool(entry.load_error) or bool(missing_deps)
                if st.button(
                    "⚙  Config",
                    key=f"cfg_card_{entry.plugin_id}",
                    use_container_width=True,
                    disabled=cfg_disabled,
                ):
                    st.session_state["configuring_plugin_id"] = entry.plugin_id
                    st.rerun()
            with btn_col3:
                if st.button(
                    "📖 Guide",
                    key=f"guide_card_{entry.plugin_id}",
                    use_container_width=True,
                ):
                    readme_md = _get_local_readme(entry.plugin_id)
                    p_inst = entry.instance
                    title = p_inst.name if p_inst else entry.plugin_id
                    icon = p_inst.icon if p_inst else "🔌"
                    ver = str(p_inst.version) if p_inst else "1.0.0"
                    author = getattr(p_inst, "author", "DynamicsSuite Team") if p_inst else "DynamicsSuite Team"
                    rdate = getattr(p_inst, "release_date", "") if p_inst else ""
                    repo = getattr(p_inst, "repo_url", "") if p_inst else ""
                    issues = getattr(p_inst, "issues_url", "") if p_inst else ""
                    if hasattr(st, "dialog"):
                        _show_guide_modal(title, icon, ver, author, rdate, readme_md, repo, issues)
                    else:
                        st.session_state["viewing_guide_plugin"] = {
                            "title": title, "icon": icon, "ver": ver, "author": author,
                            "release_date": rdate, "readme": readme_md, "repo_url": repo, "issues_url": issues
                        }
                        st.rerun()

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    _render_configure_dialog(registry)


def _render_list_view(registry, entries) -> None:
    for entry in entries:
        p = entry.instance
        missing_deps = getattr(entry, "missing_deps", [])
        badge = _plugin_status_badge(entry.enabled, entry.load_error, missing_deps)

        col_icon, col_info, col_actions = st.columns([0.6, 7, 2.5])

        with col_icon:
            icon_char = "⚠️" if missing_deps else ("❌" if entry.load_error else p.icon)
            st.markdown(
                f"<div style='font-size:28px;padding-top:8px'>{icon_char}</div>",
                unsafe_allow_html=True,
            )
        with col_info:
            if missing_deps:
                name = html.escape(entry.plugin_id)
                ver = ""
                desc = html.escape(f"Missing dependencies: {', '.join(missing_deps)}")
            else:
                name = html.escape(entry.plugin_id if entry.load_error else p.name)
                ver = "" if entry.load_error else f"  `v{html.escape(str(p.version))}`"
                desc = html.escape(entry.load_error or p.description)
            st.markdown(
                f"**{name}**{ver} {badge}  \n"
                f"<span style='font-size:13px;color:#666'>{desc}</span>",
                unsafe_allow_html=True,
            )
        with col_actions:
            if missing_deps:
                if st.button(
                    "📥 Install Dependencies",
                    key=f"install_deps_list_{entry.plugin_id}",
                    use_container_width=True,
                ):
                    with st.spinner("Installing..."):
                        if registry.install_dependencies(entry.plugin_id):
                            st.success("Installed!")
                            st.rerun()
                        else:
                            st.error("Failed!")
            else:
                a1, a2, a3 = st.columns(3)
                with a1:
                    use_disabled = not entry.enabled or bool(entry.load_error) or bool(missing_deps)
                    if st.button(
                        "▶ Use",
                        key=f"use_list_{entry.plugin_id}",
                        use_container_width=True,
                        type="primary",
                        disabled=use_disabled,
                    ):
                        st.session_state["selected_plugin_id"] = entry.plugin_id
                        st.rerun()
                with a2:
                    cfg_disabled = bool(entry.load_error) or bool(missing_deps)
                    if st.button(
                        "⚙ Config",
                        key=f"cfg_list_{entry.plugin_id}",
                        use_container_width=True,
                        disabled=cfg_disabled,
                    ):
                        st.session_state["configuring_plugin_id"] = entry.plugin_id
                        st.rerun()
                with a3:
                    if st.button(
                        "📖 Guide",
                        key=f"guide_list_{entry.plugin_id}",
                        use_container_width=True,
                    ):
                        readme_md = _get_local_readme(entry.plugin_id)
                        p_inst = entry.instance
                        title = p_inst.name if p_inst else entry.plugin_id
                        icon = p_inst.icon if p_inst else "🔌"
                        ver = str(p_inst.version) if p_inst else "1.0.0"
                        author = getattr(p_inst, "author", "DynamicsSuite Team") if p_inst else "DynamicsSuite Team"
                        rdate = getattr(p_inst, "release_date", "") if p_inst else ""
                        repo = getattr(p_inst, "repo_url", "") if p_inst else ""
                        issues = getattr(p_inst, "issues_url", "") if p_inst else ""
                        if hasattr(st, "dialog"):
                            _show_guide_modal(title, icon, ver, author, rdate, readme_md, repo, issues)
                        else:
                            st.session_state["viewing_guide_plugin"] = {
                                "title": title, "icon": icon, "ver": ver, "author": author,
                                "release_date": rdate, "readme": readme_md, "repo_url": repo, "issues_url": issues
                            }
                            st.rerun()

        st.divider()

    _render_configure_dialog(registry)


def _render_configure_dialog(registry: PluginRegistry) -> None:
    cfg_id = st.session_state.get("configuring_plugin_id")
    if not cfg_id:
        return

    entry = next((e for e in registry.all_entries() if e.plugin_id == cfg_id), None)
    if entry is None:
        del st.session_state["configuring_plugin_id"]
        return

    p = entry.instance
    st.markdown("---")
    st.subheader(f"⚙️  Configure — {p.name}")

    col_meta, col_toggle = st.columns([7, 2])
    with col_meta:
        st.markdown(
            f"**Version:** `{p.version}`  \n"
            f"**Plugin ID:** `{cfg_id}`  \n"
            f"**Description:** {p.description}"
        )
    with col_toggle:
        new_enabled = st.toggle(
            "Enabled",
            value=entry.enabled,
            key=f"cfg_toggle_{cfg_id}",
        )
        if new_enabled != entry.enabled:
            registry.set_enabled(cfg_id, new_enabled)
            registry.save_config()
            st.rerun()

    has_custom_ui = type(p).render_config is not PluginBase.render_config

    if has_custom_ui:
        tab_form, tab_json = st.tabs(["🎛️ Settings Form", "📝 JSON Configuration"])
        with tab_form:
            p.render_config()
        with tab_json:
            _render_json_config_editor(registry, cfg_id, p)
    else:
        _render_json_config_editor(registry, cfg_id, p)

    st.markdown("---")
    if st.button("✖  Close", key="cfg_close"):
        st.session_state.pop(f"cfg_json_editor_{cfg_id}", None)
        st.session_state.pop(f"cfg_json_editor_{cfg_id}_pending", None)
        st.session_state.pop(f"cfg_json_editor_{cfg_id}_msg", None)
        del st.session_state["configuring_plugin_id"]
        st.rerun()


def _render_json_config_editor(registry: PluginRegistry, plugin_id: str, plugin_instance: PluginBase) -> None:
    curr_config = registry.get_plugin_config(plugin_id)
    default_config = dict(getattr(plugin_instance, "default_config", {}))
    example_config = registry.get_example_config(plugin_id)

    session_key = f"cfg_json_editor_{plugin_id}"
    pending_key = f"{session_key}_pending"
    msg_key = f"{session_key}_msg"

    # Display any flash message queued from a previous action
    msg = st.session_state.pop(msg_key, None)
    if msg:
        level, text = msg
        if level == "success":
            st.success(text)
        elif level == "info":
            st.info(text)

    # Consume pending text update BEFORE the text_area widget is instantiated
    if pending_key in st.session_state:
        st.session_state[session_key] = st.session_state.pop(pending_key)
    elif session_key not in st.session_state:
        st.session_state[session_key] = json.dumps(curr_config, indent=2)

    st.caption("Standardized JSON configuration stored in `plugins_config.json`.")

    if example_config:
        with st.expander("💡 View Example Configuration Template", expanded=False):
            st.markdown("Below is the documented configuration schema and examples for this plugin:")
            st.code(json.dumps(example_config, indent=2), language="json")
            if st.button("📋 Apply Example Template to Editor", key=f"apply_example_{plugin_id}"):
                st.session_state[pending_key] = json.dumps(example_config, indent=2)
                st.session_state[msg_key] = ("info", "Example template loaded into editor.")
                st.rerun()

    edited_text = st.text_area(
        "Plugin JSON Configuration",
        height=220,
        key=session_key,
        help="Edit plugin settings in valid JSON format.",
    )

    btn_c1, btn_c2, btn_c3 = st.columns([3, 3, 3])
    with btn_c1:
        if st.button("💾 Save Configuration", key=f"save_cfg_{plugin_id}", type="primary", use_container_width=True):
            try:
                parsed = json.loads(edited_text)
                if not isinstance(parsed, dict):
                    st.error("Configuration must be a JSON object (`{...}`).")
                else:
                    registry.save_plugin_config(plugin_id, parsed)
                    st.session_state[pending_key] = json.dumps(parsed, indent=2)
                    st.session_state[msg_key] = ("success", f"Configuration saved for {plugin_instance.name}!")
                    st.toast(f"Configuration saved for {plugin_instance.name}!", icon="✅")
                    st.rerun()
            except json.JSONDecodeError as exc:
                st.error(f"Invalid JSON: {exc}")

    with btn_c2:
        if st.button("↺ Reset to Defaults", key=f"reset_cfg_{plugin_id}", use_container_width=True):
            registry.save_plugin_config(plugin_id, default_config)
            st.session_state[pending_key] = json.dumps(default_config, indent=2)
            st.session_state[msg_key] = ("info", "Reset to default configuration.")
            st.toast("Reset to default configuration.", icon="↺")
            st.rerun()

    with btn_c3:
        if st.button("🧹 Format JSON", key=f"fmt_cfg_{plugin_id}", use_container_width=True):
            try:
                parsed = json.loads(edited_text)
                st.session_state[pending_key] = json.dumps(parsed, indent=2)
                st.toast("JSON formatted.", icon="🧹")
                st.rerun()
            except json.JSONDecodeError as exc:
                st.error(f"Cannot format invalid JSON: {exc}")


# ==============================================================================
# Tools Library (Store) Tab
# ==============================================================================

def _render_store_tab(registry: PluginRegistry) -> None:
    catalog_url_default = getattr(config, "DEFAULT_CATALOG_URL", "")
    if "store_catalog_url" not in st.session_state:
        st.session_state["store_catalog_url"] = catalog_url_default

    col_title, col_settings = st.columns([8, 2])
    with col_title:
        st.subheader("🏪 Tools Library")
        st.caption("Browse online tools, install new plugins, or update your existing tools directly from the feed.")
    with col_settings:
        with st.popover("⚙️ Feed Settings", use_container_width=True):
            st.markdown("**Catalog Feed URL**")
            new_url = st.text_input(
                "Catalog URL",
                value=st.session_state["store_catalog_url"],
                key="input_catalog_url",
                label_visibility="collapsed",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Save Feed", key="save_catalog_url", use_container_width=True):
                    st.session_state["store_catalog_url"] = new_url.strip()
                    st.rerun()
            with c2:
                if st.button("Reset", key="reset_catalog_url", use_container_width=True):
                    st.session_state["store_catalog_url"] = catalog_url_default
                    st.rerun()

    catalog_url = st.session_state["store_catalog_url"]
    store_plugins, notice = fetch_catalog(catalog_url)

    if notice:
        st.info(f"ℹ️ {notice}")

    if not store_plugins:
        st.warning("No plugins available in the current catalog feed.")
        return

    # Filter bar
    col_search, col_filter = st.columns([7, 3])
    with col_search:
        search_query = st.text_input(
            "Search store",
            placeholder="🔍 Search catalog by name, description, author, or tags...",
            key="store_search_query",
            label_visibility="collapsed",
        )
    with col_filter:
        status_filter = (
            st.segmented_control(
                "Store Filter",
                options=["All", "Available", "Updates", "Installed"],
                default="All",
                key="store_status_filter",
                label_visibility="collapsed",
            )
            or "All"
        )

    # Filter items
    filtered: list[tuple[StorePlugin, PluginStatus, str | None]] = []
    q = search_query.lower().strip()
    for sp in store_plugins:
        status, installed_ver = get_plugin_status(sp, registry)

        # Status filtering
        if status_filter == "Available" and status != PluginStatus.NOT_INSTALLED:
            continue
        if status_filter == "Updates" and status != PluginStatus.UPDATE_AVAILABLE:
            continue
        if status_filter == "Installed" and status == PluginStatus.NOT_INSTALLED:
            continue

        # Search filtering
        if q:
            searchable = f"{sp.name} {sp.description} {sp.author} {sp.id} {' '.join(sp.tags)}".lower()
            if not all(term in searchable for term in q.split()):
                continue

        filtered.append((sp, status, installed_ver))

    if not filtered:
        st.info("No store plugins match your search criteria.")
        return

    # Render card grid
    cols = st.columns(3, gap="medium")
    for i, (sp, status, installed_ver) in enumerate(filtered):
        with cols[i % 3]:
            # Badges
            if status == PluginStatus.INSTALLED:
                badge = f'<span style="background:#1a7f37;color:white;padding:2px 8px;border-radius:12px;font-size:11px">✅ Installed v{installed_ver}</span>'
            elif status == PluginStatus.UPDATE_AVAILABLE:
                badge = f'<span style="background:#d35400;color:white;padding:2px 8px;border-radius:12px;font-size:11px">🔄 Update Available (v{installed_ver} → v{sp.version})</span>'
            else:
                badge = f'<span style="background:#0969da;color:white;padding:2px 8px;border-radius:12px;font-size:11px">📥 Available v{sp.version}</span>'

            tag_html = " ".join(
                [
                    f'<span style="background:#eef2f6;color:#334155;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:500;">{html.escape(t)}</span>'
                    for t in sp.tags[:3]
                ]
            )

            sp_name = html.escape(sp.name)
            sp_author = html.escape(sp.author)
            sp_desc = html.escape(sp.description)

            st.markdown(
                f"""
                <div style="
                    border:1px solid #dde3ea;border-radius:12px;
                    padding:20px;height:240px;
                    background:white;box-shadow:0 1px 4px rgba(0,0,0,0.07);
                    display:flex;flex-direction:column;">
                    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                        <span style="font-size:36px">{sp.icon}</span>
                        <div>
                            <div style="font-weight:700;font-size:16px;color:#1a1a1a">{sp_name}</div>
                            <div style="font-size:11px;color:#666">by {sp_author}</div>
                        </div>
                    </div>
                    <div style="margin-bottom:6px">{tag_html}</div>
                    <div style="font-size:13px;color:#444;flex:1;line-height:1.4;overflow:hidden">{sp_desc}</div>
                    <div style="margin-top:auto;padding-top:10px">{badge}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            if status == PluginStatus.NOT_INSTALLED:
                c_inst, c_guide = st.columns([6.5, 3.5])
                with c_inst:
                    if st.button(
                        "📥 Install",
                        key=f"store_install_{sp.id}",
                        use_container_width=True,
                        type="primary",
                    ):
                        with st.spinner(f"Downloading and installing {sp.name}..."):
                            ok, msg = install_or_update_plugin(sp, registry)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                with c_guide:
                    if st.button(
                        "📖 Guide",
                        key=f"store_guide_{sp.id}",
                        use_container_width=True,
                    ):
                        readme_md = sp.readme or _get_local_readme(sp.id)
                        if hasattr(st, "dialog"):
                            _show_guide_modal(sp.name, sp.icon, sp.version, sp.author, sp.release_date, readme_md, sp.repo_url, sp.issues_url)
                        else:
                            st.session_state["viewing_guide_plugin"] = {
                                "title": sp.name, "icon": sp.icon, "ver": sp.version, "author": sp.author,
                                "release_date": sp.release_date, "readme": readme_md, "repo_url": sp.repo_url, "issues_url": sp.issues_url
                            }
                            st.rerun()
            elif status == PluginStatus.UPDATE_AVAILABLE:
                c_upd, c_guide = st.columns([6.5, 3.5])
                with c_upd:
                    if st.button(
                        f"🔄 Update",
                        key=f"store_update_{sp.id}",
                        use_container_width=True,
                        type="primary",
                    ):
                        with st.spinner(f"Updating {sp.name} to v{sp.version}..."):
                            ok, msg = install_or_update_plugin(sp, registry)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                with c_guide:
                    if st.button(
                        "📖 Guide",
                        key=f"store_guide_{sp.id}",
                        use_container_width=True,
                    ):
                        readme_md = sp.readme or _get_local_readme(sp.id)
                        if hasattr(st, "dialog"):
                            _show_guide_modal(sp.name, sp.icon, sp.version, sp.author, sp.release_date, readme_md, sp.repo_url, sp.issues_url)
                        else:
                            st.session_state["viewing_guide_plugin"] = {
                                "title": sp.name, "icon": sp.icon, "ver": sp.version, "author": sp.author,
                                "release_date": sp.release_date, "readme": readme_md, "repo_url": sp.repo_url, "issues_url": sp.issues_url
                            }
                            st.rerun()
            else:
                c_use, c_guide, c_del = st.columns([4.5, 3.5, 2.0])
                with c_use:
                    if st.button(
                        "▶ Use",
                        key=f"store_use_{sp.id}",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state["selected_plugin_id"] = sp.id
                        st.rerun()
                with c_guide:
                    if st.button(
                        "📖 Guide",
                        key=f"store_guide_{sp.id}",
                        use_container_width=True,
                    ):
                        readme_md = sp.readme or _get_local_readme(sp.id)
                        if hasattr(st, "dialog"):
                            _show_guide_modal(sp.name, sp.icon, sp.version, sp.author, sp.release_date, readme_md, sp.repo_url, sp.issues_url)
                        else:
                            st.session_state["viewing_guide_plugin"] = {
                                "title": sp.name, "icon": sp.icon, "ver": sp.version, "author": sp.author,
                                "release_date": sp.release_date, "readme": readme_md, "repo_url": sp.repo_url, "issues_url": sp.issues_url
                            }
                            st.rerun()
                with c_del:
                    if st.button(
                        "🗑️",
                        key=f"store_del_{sp.id}",
                        use_container_width=True,
                        help=f"Uninstall {sp.name}",
                    ):
                        with st.spinner(f"Uninstalling {sp.name}..."):
                            ok, msg = uninstall_plugin(sp.id, registry)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
