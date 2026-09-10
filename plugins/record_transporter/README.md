# 🚚 Record Transporter

> **Move configuration records and reference data between Microsoft Dataverse environments with interactive lookup dependency resolution.**

Migrating configuration data (such as product catalogs, price lists, portal web pages, tax tables, and custom lookup entities) between Development, Test, and Production environments often fails because of missing foreign key lookups and broken GUID references.

The **Record Transporter** eliminates manual Excel imports and tedious GUID mapping by querying records from your source environment, analyzing referenced lookups, and interactively resolving or creating missing dependencies in the target environment.

---

## 🌟 Key Features

* **Cross-Environment Migration**: Transport records directly between any two Dataverse tenants or environments without exporting static CSVs.
* **Interactive Lookup Dependency Resolution**: Automatically detects foreign key lookup fields (e.g., Parent Account, Category, Currency, Owner) and checks if the referenced record exists in the target environment.
* **Missing Record Prompting**: If a referenced record is missing in target, you can choose to transport the parent record on-the-fly or map it to an existing target record.
* **Preserve Primary GUIDs**: Preserves exact record GUIDs (`Id`) to guarantee cross-environment parity and prevent duplicate configuration rows.
* **Intelligent Upsert Mode**: Updates existing records if already present or inserts net-new records using alternate keys or primary GUIDs.
* **Batch Execution & Safety Validation**: Pre-validates payloads before applying changes, reporting validation errors before committing writes.

---

## 📋 Prerequisites & Permissions

1. **Environment Access**:
   * Access to both a **Source** Dataverse environment and a **Target** Dataverse environment.
2. **User Privileges**:
   * **Source Environment**: **Read** privileges on the tables being exported and their related lookup tables.
   * **Target Environment**: **Create** and **Write** privileges on the target tables.
   * *System Customizer* or *Environment Maker* role is typically required.

---

## 🚀 How to Use

### 1. Configure Source & Target Environments
1. Select your **Source Environment** from the DynamicsSuite connection panel.
2. Authenticate to your **Target Environment** when prompted by the plugin.

### 2. Query & Select Records
1. Select the Dataverse table you wish to transport (e.g. `pricelevel`, `adx_webtemplate`, `custom_taxcode`).
2. Filter records using a query or view.
3. Select the records you wish to migrate using the interactive checkbox grid.

### 3. Review Lookup Dependencies
1. The plugin scans all foreign key lookup fields on the selected records.
2. The dependency resolver flags:
   * 🟢 **Resolved Lookups**: The referenced record exists in the target environment with matching GUID or primary name.
   * 🟡 **Missing Lookups**: The referenced record exists only in source.
3. For missing lookups, choose your strategy:
   * **Include in Transport**: Automatically package and transport the referenced parent record first.
   * **Map to Existing**: Point the lookup to an alternative existing record in target.

### 4. Execute Transport & Verify
1. Click **🚀 Transport Records**.
2. A real-time progress bar tracks each batch upsert operation.
3. Upon completion, a summary report confirms successful creations and updates, along with any validation warnings.

---

## 💡 Best Practice Tips

* **Transport Order**: For complex hierarchies, transport lookup tables (e.g., Categories, Units, Currencies) before transporting primary entity records.
* **System Attributes**: Read-only system fields (such as `createdon`, `modifiedon`, `versionnumber`) are automatically stripped before sending updates to the target API to avoid Dataverse validation errors.
