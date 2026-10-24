# Power Pages Web API Auditor: Scope, Logic & Category Analysis

This document outlines everything currently audited by the **Power Pages Web API Auditor** plugin, how detection operates across Dataverse schemas, and potential missing categories or blind spots in the detection logic.

---

## 1. What Is Currently Checked

The plugin audits Power Pages configuration and code across all three Microsoft portal data models:
- **Standard Data Model:** `adx_*` tables (Portal Management app).
- **Power Pages / Government Model:** `mspp_*` tables.
- **Enhanced Data Model (v2):** `powerpagecomponents` table with type codes.

### A. Site Settings & Whitelist Configuration
| Target Component | Schema / Tables Scanned | Checked Fields / Settings | Purpose & Logic |
| :--- | :--- | :--- | :--- |
| **Web API Enabled** | `adx_sitesettings`<br>`mspp_sitesettings`<br>`powerpagecomponents` (type 9) | `Webapi/<table_name>/enabled` | Determines if Web API endpoint `/_api/<table_name>` is enabled (`true`/`false`). |
| **Web API Fields Whitelist** | `adx_sitesettings`<br>`mspp_sitesettings`<br>`powerpagecomponents` (type 9) | `Webapi/<table_name>/fields` | Audits the column whitelist. Flags deprecated wildcard `*`, empty whitelists, and compares configured columns against actual usage. |
| **Web API Inner Error** | `adx_sitesettings`<br>`mspp_sitesettings`<br>`powerpagecomponents` (type 9) | `Webapi/<table_name>/error` | Checks whether detailed Web API error diagnostics are enabled. |

---

### B. Portal Metadata & Code Sources Scanned
The global code scanner (`scan_portal_code_for_webapi`) and table deep inspector (`get_table_deep_context`) scan the following portal components:

| Component Category | Entity Sources | Content Columns Inspected | Detection Mechanism |
| :--- | :--- | :--- | :--- |
| **Web Templates** | `adx_webtemplates`<br>`mspp_webtemplates`<br>`powerpagecomponents` (type 11) | `adx_source`<br>`mspp_source`<br>`content` (JSON/string) | Scans HTML, Liquid tags (`{% fetchxml %}`), client-side `<script>` tags, and inline JS functions/AJAX calls. |
| **Web Pages** | `adx_webpages`<br>`mspp_webpages`<br>`powerpagecomponents` (type 2) | `adx_customjavascript`<br>`adx_copy` (HTML content)<br>`mspp_customjavascript`<br>`mspp_copy` | Extracts custom page scripts and embedded HTML/Liquid copy. |
| **Basic Forms** | `adx_entityforms`<br>`mspp_entityforms`<br>`powerpagecomponents` (type 15) | `adx_registerstartupscript`<br>`adx_customjavascript`<br>`mspp_registerstartupscript`<br>`mspp_customjavascript` | Scans startup scripts, custom JavaScript, and inspects the underlying Model-Driven Form XML layout (`systemform`). |
| **Multistep Form Steps** | `adx_webformsteps`<br>`mspp_webformsteps`<br>`powerpagecomponents` (type 19) | `adx_registerstartupscript`<br>`adx_customjavascript`<br>`mspp_registerstartupscript`<br>`mspp_customjavascript` | Scans step-specific startup scripts, custom JavaScript, and underlying Model-Driven Form XML layout. |
| **Entity Lists (Lists)** | `adx_entitylists`<br>`mspp_entitylists`<br>`powerpagecomponents` (type 18/21) | `adx_registerstartupscript`<br>`adx_views` (savedquery)<br>`adx_odata_enabled` | Checks list startup scripts, OData feed status (`/_api/entitylists(...)`), and parses view `layoutxml` to whitelist displayed columns. |
| **Content Snippets** | `adx_contentsnippets`<br>`mspp_contentsnippets`<br>`powerpagecomponents` (type 7) | `adx_value`<br>`mspp_value` | Scans snippet values for embedded HTML, JavaScript, and Liquid blocks. |
| **Form / Step Metadata** | `adx_entityformmetadatas`<br>`adx_webformstepmetadatas`<br>`powerpagecomponents` (types 26 & 20) | `adx_description`<br>`adx_label`<br>`adx_controlstyle` | Extracts inline scripts inside form metadata and identifies PCF Code Components (`controlstyle = 756150001` or `756150000`). |
| **Web Files (JS Attachments)** | `adx_webfiles`<br>`mspp_webfiles`<br>`powerpagecomponents` (type 3) | `annotation.documentbody` (base64) | Queries `annotation` notes attached to `.js` Web Files, decodes base64 content, and parses JS code for Web API calls. |
| **Model-Driven Form XML** | `systemforms` | `formxml` | Reads CRM form layout definitions to discover all fields and lookup controls placed on portal forms. |

---

### C. Syntax & Code Pattern Recognition Engines
The auditor parses code using multiple specialized regex and syntax engines:
1. **Direct Web API Endpoints:** `/_api/<collection_or_table>` with `$select`, `$filter`, and `$orderby`.
2. **HTTP Clients:** `$.ajax`, `$.get`, `$.post`, `window.fetch`, `axios.*`, and `new XMLHttpRequest()`.
3. **Power Pages SDK & Wrapper Functions:**
   - Standard wrappers: `webapi.safeAjax`, `webapi.retrieveRecord`, `webapi.createRecord`, `webapi.updateRecord`, `webapi.deleteRecord`, `context.webAPI.*`.
   - **Automatic Wrapper Discovery:** Automatically detects custom developer helper functions (e.g. `function myApiCall(...)`) and traces calls routed through them.
4. **JSON Payload Keys:**
   - Extracts payload property keys passed into `data: JSON.stringify({...})` or object literals.
   - Extracts `@odata.bind` navigation property annotations (e.g. `"crimson_interviewee@odata.bind"` -> `crimson_interviewee`).
5. **Client-Side FetchXML:** Extracts attributes, conditions, and orderbys from queries using `?fetchXml=<fetch>...</fetch>`, including linked entities (`<link-entity>`).
6. **Server-Side Liquid Blocks:** Extracts entity and attribute references from `{% fetchxml %}` and Liquid entity lookups.
7. **Unprojected Query Detection:** Identifies calls omitting `$select` or specifying wildcard `$select=*`, warning of breaking changes scheduled by Microsoft.

---

## 2. Potential Missing Categories & Logic Blind Spots

While the current engine covers the primary surfaces of Power Pages Web API usage, the following categories represent possible gaps or edge cases:

### 1. Global Scripts Injected via Site Settings (Implemented ✅)
* **Description:** Injected global scripts in Site Settings (e.g. `TrackingCode`, `Header/Template`, `Footer/Template`, and custom script settings).
* **Implementation:** The scanner now queries `adx_sitesettings`, `mspp_sitesettings`, and Enhanced Data Model `powerpagecomponents` (type 9), auto-filtering for script-bearing content (`<script`, `/_api/`, `function`, `webapi`), and exposes them with the orange `⚙️ SITE SETTING SCRIPT (GLOBAL)` badge.

### 2. Power Pages Cloud Flow Web API Calls (Implemented ✅)
* **Description:** Client-side triggers to Power Automate Cloud Flows: `/_api/cloudflows/v1.0/trigger/<flow_id>`.
* **Implementation:** The scanner extracts Cloud Flow IDs and labels occurrences with the purple `⚡ CLOUD FLOW WEB API` badge, linking directly to `Webapi/cloudflows/<flow_id>/enabled` settings.

### 3. Bound Custom Actions & Custom APIs (Implemented ✅)
* **Description:** Bound Custom Action calls like `/_api/contacts(id)/Microsoft.Dynamics.CRM.new_CalculateDiscount`.
* **Implementation:** The regex parser now extracts the underlying table (`contact`) and isolates the bound action name (`new_CalculateDiscount`), preventing action names from distorting entity resolution.

### 4. Embedded Subgrid Related Tables (Implemented ✅)
* **Description:** Basic Forms and Multistep Forms containing embedded subgrids referencing related tables.
* **Implementation:** `get_form_subgrid_entities` parses the underlying model-driven `formxml` to extract `<TargetEntityType>` and registers occurrences under `Basic Form (Embedded Subgrid)` with the blue `🧩 FRAMEWORK` badge.

### 5. Multi-Language / Localized Child Web Pages
* **Description:** Multilingual child pages (`_adx_rootwebpageid_value ne null`). Scanned via `adx_copy`, `adx_summary`, and `adx_customjavascript` with fallback to `site_col eq null`.

### 6. External Scripts / CDN Bundles (Architectural Boundary)
* **Description:** Externally hosted script bundles (`<script src="https://cdn.example.com/app.js">`). Cannot be crawled from Dataverse metadata directly.

### 7. Dataverse Lookup Dual-Representation & Whitelist Expansion (Implemented ✅)
* **Description:** In Microsoft Dataverse Web API, lookup attributes have dual representation:
  - **Write Operations:** Bindings via `@odata.bind` use the SchemaName or LogicalName (e.g., `crimson_interviewee` / `crimson_Interviewee`).
  - **Read Operations:** OData single-valued navigation properties return `_<lookup>_value` (e.g., `_crimson_interviewee_value`).
* **Implementation:**
  - **Direct AST & Regex Extraction:** Detects any `_<lookup>_value` property access in client-side JavaScript (e.g. `booking._crimson_interviewee_value`, `slot['_crimson_interviewee_value']`).
  - **Auto-Expansion in Discovered Fields:** Automatically expands detected lookup fields to include `_<lookup>_value`, SchemaName, and logical name.
  - **Bidirectional Whitelist Pairing:** Recognizes that if `_crimson_interviewee_value` is configured in `Webapi/<table>/fields`, the lookup relationship is satisfied and will not falsely flag `crimson_interviewee` as missing.
  - **Comprehensive Recommendations:** Recommendation tiers (`strict`, `forms`, `server`) guarantee both `_<lookup>_value` and the SchemaName are retained and suggested, preventing runtime 403 Forbidden errors when querying or writing lookups.


