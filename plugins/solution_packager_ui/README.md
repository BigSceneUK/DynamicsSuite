# 📦 Solution Packager UI

> **Zero-admin browser interface for unpacking, inspecting, and packaging Microsoft Dataverse solution ZIP archives.**

Traditional Microsoft solution ALM tooling requires installing Windows-only developer tools (such as `SolutionPackager.exe` from the NuGet SDK) or command-line developer utilities (`pac solution unpack`). On corporate laptops with restricted admin permissions or on macOS and Linux machines, consultants and customizers frequently struggle to inspect or extract solution contents.

The **Solution Packager UI** runs entirely inside DynamicsSuite with **zero administrator privileges required**. Unpack solution ZIPs, inspect component XML files, examine web resources, and repackage folders into clean, importable solution archives directly from your browser.

---

## 🌟 Key Features

* **Zero-Admin & Cross-Platform**: Works on Windows, macOS, and Linux without requiring Visual Studio, PowerShell admin modules, or the Power Platform CLI.
* **1-Click Solution Unpacking**: Upload any managed or unmanaged Dataverse `.zip` file to extract its complete internal structure.
* **Component Explorer**:
  * Inspect `customizations.xml` with formatted syntax highlighting.
  * View entity schemas, relationship definitions, and option sets.
  * Preview Web Resources (JavaScript, CSS, HTML, SVG, PNG) without extracting to disk.
  * Review solution metadata (`solution.xml`) including version numbers and publisher details.
* **Safe Solution Repackaging**: Recombines edited or extracted folders into clean, standard zip archives ready for import back into the Power Apps Maker Portal or deployment pipelines.
* **Clean Archive Sanitization**: Automatically strips OS metadata (such as macOS `__MACOSX`, `._*` resource forks, and `.DS_Store` files) that trigger Dataverse solution import schema errors.

---

## 📋 Prerequisites & Permissions

* **No Dataverse Connection Required**: This plugin operates on solution archive files locally in your browser/user space.
* **No Admin Rights**: Runs within standard user permissions.

---

## 🚀 How to Use

### 1. Unpack a Solution ZIP
1. Open **Solution Packager UI** from the DynamicsSuite navigation bar.
2. Drag and drop your exported solution `.zip` archive into the file upload box.
3. Click **📦 Unpack Solution**.
4. The plugin parses the archive and presents a structured tree view of all solution components.

### 2. Inspect Components
* **Solution Summary**: View solution name, unique display name, publisher prefix, and version.
* **Entities**: Browse extracted entity definitions, custom fields, and form layouts.
* **Web Resources**: Review scripts and stylesheets directly in the editor.
* **Workflows & Flows**: Inspect raw workflow XAML or flow JSON definitions.

### 3. Repackage for Deployment
1. If you modified components or want to regenerate a sanitized distribution zip, select the source folder.
2. Verify package settings (Target Version, Solution Type: Managed vs Unmanaged).
3. Click **🗜️ Build Solution Package**.
4. Download the freshly generated, clean `.zip` archive ready to import into Dataverse.

---

## 💡 Troubleshooting Solution Import Errors

* **"Invalid character in XML"**: Use the component inspector to validate `customizations.xml` syntax before attempting an import.
* **"Archive contains unexpected files"**: The repackaging feature automatically prevents OS metadata files from being bundled into your solution archive, resolving common Dataverse package validator rejections.
