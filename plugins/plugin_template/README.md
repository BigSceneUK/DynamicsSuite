# 🧩 Plugin Development Project Template

> **Reference starter template and developer guide for building custom DynamicsSuite plugins.**

This project template serves as the authoritative blueprint and starter code for developing new tools for **DynamicsSuite**. Follow this guide to build, test, package, and distribute custom plugins for Microsoft Dynamics 365, Dataverse, and Power Platform.

---

## 🌟 Overview & Architecture

DynamicsSuite plugins are modular, cross-platform Python extensions built with **Streamlit** and executed inside a zero-admin application shell:
* **Zero Administration**: Runs in user space on Windows, macOS, and Linux. No DLL registration or admin privileges required.
* **Centralized Authentication**: The host shell manages Microsoft Entra ID (MSAL) authentication and token caching. Plugins never handle client secrets, password dialogs, or OAuth redirect endpoints.
* **Hot-Reloading**: Changes made to `plugin.py` reload instantly in the browser without restarting the suite.

---

## 📁 Plugin Directory Layout

Every plugin lives in its own subdirectory inside `plugins/<plugin_id>/`:

```text
plugins/my_custom_tool/
├── __init__.py           # Package marker
├── plugin.py             # Main entry point (subclasses PluginBase)
├── README.md             # End-user documentation and guide
├── requirements.txt      # (Optional) Third-party Python dependencies
├── client.py             # (Optional) Dataverse / Graph API client helpers
└── ui.py                 # (Optional) Sub-components and Streamlit tabs
```

---

## 🚀 Quick Start: Creating a New Plugin

### 1. Scaffold via CLI
Use the DynamicsSuite management CLI to scaffold your plugin structure:

```bash
python3 scripts/manage_plugin.py new my_custom_tool \
  --name "My Custom Tool" \
  --icon "🛠️" \
  --desc "Performs automated health checks on Dataverse solutions." \
  --author "Your Name / Organization" \
  --tags "Dataverse,ALM,HealthCheck"
```

This command automatically:
* Creates `plugins/my_custom_tool/`
* Scaffolds `plugin.py` with standard imports and lifecycle hooks
* Creates `__init__.py` and `README.md`
* Registers the plugin as enabled in `plugins_config.json`

---

## 💻 Anatomy of `plugin.py`

Your plugin must define a class subclassing `core.plugin_base.PluginBase`:

```python
import streamlit as st
from core.context import AppContext
from core.plugin_base import PluginBase


class MyCustomToolPlugin(PluginBase):
    # Metadata
    name = "My Custom Tool"
    icon = "🛠️"
    description = "Performs automated health checks on Dataverse solutions."
    version = "1.0.0"
    release_date = "2026-09-09"
    author = "DynamicsSuite Team"
    released_by = "DynamicsSuite Team"
    tags = ["Dataverse", "ALM"]
    requires_graph = False
    
    # Optional community links
    repo_url = "https://github.com/my-org/dynamics-tool"
    issues_url = "https://github.com/my-org/dynamics-tool/issues"

    def render(self, ctx: AppContext) -> None:
        """Render the plugin UI in Streamlit."""
        st.header(f"{self.icon} {self.name}")
        
        # Verify active connection
        if not ctx.org_url:
            st.warning("Please connect to a Dataverse environment from the sidebar first.")
            return

        # Acquire Dataverse Web API Bearer token silently
        token = ctx.auth.get_token(ctx.org_url)
        
        st.success(f"Connected to: {ctx.org_url}")
        st.write("Ready to build custom features!")
```

---

## 🔐 Working with Authentication (`AppContext`)

The `ctx: AppContext` passed into `render()` provides all environment and identity context:
* `ctx.org_url`: The currently connected Dataverse environment base URL (e.g. `https://myorg.crm.dynamics.com`).
* `ctx.user_email`: Display email of the authenticated Entra ID user.
* `ctx.tenant_id`: Active Azure AD tenant ID.
* `ctx.auth.get_token(resource)`: Acquires a valid bearer token for the specified audience:
  * For Dataverse: `ctx.auth.get_token(ctx.org_url)`
  * For Microsoft Graph: `ctx.auth.get_token("https://graph.microsoft.com")`
  * For Power Automate Management: `ctx.auth.get_token("https://service.flow.microsoft.com/")`

---

## ⚙️ Adding Plugin Configuration (`render_config`)

If your plugin requires user settings (e.g. default batch size, API timeouts, notification emails):
1. Define `default_config = {"batch_size": 50}` in your plugin class.
2. Access the active configuration with `self.get_config()`.
3. Optionally implement `render_config(self)` to provide a custom settings UI inside the Plugin Manager.

---

## 🧪 Validating & Packaging

Before publishing your plugin:

```bash
# 1. Validate AST syntax, required attributes, and documentation
python3 scripts/manage_plugin.py validate my_custom_tool

# 2. Package clean release zips and update catalogs
python3 scripts/manage_plugin.py build
```

The build script will:
* Check for required metadata and semver format
* Ingest your `README.md` into the central catalog feeds
* Package clean `.zip` distribution archives without `.DS_Store` or cache files
* Update the Web Portal Tools Library
