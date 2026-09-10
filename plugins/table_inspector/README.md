# 🔍 Table Inspector

> **Deep schema inspector, form placement analyzer, data population profiler, and Data Dictionary generator for Microsoft Dataverse.**

When managing enterprise Microsoft Dynamics 365 or Power Apps systems, architects and consultants frequently face questions such as:
* *“Which fields on this table are actually placed on user forms versus orphaned in the schema?”*
* *“Is this custom attribute actually being populated with real data, or has it sat empty for years?”*
* *“Can I export a complete data dictionary for our compliance or integration team?”*

The **Table Inspector** answers all of these questions in seconds with automated metadata queries, form XML analysis, and live data population profiling.

---

## 🌟 Key Features

* **Complete Schema & Attribute Inspection**:
  * Displays Display Name, Schema Name, Logical Name, and Description.
  * Identifies Attribute Type (String, Lookup, OptionSet, Integer, Decimal, Money, DateTime, Boolean).
  * Shows Requirement Levels (None, SystemRequired, ApplicationRequired, Recommended).
  * Displays Audit Status, Searchable flags, and ValidForForm properties.
* **Form Placement Analysis**:
  * Scans all entity forms (Main forms, Quick Create forms, Quick View forms, and Card forms).
  * Immediately flags fields that are **placed on forms** vs. **orphaned fields** that exist in the database but cannot be seen or edited by end users.
* **Live Data Population Profiling**:
  * Scans table records to calculate the percentage of non-null vs. null values for each column.
  * Identifies deprecated, unused, or legacy custom fields with 0% population rate to safely guide schema cleanup.
* **1-Click Data Dictionary Export**:
  * Export complete table dictionaries to Microsoft Excel (`.xlsx`) or CSV, fully formatted for enterprise documentation and client sign-offs.

---

## 📋 Prerequisites & Permissions

1. **Environment Access**:
   * Active connection to a Microsoft Dataverse environment.
2. **User Privileges**:
   * **Read** permissions on Dataverse metadata (`EntityDefinitions` and `AttributeDefinitions`).
   * **Read** permissions on `systemform` records to analyze form placement.
   * Standard *System Customizer*, *Environment Maker*, or *System Administrator* privileges provide full capabilities.

---

## 🚀 How to Use

### 1. Select a Dataverse Table
1. Connect to your Dataverse environment via the DynamicsSuite sidebar.
2. Use the searchable dropdown to select any standard or custom table (e.g., `account`, `contact`, `opportunity`, `incident`, or `cr123_customentity`).
3. The inspector fetches entity metadata in the background.

### 2. Inspect Attributes & Schema
1. The **Attributes Table** displays every column with sorting, filtering, and column-visibility toggles.
2. Filter columns by:
   * **Data Type** (e.g. show only Lookups or Option Sets)
   * **Requirement Level**
   * **Custom vs. Standard** attributes

### 3. Analyze Form Placement
1. Switch to the **Form Placement** tab or check the Form Placement column.
2. Review which specific Main or Quick Create forms contain each field.
3. Quickly spot fields created for legacy projects that were never added to user forms.

### 4. Profile Data Population
1. Select a sample size (e.g., *100, 500, 1,000, or 5,000 records*).
2. Click **📊 Profile Data Usage**.
3. Review the population progress bar and inspect the **Populated %** column to distinguish actively used data from empty columns.

### 5. Export Data Dictionary
* Click **📊 Export to Excel** to download an audit-ready, styled Data Dictionary workbook including sheet tabs for Attributes, Option Sets, and Relationships.
