import html
import streamlit as st

import config
from core.context import AppContext
from core.plugin_registry import PluginRegistry
from core.ui import feedback_dialog

_PLUGIN_MANAGER_ID = "__plugin_manager__"


def _get_env_name(ctx: AppContext) -> str:
    """Retrieve environment name from Dataverse API or fallback to URL domain."""
    if "env_name" in st.session_state and st.session_state["env_name"]:
        return st.session_state["env_name"]

    if not ctx.org_url:
        return ""

    try:
        import requests
        token = ctx.auth.get_token(ctx.org_url)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        # Try RetrieveCurrentOrganization first
        url = f"{ctx.org_url.rstrip('/')}/api/data/v9.2/RetrieveCurrentOrganization(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'Internet')"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            detail = data.get("Detail")
            if detail and isinstance(detail, dict):
                friendly_name = detail.get("FriendlyName")
                if friendly_name:
                    st.session_state["env_name"] = friendly_name
                    return friendly_name

        # Fallback to organizations endpoint
        url = f"{ctx.org_url.rstrip('/')}/api/data/v9.2/organizations"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            orgs = data.get("value", [])
            if orgs and isinstance(orgs, list) and len(orgs) > 0:
                friendly_name = orgs[0].get("name")
                if friendly_name:
                    st.session_state["env_name"] = friendly_name
                    return friendly_name
    except Exception:
        pass

    # Fallback to org_url domain extraction
    from urllib.parse import urlparse
    try:
        parsed = urlparse(ctx.org_url)
        host = parsed.netloc
        if host:
            parts = host.split(".")
            if parts:
                return parts[0]
    except Exception:
        pass

    return ctx.org_url


def render(registry: PluginRegistry, ctx: AppContext) -> str:
    """
    Render the navigation sidebar.

    Returns
    -------
    str
         The selected plugin_id, or "__plugin_manager__" for the Plugin Manager view.
    """
    with st.sidebar:
        # --- App branding ---
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
                <span style="font-size:22px">🔷</span>
                <span style="font-weight:700;font-size:17px;color:#0061a4">Dynamics Suite</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()

        # --- Environment Info ---
        env_name = _get_env_name(ctx)
        if env_name:
            safe_env_name = html.escape(env_name)
            safe_org_url = html.escape(ctx.org_url)
            st.markdown(
                f"""
                <div style="
                    margin-bottom:14px;padding:8px 12px;
                    background:rgba(0, 97, 164, 0.08);
                    border:1px solid rgba(0, 97, 164, 0.15);
                    border-radius:6px">
                    <div style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;color:#888;font-weight:600;margin-bottom:2px">Environment</div>
                    <div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{safe_org_url}">{safe_env_name}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # --- User info ---
        raw_initial = (ctx.user_display_name[0].upper()
                       if ctx.user_display_name else "?")
        safe_initial = html.escape(raw_initial)
        safe_user_name = html.escape(ctx.user_display_name or "")
        safe_user_email = html.escape(ctx.user_email or "")
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <div style="
                    background:#0061a4;color:white;border-radius:50%;
                    width:38px;height:38px;display:flex;align-items:center;
                    justify-content:center;font-size:16px;font-weight:bold;
                    flex-shrink:0">
                    {safe_initial}
                </div>
                <div>
                    <div style="font-weight:600;font-size:13px">{safe_user_name}</div>
                    <div style="font-size:11px;color:#888">{safe_user_email}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # --- Plugin Manager home button ---
        current = st.session_state.get("selected_plugin_id", _PLUGIN_MANAGER_ID)
        is_pm = current == _PLUGIN_MANAGER_ID
        pm_style = (
            "background:#e8f0fe;border:1px solid #0061a4;color:#0061a4;"
            if is_pm else ""
        )
        if st.button(
            "🧩  Plugin Manager",
            use_container_width=True,
            key="nav_plugin_manager",
            type="primary" if is_pm else "secondary",
        ):
            st.session_state["selected_plugin_id"] = _PLUGIN_MANAGER_ID
            st.session_state.pop("configuring_plugin_id", None)
            st.rerun()

        # --- Initialise default selection ---
        if "selected_plugin_id" not in st.session_state:
            st.session_state["selected_plugin_id"] = _PLUGIN_MANAGER_ID

        # --- Footer ---
        st.divider()
        if st.button("💬  Feedback & Support", use_container_width=True, help="Report a bug or suggest an improvement for DynamicsSuite"):
            feedback_dialog.open_feedback_dialog(
                target_name="DynamicsSuite Core System",
                version="1.0.0",
                user_email=ctx.user_email or "",
                support_email=getattr(config, "DEFAULT_SUPPORT_EMAIL", "support@bigscene.uk"),
                repo_url=getattr(config, "DEFAULT_GITHUB_REPO", "https://github.com/moluk/DynamicsSuite"),
                issues_url=getattr(config, "DEFAULT_ISSUES_URL", "https://github.com/moluk/DynamicsSuite/issues"),
                default_type="💡 Suggest Improvement / Feature Request",
                ctx=ctx,
            )

        if st.button("🚪  Sign Out", use_container_width=True):
            ctx.auth.logout()
            del st.session_state["app_context"]
            if "msal_cache" in st.session_state:
                del st.session_state["msal_cache"]
            st.rerun()

    return st.session_state.get("selected_plugin_id", _PLUGIN_MANAGER_ID)
