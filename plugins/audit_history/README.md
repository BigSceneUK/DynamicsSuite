# 📜 Audit History Explorer

> **Deep-dive field-level change auditing for Microsoft Dataverse & Dynamics 365 records.**

The **Audit History Explorer** enables Functional Consultants, System Customizers, Compliance Officers, and Developers to inspect, trace, and report on historical changes made to Dataverse records. Easily determine **who changed what, when it happened, and from which value to which**, with one-click export to Excel and CSV.

---

## 🌟 Key Features

* **Table-Level Discovery**: Automatically discovers all audit-enabled tables in your Dataverse environment.
* **Flexible Multi-Field Filtering**: Search records by primary name, email, code, status, or any custom attribute without writing FetchXML.
* **Chronological Audit Timeline**: Displays every event in order, including Creates, Updates, Deactivations, Activations, Assignments, and Shares.
* **Old vs. New Value Diff Viewer**: Highlights the exact field-level state before and after each change.
* **Action Code Decoding**: Translates numeric Dataverse audit codes into intuitive business labels (e.g. *Update*, *Deactivate*, *Assign*, *Share*).
* **Export for Compliance & Reporting**: Export audit history logs to CSV or Excel for compliance audits, governance reviews, and security incident investigations.

---

## 📋 Prerequisites & Permissions

1. **Dataverse Auditing Enabled**:
   * Global auditing must be enabled on your Power Platform environment (*Power Platform Admin Center → Environments → Settings → Audit and logs → Audit settings*).
   * Auditing must be turned on for the target table (*Power Apps Maker Portal → Table Properties → Advanced options → Audit changes to its data*).
2. **User Privileges**:
   * Your Microsoft Entra ID user account requires **Read** permission on the target table.
   * Your user account requires the **View Audit History** (`prvReadAudit`) privilege (included in *System Administrator*, *System Customizer*, and standard auditor roles).

---

## 🚀 How to Use

### 1. Select a Table
1. Connect to your Dataverse environment in the DynamicsSuite sidebar.
2. In the plugin, select the table you wish to audit (e.g., *Account*, *Contact*, or a custom table).
3. The plugin will automatically query Dataverse metadata to retrieve audit-enabled tables and available fields.

### 2. Find the Target Record
1. Use the **Filter Builder** to locate the specific record.
2. Search by primary attribute (e.g., Account Name) or add additional filters (e.g., *Email Address*, *Created On*, *Status*).
3. Click **Search Records** and select the record from the results table.

### 3. Review Historical Changes
1. The chronological timeline displays all modification events for the selected record.
2. Expand any audit entry to inspect:
   * **Modified On**: Timestamp in your local timezone.
   * **Changed By**: Display name and email of the user or system process that executed the change.
   * **Action**: Create, Update, Delete, Assign, Share, Status Change.
   * **Attribute**: Field logical and display name.
   * **Old Value ➔ New Value**: Clear side-by-side transition.

### 4. Export Audit Log
* Click **📥 Export to CSV** or **📊 Export to Excel** to save the complete record history for offline analysis or compliance evidence.

---

## ⚙️ Technical Details

* **API Endpoints**: Uses standard Microsoft Dataverse Web API `RetrieveRecordChangeHistory` and `audits` entity sets.
* **Authentication**: Seamless Microsoft Entra ID token acquisition via DynamicsSuite `AppContext`.
* **Zero Disk Footprint**: Audit logs are streamed directly into memory; no customer data is cached on disk.
