#!/usr/bin/env python3
"""
install_plugins.py — Orchestrator that installs dependencies for all DynamicsSuite plugins.

How it works
------------
Each plugin folder owns its own  install.py  which is fully self-contained and can
be run directly for that plugin alone.  This root script discovers every
plugins/*/install.py automatically — no manual registration needed.  When a new
plugin is added just drop an install.py in its folder and it will be picked up here.

Usage
-----
  python3 install_plugins.py                          # install all plugins + core
  python3 install_plugins.py sql_server_query         # specific plugin(s) only
  python3 install_plugins.py --list                   # list plugins and packages
  python3 install_plugins.py --check                  # check what is missing (no install)

Running a single plugin directly
---------------------------------
  python3 plugins/sql_server_query/install.py
  python3 plugins/sql_server_query/install.py --check
  python3 plugins/sql_server_query/install.py --list
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
PLUGINS_DIR = REPO_ROOT / "plugins"
ROOT_REQUIREMENTS = REPO_ROOT / "requirements.txt"


# ---------------------------------------------------------------------------
# Discovery — the only registry is the filesystem
# ---------------------------------------------------------------------------

def _discover() -> dict[str, Path]:
    """Return {plugin_id: install_script_path} for every plugins/*/install.py found."""
    return {
        p.parent.name: p
        for p in sorted(PLUGINS_DIR.glob("*/install.py"))
    }


# ---------------------------------------------------------------------------
# Helpers shared with per-plugin scripts
# ---------------------------------------------------------------------------

def _read_packages(install_script: Path) -> list[str]:
    """Read requirements.txt next to *install_script*, stripping comments."""
    req = install_script.parent / "requirements.txt"
    if not req.exists():
        return []
    lines = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            lines.append(line)
    return lines


def _is_installed(spec: str) -> bool:
    bare = spec.split(">=")[0].split("==")[0].split("<=")[0].split("!=")[0].strip()
    return importlib.util.find_spec(bare.replace("-", "_").lower()) is not None


def _run_plugin_install(install_script: Path) -> int:
    """Delegate to the plugin's own install.py via subprocess."""
    result = subprocess.run([sys.executable, str(install_script)])
    return result.returncode


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(plugins: dict[str, Path]) -> None:
    print(f"\n{'Plugin':<30} install.py location")
    print("-" * 72)
    for plugin_id, script in plugins.items():
        packages = _read_packages(script)
        print(f"\n  {plugin_id}  ({script.relative_to(REPO_ROOT)})")
        for pkg in packages:
            print(f"    • {pkg}")


def cmd_check(plugins: dict[str, Path]) -> None:
    print("\nChecking installed packages …\n")
    missing_total = 0
    for plugin_id, script in plugins.items():
        packages = _read_packages(script)
        missing = [p for p in packages if not _is_installed(p)]
        status = "✅ OK" if not missing else f"❌ missing: {', '.join(missing)}"
        print(f"  {plugin_id:<35} {status}")
        missing_total += len(missing)
    print()
    if missing_total:
        print(f"  {missing_total} package(s) not installed. Run without --check to install them.")
    else:
        print("  All plugin dependencies are satisfied.")


def cmd_install(plugins: dict[str, Path], selected: list[str] | None) -> None:
    # 1. Core requirements first (shared by all plugins)
    if ROOT_REQUIREMENTS.exists():
        print("\n[core] Installing root requirements …")
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(ROOT_REQUIREMENTS)]
        ).returncode
        if rc != 0:
            print("[core] ⚠  Root requirements install exited non-zero — continuing …")

    # 2. Validate requested plugin names
    if selected:
        unknown = set(selected) - set(plugins)
        if unknown:
            print(f"\n⚠  Unknown plugin(s): {', '.join(sorted(unknown))}")
            print(f"   Available: {', '.join(sorted(plugins))}")

    # 3. Delegate to each plugin's own install.py
    targets = {k: v for k, v in plugins.items() if not selected or k in selected}
    failed = []
    for plugin_id, script in targets.items():
        rc = _run_plugin_install(script)
        if rc != 0:
            failed.append(plugin_id)

    print()
    if failed:
        print(f"⚠  {len(failed)} plugin(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All plugins installed successfully.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Sanity-check: warn if not running under the correct Python.
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            f"⚠  Warning: 'streamlit' is not importable from {sys.executable}.\n"
            f"   DynamicsSuite requires python3. Try:\n"
            f"     python3 {Path(__file__).name} [args]\n"
        )

    parser = argparse.ArgumentParser(
        description="Orchestrate plugin dependency installation for DynamicsSuite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "plugins",
        nargs="*",
        metavar="PLUGIN",
        help="Plugin names to target (default: all).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all discovered plugins and their packages, then exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check which packages are missing without installing anything.",
    )
    args = parser.parse_args()

    plugins = _discover()
    if not plugins:
        print("No plugins/*/install.py files found. Nothing to do.")
        sys.exit(1)

    if args.list:
        cmd_list(plugins)
    elif args.check:
        cmd_check(plugins)
    else:
        cmd_install(plugins, args.plugins or None)


if __name__ == "__main__":
    main()

