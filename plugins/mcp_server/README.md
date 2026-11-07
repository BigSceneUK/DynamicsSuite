# MCP Server Plugin for DynamicsSuite

Runs a local **Model Context Protocol (MCP)** service with **in-memory session security (Zero Disk Storage)**, providing AI coding assistants (**Antigravity**, **Claude Desktop**, **Cursor**, **Copilot**) with direct access to inspect Dynamics 365 / Dataverse systems, run diagnostics, audit security, and execute cross-table queries.

---

## 🔒 Security & Connection Highlights

- **Zero Disk Cache**: No `.msal_token_cache.json` or `.dynamics_session.json` saved to disk. All credentials and tokens stay purely in RAM.
- **Configurable Port**: Run on any port (default: `58999` for DynamicsSuite, `58998` for DynamicsSuite_mo).
- **Dual Transport Support**:
  - Direct **SSE / HTTP** (`http://127.0.0.1:<port>/sse`) for web-based and modern MCP clients (Antigravity, Cursor, Claude Desktop).
  - In-memory **IPC Token Endpoint** (`http://127.0.0.1:<port>/api/token`) for stdio-based MCP clients.
- **Automatic Lifecycle**: As soon as you log out or close DynamicsSuite, all in-memory tokens vanish from RAM immediately.

---

## 🛠️ MCP Tools & Functions Reference

The server exposes **Core Built-in Diagnostic Tools** as well as **Auto-Discovered Plugin Tools** dynamically registered across the suite.

### 1. Identity, Session & Environment

| Function | Parameters | Description |
|---|---|---|
| `get_active_session()` | *none* | Checks the live active Dynamics 365 session: returns whether a user is logged in via DynamicsSuite, environment name, organization URL, user email, and workspace directory. |
| `list_environments()` | *none* | Lists all configured Dynamics 365 environments (`dev`, `uat`, `prod`, or active session) and their authentication mode. |
| `list_accounts()` | *none* | Lists all connected user identities (`standard_user`, `c_account`, `service_account`, `service_principal`) stored in the shared session. |

### 2. Case Management & Customer Correspondence

| Function | Parameters | Description |
|---|---|---|
| `get_case(identifier, environment?, account?)` | `identifier` (ticket # or GUID) | Retrieves full case details with friendly labels for status, priority, customer, and SLA. |
| `search_cases(query?, status?, customer_name?, limit?, environment?, account?)` | `query`, `status`, `customer_name`, `limit` | Searches cases by keyword in title/ticket, status (`Active`/`Resolved`/`Cancelled`), or customer name. |
| `get_case_timeline(case_identifier, environment?, account?)` | `case_identifier` | Returns a consolidated chronological timeline of **Notes** (`annotations`), **Emails**, **Tasks**, and **Case Resolutions**. |
| `add_case_note(case_identifier, title, note_text, environment?, account?)` | `case_identifier`, `title`, `note_text` | Appends a timeline note (`annotation`) to an existing case. |

### 3. Dataverse Direct Query & System Diagnostics

| Function | Parameters | Description |
|---|---|---|
| `query_dataverse_table(table_name, filter_expr?, select_fields?, top?, environment?, account?)` | `table_name`, `filter_expr`, `select_fields`, `top` | General OData v9.2 query tool with automatic `@OData.Community.Display.V1.FormattedValue` formatting. |
| `get_entity_metadata(entity_logical_name, environment?, account?)` | `entity_logical_name` | Fetches table schema definitions, display names, primary key/name, and field data types. |
| `execute_fetchxml(entity_set_name, fetch_xml, environment?, account?)` | `entity_set_name`, `fetch_xml` | Direct FetchXML query engine with formatted label and option set translation. |
| `diagnose_record(entity_set_name, record_id, environment?, account?)` | `entity_set_name`, `record_id` | 360° record diagnosis: current attribute values, field-level audit history, related async jobs, and plugin trace logs. |

### 4. Security & Access Inspection

| Function | Parameters | Description |
|---|---|---|
| `list_user_roles(user_email_or_id, environment?, account?)` | `user_email_or_id` | Lists direct security roles, assigned teams, and team-inherited security roles for a user. |
| `check_user_access(user_email_or_id, entity_logical_name, environment?, account?)` | `user_email_or_id`, `entity_logical_name` | Checks effective table-level privileges (Read, Write, Create, Delete, Append, AppendTo, Assign, Share) and depth levels for a user. |

### 5. Schema & Relationship Graph

| Function | Parameters | Description |
|---|---|---|
| `find_relationship_path(source_entity, target_entity, max_hops?, environment?, account?)` | `source_entity`, `target_entity`, `max_hops` | Uses NetworkX multi-graph traversal across 1:N and N:N relationship metadata to determine foreign key join paths between tables. |
| `get_table_components(entity_logical_name, environment?, account?)` | `entity_logical_name` | Aggregates all Model-Driven App forms, saved system views, and active business rules associated with a table. |

### 6. Automation Dependency & Flow Triggers

| Function | Parameters | Description |
|---|---|---|
| `list_entity_triggers(entity_logical_name, message?, environment?, account?)` | `entity_logical_name`, `message` | Inspects active Power Automate Cloud Flows to find flows triggered on Create, Update, or Delete for an entity, including filter attributes. |
| `get_failed_flow_runs(environment?, time_window_hours?, account?)` | `time_window_hours` | Queries recent failed Cloud Flow sessions and failed asynchronous system jobs in the environment. |

### 7. Power Pages / Portal Security

| Function | Parameters | Description |
|---|---|---|
| `audit_portal_webapi(website_id?, environment?, account?)` | `website_id` | Audits Power Pages Web API site settings, exposed tables, and flags wildcard (`*`) column exposure risks. |
| `get_portal_page_hierarchy(webpage_id_or_url, environment?, account?)` | `webpage_id_or_url` | Inspects the complete component hierarchy of a web page: page templates, basic forms, and multistep forms. |

---

## 🧩 Auto-Discovered Plugin Tools

Installed plugins can expose additional tools by including an `mcp_tools.py` file. These tools are automatically loaded by [`mcp_server/plugin_discovery.py`](file:///Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/mcp_server/plugin_discovery.py) at startup:

| Plugin Area | Tool / Function | Purpose |
|---|---|---|
| **MCP Server Status** | `get_mcp_server_status()` | Real-time status, port, uptime, storage mode, and active session details. |
| **Audit History** | `get_record_audit_history(entity_set_name, record_id, limit?)` | Retrieves chronological field changes and audit trails for a record. |
| | `list_audit_enabled_entities(environment?)` | Lists all tables currently enabled for auditing in Dataverse. |
| | `search_audit_records(entity_set_name, action?, ...)` | Searches audit log entries by entity and action type. |
| **Cloud Flow** | `list_cloud_flows(environment?)` | Lists all active and inactive Cloud Flows in the environment. |
| | `get_cloud_flow_runs(workflow_id, top?)` | Fetches execution run histories and statuses for a specific flow. |
| | `search_cloud_flows(search_text)` | Searches flows by name or workflow definition criteria. |
| | `get_cloud_flow_sessions_by_date(start_datetime, end_datetime?)` | Finds flow sessions within a specific date/time window. |
| **Portal Inspector** | `list_portal_pages(environment?)` | Lists all published web pages in the active portal. |
| | `search_portal_code(search_text)` | Searches JavaScript, CSS, and Liquid templates across portal records. |
| **Portal WebAPI** | `audit_table_portal_context(table_name)` | Audits portal table permissions, scopes, and Web API accessibility. |
| | `get_portal_websites_list(environment?)` | Lists all configured portal website instances. |
| **Record Transporter** | `get_record_transport_details(entity_set_name, record_id)` | Inspects export/transport dependencies for a given record. |
| | `check_record_exists_in_env(entity_set_name, record_id, target_env)` | Checks if a record GUID exists in a target environment before transporting. |
| **Table Inspector** | `get_table_attributes_and_forms(logical_name)` | Summarizes table columns, forms, and primary attributes. |
| | `get_table_total_record_count(logical_name)` | Returns the approximate or exact total record count of a table. |
