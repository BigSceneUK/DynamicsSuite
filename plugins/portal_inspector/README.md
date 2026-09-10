# 🌐 Portal Inspector

> **Interactive component hierarchy, Page Structure Explorer, and Liquid template viewer for Microsoft Power Pages & Dynamics 365 Portals.**

The **Portal Inspector** empowers Power Pages Architects, Front-End Developers, and Functional Consultants to inspect, understand, and debug complex portal solutions without clicking back and forth across dozens of individual forms and web templates in the Power Apps Maker Portal.

---

## 🌟 Key Features

* **Dual Data Model Support**: Seamlessly supports both the **Standard Data Model** (`adx_*` tables) and the **Enhanced Data Model** (`mspp_*` tables).
* **Page Structure Explorer**: Visualize the full tree hierarchy of any web page:
  * Web Pages & Child Pages
  * Page Templates & Web Templates
  * Basic Forms (`entityform`) & Multistep Forms (`webform`)
  * Lists (`entitylist`) & Content Snippets
* **Embedded Liquid Template Viewer**: Inspect Liquid code directly in the tool with syntax formatting, without opening VS Code or portal management tables.
* **Form & Metadata Deep Dive**: View associated Dataverse table names, form modes (Insert, Edit, Read-Only), tab names, field metadata overrides, and action buttons.
* **Site Settings Explorer**: Quickly review active portal configuration settings, security flags, authentication endpoints, and custom feature toggles.

---

## 📋 Prerequisites & Permissions

1. **Power Pages / Dynamics Portal Installed**:
   * The target environment must have a Power Pages site or Dynamics 365 Portal provisioned.
2. **User Privileges**:
   * Your user account requires **Read** permissions on Power Pages configuration tables:
     * `adx_webpage` / `mspp_webpage`
     * `adx_pagetemplate` / `mspp_pagetemplate`
     * `adx_webtemplate` / `mspp_webtemplate`
     * `adx_entityform` / `mspp_entityform`
     * `adx_webform` / `mspp_webform`
     * `adx_entitylist` / `mspp_entitylist`
     * `adx_contentsnippet` / `mspp_contentsnippet`
     * `adx_sitesetting` / `mspp_sitesetting`
   * *System Customizer*, *System Administrator*, or a custom Power Pages Admin role is recommended.

---

## 🚀 How to Use

### 1. Connect & Select Portal Website
1. Connect to your Dataverse environment in the DynamicsSuite sidebar.
2. If your environment hosts multiple Power Pages websites, select the target site from the dropdown.
3. The inspector automatically detects whether the site uses the Standard (`adx_`) or Enhanced (`mspp_`) schema.

### 2. Browse the Page Structure Explorer
1. Select a **Web Page** from the site directory or search by partial URL path.
2. The interactive tree displays the complete component stack:
   * **Root Web Page**
     * └── **Page Template** (e.g., *Default Studio Template*)
       * └── **Web Template** (with full Liquid source code)
         * └── **Basic Form / Multistep Form** (with target Dataverse table)
         * └── **Entity List** (with views and filter criteria)

### 3. Inspect Templates & Liquid Logic
1. Click on any Web Template node to view its Liquid markup.
2. Search within the template code for specific tags (`{% fetchxml %}`, `{% include %}`, `{{ user.id }}`).
3. Verify Liquid includes and dependencies without opening solution zip files.

### 4. Review Forms & Metadata Overrides
1. View the underlying Dataverse entity name and system form assigned to the portal form.
2. Inspect metadata overrides such as custom validation messages, hidden fields, pre-populated lookups, and subgrid actions.

---

## 💡 Troubleshooting Common Portal Issues

* **Form Not Showing on Page?** Check the Page Structure Explorer to ensure the Page Template points to a Web Template that renders `{% entityform %}` or `{% webform %}`.
* **Liquid Syntax Errors?** Inspect the raw Web Template source directly in the viewer to check for unmatched tags or deprecated filters.
