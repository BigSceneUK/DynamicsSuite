# ⚡ Cloud Flow Run History

> **Real-time execution monitoring, error diagnostics, and run payload inspection for solution-aware Cloud Flows.**

The **Cloud Flow** plugin allows Power Platform Architects, Support Engineers, and Functional Consultants to inspect run histories and debug automation failures across Microsoft Power Automate flows without clicking through individual runs in the Power Automate Maker portal.

---

## 🌟 Key Features

* **Solution-Aware Flow Catalog**: Automatically lists all automated, instant, and scheduled Cloud Flows stored in your Dataverse environment (`workflows` table).
* **Multi-Criteria Run Filtering**: Filter flow executions by execution status (*Succeeded*, *Failed*, *Running*, *Cancelled*, *Waiting*), flow name, or custom date range.
* **Instant Failure Diagnosis**: Quickly locate runs that failed, extract top-level error codes, and pinpoint the exact failing action in the execution graph.
* **Payload & Action Detail Inspection**: Drill down into trigger inputs, action execution states, and custom detail fields.
* **🔍 Deep Search Payloads (Loop Iteration Inspection)**: Recursively search for any target record GUID, attribute, FetchXML string, or business key across every loop repetition (`Apply to each`, `Do until`) of a flow run.
* **Dual-Engine Integration (Dataverse + Power Automate API)**: Connect directly to Microsoft Power Automate management endpoints for full action graphs, loop iteration data, and cross-flow parallel scans.
* **Configurable Detail Columns**: Configure custom business fields to display in run summary tables via `plugins_config.json`.

---

## 💡 Pro-Tip: Connect to Power Automate API for Optimal Results

> **⚠️ Recommendation: Authorize Power Automate & Select Your Environment**
>
> While basic flow metadata is queryable through Dataverse (`workflow` and `flowrun` tables), Dataverse records can often be delayed, sparse, or lack action-level execution details.
>
> To unlock **full search results, live execution metrics, and loop payload inspection**:
> 1. Expand the **⚡ Power Automate API (optional — more detailed runs)** panel at the top of the plugin.
> 2. Click **🔗 Authorize Power Automate** and sign in with your Microsoft Entra ID account.
> 3. **Select your Power Automate Environment** from the dropdown list.
> 
> Connecting to the Power Automate API enables parallel cross-flow scanning, real-time trigger and action payloads, and the powerful **Deep Search Payloads** feature.

---

## 📋 Prerequisites & Permissions

1. **Solution-Aware Flows**:
   * Flows must be solution-aware (stored inside a Power Apps / Dataverse solution) so execution metadata is accessible via the Dataverse API.
2. **User Privileges**:
   * **Dataverse**: Read access to `workflow` (Process) and `flowrun` (Flow Run) tables. System Customizer or Environment Maker roles typically provide sufficient access.
   * **Power Automate API**: Permissions to view flow execution history in the target Power Platform environment.

---

## 🚀 How to Use

### 1. Connect to Power Automate API & Select Environment *(Recommended)*
1. At the top of the plugin page, expand **⚡ Power Automate API (optional — more detailed runs)**.
2. Click **🔗 Authorize Power Automate** to obtain an API token for `api.flow.microsoft.com`.
3. In the environment selector dropdown, select your active Power Automate environment.
4. Once connected (marked by a green ✅), all subsequent queries will benefit from high-fidelity run telemetry and action details.

### 2. Select a Cloud Flow
1. Connect to your Dataverse environment from the DynamicsSuite sidebar.
2. In the **📋 Per-Flow History** tab, search and select the flow you want to analyze from the searchable dropdown.
3. The plugin displays the flow's status (*Enabled / Draft*), unique ID, trigger type, and creation metadata.

### 3. Query Run History
1. Select a date range (e.g. *Past 24 Hours*, *Past 7 Days*, or *Custom Range*).
2. Choose a status filter:
   * **All Runs**
   * **Failed Only** (ideal for rapid morning triage)
   * **Succeeded Only**
   * **Running / In-Progress**
3. Click **Fetch Runs** to retrieve the execution log.
*(Tip: You can also use the **📅 Date-Range Search** tab to scan executions across multiple flows simultaneously when connected to the Power Automate API).*

### 4. Drill Down into Execution Details
1. Click on any run row in the summary table to view its full execution trace.
2. Inspect:
   * **Run Start & End Time**: Precise execution duration in seconds or minutes.
   * **Trigger Payload**: Input body and parameters that initiated the flow.
   * **Failed Action Name**: The specific step where the flow crashed or timed out.
   * **Error Message**: Raw and formatted error descriptions returned by the Power Automate engine.

### 5. Deep Search Payloads (Search Inside Loops)
When a flow processes batches of records (e.g. iterating over 500 accounts or contacts in an `Apply to each` or `Do until` loop), finding which specific iteration handled a target record or encountered an issue is notoriously tedious in the Power Automate Maker portal.

The **Deep Search Payloads** feature recursively traverses nested inputs, outputs, conditions, and loop iterations to pinpoint exactly where a record was processed.

#### How to Use Deep Search:
1. **Prerequisite**: Ensure the **Power Automate API** is connected and your environment is selected (Step 1 above).
2. Query runs for your selected flow (Step 3 above).
3. Scroll down below the Run History table to the **🔍 Deep Search Payloads** section.
4. **Select Target Run**: Use the dropdown to choose the specific flow execution you want to inspect.
5. **Enter Search Query**: Enter any search term in the **Query / Condition to find** box:
   * **Record GUID**: e.g. `f6ff7545-4919-f111-8342-6045bdc1eeb4`
   * **Business Key / Attribute**: Account name, email address, invoice number, or order ID
   * **FetchXML / Field Name**: Specific attribute name like `telephone1` or XML tag
6. **Choose Search Scope**:
   * **Selected Run (deepest: inspects all loop repetitions)** *(Recommended)*: Downloads the full action execution graph and up to 500 iterations per loop, scanning every nested repetition.
   * **All Filtered Runs (scans triggers & top-level actions)**: Rapidly scans top-level actions across recent filtered runs.
7. **Bypass Cache if Needed**: If the flow recently finished or updated, check **🔄 Force fresh fetch (bypass cache)** to retrieve live data from the API.
8. Click **🔍 Search Payloads**.
9. **Review Matches**:
   * The tool displays the exact count of actions and loop iterations scanned (e.g., *Scanned: 18 actions and 142 loop iterations across 2 loops*).
   * For each match, an expandable card reveals:
     * **Exact Location**: e.g., `Loop 'Apply_to_each_account' → Iteration #37`
     * **Matched Snippet**: Contextual snippet highlighting where your query occurred.
     * **Full Payload**: Formatted JSON or XML code viewer showing inputs/outputs of that specific loop iteration.

---

## ⚙️ Configuration (`plugins_config.json`)

You can customize which detail columns appear in the run inspector by editing the plugin configuration:

```json
{
  "cloud_flow": {
    "enabled": true,
    "config": {
      "detail_fields": [
        "Operation",
        "Fetch XML",
        "Update Action",
        "Update Payload"
      ]
    }
  }
}
```

This configuration can also be modified directly from the **Plugin Manager → Installed Tools → Config** UI.
