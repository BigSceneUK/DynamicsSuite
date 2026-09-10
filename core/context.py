from dataclasses import dataclass, field
import msal


@dataclass
class AppContext:
    """
    Shared authentication context passed to every plugin's render() call.

    Plugins must NOT manage their own MSAL login.  They call:
        token = ctx.auth.get_token("https://graph.microsoft.com")
    or:
        result = ctx.auth.acquire_token_silent(scopes=[...])
    to obtain bearer tokens for any resource they need.
    """
    # User identity (populated after login)
    user_display_name: str = ""
    user_email: str = ""

    # Azure AD app registration
    client_id: str = ""
    tenant_id: str = "common"

    # Power Platform environment (can be overridden per-plugin in session_state)
    org_url: str = ""
    env_id: str = ""

    # MSAL token cache — shared so silent acquisition works across plugins
    msal_cache: msal.SerializableTokenCache = field(
        default_factory=msal.SerializableTokenCache
    )

    # Reference to the GraphAuth instance — DO NOT replace; use its methods
    auth: object = field(default=None, repr=False)
