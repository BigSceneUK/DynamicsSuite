# ⚡ Cloud Flow Run History

> **Real-time execution monitoring, error diagnostics, and run payload inspection for solution-aware Cloud Flows.**

The **Cloud Flow** plugin allows Power Platform Architects, Support Engineers, and Functional Consultants to inspect run histories and debug automation failures across Microsoft Power Automate flows without clicking through individual runs in the Power Automate Maker portal.

---

## 🌟 Key Features

* **Solution-Aware Flow Catalog**: Automatically lists all automated, instant, and scheduled Cloud Flows stored in your Dataverse environment (`workflows` table).
* **Multi-Criteria Run Filtering**: Filter flow executions by execution status (*Succeeded*, *Failed*, *Running*, *Cancelled*, *Waiting*), flow name, or custom date range.
* **Instant Failure Diagnosis**: Quickly locate runs that failed, extract top-level error codes, and pinpoint the exact failing action in the execution graph.
* **Payload & Action Detail Inspection**: Drill down into trigger inputs, action execution states, and custom detail fields.
* **Configurable Detail Columns**: Configure custom business fields to display in run summary tables via `plugins_config.json`.

---

## 📋 Prerequisites & Permissions

1. **Solution-Aware Flows**:
   * Flows must be solution-aware (stored inside a Power Apps / Dataverse solution) so execution metadata is accessible via the Dataverse API.
2. **User Privileges**:
   * Your Microsoft Entra ID account requires **Read** access to the `workflow` (Process) and `flowrun` (Flow Run) tables in Dataverse.
   * System Customizer or Environment Maker roles typically provide sufficient access.

---

## 🚀 How to Use

### 1. Select a Cloud Flow
1. Connect to your Dataverse environment from the DynamicsSuite sidebar.
2. Search and select the flow you want to analyze from the searchable dropdown.
3. The plugin will display the flow's status (*Enabled / Draft*), unique ID, and creation metadata.

### 2. Query Run History
1. Select a date range (e.g. *Past 24 Hours*, *Past 7 Days*, or *Custom Range*).
2. Choose a status filter:
   * **All Runs**
   * **Failed Only** (ideal for rapid morning triage)
   * **Succeeded Only**
   * **Running / In-Progress**
3. Click **Fetch Runs** to retrieve the execution log.

### 3. Drill Down into Execution Details
1. Click on any run row to view the full execution trace.
2. Inspect:
   * **Run Start & End Time**: Precise execution duration in seconds or minutes.
   * **Trigger Payload**: Input body that initiated the flow execution.
   * **Failed Action Name**: The specific step where the flow crashed or timed out.
   * **Error Message**: Raw and formatted error description returned by the Power Automate engine.

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
