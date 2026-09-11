# DynamicsSuite

A unified, zero-admin desktop and local portal for **Microsoft Dynamics 365**, **Dataverse**, and **Power Platform** tools. DynamicsSuite runs all developer, administration, and auditing utilities as modular, independently loadable plugins under a single, centralized Microsoft Entra ID (MSAL) authentication layer — one URL, one sign-in.

---

## 🤖 About This Project & AI Development

> **Written with AI — Driven by Ideas and Rigorous Testing**
>
> DynamicsSuite is developed collaboratively with generative AI. The creator and lead designer (**Mo**) defines the product vision, tool concepts, user workflows, and comprehensive real-world testing against live Dataverse instances, while AI accelerates the code synthesis.
>
> Because of this human-directed, AI-assisted approach, **we warmly welcome all developers, Dynamics 365 consultants, and Power Platform architects to join the project!** Whether you want to fix bugs, optimize performance, review code quality, or build brand new plugins, your contributions, pull requests, and feedback are actively encouraged.

---

## 🔒 Security Policy & Reporting

Security is an absolute priority for DynamicsSuite. We have strived to build a robust, secure architecture:

- **Zero Secret Exposure**: Public client OAuth 2.0 PKCE flow. No client secrets, passwords, or tenant credentials are ever stored or transmitted to external servers.
- **Localhost Execution**: Runs exclusively in user space on `localhost`. Tokens reside only in the local runtime session.
- **Responsible Disclosure**: If you discover any security issue, vulnerability, or unexpected credential exposure, **please report it immediately to the DynamicsSuite security team at `support@bigscene.uk`** or through a private GitHub Security Advisory. We commit to reviewing and addressing valid reports with urgency.

### 🛡️ Recommended Best Practices for Production Environments

1. **Use Your Own Company App Registration**:
   For production use, do not rely on generic or default client IDs. Register your own Enterprise Application / App Registration in Microsoft Entra ID (Azure AD) with `Platform = Mobile and desktop applications` and `Redirect URI = http://localhost`.
2. **Verify Plugin Source Integrity**:
   Always verify that plugins installed in your environment are authentic and have not been modified or tampered with by unauthorized third parties. Check file hashes against official releases or catalog feeds.
3. **Use AI to Audit and Self-Package**:
   We strongly encourage users and enterprise teams to use AI tools (or manual static code analysis) to review plugin source code security and package the zip releases independently before deploying into production systems.

---

## 🧩 Included Tools & Plugins

DynamicsSuite comes bundled with an enterprise toolset for Dynamics 365 and Power Platform:

| Plugin | Name | Description |
|---|---|---|
| 🔍 | **Table Inspector** | Inspect table schema, metadata, form placement analysis (distinguish fields placed on forms vs. orphaned fields), data population profiling, and Excel data dictionary generator. |
| 🛡️ | **Power Page API Wildcard Auditor** | Audit Power Pages Web API site settings, scan portal JavaScript/HTML for table & column usage, detect deprecated `*` wildcards, and 1-click update column whitelists. |
| 📜 | **Audit History Explorer** | Fast search and timeline analysis of Dataverse audit logs, field-level change diffs, and compliance reporting. |
| ⚡ | **Cloud Flow Analytics** | Health check, telemetry analysis, and run history inspector for Power Automate cloud flows. |
| 🚚 | **Record Transporter** | Export, transport, and synchronize configuration and master data across development, test, and production Dataverse instances. |
| 🌐 | **Power Pages Portal Inspector** | Deep inspection of portal entities, webpages, web files, templates, and content snippets. |
| 🧩 | **Plugin Development Template** | Authoritative reference template and guide for building custom plugins for DynamicsSuite. |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9 or higher
- A modern web browser
- Microsoft 365 / Entra ID work or school account with access to a Dataverse / Dynamics 365 environment

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/BigSceneUK/DynamcisSuite.git
cd DynamcisSuite

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Launching

```bash
# Using the startup script (macOS / Linux)
./start.sh

# Or on Windows
start.bat

# Or directly via Streamlit
streamlit run app.py
```

DynamicsSuite will launch in your browser at `http://localhost:8501`.

---

## 🛠️ Developing Custom Plugins

Building a custom plugin takes just a few minutes:

1. Create a folder inside `plugins/<your_plugin_id>/`.
2. Add a `plugin.py` file inheriting from `core.plugin_base.PluginBase`:

```python
import streamlit as st
from core.context import AppContext
from core.plugin_base import PluginBase


class MyToolPlugin(PluginBase):
    name = "My Custom Tool"
    icon = "🛠️"
    description = "Inspects custom Dataverse logic."
    version = "1.0.0"
    author = "Your Name"

    def render(self, ctx: AppContext) -> None:
        st.header(f"{self.icon} {self.name}")
        if not ctx.org_url:
            st.warning("Please connect to Dataverse from the sidebar.")
            return
        token = ctx.auth.get_token(ctx.org_url)
        st.success(f"Connected to: {ctx.org_url}")
        # Build your Streamlit UI here
```

3. Register your plugin in `plugins_config.json` with `"enabled": true`.

Refer to the bundled `plugins/plugin_template/` for full details on UI layout, token acquisition, configuration screens, and client patterns.

---

## 🤝 Contributing

We welcome community contributions!
1. **Fork** the repository: `https://github.com/BigSceneUK/DynamcisSuite`
2. **Create** your feature or bug fix branch (`git checkout -b feature/amazing-plugin`)
3. **Commit** your changes with clear messages (`git commit -m 'feat: add security scanner plugin'`)
4. **Push** to the branch (`git push origin feature/amazing-plugin`)
5. **Open a Pull Request** on GitHub for review and testing.

---

## 📄 License

Distributed under the **BSD 3-Clause License**. See [LICENSE](LICENSE) for details.
