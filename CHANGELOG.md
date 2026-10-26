# Changelog

All notable changes to the **DynamicsSuite** project (both Core and Plugins) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.4] - 2026-10-26

### 🛡️ Power Pages Web API Auditor (`portal_webapi_auditor` v1.4.0)
- **Enterprise Pagination & High-Volume Dataverse Environments**:
  - Expanded OData pagination safety limit from 15 pages to 500 pages with `odata.maxpagesize=5000` header, supporting large environments with up to 2.5 million records without truncation.
  - Added circular pagination loop detection (`seen_urls` tracking) and safety limit warning logs.
- **Enhanced Data Model (EDM) Component Handling**:
  - Web Templates (component type 8) parsing for Web API and FetchXML occurrences.
  - Expanded Basic / Multistep Form entity discovery (component types 19 and 20).
  - Refined Entity Lists (component type 17) and Table Permissions (component type 18) mapping.
  - Fallback querying across `mspp` and `adx` site settings when Enhanced Data Model yields no site settings.
- **Cache Synchronization**:
  - Bumped version invalidation hash to `v42_expand_max_pages_to_500` to automatically clear stale session caches.

### 🚀 DynamicsSuite Core (v1.0.4)
- **Diagnostics & Feedback**: Bumped core version indicator in `core/ui/feedback_dialog.py` to `v1.0.4`.
- **Packaging & Distribution**: Packaged official `v1.0.4` release archives with `portal_webapi_auditor` v1.4.0.

---

## [1.0.3] - 2026-10-24

### 🛡️ Power Pages Web API Auditor (`portal_webapi_auditor` v1.3.0)
- **Expanded Surface & Blind Spot Detection**:
  - **Injected Global Scripts**: Scans `adx_sitesettings`, `mspp_sitesettings`, and Enhanced Data Model `powerpagecomponents` (type 9) for script-bearing settings (`<script>`, `/_api/`, `function`, `webapi`) with global badge tagging.
  - **Cloud Flow Web API Triggers**: Detects client-side triggers to Power Automate Cloud Flows (`/_api/cloudflows/v1.0/trigger/<flow_id>`).
  - **Bound Custom Actions & Custom APIs**: Parses bound actions on entity endpoints (e.g. `/_api/contacts(id)/Microsoft.Dynamics.CRM.actionName`) without distorting entity resolution.
  - **Embedded Subgrids**: Parses model-driven `formxml` in Basic/Multistep Forms to extract `<TargetEntityType>` references.
- **Lookup Dual-Representation & Whitelist Expansion**:
  - Direct AST and regex extraction of `_<lookup>_value` property accesses in client scripts.
  - Auto-expansion and bidirectional whitelist pairing for Lookups across Logical Names, Schema Names, and `_<lookup>_value`.
- **Diagnostics & UI Improvements**:
  - Added Website Discovery Details diagnostic inspector for multi-site Power Pages environments.
  - Improved caching invalidation and site settings resolution.

### 🚀 DynamicsSuite Core (v1.0.3)
- **Diagnostics & Feedback**: Bumped core version indicator in `core/ui/feedback_dialog.py` to `v1.0.3`.
- **Packaging & Distribution**: Packaged official `v1.0.3` release archives with `portal_webapi_auditor` v1.3.0.

---

## [1.0.2] - 2026-10-17

### 🛡️ Power Pages Web API Auditor (`portal_webapi_auditor` v1.2.0)
- **Dataverse Column Casing & Lookup `@odata.bind` Dual-Registration**:
  - Fixed Power Pages runtime Error `90040101` (*"Attribute <SchemaName> is not enabled for Web API"*).
  - Dataverse columns possess two internal names:
    - **Logical Name**: Always lowercase (e.g. `app_candidate`). Used by OData `GET` reading queries.
    - **Schema Name**: Preserves original mixed-case capitalization (e.g. `app_Candidate`). Used by Power Pages OData `POST` / `PATCH` requests on Lookup fields using `@odata.bind` notation (e.g., `"app_Candidate@odata.bind": "/contacts(...)"`).
  - Power Pages gatekeeper checks the stripped property name against the `Webapi/<table_name>/fields` whitelist. When the auditor previously normalized all fields to lowercase, `app_Candidate` was lost, blocking record creation and updates.
  - The auditor now discovers, tracks, and registers **both** names simultaneously in `Webapi/<table_name>/fields`.
- **Smart Filter & Attribute Extraction**:
  - `filter_table_attributes()`: Retains both lowercase Logical Names and mixed-case Schema Names without lossy deduplication.
  - `get_table_attributes()`: Queries and caches Schema Names for all `Lookup`, `Customer`, and `Owner` attribute types in `self._table_lookup_schema_map`.
  - `_extract_payload_fields()`: Preserves exact mixed-case property keys from `JSON.stringify` object payloads in client scripts.
  - Recommendation tiers (`strict`, `forms`, `server`) and unprojected wildcard `*` remediation automatically ensure dual registration for any lookup field.
- **Documentation**: Added dedicated section in plugin `README.md` detailing Dataverse dual-name architecture and troubleshooting Power Pages Error `90040101`.

### 🚀 DynamicsSuite Core (v1.0.2)
- **Diagnostics & Feedback**: Bumped core version indicator in [core/ui/feedback_dialog.py](file:///Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/core/ui/feedback_dialog.py) to `v1.0.2`.
- **Streamlined Distribution**: Pointed all download entry points across the web portal directly to the official [GitHub Latest Release](https://github.com/BigSceneUK/DynamicsSuite/releases/latest) page, eliminating duplicate file maintenance.
- **Catalog Feeds**: Rebuilt root and web portal plugin distribution catalogs with `portal_webapi_auditor` v1.2.0 and suite v1.0.2 assets.

---

## [1.0.1] - 2026-10-17

### 🚀 DynamicsSuite Core (v1.0.1)
- **Portable Python Runtime**: Introduced self-contained, zero-admin Python 3.11 environment packaging.
- **Startup Optimization**: Enhanced `start.sh`, `start.bat`, and `start.ps1` to skip redundant dependency downloads on subsequent launches when the environment is already provisioned.
- **Launcher Parity**: Cross-platform launcher consistency across Windows, macOS, and Linux.

---

## [1.0.0] - 2026-10-16

### 🚀 Initial Public Release
- **Unified Zero-Admin Application Shell**:
  - Browser-based UI powered by Streamlit, running completely in user space without requiring administrator rights.
  - Centralized Microsoft Entra ID (MSAL) authentication with zero credentials or secrets stored on disk.
  - In-memory Bearer token caching across Dataverse Web API, Microsoft Graph, and Power Automate endpoints.
- **In-App Plugin Store & Manager**:
  - 1-click install, upgrade, and removal of modular plugins.
  - Automatic dependency installation and hot-reloading without application restart.
  - JSON-based catalog feed distribution system.
- **Initial Plugin Toolset**:
  - 🔍 **Table Inspector (v1.0.0)**: Deep table schema inspection, form placement analysis (distinguishing placed vs. orphaned fields), live data population profiling, and Excel Data Dictionary generator.
  - 🛡️ **Power Page API Wildcard Auditor (v1.0.0)**: Power Pages Web API site setting auditor, client script code scanner, and automated least-privilege column whitelist remediation.
  - 📜 **Audit History Explorer (v1.0.0)**: Dataverse audit log timeline search, field-level diff viewer, and compliance reporting.
  - ⚡ **Cloud Flow Analytics (v1.0.0)**: Solution-aware cloud flow telemetry, run history querying, and recursive loop iteration deep payload search.
  - 🚚 **Record Transporter (v1.0.0)**: Configuration and master data transport across development, test, and production Dataverse instances.
  - 🌐 **Power Pages Portal Inspector (v1.0.0)**: Portal website metadata, webpage, and content snippet hierarchy explorer.
  - 🧩 **Plugin Development Template (v1.0.0)**: Reference starter template and scaffolding CLI for community developers.
