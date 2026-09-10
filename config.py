# Global configuration defaults
# These values are presented as pre-filled defaults on the login page.
# Users can change them at login time.

# Azure AD App Registration
# Replace with your own App Registration Client ID.
# Register at: https://portal.azure.com → Azure Active Directory → App Registrations
# Required settings: Platform = "Mobile and desktop applications", Redirect URI = http://localhost
DEFAULT_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
DEFAULT_TENANT_ID = "common"

# Power Platform / Dataverse environment
DEFAULT_ORG_URL = ""
DEFAULT_ENV_ID = ""

# Scopes requested at login (Graph .default covers all delegated Graph permissions)
REQUIRED_SCOPES = [
    "https://graph.microsoft.com/.default",
]

# Tools Library / Plugin Store catalog URL
# Hosted on GitHub — served directly from the main branch of the public repository.
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/BigSceneUK/DynamicsSuite/main/plugins_catalog.json"

# Feedback and support defaults
DEFAULT_SUPPORT_EMAIL = "support@bigscene.uk"
DEFAULT_GITHUB_REPO = "https://github.com/BigSceneUK/DynamicsSuite"
DEFAULT_ISSUES_URL = "https://github.com/BigSceneUK/DynamicsSuite/issues"

