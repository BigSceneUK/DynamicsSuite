from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from core.plugin_base import PluginBase

_CONFIG_FILE = Path(__file__).parent.parent / "plugins_config.json"
_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


class _PluginEntry:
    """Internal record kept by the registry for each discovered plugin."""

    def __init__(
        self,
        plugin_id: str,
        instance: PluginBase,
        load_error: str = None,
        missing_deps: list[str] = None,
    ):
        self.plugin_id = plugin_id
        self.instance = instance          # None when load_error is set
        self.load_error = load_error      # Non-None when import failed
        self.enabled = True               # Default — overridden by saved config
        self.config = {}                  # Per-plugin config dictionary
        self.missing_deps = missing_deps or []


class PluginRegistry:
    """
    Discovers, loads, and manages the lifecycle of all plugins.

    Usage (in app.py)::

        registry = PluginRegistry()
        registry.discover()
        registry.load_config()

        # During admin save:
        registry.set_enabled("power_suite", True)
        registry.save_config()

        # Render loop:
        for plugin in registry.get_enabled():
            ...
    """

    def __init__(self):
        self._entries: dict[str, _PluginEntry] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, force: bool = False) -> None:
        """
        Walk the plugins/ directory and import each plugin.py.

        Import errors are caught and stored as _PluginEntry.load_error so the
        rest of the app continues to work.
        """
        if not _PLUGINS_DIR.exists():
            self._entries.clear()
            return

        # Prune plugins that have been removed from disk
        existing_ids = {
            item.name
            for item in _PLUGINS_DIR.iterdir()
            if item.is_dir() and not item.name.startswith("_") and (item / "plugin.py").exists()
        }
        for pid in list(self._entries.keys()):
            if pid not in existing_ids:
                del self._entries[pid]

        for item in sorted(_PLUGINS_DIR.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue

            plugin_id = item.name
            plugin_module_path = item / "plugin.py"
            if not plugin_module_path.exists():
                continue

            # Skip already-discovered successfully loaded plugins (unless force=True)
            if not force and plugin_id in self._entries and self._entries[plugin_id].instance is not None:
                continue

            # Preserve enabled status and config if already existed in registry
            was_enabled = True
            prev_config = {}
            if plugin_id in self._entries:
                was_enabled = self._entries[plugin_id].enabled
                prev_config = getattr(self._entries[plugin_id], "config", {})

            # Automatically add any plugin-local libs/ subfolder to sys.path
            libs_dir = item / "libs"
            if libs_dir.exists() and libs_dir.is_dir():
                libs_path = str(libs_dir.resolve())
                if libs_path not in sys.path:
                    sys.path.insert(0, libs_path)

            # Check requirements.txt if present
            req_file = item / "requirements.txt"
            missing_deps = []
            if req_file.exists():
                try:
                    with open(req_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            try:
                                from packaging.requirements import Requirement
                                req = Requirement(line)
                                name = req.name
                                specifier = req.specifier
                            except Exception:
                                import re
                                match = re.match(r"^([a-zA-Z0-9_\-]+)", line)
                                if match:
                                    name = match.group(1)
                                    specifier = None
                                else:
                                    name = line
                                    specifier = None

                            try:
                                installed_version = importlib_metadata.version(name)
                                if specifier:
                                    from packaging.version import parse as parse_version
                                    if not specifier.contains(installed_version, prereleases=True):
                                        missing_deps.append(line)
                            except Exception:
                                missing_deps.append(line)
                except Exception as e:
                    logging.warning("Failed to parse requirements.txt for %s: %s", plugin_id, e)

            if missing_deps:
                entry = _PluginEntry(plugin_id=plugin_id, instance=None, missing_deps=missing_deps)
                entry.enabled = was_enabled
                entry.config = prev_config
                self._entries[plugin_id] = entry
                continue

            try:
                module_name = f"plugins.{plugin_id}.plugin"
                if module_name in sys.modules:
                    module = importlib.reload(sys.modules[module_name])
                else:
                    module = importlib.import_module(module_name)

                # Find the PluginBase subclass (not PluginBase itself)
                plugin_class = None
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, PluginBase) and obj is not PluginBase:
                        plugin_class = obj
                        break

                if plugin_class is None:
                    raise ImportError(
                        f"No PluginBase subclass found in {module_name}"
                    )

                instance = plugin_class()
                instance._plugin_id = plugin_id
                instance._registry = self
                entry = _PluginEntry(plugin_id=plugin_id, instance=instance)
                entry.enabled = was_enabled
                entry.config = prev_config
                self._entries[plugin_id] = entry
                logging.info("Discovered plugin: %s (%s)", plugin_id, instance.name)

            except Exception as exc:
                logging.error("Failed to load plugin '%s': %s", plugin_id, exc)
                entry = _PluginEntry(
                    plugin_id=plugin_id, instance=None, load_error=str(exc)
                )
                entry.enabled = was_enabled
                entry.config = prev_config
                self._entries[plugin_id] = entry

    def install_dependencies(self, plugin_id: str) -> bool:
        """
        Install requirements for the given plugin using active python interpreter.
        Returns True if successful, False otherwise.
        """
        entry = self._entries.get(plugin_id)
        if not entry:
            return False

        req_file = _PLUGINS_DIR / plugin_id / "requirements.txt"
        if not req_file.exists():
            return True

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                capture_output=True,
                text=True,
                check=True
            )
            logging.info("Successfully installed dependencies for %s: %s", plugin_id, result.stdout)
            
            entry.load_error = None
            entry.missing_deps = []
            
            self.discover()
            return True
        except subprocess.CalledProcessError as err:
            logging.error("Failed to install dependencies for %s: %s", plugin_id, err.stderr)
            entry.load_error = f"Dependency installation failed: {err.stderr}"
            return False

    # ------------------------------------------------------------------
    # Enable / Disable
    # ------------------------------------------------------------------

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if plugin_id not in self._entries:
            return
        entry = self._entries[plugin_id]
        was_enabled = entry.enabled
        entry.enabled = enabled

        if entry.load_error or getattr(entry, "missing_deps", []):
            return  # Cannot call lifecycle hooks if plugin failed to load or has missing deps

        try:
            if enabled and not was_enabled:
                entry.instance.on_load()
            elif not enabled and was_enabled:
                entry.instance.on_unload()
        except Exception as exc:
            logging.warning("Plugin %s lifecycle hook error: %s", plugin_id, exc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_enabled(self) -> list[PluginBase]:
        """Return all successfully loaded AND enabled plugin instances."""
        return [
            e.instance
            for e in self._entries.values()
            if e.enabled and e.instance is not None
        ]

    def get_plugin(self, plugin_id: str) -> PluginBase | None:
        """Return a plugin instance by its folder id, or None."""
        entry = self._entries.get(plugin_id)
        if entry and entry.instance:
            return entry.instance
        return None

    def all_entries(self) -> list[_PluginEntry]:
        """Return all entries (including failed ones) for the admin page."""
        return list(self._entries.values())

    # ------------------------------------------------------------------
    # Persistence & Configuration
    # ------------------------------------------------------------------

    def save_config(self) -> None:
        """Write the current enabled/disabled state and plugin configurations to plugins_config.json."""
        existing_data = {}
        if _CONFIG_FILE.exists():
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception:
                pass

        for pid, entry in self._entries.items():
            item = existing_data.get(pid, {})
            item["enabled"] = entry.enabled
            if getattr(entry, "config", None):
                item["config"] = entry.config
            elif "config" in item and not entry.config:
                item["config"] = {}
            existing_data[pid] = item

        try:
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2)
        except OSError as exc:
            logging.error("Could not save plugins_config.json: %s", exc)

    def load_config(self) -> None:
        """Apply saved enabled/disabled state and configs from plugins_config.json."""
        if not _CONFIG_FILE.exists():
            return
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Could not load plugins_config.json: %s", exc)
            return

        for plugin_id, cfg in data.items():
            if isinstance(cfg, dict) and plugin_id in self._entries:
                self._entries[plugin_id].enabled = cfg.get("enabled", True)
                self._entries[plugin_id].config = cfg.get("config", {})

    def get_plugin_config(self, plugin_id: str) -> dict:
        """Return the combined configuration for the plugin (default_config + saved config)."""
        entry = self._entries.get(plugin_id)
        default_cfg = {}
        if entry and entry.instance:
            default_cfg = dict(getattr(entry.instance, "default_config", {}))

        saved_cfg = getattr(entry, "config", {}) if entry else {}
        merged = dict(default_cfg)
        merged.update(saved_cfg)
        return merged

    def save_plugin_config(self, plugin_id: str, new_config: dict) -> None:
        """Update and persist configuration for a specific plugin."""
        if plugin_id in self._entries:
            entry = self._entries[plugin_id]
            entry.config = dict(new_config)
            self.save_config()
            if entry.instance:
                try:
                    entry.instance.on_config_updated(entry.config)
                except Exception as exc:
                    logging.warning("Error in on_config_updated for %s: %s", plugin_id, exc)

    def get_example_config(self, plugin_id: str) -> dict:
        """Return example configuration from plugins/<plugin_id>/config.example.json or default_config."""
        example_file = _PLUGINS_DIR / plugin_id / "config.example.json"
        if example_file.exists():
            try:
                with open(example_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logging.warning("Could not read config.example.json for %s: %s", plugin_id, exc)

        entry = self._entries.get(plugin_id)
        if entry and entry.instance:
            return dict(getattr(entry.instance, "default_config", {}))
        return {}
