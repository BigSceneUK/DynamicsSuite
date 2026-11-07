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
        placeholder="https://<yoursite>.dynamics.com/",
        help="e.g. https://<yoursite>.dynamics.com/",
    )
    st.caption("Example: `https://<yoursite>.dynamics.com/`")

    with st.expander("⚙️ Advanced Settings"):
        client_id = st.text_input(
            "Application (Client) ID",
            value=config.DEFAULT_CLIENT_ID,
            placeholder="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
            help="Azure AD App Registration Client ID",
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

        # Initialise in-memory MSAL cache for this session (zero disk storage)
        if "msal_cache" not in st.session_state:
            st.session_state["msal_cache"] = msal.TokenCache()

        auth = GraphAuth(
            client_id=active_client_id,
            tenant_id=active_tenant_id,
            token_cache=st.session_state["msal_cache"],
            persist_cache=False,
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
            env_name = ""
            if org_url:
                with st.spinner("Connecting to Power Platform… (Step 2 of 2)"):
                    try:
                        auth.acquire_token_for_resource(org_url)
                        try:
                            dv_token = auth.get_token(org_url)
                            headers = {
                                "Authorization": f"Bearer {dv_token}",
                                "Accept": "application/json",
                                "OData-MaxVersion": "4.0",
                                "OData-Version": "4.0",
                            }
                            # Try RetrieveCurrentOrganization for friendly name
                            api_url = f"{org_url.rstrip('/')}/api/data/v9.2/RetrieveCurrentOrganization(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'Internet')"
                            import requests
                            res = requests.get(api_url, headers=headers, timeout=5)
                            if res.status_code == 200:
                                detail = res.json().get("Detail", {})
                                fname = detail.get("FriendlyName")
                                if fname and not fname.lower().startswith("unq"):
                                    env_name = fname
                        except Exception as e:
                            import logging
                            logging.warning(f"Could not fetch environment name from Dataverse: {e}")
                    except Exception as dv_exc:
                        st.warning(
                            f"Power Platform token could not be acquired at sign-in: {dv_exc}  \n"
                            "You can still proceed — the token will be requested when a plugin needs it."
                        )

            # Step 2.5 — Acquire BAP token and fetch friendly name from BAP API
            if env_id:
                try:
                    auth.acquire_token_for_resource("https://api.bap.microsoft.com/")
                    bap_token = auth.get_token("https://api.bap.microsoft.com/")
                    headers = {
                        "Authorization": f"Bearer {bap_token}",
                        "Accept": "application/json",
                    }
                    api_url = f"https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/scopes/admin/environments/{env_id}?api-version=2021-04-01"
                    import requests
                    res = requests.get(api_url, headers=headers, timeout=5)
                    if res.status_code in (403, 404):
                        api_url = f"https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments/{env_id}?api-version=2021-04-01"
                        res = requests.get(api_url, headers=headers, timeout=5)
                    
                    if res.status_code == 200:
                        env_data = res.json()
                        displayName = env_data.get("properties", {}).get("displayName", "")
                        if displayName:
                            env_name = displayName
                except Exception as bap_exc:
                    import logging
                    logging.warning(f"BAP friendly name fetch failed: {bap_exc}")

            # Fallback to subdomain/host of org_url if API name could not be retrieved
            if not env_name and org_url:
                from urllib.parse import urlparse
                try:
                    parsed = urlparse(org_url)
                    if parsed.netloc:
                        parts = parsed.netloc.split('.')
                        if parts:
                            env_name = parts[0]
                except Exception:
                    pass

            if not env_name and env_id:
                env_name = env_id

            account = auth.get_current_account()
            display_name = account.get("name", "User") if account else "User"
            email = account.get("username", "") if account else ""

            # In-memory session: sensitive tokens and session details remain in RAM only

            ctx = AppContext(
                user_display_name=display_name,
                user_email=email,
                client_id=active_client_id,
                tenant_id=active_tenant_id,
                org_url=org_url,
                env_id=env_id,
                environment_name=env_name,
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
