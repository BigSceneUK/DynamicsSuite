# Dynamics 365 Architecture & System Diagnostics — MCP Server

This Model Context Protocol (MCP) server enables AI assistants (**Antigravity**, **Claude Desktop**, **Cursor**, **Copilot**) to inspect Dynamics 365 Customer Service Cases, query Dataverse tables, analyze entity relationships, audit security privileges, trace Cloud Flow triggers, and diagnose system errors across **multiple user identities** and **environments**.

---

## 🛠️ Complete Tools Reference (18 Tools)

### 1. Identity & Environments

| Tool | Parameters | Description |
|---|---|---|
| `list_accounts()` | *none* | Lists all connected user identities (`standard_user`, `c_account`, `service_account`, `service_principal`) and active status. |
| `list_environments()` | *none* | Lists all configured environments (`dev`, `uat`, `prod`, or active session) and their authentication mode. |

### 2. Case Management & Customer Correspondence

| Tool | Parameters | Description |
|---|---|---|
| `get_case(identifier, environment?, account?)` | `identifier` (ticket # or GUID) | Retrieves full case details with friendly labels for status, priority, and customer. |
| `search_cases(query?, status?, customer_name?, limit?, environment?, account?)` | `query`, `status`, `customer_name`, `limit` | Searches cases by keyword in title/ticket, status (`Active`/`Resolved`/`Cancelled`), or customer. |
| `get_case_timeline(case_identifier, environment?, account?)` | `case_identifier` | Returns consolidated chronological timeline of **Notes**, **Emails**, **Tasks**, and **Case Resolutions**. |
| `add_case_note(case_identifier, title, note_text, environment?, account?)` | `case_identifier`, `title`, `note_text` | Adds a timeline note (`annotation`) to an existing case. |

### 3. Dataverse Direct Query & System Diagnostics

| Tool | Parameters | Description |
|---|---|---|
| `query_dataverse_table(table_name, filter_expr?, select_fields?, top?, environment?, account?)` | `table_name`, `filter_expr`, `select_fields`, `top` | General OData v9.2 query tool with automatic `@OData.Community.Display.V1.FormattedValue` formatting. |
| `get_entity_metadata(entity_logical_name, environment?, account?)` | `entity_logical_name` | Fetches table schema definitions, display names, primary key/name, and expanded attribute types. |
| `execute_fetchxml(entity_set_name, fetch_xml, environment?, account?)` | `entity_set_name`, `fetch_xml` | Direct FetchXML query engine with formatted label translation. |
| `diagnose_record(entity_set_name, record_id, environment?, account?)` | `entity_set_name`, `record_id` | 360° record diagnosis: current attribute values, field-level audit history, related async jobs, and plugin trace logs. |

### 4. Security & Access Inspection

| Tool | Parameters | Description |
|---|---|---|
| `list_user_roles(user_email_or_id, environment?, account?)` | `user_email_or_id` | Lists direct security roles, teams, and team-inherited security roles for a user. |
| `check_user_access(user_email_or_id, entity_logical_name, environment?, account?)` | `user_email_or_id`, `entity_logical_name` | Checks effective table-level privileges (Read, Write, Create, Delete, Append, AppendTo, Assign, Share) and depth levels for a user. |

### 5. Schema & Relationship Graph

| Tool | Parameters | Description |
|---|---|---|
| `find_relationship_path(source_entity, target_entity, max_hops?, environment?, account?)` | `source_entity`, `target_entity`, `max_hops` | Uses NetworkX multi-graph traversal across 1:N and N:N relationship metadata to determine foreign key joins between tables. |
| `get_table_components(entity_logical_name, environment?, account?)` | `entity_logical_name` | Aggregates all Model-Driven App forms, saved system views, and active business rules associated with a table. |

### 6. Automation Dependency & Flow Triggers

| Tool | Parameters | Description |
|---|---|---|
| `list_entity_triggers(entity_logical_name, message?, environment?, account?)` | `entity_logical_name`, `message` | Inspects active Power Automate Cloud Flows to find those that trigger on Create, Update, or Delete on an entity, including filtering attributes. |
| `get_failed_flow_runs(environment?, time_window_hours?, account?)` | `time_window_hours` | Queries recent failed Cloud Flow sessions and failed asynchronous system jobs in the environment. |

### 7. Power Pages / Portal Security

| Tool | Parameters | Description |
|---|---|---|
| `audit_portal_webapi(website_id?, environment?, account?)` | `website_id` | Audits Power Pages Web API site settings, exposed tables, and flags wildcard (`*`) field exposure risks. |
| `get_portal_page_hierarchy(webpage_id_or_url, environment?, account?)` | `webpage_id_or_url` | Inspects the complete component hierarchy of a web page: page templates, basic forms, and multistep forms. |

---

## 🔌 Connecting to AI Assistants

### Antigravity & Claude Desktop

Add to your `claude_desktop_config.json`:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "dynamics365": {
      "command": "/Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/.venv/bin/python3",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite"
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

The workspace already includes [`.cursor/mcp.json`](file:///Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/.cursor/mcp.json):

```json
{
  "mcpServers": {
    "dynamics365": {
      "command": "${workspaceFolder}/.venv/bin/python3",
      "args": ["-m", "mcp_server.server"],
      "env": {}
    }
  }
}
```

---

## 🧩 Adding New Plugins in the Future (Auto-Discovery)

The MCP server includes a **Dynamic Auto-Discovery Hook** ([`mcp_server/plugin_discovery.py`](file:///Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/mcp_server/plugin_discovery.py)).

When you create or install a new plugin:
1. Create an `mcp_tools.py` inside your plugin folder (e.g. `plugins/<plugin_name>/mcp_tools.py`).
2. Define a `register_mcp_tools(app, auth)` function and decorate your AI tools with `@app.tool()`:

```python
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider

def register_mcp_tools(app: MCPServer, auth: DynamicsAuthProvider) -> None:
    @app.tool()
    def my_plugin_analysis(entity_name: str) -> str:
        """Tool description for AI assistant..."""
        return json.dumps({"result": "done"})
```

3. **Zero configuration required**: On the next startup of the MCP server, the new tools are auto-discovered and immediately available to the AI model. See [`plugins/plugin_template/mcp_tools.py`](file:///Users/moluk/Development/Microsoft_Dynamics/DynamicsSuite/plugins/plugin_template/mcp_tools.py) for a reference starter template.

---

## 💡 Example AI Prompts

- **System Diagnostics**: *"Diagnose why student application record `41d08e5c-1234-4567-89ab-cdef01234567` failed to submit in Dev."*
  $\rightarrow$ AI calls `diagnose_record("incidents", "41d08e5c-...")` and retrieves field audit changes, related system jobs, and plugin trace exceptions.
- **Security Audit**: *"Why can't user `john.doe@domain.com` edit the `contact` table?"*
  $\rightarrow$ AI calls `check_user_access("john.doe@domain.com", "contact")` and reports missing Write privilege.
- **Relationship Mapping**: *"How is the `lead` table joined to the `campaign` table in Dataverse?"*
  $\rightarrow$ AI calls `find_relationship_path("lead", "campaign")` and outputs the lookup attribute name.
- **Automation Blast Radius**: *"What Cloud Flows trigger when an `incident` record is updated?"*
  $\rightarrow$ AI calls `list_entity_triggers("incident", message="Update")` and returns the flow names and filter attributes.
- **Power Pages Security**: *"Check if any of our Power Pages Web API endpoints expose sensitive fields with wildcards."*
  $\rightarrow$ AI calls `audit_portal_webapi()` and reports any overexposed tables.

---

## 🧪 Testing the Server

Run unit tests:
```bash
.venv/bin/python3 -m unittest mcp_server/test_mcp.py
```

Run stdio mode interactively:
```bash
.venv/bin/python3 -m mcp_server.server --transport stdio
```
