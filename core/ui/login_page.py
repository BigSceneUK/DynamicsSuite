import msal
import streamlit as st

import config
from core.auth import GraphAuth
from core.context import AppContext


def render() -> None:
    """
    Render the login screen.

    On successful authentication this function stores an AppContext in
    st.session_state['app_context'] and calls st.rerun() so the main shell
    can switch to the authenticated layout.
    """
    # --- Sidebar: mirrors the post-login layout so the transition feels seamless ---
    with st.sidebar:
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
        st.markdown(
            "<div style='font-size:13px;color:#aaa;padding:4px 0'>Sign in to access your plugins.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        # Placeholder greyed-out nav items to show shape of post-login UI
        for label in ["🧩  Plugin Manager"]:
            st.button(label, use_container_width=True, disabled=True, key=f"login_placeholder_{label}")

    # --- Main area ---
    st.markdown(
        """
        <div style="max-width:480px;margin:0 auto;padding-top:32px">
        """,
        unsafe_allow_html=True,
    )

    st.title("🔷 Dynamics Suite")
    st.markdown(
        "A unified portal for Microsoft Dynamics 365 and Power Platform tools.  \n"
        "Sign in with your Microsoft account to continue."
    )
    st.divider()

    st.subheader("🔑 Sign In")

    org_url = st.text_input(
        "Dataverse / CRM URL",
        value=config.DEFAULT_ORG_URL,
        help="e.g. https://yourorg.crm11.dynamics.com",
    )

    with st.expander("⚙️ Advanced Settings"):
        client_id = st.text_input(
            "Application (Client) ID",
            value="",
            placeholder=config.DEFAULT_CLIENT_ID,
            help="Azure AD App Registration Client ID (falls back to default if left blank)",
        )
        tenant_id = st.text_input(
            "Directory (Tenant) ID",
            value="",
            placeholder=config.DEFAULT_TENANT_ID,
            help="Your Azure AD Tenant ID or 'common' (falls back to default if left blank)",
        )
        env_id = st.text_input(
            "Power Platform Environment ID",
            value=config.DEFAULT_ENV_ID,
            help="GUID from Power Platform Admin Centre",
        )

    st.divider()

    if st.button("🔐 Sign in with Microsoft", type="primary", use_container_width=True):
        # Resolve values: use input value if typed, fallback to defaults if blank
        active_client_id = client_id.strip() or config.DEFAULT_CLIENT_ID
        active_tenant_id = tenant_id.strip() or config.DEFAULT_TENANT_ID

        if not active_client_id or not active_tenant_id:
            st.error("Client ID and Tenant ID are required.")
            return

        # Sanitize organization URL: extract scheme and host only.
        # This prevents invalid_resource errors if a user pastes a full page URL.
        from urllib.parse import urlparse
        if org_url:
            parsed = urlparse(org_url)
            if not parsed.scheme or not parsed.netloc:
                if "//" not in org_url:
                    parsed = urlparse(f"https://{org_url}")
            
            if parsed.scheme and parsed.netloc:
                org_url = f"{parsed.scheme}://{parsed.netloc}"
            else:
                st.error("Invalid Dataverse URL format.")
                return

        # Initialise MSAL cache for this session
        if "msal_cache" not in st.session_state:
            st.session_state["msal_cache"] = msal.SerializableTokenCache()

        auth = GraphAuth(
            client_id=active_client_id,
            tenant_id=active_tenant_id,
            token_cache=st.session_state["msal_cache"],
        )

        # Step 1 — Sign in with Microsoft (Graph only).
        # IMPORTANT: do NOT include Dataverse scopes here.
        # Mixing `https://graph.microsoft.com/.default` with a resource-specific
        # `user_impersonation` scope triggers AADSTS70011.  Dataverse consent
        # is requested separately in Step 2 below.
        scopes = config.REQUIRED_SCOPES.copy()

        try:
            with st.spinner("Opening sign-in window… (Step 1 of 2: Microsoft account)"):
                result = auth.login(scopes=scopes, force_select_account=True)

            # Step 2 — Acquire Dataverse token separately (incremental consent).
            if org_url:
                with st.spinner("Connecting to Power Platform… (Step 2 of 2)"):
                    try:
                        auth.acquire_token_for_resource(org_url)
                    except Exception as dv_exc:
                        st.warning(
                            f"Power Platform token could not be acquired at sign-in: {dv_exc}  \n"
                            "You can still proceed — the token will be requested when a plugin needs it."
                        )

            account = auth.get_current_account()
            display_name = account.get("name", "User") if account else "User"
            email = account.get("username", "") if account else ""

            ctx = AppContext(
                user_display_name=display_name,
                user_email=email,
                client_id=active_client_id,
                tenant_id=active_tenant_id,
                org_url=org_url,
                env_id=env_id,
                msal_cache=st.session_state["msal_cache"],
                auth=auth,
            )
            st.session_state["app_context"] = ctx
            st.rerun()

        except Exception as exc:
            st.error(f"Sign-in failed: {exc}")
            st.info(
                "Make sure pop-ups are allowed in your browser for this page."
            )

    st.markdown("</div>", unsafe_allow_html=True)
