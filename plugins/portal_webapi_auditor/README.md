# 🛡️ Power Page API Wildcard Auditor

> **Automated security auditor and 1-click remediation tool for Microsoft Power Pages Web API `*` wildcard deprecation.**

Microsoft is actively enforcing security updates across Power Pages that deprecate and restrict the use of wildcard asterisks (`*`) in Web API Site Settings (e.g., `Webapi/<table_name>/fields = *`). Leaving wildcards enabled creates critical data exposure risks and will break client-side API calls when Microsoft's enforcement deadline takes effect.

The **Power Page API Wildcard Auditor** scans your site settings, runs static code analysis on your portal JavaScript and Liquid templates to find every field actually used in code, and automatically generates clean, secure, minimal field whitelists with 1-click remediation.

---

## 🌟 Key Features

* **Wildcard Exposure Scanner**: Instantly identifies all Site Settings using dangerous `*` wildcards across both Standard (`adx_`) and Enhanced (`mspp_`) data models.
* **Intelligent Static Code Analysis**: Scans all portal Web Templates, Web Pages, Content Snippets, and Custom JavaScript files for Web API operations:
  * `webapi.safeAjax(...)`
  * `Xrm.WebApi.retrieveRecord(...)` / `retrieveMultipleRecords(...)`
  * Standard `fetch()` and `$.ajax()` calls targeting `/_api/...`
* **Exposed vs. Used Field Matrix**: Displays a side-by-side comparison showing which fields are currently exposed by wildcards vs. which fields are genuinely required by your portal code.
* **1-Click Whitelist Remediation**: Replaces the insecure `*` wildcard entry with the exact comma-separated list of required fields directly in your environment.
* **Reversible & Auditable**: Backs up previous site setting values before updating so you can revert any changes immediately.

---

## 📋 Prerequisites & Permissions

1. **Power Pages / Dynamics Portal Website**:
   * One or more active Power Pages websites with Web API enabled.
2. **User Privileges**:
   * **Read** permissions on Web Pages, Web Templates, Content Snippets, and Basic/Multistep Forms.
   * **Read & Write** permissions on `adx_sitesetting` / `mspp_sitesetting` to apply 1-click whitelist updates.
   * *System Administrator* or *System Customizer* role is recommended.

---

## 🚀 How to Use

### 1. Run the Security Audit
1. Connect to your Dataverse environment in DynamicsSuite.
2. Select your Power Pages website from the dropdown.
3. Click **🔍 Start Audit**. The plugin will scan:
   * Active Site Settings matching `Webapi/*/fields`
   * Portal JavaScript libraries and inline scripts
   * Liquid templates containing Web API queries

### 2. Review Detected Vulnerabilities
1. Insecure site settings with `*` wildcards are flagged in red.
2. The audit results table shows:
   * **Table Name**: Target Dataverse entity (e.g., `contact`, `incident`, `custom_order`).
   * **Current Site Setting**: `Webapi/<table_name>/fields = *`
   * **Detected In-Use Fields**: e.g., `firstname, lastname, emailaddress1, telephone1`
   * **Unused Exposed Fields**: Attributes exposed to anonymous or authenticated web visitors that your portal code never actually calls.

### 3. Drill Down into Calling Code
* Click on any detected field to see the exact file, template name, and code snippet where the field is referenced in your portal front-end code.

### 4. 1-Click Whitelist Remediation
1. Review the proposed minimal whitelist.
2. Click **🛡️ Replace Wildcard with Whitelist**.
3. The plugin will update the site setting in Dataverse:
   ```text
   Before: Webapi/contact/fields = *
   After:  Webapi/contact/fields = firstname,lastname,emailaddress1,telephone1
   ```
4. Clear your portal server cache to activate the updated whitelist.

---

## 🔒 Security Best Practice Note

Following Microsoft's Well-Architected Framework for Power Platform, Web API access should strictly follow the **principle of least privilege**:
* Never expose administrative, system-calculated, or internal flag attributes over public Web APIs.
* Always combine field whitelists with strict **Table Permissions** and **Web Roles**.
