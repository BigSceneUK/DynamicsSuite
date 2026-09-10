"""
plugin_header.py — Standardized Plugin Header for DynamicsSuite.

Renders an integrated top bar above every active plugin:
- Plugin Icon, Name, and Version badge
- '← Plugin Manager' navigation shortcut
- '📖 Guide' button to view full README documentation
- '💡 Suggest Improvement / Feedback' button to submit ideas or bugs to support@bigscene.uk
"""

from __future__ import annotations

import html
from pathlib import Path
import streamlit as st
import bleach

from core.context import AppContext
from core.plugin_base import PluginBase
from core.ui import feedback_dialog

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"

# HTML tags and attributes safe to render in plugin READMEs.
_SAFE_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "ul", "ol", "li",
    "strong", "em", "b", "i", "s", "del",
    "code", "pre", "blockquote",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "details", "summary", "div", "span",
]
_SAFE_ATTRS = {
    "a": ["href", "title", "target"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["align"], "td": ["align"],
    "*": ["class", "style"],
}


def _safe_markdown(text: str) -> str:
    """Sanitize embedded HTML before markdown rendering."""
    if not text:
        return text
    return bleach.clean(text, tags=_SAFE_TAGS, attributes=_SAFE_ATTRS, strip=True)


def _get_plugin_readme(plugin_id: str) -> str:
    readme_path = _PLUGINS_DIR / plugin_id / "README.md"
    if readme_path.exists():
        try:
            return readme_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


if hasattr(st, "dialog"):
    @st.dialog("📖 Plugin Guide & Documentation", width="large")
    def _show_guide_modal(
        title: str,
        icon: str,
        version: str,
        author: str,
        release_date: str,
        readme_md: str,
        repo_url: str = "",
        issues_url: str = "",
    ):
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
        if st.button("Close", key=f"hdr_close_guide_{title}", use_container_width=True):
            st.rerun()


def render(plugin: PluginBase, ctx: AppContext) -> None:
    """Render the standard header banner above the plugin's main UI."""
    plugin_id = getattr(plugin, "_plugin_id", "") or plugin.name.lower().replace(" ", "_")
    safe_name = html.escape(plugin.name)
    safe_ver = html.escape(str(plugin.version))
    safe_icon = plugin.icon or "🔌"
    author = getattr(plugin, "author", "DynamicsSuite Team")
    release_date = getattr(plugin, "release_date", "")
    repo_url = getattr(plugin, "repo_url", "")
    issues_url = getattr(plugin, "issues_url", "")
    support_email = getattr(plugin, "support_email", "support@bigscene.uk") or "support@bigscene.uk"

    # Header row
    col_nav, col_meta, col_actions = st.columns([2.0, 5.0, 4.5])

    with col_nav:
        if st.button("← Plugins", key=f"back_pm_{plugin_id}", help="Return to Plugin Manager"):
            st.session_state["selected_plugin_id"] = "__plugin_manager__"
            st.rerun()

    with col_meta:
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:8px;padding-top:4px">
                <span style="font-size:22px">{safe_icon}</span>
                <span style="font-weight:700;font-size:16px;color:#1e293b">{safe_name}</span>
                <span style="background:#e2e8f0;color:#334155;font-size:11px;font-weight:600;padding:2px 7px;border-radius:10px">v{safe_ver}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_actions:
        btn_c1, btn_c2 = st.columns([1, 1.4])
        with btn_c1:
            if st.button("📖 Guide", key=f"hdr_guide_{plugin_id}", use_container_width=True, help="View How-To Guide"):
                readme_text = _get_plugin_readme(plugin_id)
                if hasattr(st, "dialog"):
                    _show_guide_modal(
                        title=plugin.name,
                        icon=safe_icon,
                        version=safe_ver,
                        author=author,
                        release_date=release_date,
                        readme_md=readme_text,
                        repo_url=repo_url,
                        issues_url=issues_url,
                    )
                else:
                    st.session_state["viewing_guide_plugin"] = {
                        "title": plugin.name,
                        "icon": safe_icon,
                        "ver": safe_ver,
                        "author": author,
                        "release_date": release_date,
                        "readme": readme_text,
                        "repo_url": repo_url,
                        "issues_url": issues_url,
                    }
                    st.rerun()

        with btn_c2:
            if st.button(
                "💡 Feedback",
                key=f"hdr_feedback_{plugin_id}",
                use_container_width=True,
                help="Suggest an improvement, report a bug, or contact support",
            ):
                feedback_dialog.open_feedback_dialog(
                    target_name=plugin.name,
                    version=safe_ver,
                    user_email=ctx.user_email or "",
                    support_email=support_email,
                    repo_url=repo_url,
                    issues_url=issues_url,
                    default_type="💡 Suggest Improvement / Feature Request",
                    ctx=ctx,
                )

    st.divider()
