#!/usr/bin/env python3
"""Standalone installer for the audit_history plugin.

Usage
-----
  python3 install.py          # install this plugin's dependencies
  python3 install.py --check  # check if dependencies are satisfied (no install)
  python3 install.py --list   # list required packages
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PLUGIN_ID = "audit_history"
PLUGIN_DIR = Path(__file__).parent.resolve()
REQUIREMENTS = PLUGIN_DIR / "requirements.txt"


def _read_packages() -> list[str]:
    lines = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            lines.append(line)
    return lines


def _is_installed(spec: str) -> bool:
    bare = spec.split(">=")[0].split("==")[0].split("<=")[0].split("!=")[0].strip()
    return importlib.util.find_spec(bare.replace("-", "_").lower()) is not None


def run(quiet: bool = False) -> int:
    """Install this plugin's packages. Returns pip exit code (0 = success)."""
    if not quiet:
        print(f"[{PLUGIN_ID}] Installing dependencies …")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        capture_output=quiet,
    )
    if not quiet:
        if result.returncode == 0:
            print(f"[{PLUGIN_ID}] ✅ Done")
        else:
            print(f"[{PLUGIN_ID}] ❌ pip exited with code {result.returncode}")
    return result.returncode


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f"Install {PLUGIN_ID} plugin dependencies.")
    parser.add_argument("--check", action="store_true", help="Check without installing.")
    parser.add_argument("--list", action="store_true", help="List required packages.")
    args = parser.parse_args()

    packages = _read_packages()

    if args.list:
        print(f"\n{PLUGIN_ID} requires:")
        for p in packages:
            print(f"  • {p}")
    elif args.check:
        missing = [p for p in packages if not _is_installed(p)]
        if missing:
            print(f"❌ {PLUGIN_ID}: missing — {', '.join(missing)}")
            sys.exit(1)
        else:
            print(f"✅ {PLUGIN_ID}: all dependencies satisfied")
    else:
        sys.exit(run())
