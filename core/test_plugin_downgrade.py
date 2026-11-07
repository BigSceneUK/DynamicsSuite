"""
Unit tests for plugin rollback and downgrade functionality in core.plugin_store.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.plugin_store import (
    _PLUGINS_DIR,
    get_plugin_backup_info,
    rollback_plugin,
    get_plugin_git_versions,
    downgrade_plugin_from_tag,
)


class TestPluginDowngrade(unittest.TestCase):
    def setUp(self):
        self.test_plugin_id = "test_downgrade_dummy"
        self.plugin_dir = _PLUGINS_DIR / self.test_plugin_id
        self.backup_dir = _PLUGINS_DIR / f"_{self.test_plugin_id}_backup"
        self.swap_dir = _PLUGINS_DIR / f"_{self.test_plugin_id}_swap_temp"

        # Cleanup if left over
        for d in (self.plugin_dir, self.backup_dir, self.swap_dir):
            if d.exists():
                shutil.rmtree(d)

    def tearDown(self):
        for d in (self.plugin_dir, self.backup_dir, self.swap_dir):
            if d.exists():
                shutil.rmtree(d)

    def test_backup_info_when_empty(self):
        info = get_plugin_backup_info(self.test_plugin_id)
        self.assertIsNone(info)

    def test_rollback_and_backup_swap(self):
        # 1. Setup mock current plugin v2.0.0
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "plugin.py").write_text(
            'from core.plugin_base import PluginBase\n\nclass TestPlugin(PluginBase):\n    name = "Dummy"\n    version = "2.0.0"\n',
            encoding="utf-8",
        )

        # 2. Setup mock backup plugin v1.0.0
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / "plugin.py").write_text(
            'from core.plugin_base import PluginBase\n\nclass TestPlugin(PluginBase):\n    name = "Dummy"\n    version = "1.0.0"\n',
            encoding="utf-8",
        )

        # 3. Test get_plugin_backup_info
        info = get_plugin_backup_info(self.test_plugin_id)
        self.assertIsNotNone(info)
        self.assertEqual(info["version"], "1.0.0")

        # 4. Perform rollback
        mock_registry = MagicMock()
        ok, msg = rollback_plugin(self.test_plugin_id, mock_registry)
        self.assertTrue(ok)
        self.assertIn("v1.0.0", msg)

        # 5. Verify the active plugin is now v1.0.0
        active_content = (self.plugin_dir / "plugin.py").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.0"', active_content)

        # 6. Verify the backup now holds v2.0.0 (reversible!)
        new_backup_info = get_plugin_backup_info(self.test_plugin_id)
        self.assertIsNotNone(new_backup_info)
        self.assertEqual(new_backup_info["version"], "2.0.0")

    def test_get_plugin_git_versions(self):
        versions = get_plugin_git_versions("audit_history")
        self.assertIsInstance(versions, list)
        self.assertGreater(len(versions), 0)
        # Check first tag has required keys
        v0 = versions[0]
        self.assertIn("tag", v0)
        self.assertIn("version", v0)
        self.assertIn("source", v0)

    def test_get_plugin_git_versions_deduplication(self):
        # cloud_flow was at v1.0.0 across all git tags v1.0.0 through v1.0.4.
        # Deduplication should ensure each distinct plugin version is only returned once.
        versions = get_plugin_git_versions("cloud_flow")
        self.assertIsInstance(versions, list)
        ver_list = [v["version"] for v in versions]
        self.assertEqual(len(ver_list), len(set(ver_list)), "Plugin versions must be deduplicated")

    def test_downgrade_validation_errors(self):
        mock_reg = MagicMock()
        ok, msg = downgrade_plugin_from_tag("bad!id", "v1.0.0", mock_reg)
        self.assertFalse(ok)
        self.assertIn("Invalid plugin identifier", msg)

        ok, msg = downgrade_plugin_from_tag("audit_history", "bad;tag", mock_reg)
        self.assertFalse(ok)
        self.assertIn("Invalid tag name", msg)


if __name__ == "__main__":
    unittest.main()
