"""Persistence helpers for Dataverse Audit History plugin.

Provides auto-saved session cache (.ah_results_cache.json) and
named history records (history/ah_history_*.json), allowing users to
save analysis results and restore them without re-querying or re-correlating.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from plugins.audit_history.correlator import (
    AuditCorrelationItem,
    CorrelatedFlow,
    FlowRunExecution,
    OutboundTriggerItem,
)

_CACHE_FILE = Path(".ah_results_cache.json")
_CLIENTDATA_CACHE_FILE = Path(".ah_clientdata_cache.json")
_HISTORY_DIR = Path("history")


def _ensure_history_dir() -> None:
    _HISTORY_DIR.mkdir(exist_ok=True)


def load_clientdata_cache() -> dict[str, str]:
    """Load cached workflow clientdata JSON strings from disk."""
    if not _CLIENTDATA_CACHE_FILE.exists():
        return {}
    try:
        with open(_CLIENTDATA_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logging.debug("Could not read clientdata disk cache: %s", exc)
        return {}


def save_clientdata_cache(cache: dict[str, str]) -> None:
    """Save workflow clientdata JSON strings to disk cache."""
    try:
        with open(_CLIENTDATA_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as exc:
        logging.debug("Could not write clientdata disk cache: %s", exc)


# ---------------------------------------------------------------------------
# Dataclass serialization & deserialization helpers
# ---------------------------------------------------------------------------

def _serialize_obj(obj: Any) -> Any:
    """Recursively convert dataclasses, datetimes, sets to JSON-serializable types."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize_obj(v) for k, v in asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _serialize_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_obj(x) for x in obj]
    return obj


def _parse_dt(val: Any) -> datetime | None:
    """Parse string or datetime to timezone-aware datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            pdt = pd.to_datetime(val).to_pydatetime()
            return pdt if pdt.tzinfo else pdt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _deserialize_flow(d: Any) -> CorrelatedFlow | None:
    if not d or not isinstance(d, dict):
        return d if isinstance(d, CorrelatedFlow) else None
    return CorrelatedFlow(
        workflow_id=d.get("workflow_id", ""),
        name=d.get("name", ""),
        category=int(d.get("category", 5)),
        category_label=d.get("category_label", ""),
        state_code=int(d.get("state_code", 1)),
        primary_entity=d.get("primary_entity", ""),
        modified_on=d.get("modified_on"),
        description=d.get("description", ""),
        triggers_on_entity=bool(d.get("triggers_on_entity", False)),
        trigger_events=d.get("trigger_events", []) or [],
        trigger_attributes=d.get("trigger_attributes", []) or [],
        trigger_filter_rule=d.get("trigger_filter_rule", ""),
        trigger_conditions=d.get("trigger_conditions", []) or [],
        modifies_attributes=set(d.get("modifies_attributes", []) or []),
        action_details=d.get("action_details", []) or [],
        action_catalog=d.get("action_catalog", {}) or {},
        raw_definition=d.get("raw_definition", {}) or {},
    )


def _deserialize_run(d: Any) -> FlowRunExecution | None:
    if not d or not isinstance(d, dict):
        return d if isinstance(d, FlowRunExecution) else None
    started = _parse_dt(d.get("started_on")) or datetime.now(timezone.utc)
    completed = _parse_dt(d.get("completed_on"))
    return FlowRunExecution(
        run_id=d.get("run_id", ""),
        workflow_id=d.get("workflow_id", ""),
        flow_name=d.get("flow_name", ""),
        started_on=started,
        completed_on=completed,
        status=d.get("status", "Unknown"),
        source=d.get("source", "flowsessions"),
        duration_str=d.get("duration_str", ""),
        error_message=d.get("error_message", ""),
        maker_url=d.get("maker_url", ""),
        arm_resource_id=d.get("arm_resource_id", ""),
        trigger_data=d.get("trigger_data", {}) or {},
    )


def deserialize_correlation_item(d: Any) -> AuditCorrelationItem:
    """Reconstruct an AuditCorrelationItem instance from a serialized dict."""
    if isinstance(d, AuditCorrelationItem):
        return d
    if not isinstance(d, dict):
        return d

    ts = _parse_dt(d.get("timestamp")) or datetime.now(timezone.utc)
    matched_flow = _deserialize_flow(d.get("matched_flow"))
    matched_run = _deserialize_run(d.get("matched_run"))
    candidate_flows = [
        cf for cf in (_deserialize_flow(f) for f in (d.get("candidate_flows") or [])) if cf
    ]

    return AuditCorrelationItem(
        audit_id=str(d.get("audit_id", "")),
        timestamp=ts,
        changed_date_str=str(d.get("changed_date_str", "")),
        user_name=str(d.get("user_name", "")),
        is_automation=bool(d.get("is_automation", False)),
        event=str(d.get("event", "")),
        changed_field=str(d.get("changed_field", "")),
        changed_field_logical=str(d.get("changed_field_logical", "")),
        old_value=d.get("old_value"),
        new_value=d.get("new_value"),
        transaction_id=str(d.get("transaction_id", "")),
        extra_info=str(d.get("extra_info", "")),
        matched_flow=matched_flow,
        matched_run=matched_run,
        candidate_flows=candidate_flows,
        confidence=str(d.get("confidence", "Uncorrelated")),
        confidence_badge=str(d.get("confidence_badge", "⚪")),
        attribution_summary=str(d.get("attribution_summary", "")),
        process_url=str(d.get("process_url", "")),
        process_link_label=str(d.get("process_link_label", "")),
        flow_history_url=str(d.get("flow_history_url", "")),
        flow_history_label=str(d.get("flow_history_label", "")),
    )


def deserialize_outbound_item(d: Any) -> OutboundTriggerItem:
    """Reconstruct an OutboundTriggerItem instance from a serialized dict."""
    if isinstance(d, OutboundTriggerItem):
        return d
    if not isinstance(d, dict):
        return d

    ts = _parse_dt(d.get("timestamp")) or datetime.now(timezone.utc)
    started = _parse_dt(d.get("started_on"))
    matched_run = _deserialize_run(d.get("matched_run")) if d.get("matched_run") else None

    return OutboundTriggerItem(
        audit_id=str(d.get("audit_id", "")),
        timestamp=ts,
        changed_date_str=str(d.get("changed_date_str", "")),
        user_name=str(d.get("user_name", "")),
        event=str(d.get("event", "Update")),
        changed_fields=d.get("changed_fields", []) or [],
        changed_field_labels=d.get("changed_field_labels", []) or [],
        old_values=d.get("old_values", {}) or {},
        new_values=d.get("new_values", {}) or {},
        transaction_id=str(d.get("transaction_id", "")),
        flow_id=str(d.get("flow_id", "")),
        flow_name=str(d.get("flow_name", "")),
        category=int(d.get("category", 5)),
        category_label=str(d.get("category_label", "")),
        is_active=bool(d.get("is_active", True)),
        state_label=str(d.get("state_label", "Active")),
        modified_on=d.get("modified_on"),
        trigger_events=d.get("trigger_events", []) or [],
        trigger_attributes=d.get("trigger_attributes", []) or [],
        matched_columns=d.get("matched_columns", []) or [],
        filter_expression=str(d.get("filter_expression", "")),
        trigger_conditions=d.get("trigger_conditions", []) or [],
        qualification_status=str(d.get("qualification_status", "Qualified")),
        status_badge=str(d.get("status_badge", "🟢")),
        status_reason=str(d.get("status_reason", "")),
        matched_run=matched_run,
        run_status=str(d.get("run_status", "")),
        run_id=str(d.get("run_id", "")),
        started_on=started,
        duration_str=str(d.get("duration_str", "")),
        process_url=str(d.get("process_url", "")),
        flow_history_url=str(d.get("flow_history_url", "")),
    )


# ---------------------------------------------------------------------------
# Temp cache (auto-save / restore between sessions)
# ---------------------------------------------------------------------------

def save_results(data: dict) -> bool:
    """Persist active audit session data to the temp cache file."""
    try:
        payload = _serialize_obj(data)
        _CACHE_FILE.write_text(
            json.dumps(payload, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logging.error("ah: save_results failed: %s", exc)
        return False


def load_results() -> dict:
    """Load the temp cache, returning an empty dict if unavailable."""
    try:
        if _CACHE_FILE.exists():
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if "correlation_results" in raw and isinstance(raw["correlation_results"], list):
                    raw["correlation_results"] = [
                        deserialize_correlation_item(x) for x in raw["correlation_results"]
                    ]
                if "outbound_results" in raw and isinstance(raw["outbound_results"], list):
                    raw["outbound_results"] = [
                        deserialize_outbound_item(x) for x in raw["outbound_results"]
                    ]
                return raw
    except Exception as exc:
        logging.error("ah: load_results failed: %s", exc)
    return {}


def clear_results() -> None:
    """Delete the temp cache file."""
    try:
        _CACHE_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logging.error("ah: clear_results failed: %s", exc)


# ---------------------------------------------------------------------------
# Named history
# ---------------------------------------------------------------------------

def save_history(
    data: dict,
    name: str = "",
    remark: str = "",
) -> str | None:
    """Save active audit session data as a named history record.

    Returns the filename (stem only) on success, None on failure.
    """
    _ensure_history_dir()
    slug = (name.strip().replace(" ", "_") or "unnamed")[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ah_history_{ts}_{slug}.json"
    path = _HISTORY_DIR / filename

    audit_data = data.get("audit_data", [])
    corr_results = data.get("correlation_results", [])
    entity_name = data.get("entity_display_name") or data.get("entity_logical_name", "")
    record_label = data.get("record_label") or data.get("record_id", "")

    display_name = name.strip() or f"{entity_name} ({record_label})"

    step5_dur = data.get("step5_duration")
    payload = {
        "name": display_name,
        "remark": remark.strip(),
        "timestamp": datetime.now().isoformat(),
        "entity_logical_name": data.get("entity_logical_name", ""),
        "entity_display_name": data.get("entity_display_name", ""),
        "record_id": data.get("record_id", ""),
        "record_label": record_label,
        "audit_count": len(audit_data),
        "correlation_count": len(corr_results),
        "step5_duration": step5_dur,
        "data": _serialize_obj(data),
    }

    try:
        path.write_text(
            json.dumps(payload, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return filename
    except Exception as exc:
        logging.error("ah: save_history failed: %s", exc)
        return None


def list_history() -> list[dict]:
    """Return summary metadata for all saved audit history records (newest first)."""
    _ensure_history_dir()
    records: list[dict] = []
    for p in sorted(_HISTORY_DIR.glob("ah_history_*.json"), reverse=True):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            dur = raw.get("step5_duration")
            if dur is None:
                dur = raw.get("data", {}).get("step5_duration")
            dur_display = (
                f"{dur:.1f}s"
                if isinstance(dur, (int, float)) and dur < 60
                else (f"{int(dur//60)}m {int(dur%60)}s" if isinstance(dur, (int, float)) else "—")
            )
            records.append(
                {
                    "filename": p.name,
                    "name": raw.get("name", ""),
                    "remark": raw.get("remark", ""),
                    "timestamp": raw.get("timestamp", ""),
                    "table": raw.get("entity_display_name") or raw.get("entity_logical_name", "-"),
                    "record_label": raw.get("record_label", "-"),
                    "audit_count": raw.get(
                        "audit_count", len(raw.get("data", {}).get("audit_data", []))
                    ),
                    "correlation_count": raw.get(
                        "correlation_count", len(raw.get("data", {}).get("correlation_results", []))
                    ),
                    "step5_duration": dur_display,
                }
            )
        except Exception as exc:
            logging.warning("ah: list_history: cannot parse %s — %s", p.name, exc)
    return records


def load_history(filename: str) -> tuple[str, dict]:
    """Load a history record by filename.

    Returns (remark, session_data). Raises FileNotFoundError or Exception on failure.
    """
    path = _HISTORY_DIR / filename
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("data", {})
    if "correlation_results" in data and isinstance(data["correlation_results"], list):
        data["correlation_results"] = [
            deserialize_correlation_item(x) for x in data["correlation_results"]
        ]
    if "outbound_results" in data and isinstance(data["outbound_results"], list):
        data["outbound_results"] = [
            deserialize_outbound_item(x) for x in data["outbound_results"]
        ]
    return raw.get("remark", ""), data


def update_history_remark(filename: str, new_remark: str) -> bool:
    """Update the remark field of an existing history record."""
    path = _HISTORY_DIR / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["remark"] = new_remark.strip()
        path.write_text(
            json.dumps(raw, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logging.error("ah: update_history_remark failed: %s", exc)
        return False


def delete_history(filename: str) -> bool:
    """Delete a history file."""
    path = _HISTORY_DIR / filename
    try:
        path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        logging.error("ah: delete_history failed: %s", exc)
        return False
