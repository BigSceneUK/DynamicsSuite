import html
import textwrap
import streamlit as st

import config
from core.context import AppContext
from core.plugin_registry import PluginRegistry
from core.ui import feedback_dialog

_PLUGIN_MANAGER_ID = "__plugin_manager__"


def _is_unfriendly_name(name: str) -> bool:
    """Check if name is an internal unique database id like unq55eb000... or raw guid."""
    if not name:
        return True
    low = name.strip().lower()
    if low.startswith("unq") and len(low) > 10:
        return True
    if len(low) == 36 and "-" in low and low.count("-") == 4:
        return True
    return False


def _get_env_name(ctx: AppContext) -> str:
    """Retrieve environment name from Dataverse API or fallback to URL domain."""
    active_env_id = ctx.env_id

    # Try to resolve friendly name from any active environment lists in session state
    if active_env_id:
        found_name = None
        for key in ["ps_available_envs", "cf_pa_environments", "fti_pa_envs"]:
            envs_list = st.session_state.get(key)
            if envs_list and isinstance(envs_list, list):
                for e in envs_list:
                    if e.get("name") == active_env_id:
                        found_name = e.get("properties", {}).get("displayName")
                        break
            if found_name:
                break
        if found_name and not _is_unfriendly_name(found_name):
            st.session_state["env_name"] = found_name
            return found_name

    cached = st.session_state.get("env_name")
    if cached and not _is_unfriendly_name(cached):
        return cached

    if getattr(ctx, "environment_name", None) and not _is_unfriendly_name(ctx.environment_name):
        st.session_state["env_name"] = ctx.environment_name
        return ctx.environment_name

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
                if friendly_name and not _is_unfriendly_name(friendly_name):
                    st.session_state["env_name"] = friendly_name
                    return friendly_name
    except Exception:
        pass

    return ""


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
        env_url = (ctx.org_url or "").strip().rstrip("/")
        friendly_name = _get_env_name(ctx)
        safe_env_url = html.escape(env_url)
        safe_friendly_name = html.escape(friendly_name) if friendly_name else ""
        safe_env_id = html.escape(ctx.env_id or "N/A")

        if safe_env_url or safe_friendly_name:
            if safe_friendly_name and safe_env_url and safe_friendly_name.lower() != safe_env_url.lower():
                inner_env_html = (
                    f'<div style="font-size:13px;font-weight:700;color:#ffffff;line-height:1.3;margin-bottom:2px">{safe_friendly_name}</div>'
                    f'<div style="font-size:12px;color:#93c5fd;word-break:break-all;line-height:1.35">{safe_env_url}</div>'
                )
            else:
                display_env = safe_env_url or safe_friendly_name
                inner_env_html = (
                    f'<div style="font-size:13px;font-weight:600;color:#ffffff;word-break:break-all;line-height:1.35">{display_env}</div>'
                )

            env_card_html = textwrap.dedent(f"""\
<div style="margin-bottom:14px;padding:10px 12px;background:rgba(0, 97, 164, 0.12);border:1px solid rgba(0, 97, 164, 0.28);border-radius:8px;" title="URL: {safe_env_url}&#10;ID: {safe_env_id}">
<div style="font-size:9.5px;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;font-weight:700;margin-bottom:4px;">Environment</div>
{inner_env_html}
</div>
""").strip()

            st.markdown(env_card_html, unsafe_allow_html=True)

        # --- User & Multi-Account Info ---
        accounts = ctx.auth.get_accounts() if hasattr(ctx.auth, "get_accounts") else []
        current_acc = ctx.auth.get_current_account() if hasattr(ctx.auth, "get_current_account") else None
        curr_username = (current_acc.get("username") if current_acc else ctx.user_email) or ""
        curr_name = (current_acc.get("name") if current_acc else ctx.user_display_name) or "User"

        initial = curr_name[0].upper() if curr_name else "?"
        curr_username_low = curr_username.lower()
        if "svc" in curr_username_low or "service" in curr_username_low:
            acc_role = "service_account"
            badge_html = '<span style="background:#e8fdf0;color:#0b7c3e;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:6px;font-weight:600">🤖 Service Account</span>'
        elif (
            curr_username_low.startswith("c-")
            or curr_username_low.startswith("c_")
            or "admin" in curr_username_low
            or (curr_username_low.startswith("c") and "onmicrosoft.com" in curr_username_low)
        ):
            acc_role = "c_account"
            badge_html = '<span style="background:#ffefe5;color:#b33a00;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:6px;font-weight:600">🔑 C-Account</span>'
        else:
            acc_role = "standard_user"
            badge_html = '<span style="background:#e8f4fd;color:#0061a4;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:6px;font-weight:600">👤 User</span>'

        safe_initial = html.escape(initial)
        safe_curr_name = html.escape(curr_name)
        safe_curr_user = html.escape(curr_username)

        user_card_html = textwrap.dedent(f"""\
<div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:12px;">
<div style="background:#0061a4;color:white;border-radius:50%;width:42px;height:42px;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;flex-shrink:0;margin-top:2px;">
{safe_initial}
</div>
<div style="overflow:hidden;flex-grow:1;">
<div style="font-weight:600;font-size:13px;display:flex;align-items:center;margin-bottom:3px;color:#cbd5e1;">
{safe_curr_name} {badge_html}
</div>
<div style="font-size:16px;font-weight:600;color:#ffffff;word-break:break-all;line-height:1.35;" title="{safe_curr_user}">
{safe_curr_user}
</div>
</div>
</div>
""").strip()

        st.markdown(user_card_html, unsafe_allow_html=True)

        # Multi-account Switcher if more than 1 account is signed in
        if len(accounts) > 1:
            def _get_acc_badge_label(u_str: str) -> str:
                u_l = u_str.lower()
                if "svc" in u_l or "service" in u_l:
                    return "🤖 [Service Account] "
                elif u_l.startswith("c-") or u_l.startswith("c_") or "admin" in u_l or (u_l.startswith("c") and "onmicrosoft.com" in u_l):
                    return "🔑 [C-Account] "
                return "👤 [Standard] "

            acc_options = {
                acc.get("username", ""): f"{_get_acc_badge_label(acc.get('username', ''))}{acc.get('name', 'User')} ({acc.get('username', '')})"
                for acc in accounts if acc.get("username")
            }
            selected_acc_user = st.selectbox(
                "Active Identity:",
                options=list(acc_options.keys()),
                index=list(acc_options.keys()).index(curr_username) if curr_username in acc_options else 0,
                format_func=lambda u: acc_options.get(u, u),
                key="sidebar_active_account_select",
            )
            if selected_acc_user != curr_username:
                ctx.auth.set_active_account(selected_acc_user)
                new_acc = ctx.auth.get_current_account()
                if new_acc:
                    ctx.user_display_name = new_acc.get("name", "User")
                    ctx.user_email = new_acc.get("username", "")
                st.rerun()

        # Add Account button
        with st.expander("➕ Connect Another Account (C-Account / Service Account)"):
            st.caption("Sign in with your C-Account (Azure admin), Service Account (e.g. `c-svc-...`), or another Microsoft account.")
            if st.button("🔑 Sign in with another account", use_container_width=True, key="btn_add_another_account"):
                with st.spinner("Opening sign-in window..."):
                    try:
                        ctx.auth.login_additional_account(scopes=config.REQUIRED_SCOPES)
                        new_acc = ctx.auth.get_current_account()
                        if new_acc:
                            ctx.user_display_name = new_acc.get("name", "User")
                            ctx.user_email = new_acc.get("username", "")
                        st.success("Account connected successfully!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Sign-in failed: {exc}")

        # Service Principals Manager
        with st.expander("🤖 Service Accounts (Deployer / Target)"):
            st.caption("Configure Service Principals for unattended deployments and target environments.")
            try:
                from core.service_accounts import load_service_accounts, save_service_account, delete_service_account
                svc_accs = load_service_accounts()
                if svc_accs:
                    for s_name, s_data in list(svc_accs.items()):
                        col_sa1, col_sa2 = st.columns([4, 1])
                        with col_sa1:
                            st.markdown(f"**`{s_name}`**  \n<span style='font-size:11px;color:#888'>{s_data.get('url')}</span>", unsafe_allow_html=True)
                        with col_sa2:
                            if st.button("🗑️", key=f"del_sa_{s_name}", help=f"Delete {s_name}"):
                                delete_service_account(s_name)
                                st.rerun()
                    st.divider()

                with st.form("form_add_service_account", clear_on_submit=True):
                    st.caption("Add New Service Principal:")
                    sa_name = st.text_input("Profile Name", placeholder="e.g. uat_deployer")
                    sa_url = st.text_input("Target Environment URL", placeholder="https://org.crm.dynamics.com")
                    sa_tenant = st.text_input("Tenant ID", value=ctx.tenant_id or "common")
                    sa_client = st.text_input("App Registration Client ID")
                    sa_secret = st.text_input("Client Secret", type="password")
                    if st.form_submit_button("💾 Save Service Account", use_container_width=True):
                        if sa_name and sa_url and sa_client and sa_secret:
                            save_service_account(sa_name, sa_url, sa_tenant, sa_client, sa_secret)
                            st.success(f"Saved Service Account '{sa_name}'!")
                            st.rerun()
                        else:
                            st.error("Please fill in all required fields.")
            except Exception as e:
                st.caption(f"Service accounts manager unavailable: {e}")

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        # --- Plugin Manager home button ---
        current = st.session_state.get("selected_plugin_id", _PLUGIN_MANAGER_ID)
        is_pm = current == _PLUGIN_MANAGER_ID
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

        if len(accounts) > 1:
            col_so1, col_so2 = st.columns(2)
            with col_so1:
                if st.button("🚪 Sign Out This", use_container_width=True, help="Sign out only the current active identity"):
                    ctx.auth.remove_account(curr_username)
                    curr = ctx.auth.get_current_account()
                    if curr:
                        ctx.user_display_name = curr.get("name", "User")
                        ctx.user_email = curr.get("username", "")
                    else:
                        del st.session_state["app_context"]
                    st.rerun()
            with col_so2:
                if st.button("🚪 Sign Out All", use_container_width=True, help="Sign out all connected identities"):
                    ctx.auth.logout()
                    del st.session_state["app_context"]
                    if "msal_cache" in st.session_state:
                        del st.session_state["msal_cache"]
                    st.rerun()
        else:
            if st.button("🚪  Sign Out", use_container_width=True):
                ctx.auth.logout()
                del st.session_state["app_context"]
                if "msal_cache" in st.session_state:
                    del st.session_state["msal_cache"]
                st.rerun()

    return st.session_state.get("selected_plugin_id", _PLUGIN_MANAGER_ID)
