"""Persistence helpers for the Cloud Flow plugin.

Results are stored relative to the *current working directory* so they remain
in the project root regardless of how the app is launched.

Layout
------
  .cf_results_cache.json       — latest auto-saved search results (temp)
  history/
    cf_history_YYYYMMDD_HHMMSS_<name>.json   — named history records
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

_CACHE_FILE = Path(".cf_results_cache.json")
_HISTORY_DIR = Path("history")


def _ensure_history_dir() -> None:
    _HISTORY_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Temp cache  (auto-save / restore between sessions)
# ---------------------------------------------------------------------------

def save_results(run_data: list[dict]) -> bool:
    """Persist *run_data* to the temp cache file."""
    try:
        _CACHE_FILE.write_text(
            json.dumps(run_data, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logging.error("save_results failed: %s", exc)
        return False


def load_results() -> list[dict]:
    """Load the temp cache, returning an empty list if unavailable."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as exc:
        logging.error("load_results failed: %s", exc)
    return []


def clear_results() -> None:
    """Delete the temp cache file."""
    try:
        _CACHE_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logging.error("clear_results failed: %s", exc)


# ---------------------------------------------------------------------------
# Named history
# ---------------------------------------------------------------------------

def save_history(
    run_data: list[dict],
    name: str = "",
    remark: str = "",
) -> str | None:
    """Save *run_data* as a named history record.

    Returns the filename (stem only) on success, ``None`` on failure.
    """
    _ensure_history_dir()
    slug = (name.strip().replace(" ", "_") or "unnamed")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cf_history_{ts}_{slug}.json"
    path = _HISTORY_DIR / filename

    has_details = any(r.get("has_details") for r in run_data)
    payload = {
        "name": name.strip(),
        "remark": remark.strip(),
        "timestamp": datetime.now().isoformat(),
        "has_details": has_details,
        "data": run_data,
    }
    try:
        path.write_text(
            json.dumps(payload, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return filename
    except Exception as exc:
        logging.error("save_history failed: %s", exc)
        return None


def list_history() -> list[dict]:
    """Return summary metadata for all saved history records (newest first)."""
    _ensure_history_dir()
    records: list[dict] = []
    for p in sorted(_HISTORY_DIR.glob("cf_history_*.json"), reverse=True):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            records.append(
                {
                    "filename": p.name,
                    "name": raw.get("name", ""),
                    "remark": raw.get("remark", ""),
                    "timestamp": raw.get("timestamp", ""),
                    "Fetched Detail": raw.get("has_details", False),
                    "count": len(raw.get("data", [])),
                }
            )
        except Exception as exc:
            logging.warning("list_history: cannot parse %s — %s", p.name, exc)
    return records


def load_history(filename: str) -> tuple[str, list[dict]]:
    """Load a history record by filename.

    Returns ``(remark, run_data)``. Raises ``FileNotFoundError`` if missing.
    """
    safe_name = Path(filename).name
    path = _HISTORY_DIR / safe_name
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("remark", ""), raw.get("data", [])


def update_history_remark(filename: str, new_remark: str) -> bool:
    """Update the remark field of an existing history record."""
    safe_name = Path(filename).name
    path = _HISTORY_DIR / safe_name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["remark"] = new_remark.strip()
        path.write_text(
            json.dumps(raw, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logging.error("update_history_remark failed: %s", exc)
        return False


def delete_history(filename: str) -> bool:
    """Delete a history file."""
    safe_name = Path(filename).name
    path = _HISTORY_DIR / safe_name
    try:
        path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logging.error("delete_history failed: %s", exc)
        return False
