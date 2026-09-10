from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
from pathlib import Path
import re
import shutil
import urllib.parse
import urllib.request
import zipfile

from packaging.version import parse as parse_version

from core.plugin_registry import PluginRegistry

_REPO_ROOT = Path(__file__).parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"
_LOCAL_CATALOG_FILE = _REPO_ROOT / "plugins_catalog.json"


class PluginStatus(str, Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    UPDATE_AVAILABLE = "update_available"


@dataclass
class StorePlugin:
    """Metadata representing a plugin in the Tools Library (Store)."""
    id: str
    name: str
    version: str
    author: str = "Community"
    released_by: str = "Bigscene"
    release_date: str = ""
    description: str = ""
    icon: str = "🔌"
    download_url: str = ""
    sha256: str = ""
    tags: list[str] = field(default_factory=list)
    min_core_version: str = "1.0.0"
    readme: str = ""
    repo_url: str = ""
    issues_url: str = ""
    doc_url: str = ""
    support_email: str = "support@bigscene.uk"

    @classmethod
    def from_dict(cls, data: dict) -> StorePlugin:
        released_by = data.get("released_by", "").strip() or data.get("author", "Community").strip()
        return cls(
            id=data.get("id", "").strip(),
            name=data.get("name", "").strip() or data.get("id", "Unnamed"),
            version=data.get("version", "1.0.0").strip(),
            author=data.get("author", "").strip() or released_by,
            released_by=released_by,
            release_date=data.get("release_date", "").strip(),
            description=data.get("description", "").strip(),
            icon=data.get("icon", "🔌").strip() or "🔌",
            download_url=data.get("download_url", "").strip(),
            sha256=data.get("sha256", "").strip(),
            tags=data.get("tags", []) if isinstance(data.get("tags"), list) else [],
            min_core_version=data.get("min_core_version", "1.0.0").strip(),
            readme=data.get("readme", "").strip(),
            repo_url=data.get("repo_url", "").strip(),
            issues_url=data.get("issues_url", "").strip(),
            doc_url=data.get("doc_url", "").strip(),
            support_email=data.get("support_email", "support@bigscene.uk").strip() or "support@bigscene.uk",
        )


def _get_ssl_context():
    """Create SSL context using certifi CA bundle if available."""
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def fetch_catalog(catalog_url: str | None = None) -> tuple[list[StorePlugin], str | None]:
    """
    Fetch the catalog from a remote URL or fallback to the local plugins_catalog.json file.
    Returns (list of StorePlugin, notice_or_error_message).
    """
    plugins: list[StorePlugin] = []
    notice: str | None = None

    data: dict | None = None

    if catalog_url and catalog_url.strip():
        url = catalog_url.strip()
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "DynamicsSuite-PluginStore/1.0"},
            )
            ssl_ctx = _get_ssl_context()
            with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as response:
                content = response.read().decode("utf-8")
                data = json.loads(content)
        except Exception as exc:
            logging.warning("Failed to fetch remote catalog from %s: %s", url, exc)
            notice = f"Could not reach remote catalog ({exc}). Showing local fallback catalog."

    # Fallback to local catalog if remote failed or no URL provided
    if data is None and _LOCAL_CATALOG_FILE.exists():
        try:
            with open(_LOCAL_CATALOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not notice and catalog_url:
                notice = "Loaded from local catalog."
        except Exception as exc:
            logging.error("Failed to read local catalog: %s", exc)
            return [], f"Failed to load catalog: {exc}"

    if not data:
        return [], notice or "No catalog found."

    raw_list = data.get("plugins", [])
    for item in raw_list:
        if isinstance(item, dict) and item.get("id"):
            plugins.append(StorePlugin.from_dict(item))

    return plugins, notice


def get_plugin_status(store_plugin: StorePlugin, registry: PluginRegistry) -> tuple[PluginStatus, str | None]:
    """
    Determine whether a store plugin is not installed, installed, or has an update available.
    Returns (PluginStatus, installed_version_or_None).
    """
    installed_plugin = registry.get_plugin(store_plugin.id)
    
    if installed_plugin is not None:
        installed_version = getattr(installed_plugin, "version", "1.0.0")
    else:
        # Check if the folder exists on disk even if there was an error or it's uninitialized
        plugin_dir = _PLUGINS_DIR / store_plugin.id
        if not plugin_dir.exists() or not (plugin_dir / "plugin.py").exists():
            return PluginStatus.NOT_INSTALLED, None
        installed_version = "1.0.0"

    try:
        remote_v = parse_version(store_plugin.version)
        local_v = parse_version(installed_version)
        if remote_v > local_v:
            return PluginStatus.UPDATE_AVAILABLE, installed_version
        return PluginStatus.INSTALLED, installed_version
    except Exception:
        if store_plugin.version != installed_version:
            return PluginStatus.UPDATE_AVAILABLE, installed_version
        return PluginStatus.INSTALLED, installed_version


def install_or_update_plugin(store_plugin: StorePlugin, registry: PluginRegistry) -> tuple[bool, str]:
    """
    Download and extract a plugin ZIP package into plugins/<id>/,
    install dependencies, and reload the plugin registry.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", store_plugin.id):
        return False, f"Invalid plugin identifier: '{store_plugin.id}'."

    if not store_plugin.download_url:
        return False, "Plugin does not provide a download URL."

    if not store_plugin.sha256 or not store_plugin.sha256.strip():
        return False, (
            f"Security: Plugin '{store_plugin.name}' has no SHA-256 checksum in the catalog. "
            "Installation blocked — only integrity-verified plugins can be installed."
        )

    download_url = store_plugin.download_url.strip()
    if download_url.startswith("http://"):
        return False, "Insecure HTTP download URLs are not allowed. Please use HTTPS."

    try:
        # Fetch archive data
        if download_url.startswith("https://"):
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "DynamicsSuite-PluginStore/1.0"},
            )
            ssl_ctx = _get_ssl_context()
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                archive_bytes = resp.read()
        elif download_url.startswith("file://"):
            local_path = Path(urllib.request.url2pathname(urllib.parse.urlparse(download_url).path))
            archive_bytes = local_path.read_bytes()
        else:
            local_path = _REPO_ROOT / download_url
            if not local_path.exists():
                return False, f"Package file not found: {download_url}"
            archive_bytes = local_path.read_bytes()

        # Integrity verification: check SHA-256 hash if provided by catalog
        if store_plugin.sha256:
            computed_sha = hashlib.sha256(archive_bytes).hexdigest().lower()
            expected_sha = store_plugin.sha256.lower().strip()
            if computed_sha != expected_sha:
                logging.error(
                    "Security error: SHA-256 checksum mismatch for '%s' (expected: %s, got: %s)",
                    store_plugin.id, expected_sha, computed_sha,
                )
                return False, f"Integrity check failed: package SHA-256 mismatch for '{store_plugin.name}'. Download aborted."

        # Validate ZIP archive
        zip_buffer = io.BytesIO(archive_bytes)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            namelist = zf.namelist()

            # Security: check for zip-slip (path traversal)
            for member in namelist:
                if member.startswith("/") or member.startswith("\\") or ".." in member:
                    return False, f"Security warning: malformed file path in archive: {member}"

            target_dir = _PLUGINS_DIR / store_plugin.id
            temp_extract_dir = _PLUGINS_DIR / f"_{store_plugin.id}_extract_temp"

            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)
            temp_extract_dir.mkdir(parents=True, exist_ok=True)

            zf.extractall(temp_extract_dir)

            # Check if archive has a single root folder matching plugin_id or contains contents directly
            extracted_items = list(temp_extract_dir.iterdir())
            source_content_dir = temp_extract_dir
            if len(extracted_items) == 1 and extracted_items[0].is_dir():
                source_content_dir = extracted_items[0]

            # Verify plugin.py exists in the source content
            if not (source_content_dir / "plugin.py").exists():
                shutil.rmtree(temp_extract_dir)
                return False, "Invalid plugin archive: missing 'plugin.py'."

            # Safely replace target_dir
            if target_dir.exists():
                backup_dir = _PLUGINS_DIR / f"_{store_plugin.id}_backup"
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                shutil.move(str(target_dir), str(backup_dir))

            shutil.move(str(source_content_dir), str(target_dir))

            # Cleanup temp folder if still exists
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

        # Install any dependencies in user-space venv
        registry.install_dependencies(store_plugin.id)

        # Force re-discovery and activate plugin
        registry.discover(force=True)
        registry.set_enabled(store_plugin.id, True)
        registry.save_config()

        return True, f"Successfully installed '{store_plugin.name}' (v{store_plugin.version})!"

    except Exception as exc:
        logging.exception("Failed to install plugin %s: %s", store_plugin.id, exc)
        return False, f"Installation failed: {exc}"


def uninstall_plugin(plugin_id: str, registry: PluginRegistry) -> tuple[bool, str]:
    """
    Remove the plugin folder from disk, reload the registry, and persist config.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", plugin_id):
        return False, f"Invalid plugin identifier: '{plugin_id}'."

    target_dir = _PLUGINS_DIR / plugin_id
    if not target_dir.exists():
        return False, f"Plugin directory '{plugin_id}' does not exist."

    try:
        shutil.rmtree(target_dir)
        registry.discover(force=True)
        registry.save_config()
        return True, f"Successfully uninstalled plugin '{plugin_id}'."
    except Exception as exc:
        logging.exception("Failed to uninstall plugin %s: %s", plugin_id, exc)
        return False, f"Uninstall failed: {exc}"
