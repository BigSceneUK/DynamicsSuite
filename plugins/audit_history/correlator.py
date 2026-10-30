"""Process and Cloud Flow correlation engine for Dataverse Audit History.

Analyzes audit mutations (especially by # PowerAutomate or automated services)
and correlates them with:
1. Candidate Cloud Flows & Classic Workflows (via action and trigger definitions in clientdata)
2. Exact Flow Runs (via Dataverse flowruns elastic table, flowsessions, and asyncoperations)
3. Confidence-ranked attribution (Exact Run Match, Probable Session Match, Candidate Flow, User Action)
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import threading
import urllib.parse
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import dateutil.parser


# Action code -> human-readable label
ACTION_MAP: dict[int, str] = {
    1: "Create",
    2: "Update",
    3: "Delete",
    4: "Activate",
    5: "Deactivate",
    10: "Assign",
    11: "Share",
    13: "Unshare",
    14: "Link Entities",
    15: "Unlink Entities",
    16: "Win",
    17: "Lose",
    20: "Audit Enabled",
    21: "Audit Disabled",
    100: "Attribute Change",
    101: "Internal Info",
}

CATEGORY_MAP: dict[int, str] = {
    0: "Classic Workflow",
    1: "Custom Activity",
    2: "Business Rule",
    3: "Action",
    4: "Business Process Flow",
    5: "Modern Cloud Flow",
    6: "Azure Logic App",
    7: "Power Pages / Portal Form",
}

_URI_ENCODE_RE = re.compile(
    r"@\{encodeURIComponent\((?:encodeURIComponent\()?['\"]([^'\"]+)['\"]\)?\)\}",
    re.IGNORECASE,
)
_TABLE_PATH_RE = re.compile(
    r"(?:/tables/|/api/data/v\d+\.\d+/)(?:@\{encodeURIComponent\((?:encodeURIComponent\()?['\"]([^'\"]+)['\"]\)?\)\}|([^/?&()]+))",
    re.IGNORECASE,
)


def _clean_table_name(raw: Any) -> str:
    """Normalize extracted entity/table names from Logic App and Cloud Flow paths."""
    if not raw or not isinstance(raw, str):
        return ""
    val = raw.strip()
    match = _URI_ENCODE_RE.search(val)
    if match:
        val = match.group(1)
    try:
        val = urllib.parse.unquote(val)
    except Exception:
        pass
    val = val.strip("@{}'\"")
    return val.lower()


def _is_entity_match(ent: str, target: str, trig_path: str = "") -> bool:
    """Robustly checks whether an extracted entity name or trigger path matches target table."""
    if not target:
        return False
    norm_ent = (ent or "").strip().lower()
    norm_target = target.strip().lower()

    if norm_ent and norm_ent == norm_target:
        return True

    if norm_ent:
        # Standard plural (s, es)
        if norm_ent in (f"{norm_target}s", f"{norm_target}es") or norm_target in (f"{norm_ent}s", f"{norm_ent}es"):
            return True
        # Irregular Latin / English plural (e.g. opportunity -> opportunities, company -> companies)
        if norm_target.endswith("y") and norm_ent == f"{norm_target[:-1]}ies":
            return True
        if norm_ent.endswith("y") and norm_target == f"{norm_ent[:-1]}ies":
            return True
        # Common stem equality
        if norm_ent.rstrip("s") == norm_target.rstrip("s"):
            return True

    if trig_path:
        clean_path = _clean_table_name(trig_path).lower()
        if norm_target in clean_path or (norm_target.endswith("y") and f"{norm_target[:-1]}ies" in clean_path):
            return True
        if f"tables/{norm_target}" in trig_path.lower():
            return True
        m = _TABLE_PATH_RE.search(trig_path)
        if m:
            extracted = _clean_table_name(m.group(1) or m.group(2)).lower()
            if extracted and _is_entity_match(extracted, norm_target):
                return True
    return False


@dataclass
class CorrelatedFlow:
    """Represents a discovered workflow/flow relevant to the target entity."""

    workflow_id: str
    name: str
    category: int  # 5 = Cloud Flow, 0 = Classic Workflow, etc.
    category_label: str
    state_code: int  # 1 = Active, 0 = Draft
    primary_entity: str
    modified_on: Optional[str] = None
    description: str = ""
    triggers_on_entity: bool = False
    trigger_events: List[str] = field(default_factory=list)
    trigger_attributes: List[str] = field(default_factory=list)
    trigger_filter_rule: str = ""
    trigger_conditions: List[str] = field(default_factory=list)
    modifies_attributes: Set[str] = field(default_factory=set)
    action_details: List[Dict[str, Any]] = field(default_factory=list)
    # action name -> {"type", "operation_id", "method", "targets_entity"} for EVERY action in the definition
    action_catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    raw_definition: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowRunExecution:
    """Represents an execution instance from flowruns, flowsessions, or asyncoperations."""

    run_id: str
    workflow_id: str
    flow_name: str
    started_on: datetime
    completed_on: Optional[datetime] = None
    status: str = "Unknown"  # Succeeded, Failed, Cancelled, Running
    source: str = "flowsessions"  # "flowruns", "flowsessions", "asyncoperations"
    duration_str: str = ""
    error_message: str = ""
    maker_url: str = ""
    arm_resource_id: str = ""
    trigger_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditCorrelationItem:
    """An individual audit row correlated with flow/process attribution."""

    audit_id: str
    timestamp: datetime
    changed_date_str: str
    user_name: str
    is_automation: bool
    event: str
    changed_field: str
    changed_field_logical: str
    old_value: Any
    new_value: Any
    transaction_id: str = ""
    extra_info: str = ""
    matched_flow: Optional[CorrelatedFlow] = None
    matched_run: Optional[FlowRunExecution] = None
    candidate_flows: List[CorrelatedFlow] = field(default_factory=list)
    confidence: str = "Uncorrelated"  # Exact Run Match, Probable Session Match, Candidate Flow, User Action
    confidence_badge: str = "⚪"
    attribution_summary: str = ""
    process_url: str = ""
    process_link_label: str = ""
    flow_history_url: str = ""
    flow_history_label: str = ""


@dataclass
class GroupedAuditExecution:
    """Represents a set of field mutations caused by the same process/flow execution."""

    group_id: str
    timestamp: datetime
    changed_date_str: str
    user_name: str
    is_automation: bool
    event: str
    transaction_id: str
    flow_name: str
    process_url: str
    process_link_label: str
    flow_history_url: str = ""
    flow_history_label: str = ""
    run_id: str = ""
    started_on: Optional[datetime] = None
    completed_on: Optional[datetime] = None
    confidence: str = "Uncorrelated"
    confidence_badge: str = "⚪"
    run_status: str = "-"
    duration_str: str = ""
    attribution_summary: str = ""
    fields_changed: List[Dict[str, Any]] = field(default_factory=list)
    changed_field_lines: str = ""
    old_value_lines: str = ""
    new_value_lines: str = ""
    changes_summary: str = ""
    field_count: int = 0
    matched_flow: Optional[CorrelatedFlow] = None
    matched_run: Optional[FlowRunExecution] = None


@dataclass
class OutboundTriggerItem:
    """Represents an automation triggered (or eligible to be triggered) by a specific audit update."""

    audit_id: str
    timestamp: datetime
    changed_date_str: str
    user_name: str
    event: str  # Update, Create, Delete, etc.
    changed_fields: List[str]  # Logical names of fields changed in this audit mutation
    changed_field_labels: List[str]
    old_values: Dict[str, Any]
    new_values: Dict[str, Any]
    transaction_id: str

    flow_id: str
    flow_name: str
    category: int  # 5 = Cloud Flow, 0 = Classic Workflow, 6 = Logic App, etc.
    category_label: str
    is_active: bool  # state_code == 1
    state_label: str  # "Active" or "🔴 Turned Off / Draft"
    modified_on: Optional[str] = None

    trigger_events: List[str] = field(default_factory=list)
    trigger_attributes: List[str] = field(default_factory=list)
    matched_columns: List[str] = field(default_factory=list)
    filter_expression: str = ""
    trigger_conditions: List[str] = field(default_factory=list)

    qualification_status: str = "Qualified"
    status_badge: str = "🟢"
    status_reason: str = ""

    # Downstream run link if found
    matched_run: Optional[FlowRunExecution] = None
    run_status: str = ""
    run_id: str = ""
    started_on: Optional[datetime] = None
    duration_str: str = ""
    process_url: str = ""
    flow_history_url: str = ""


@dataclass
class GroupedOutboundExecution:
    """Represents the outbound automations triggered by a specific audit transaction."""

    group_id: str = ""
    timestamp: Optional[datetime] = None
    changed_date_str: str = ""
    user_name: str = ""
    event: str = "Update"
    transaction_id: str = ""
    changed_fields_summary: str = ""
    changes_summary: str = ""
    field_count: int = 0
    fields_changed: List[Dict[str, Any]] = field(default_factory=list)

    triggers: List[OutboundTriggerItem] = field(default_factory=list)

    triggered_count: int = 0
    qualified_count: int = 0
    suppressed_turned_off_count: int = 0
    conditional_count: int = 0
    has_turned_off_alert: bool = False

    def __post_init__(self):
        if not self.changes_summary and self.changed_fields_summary:
            self.changes_summary = self.changed_fields_summary
        elif not self.changed_fields_summary and self.changes_summary:
            self.changed_fields_summary = self.changes_summary

    @property
    def suppressed_count(self) -> int:
        return self.suppressed_turned_off_count

    @suppressed_count.setter
    def suppressed_count(self, val: int) -> None:
        self.suppressed_turned_off_count = val


def clean_guid(val: Any) -> str:
    """Strip braces, quotes, and whitespace from a GUID string."""
    if not val:
        return ""
    return str(val).strip("{}() '\"").lower()


def is_automation_user(user_name: str) -> bool:
    """Determine if the changed_by user is PowerAutomate, Portal, or a system/service account."""
    if not user_name:
        return True
    u_lower = user_name.lower()
    return (
        "# powerautomate" in u_lower
        or "powerautomate" in u_lower
        or "system" in u_lower
        or "crm portals" in u_lower
        or "power pages" in u_lower
        or "powerpages" in u_lower
        or "portal" in u_lower
        or "service" in u_lower
        or "flow" in u_lower
        or "app user" in u_lower
        or u_lower.startswith("#")
    )


def _extract_payload_keys(data: Any, max_depth: int = 4) -> Set[str]:
    """Recursively extract property keys from JSON payloads, dicts, and stringified JSON bodies."""
    keys: Set[str] = set()
    if max_depth <= 0 or data is None:
        return keys

    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8", errors="replace")
        except Exception:
            return keys

    if isinstance(data, str):
        s = data.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                parsed = json.loads(s)
                return _extract_payload_keys(parsed, max_depth - 1)
            except Exception:
                pass
        return keys

    if isinstance(data, list):
        for item in data[:25]:
            keys.update(_extract_payload_keys(item, max_depth - 1))
        return keys

    if isinstance(data, dict):
        for k, v in data.items():
            k_str = str(k).strip()
            k_lower = k_str.lower()
            if not k_str.startswith("@") and not k_str.startswith("$"):
                if k_lower.startswith("item/"):
                    keys.add(k_lower[5:])
                elif "/" not in k_str:
                    keys.add(k_lower)

            # Recurse into nested structures commonly holding incoming/outgoing payloads
            if k_lower in ("body", "item", "parameters", "value", "inputs", "outputs", "content", "attributes"):
                keys.update(_extract_payload_keys(v, max_depth - 1))
            elif isinstance(v, (dict, list)):
                keys.update(_extract_payload_keys(v, max_depth - 1))
            elif isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                keys.update(_extract_payload_keys(v, max_depth - 1))

    return keys


# --------------------------------------------------------------------------- #
# Run payload inspection helpers                                              #
# --------------------------------------------------------------------------- #

# Keys produced by connector/HTTP envelopes that are never Dataverse column names
_NOISE_PAYLOAD_KEYS: Set[str] = {
    "host", "connection", "connectionname", "connectionreferencename", "api", "apiid", "operationid",
    "parameters", "path", "method", "body", "headers", "queries", "uri", "url", "authentication",
    "entityname", "entitysetname", "recordid", "iteminternalid", "dataset", "table", "item", "record",
    "inputs", "outputs", "content", "value", "retrypolicy", "x-ms-workflow-name", "content-type",
    "accept", "prefer", "if-match", "odata.etag",
}

# Action types that never write to Dataverse (control flow, data shaping, variables)
_NON_DATA_ACTION_TYPES: Set[str] = {
    "compose", "initializevariable", "setvariable", "incrementvariable", "decrementvariable",
    "appendtoarrayvariable", "appendtostringvariable", "if", "switch", "scope", "foreach", "until",
    "terminate", "response", "parsejson", "query", "select", "table", "join", "wait", "expression",
}

# Action-name prefixes (Power Automate / Logic Apps default naming) for non-data actions
_NON_DATA_NAME_PREFIXES: Tuple[str, ...] = (
    "compose", "initialize_variable", "set_variable", "increment_variable", "decrement_variable",
    "append_to_", "condition", "scope", "apply_to_each", "for_each", "foreach", "do_until", "until",
    "terminate", "response", "parse_json", "filter_array", "create_html_table", "create_csv_table",
    "join", "delay", "switch", "select",
)

_WRITE_NAME_KEYWORDS: Tuple[str, ...] = ("update", "patch", "modify", "create", "upsert", "add_a_new_row", "add_new_row")
_WRITE_OPERATION_KEYWORDS: Tuple[str, ...] = ("update", "create", "patch", "upsert", "post", "put", "merge")
_READ_OPERATION_KEYWORDS: Tuple[str, ...] = ("get", "list", "retrieve", "query")
_WRITE_METHODS: Set[str] = {"patch", "post", "put", "merge"}
_SKIPPED_ACTION_STATUSES: Set[str] = {"skipped", "failed", "timedout", "cancelled", "aborted"}

_ODATA_BIND_RE = re.compile(r'"[^"]*@odata\.bind"\s*:\s*"[^"]*"', re.IGNORECASE)
_GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _payload_text(data: Any) -> str:
    """Serialise a payload once into lower-case text for identifier matching."""
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        return data.lower()
    try:
        return json.dumps(data, default=str, ensure_ascii=False).lower()
    except Exception:
        return str(data).lower()


class _TargetMatcher:
    """Fast, boundary-aware matching of target record identifiers inside run payloads.

    * GUIDs (dashed or 32-hex) are matched as plain substrings (collisions are practically impossible).
    * Purely numeric identifiers (e.g. student numbers) require digit boundaries so they are not
      matched inside timestamps, phone numbers or other ids.
    * Values of ``@odata.bind`` lookup bindings are ignored: a payload that merely *links to* the
      target (e.g. setting a child row's parent lookup) does not prove the run modified the target.
    """

    def __init__(self, tokens: Set[str]) -> None:
        self.substr_tokens: List[str] = []
        boundary_tokens: List[str] = []
        for t in tokens or set():
            t = (t or "").strip().lower()
            if not t:
                continue
            if t.isdigit():
                boundary_tokens.append(re.escape(t))
            else:
                self.substr_tokens.append(t)
        self.boundary_re = re.compile(r"(?<!\d)(?:" + "|".join(boundary_tokens) + r")(?!\d)") if boundary_tokens else None
        self.target_guids: Set[str] = {t for t in self.substr_tokens if _GUID_RE.fullmatch(t)}

    def __bool__(self) -> bool:
        return bool(self.substr_tokens or self.boundary_re)

    def text_matches(self, text: str) -> bool:
        if not text:
            return False
        if "@odata.bind" in text:
            text = _ODATA_BIND_RE.sub("", text)
        if any(tok in text for tok in self.substr_tokens):
            return True
        return bool(self.boundary_re and self.boundary_re.search(text))

    def matches(self, data: Any) -> bool:
        return self.text_matches(_payload_text(data))

    def explicit_other_record(self, data: Any) -> bool:
        """True when a write payload explicitly addresses a *different* record id (recordId / path key)."""
        rec_ids: Set[str] = set()
        if isinstance(data, dict):
            params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
            for k in ("recordId", "recordid", "itemInternalId", "id"):
                v = params.get(k) or data.get(k)
                if isinstance(v, str):
                    rec_ids.update(g.lower() for g in _GUID_RE.findall(v))
            for pk in ("path", "uri"):
                pv = data.get(pk)
                if isinstance(pv, str):
                    for m in re.finditer(r"\(([^)]*)\)", pv):
                        rec_ids.update(g.lower() for g in _GUID_RE.findall(m.group(1)))
        if not rec_ids or not self.target_guids:
            return False
        return not (rec_ids & self.target_guids)


def _extract_written_fields(data: Any) -> Set[str]:
    """Column names written by a write-action request payload (envelope keys removed, lookups normalised)."""
    out: Set[str] = set()
    for k in _extract_payload_keys(data):
        k = k.strip().lower()
        if "@" in k:  # e.g. parentcustomerid_contact@odata.bind
            k = k.split("@", 1)[0]
        if k.startswith("_") and k.endswith("_value"):
            k = k[1:-6]
        if k and k not in _NOISE_PAYLOAD_KEYS:
            out.add(k)
    return out


def _classify_action(act: Dict[str, Any], catalog_entry: Optional[Dict[str, Any]]) -> str:
    """Classify a runtime action as 'skip', 'write_target', 'write', 'read' or 'other'.

    Uses the flow definition (catalog) when available because run-history action records only carry
    the ARM resource type, not the workflow action type. Falls back to default naming conventions.
    """
    props = act.get("properties", {}) if isinstance(act.get("properties"), dict) else {}
    status = str(props.get("status") or "").lower()
    if status in _SKIPPED_ACTION_STATUSES:
        return "skip"

    name = str(act.get("name") or "").lower().replace(" ", "_")
    rt_type = str(act.get("type") or props.get("type") or "").lower()
    if rt_type in _NON_DATA_ACTION_TYPES:
        return "skip"

    if catalog_entry:
        c_type = str(catalog_entry.get("type") or "").lower()
        if c_type in _NON_DATA_ACTION_TYPES:
            return "skip"
        op = str(catalog_entry.get("operation_id") or "").lower()
        method = str(catalog_entry.get("method") or "").lower()
        is_write = method in _WRITE_METHODS or any(kw in op for kw in _WRITE_OPERATION_KEYWORDS)
        if not is_write and not op and not method:
            is_write = any(kw in name for kw in _WRITE_NAME_KEYWORDS)
        if is_write:
            return "write_target" if catalog_entry.get("targets_entity") else "write"
        if method == "get" or any(kw in op for kw in _READ_OPERATION_KEYWORDS):
            return "read"
        return "other"

    if name.startswith(_NON_DATA_NAME_PREFIXES):
        return "skip"
    inline_in = props.get("inputs")
    inline_method = str(inline_in.get("method") or "").lower() if isinstance(inline_in, dict) else ""
    if inline_method in _WRITE_METHODS or any(kw in name for kw in _WRITE_NAME_KEYWORDS):
        return "write"
    if inline_method == "get" or name.startswith(("get_", "list_", "retrieve")):
        return "read"
    return "other"


_ACTION_PRIORITY = {"write_target": 0, "write": 1, "read": 2, "other": 3}


class _BlobFetcher:
    """Memoised, de-duplicated, concurrent downloader for SAS payload links."""

    def __init__(self, executor: concurrent.futures.ThreadPoolExecutor) -> None:
        self._executor = executor
        self._futures: Dict[str, concurrent.futures.Future] = {}
        self._lock = threading.Lock()

    def prefetch(self, fetch_fn: Callable[[str], Any], url: Optional[str]) -> None:
        if not url:
            return
        with self._lock:
            if url not in self._futures:
                self._futures[url] = self._executor.submit(self._safe, fetch_fn, url)

    def get(self, fetch_fn: Callable[[str], Any], url: Optional[str]) -> Any:
        if not url:
            return None
        self.prefetch(fetch_fn, url)
        try:
            return self._futures[url].result()
        except Exception:
            return None

    @staticmethod
    def _safe(fetch_fn: Callable[[str], Any], url: str) -> Any:
        try:
            return fetch_fn(url)
        except Exception as ex:
            logging.debug("Payload link download failed: %s", ex)
            return None


def _link_uri(props: Dict[str, Any], key: str) -> Optional[str]:
    link = props.get(key) if isinstance(props, dict) else None
    return link.get("uri") if isinstance(link, dict) else None


def _inspect_run_payloads(
    trigger: Dict[str, Any],
    actions: List[Dict[str, Any]],
    fetch_fn: Callable[[str], Any],
    fetcher: _BlobFetcher,
    matcher: _TargetMatcher,
    catalog: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Decide whether a run operated on the target record and which columns it wrote.

    Evidence levels (strongest first):
      * ``write``     - a write action's request payload addresses the target record
      * ``trigger``   - the trigger payload contains the target record identifier
      * ``reference`` - only a read/other action references the target (weak; no field evidence)

    Columns are ONLY taken from write-action *request* payloads. Trigger payloads and action outputs
    usually echo the whole row, so harvesting keys from them would mark every column as "updated".
    """
    result: Dict[str, Any] = {
        "status": None, "evidence": "", "action_name": "", "updated_fields": set(),
        "has_payloads": False, "actions_inspected": 0, "trigger_matched": False,
    }
    trigger = trigger if isinstance(trigger, dict) else {}

    # Kick off every request-payload download up-front so they run concurrently.
    trig_links = [_link_uri(trigger, "outputsLink"), _link_uri(trigger, "inputsLink")]
    for u in trig_links:
        fetcher.prefetch(fetch_fn, u)

    classified: List[Tuple[int, Dict[str, Any], str]] = []
    for idx, act in enumerate(actions or []):
        if not isinstance(act, dict):
            continue
        cls = _classify_action(act, catalog.get(str(act.get("name") or "")) if catalog else None)
        if cls == "skip":
            continue
        classified.append((idx, act, cls))
        props = act.get("properties", {}) if isinstance(act.get("properties"), dict) else {}
        fetcher.prefetch(fetch_fn, _link_uri(props, "inputsLink"))
    classified.sort(key=lambda x: (_ACTION_PRIORITY.get(x[2], 9), x[0]))

    # 1. Trigger
    trig_matched = False
    inline_trig = trigger.get("outputs") or trigger.get("inputs")
    if inline_trig:
        result["has_payloads"] = True
        trig_matched = matcher.matches(inline_trig)
    if not trig_matched:
        for u in trig_links:
            data = fetcher.get(fetch_fn, u)
            if data:
                result["has_payloads"] = True
                if matcher.matches(data):
                    trig_matched = True
                    break
    result["trigger_matched"] = trig_matched

    # 2. Actions (target-table writes first, then other writes, reads, others)
    write_matched = False
    target_write_named = False
    weak_name = ""
    for _idx, act, cls in classified:
        props = act.get("properties", {}) if isinstance(act.get("properties"), dict) else {}
        act_name = str(act.get("name") or "Action")
        result["actions_inspected"] += 1
        inline_in = props.get("inputs")
        link_in = fetcher.get(fetch_fn, _link_uri(props, "inputsLink"))
        in_payloads = [p for p in (inline_in, link_in) if p]
        if in_payloads:
            result["has_payloads"] = True
        in_match = any(matcher.matches(p) for p in in_payloads)

        if cls in ("write_target", "write"):
            attributed = in_match
            if not attributed and trig_matched and in_payloads:
                # Trigger-owned run: attribute writes unless they explicitly address another record
                attributed = not any(matcher.explicit_other_record(p) for p in in_payloads)
            if not attributed:
                # e.g. Create: the new row id only appears in the response
                out_data = props.get("outputs") or fetcher.get(fetch_fn, _link_uri(props, "outputsLink"))
                if out_data:
                    result["has_payloads"] = True
                    attributed = matcher.matches(out_data)
            if attributed:
                write_matched = True
                for p in in_payloads:
                    result["updated_fields"] |= _extract_written_fields(p)
                if cls == "write_target" and not target_write_named:
                    result["action_name"] = act_name
                    target_write_named = True
                elif not result["action_name"]:
                    result["action_name"] = act_name
        elif in_match and not weak_name:
            weak_name = act_name

    if write_matched:
        result.update(status=True, evidence="write")
    elif trig_matched:
        result.update(status=True, evidence="trigger")
    elif weak_name:
        result.update(status=True, evidence="reference", action_name=weak_name)
    elif result["has_payloads"]:
        result["status"] = False
    return result


def extract_flow_definition(flow_row: Dict[str, Any], target_entity_logical: str) -> CorrelatedFlow:
    """Parse a workflow row and its clientdata JSON to extract trigger & action details."""
    wid = clean_guid(flow_row.get("workflowid"))
    name = flow_row.get("name") or "Unnamed Flow"
    category = flow_row.get("category", 5)
    category_label = CATEGORY_MAP.get(category, f"Category {category}")
    state_code = flow_row.get("statecode", 1)
    primary_entity = (flow_row.get("primaryentity") or "").strip().lower()

    flow_obj = CorrelatedFlow(
        workflow_id=wid,
        name=name,
        category=category,
        category_label=category_label,
        state_code=state_code,
        primary_entity=primary_entity,
        modified_on=flow_row.get("modifiedon"),
        description=flow_row.get("description") or "",
        raw_definition=flow_row,
    )

    client_data_raw = flow_row.get("clientdata")
    data = None
    if isinstance(client_data_raw, dict):
        data = client_data_raw
    elif isinstance(client_data_raw, str):
        try:
            data = json.loads(client_data_raw)
        except Exception:
            data = None
    elif "properties" in flow_row and isinstance(flow_row["properties"], dict):
        data = flow_row["properties"]
    elif "definition" in flow_row and isinstance(flow_row["definition"], dict):
        data = flow_row
    elif "raw_definition" in flow_row and isinstance(flow_row["raw_definition"], dict):
        raw_def = flow_row["raw_definition"]
        data = raw_def.get("properties", {}) or raw_def

    norm_target = target_entity_logical.strip().lower()

    if not data:
        # Classic workflow or empty clientdata
        if primary_entity == norm_target:
            is_ondemand = flow_row.get("ondemand") in (1, True, "1", "true")
            is_subprocess = flow_row.get("subprocess") in (1, True, "1", "true")
            raw_update_attrs = flow_row.get("triggeronupdateattributelist")
            has_create = flow_row.get("triggeroncreate") in (1, True, "1", "true")
            has_delete = flow_row.get("triggerondelete") in (1, True, "1", "true")

            if raw_update_attrs:
                flow_obj.trigger_attributes = [
                    a.strip().lower() for a in str(raw_update_attrs).split(",") if a.strip()
                ]
            if has_create:
                flow_obj.trigger_events.append("Create")
            if has_delete:
                flow_obj.trigger_events.append("Delete")
            if raw_update_attrs:
                flow_obj.trigger_events.append("Update")

            # A classic workflow is an automated trigger on entity mutations only if
            # it explicitly triggers on update/create/delete and is not purely a child subprocess or adhoc on-demand workflow
            if flow_obj.trigger_events or flow_obj.trigger_attributes:
                flow_obj.triggers_on_entity = True
            elif not is_ondemand and not is_subprocess:
                # If neither ondemand nor subprocess was indicated and no trigger attributes were set,
                # check if it triggers on update
                flow_obj.triggers_on_entity = True
                flow_obj.trigger_events.append("Update")
            else:
                flow_obj.triggers_on_entity = False
        return flow_obj

    definition = data.get("properties", {}).get("definition", {}) or data.get("definition", {})
    if not definition:
        if primary_entity == norm_target:
            flow_obj.triggers_on_entity = True
        return flow_obj

    # 1. Parse Triggers
    triggers = definition.get("triggers", {})
    if isinstance(triggers, dict):
        for trig_key, trig_val in triggers.items():
            if not isinstance(trig_val, dict):
                continue
            inputs = trig_val.get("inputs", {}) if isinstance(trig_val, dict) else {}
            params = inputs.get("parameters", {}) if isinstance(inputs, dict) else {}
            queries = inputs.get("queries", {}) if isinstance(inputs, dict) else {}
            host = inputs.get("host", {}) if isinstance(inputs, dict) else {}
            op_id = str(host.get("operationId") or "").lower()

            ent = (
                params.get("entityName")
                or params.get("table")
                or params.get("subscriptionRequest/entityname")
                or params.get("entitySetName")
                or ""
            ).strip().lower()
            trig_path = str(inputs.get("path") or "").lower()

            if not ent and trig_path:
                m = _TABLE_PATH_RE.search(trig_path)
                if m:
                    ent = _clean_table_name(m.group(1) or m.group(2))

            if _is_entity_match(ent, norm_target, trig_path) or (primary_entity and _is_entity_match(primary_entity, norm_target)):
                flow_obj.triggers_on_entity = True
                msg = params.get("subscriptionRequest/message") or params.get("message")
                msg_map = {
                    1: "Create", "1": "Create",
                    2: "Delete", "2": "Delete",
                    3: "Update", "3": "Update",
                    4: "Create or Update", "4": "Create or Update",
                }
                if msg in msg_map:
                    event_name = msg_map[msg]
                elif "addedormodified" in op_id or "neworupdated" in op_id:
                    event_name = "Create or Update"
                elif "added" in op_id:
                    event_name = "Create"
                elif "deleted" in op_id:
                    event_name = "Delete"
                elif "updated" in op_id:
                    event_name = "Update"
                else:
                    event_name = str(msg or "Update")

                if event_name not in flow_obj.trigger_events:
                    flow_obj.trigger_events.append(event_name)

                filter_attrs = (
                    params.get("filterAttributes")
                    or params.get("selectColumns")
                    or params.get("subscriptionRequest/filteringattributes")
                    or params.get("filteringattributes")
                    or queries.get("filteringattributes")
                )
                if filter_attrs:
                    if isinstance(filter_attrs, list):
                        for a in filter_attrs:
                            if str(a).strip().lower() not in flow_obj.trigger_attributes:
                                flow_obj.trigger_attributes.append(str(a).strip().lower())
                    elif isinstance(filter_attrs, str):
                        for a in filter_attrs.split(","):
                            clean_a = a.strip().lower()
                            if clean_a and clean_a not in flow_obj.trigger_attributes:
                                flow_obj.trigger_attributes.append(clean_a)

                filter_expr = (
                    params.get("filterExpression")
                    or params.get("subscriptionRequest/filterexpression")
                    or queries.get("filter")
                    or queries.get("$filter")
                    or ""
                )
                if filter_expr:
                    flow_obj.trigger_filter_rule = str(filter_expr).strip()

                conds = trig_val.get("conditions") or []
                if isinstance(conds, list):
                    for cd in conds:
                        expr = cd.get("expression") if isinstance(cd, dict) else str(cd)
                        if expr and str(expr).strip():
                            expr_str = str(expr).strip()
                            if expr_str not in flow_obj.trigger_conditions:
                                flow_obj.trigger_conditions.append(expr_str)

    # Classic workflow fallback trigger metadata
    if flow_obj.category == 0 and primary_entity == norm_target:
        is_ondemand = flow_row.get("ondemand") in (1, True, "1", "true")
        is_subprocess = flow_row.get("subprocess") in (1, True, "1", "true")
        raw_update_attrs = flow_row.get("triggeronupdateattributelist")
        has_create = flow_row.get("triggeroncreate") in (1, True, "1", "true")
        has_delete = flow_row.get("triggerondelete") in (1, True, "1", "true")

        if raw_update_attrs:
            for a in str(raw_update_attrs).split(","):
                clean_a = a.strip().lower()
                if clean_a and clean_a not in flow_obj.trigger_attributes:
                    flow_obj.trigger_attributes.append(clean_a)
            if "Update" not in flow_obj.trigger_events:
                flow_obj.trigger_events.append("Update")
        if has_create and "Create" not in flow_obj.trigger_events:
            flow_obj.trigger_events.append("Create")
        if has_delete and "Delete" not in flow_obj.trigger_events:
            flow_obj.trigger_events.append("Delete")

        if flow_obj.trigger_events or flow_obj.trigger_attributes:
            flow_obj.triggers_on_entity = True
        elif not is_ondemand and not is_subprocess:
            flow_obj.triggers_on_entity = True
            flow_obj.trigger_events.append("Update")
        else:
            flow_obj.triggers_on_entity = False

    # 2. Parse Actions recursively for Dataverse update operations
    actions = definition.get("actions", {})
    _extract_actions_recursive(actions, norm_target, flow_obj)

    return flow_obj


def _extract_actions_recursive(
    actions_dict: Any, norm_target: str, flow_obj: CorrelatedFlow
) -> None:
    """Walk all actions and nested action blocks (If, Scope, Switch, Foreach)."""
    if not isinstance(actions_dict, dict):
        return

    for act_name, act_val in actions_dict.items():
        if not isinstance(act_val, dict):
            continue

        act_type = act_val.get("type", "")
        inputs = act_val.get("inputs", {})

        # Catalog every action so runtime actions can be classified by real type / operation
        if isinstance(inputs, dict):
            host = inputs.get("host") if isinstance(inputs.get("host"), dict) else {}
            flow_obj.action_catalog[act_name] = {
                "type": str(act_type or ""),
                "operation_id": str(host.get("operationId") or host.get("operationid") or ""),
                "method": str(inputs.get("method") or ""),
                "targets_entity": False,
            }
        else:
            flow_obj.action_catalog[act_name] = {"type": str(act_type or ""), "operation_id": "", "method": "", "targets_entity": False}

        # Check for nested scopes / branches / foreach loops
        for container_key in ("actions", "else"):
            if container_key in act_val and isinstance(act_val[container_key], dict):
                _extract_actions_recursive(act_val[container_key], norm_target, flow_obj)
        if "cases" in act_val and isinstance(act_val["cases"], dict):
            for case_val in act_val["cases"].values():
                if isinstance(case_val, dict) and "actions" in case_val:
                    _extract_actions_recursive(case_val["actions"], norm_target, flow_obj)

        # Inspect Dataverse / CDS operations
        if isinstance(inputs, dict):
            path = str(inputs.get("path") or inputs.get("uri") or "").strip()
            params = inputs.get("parameters", {}) if isinstance(inputs.get("parameters"), dict) else {}
            body = inputs.get("body", {}) if isinstance(inputs.get("body"), dict) else {}

            act_ent = (
                params.get("entityName")
                or params.get("table")
                or params.get("entitySetName")
                or ""
            ).strip().lower()

            if not act_ent and path:
                m = _TABLE_PATH_RE.search(path)
                if m:
                    act_ent = _clean_table_name(m.group(1) or m.group(2))
                elif "tables/" in path.lower():
                    try:
                        act_ent = path.lower().split("tables/")[1].split("/")[0].split("?")[0].strip()
                    except Exception:
                        pass

            path_lower = path.lower()
            if (
                act_ent in (norm_target, f"{norm_target}s")
                or f"tables/{norm_target}" in path_lower
                or f"tables/{norm_target}s" in path_lower
                or ("/api/data/" in path_lower and norm_target in act_ent)
            ):
                # This action modifies or interacts with the target table!
                modified_fields: Set[str] = set()

                # 1. From parameters dict (Cloud Flows)
                for p_key, p_val in params.items():
                    k_clean = p_key.lower()
                    if k_clean.startswith("item/"):
                        modified_fields.add(k_clean[5:])
                    elif k_clean == "item" and isinstance(p_val, dict):
                        for ik in p_val.keys():
                            modified_fields.add(ik.lower())
                    elif k_clean == "record" and isinstance(p_val, dict):
                        for rk in p_val.keys():
                            modified_fields.add(rk.lower())
                    elif k_clean not in (
                        "entityname",
                        "table",
                        "entitysetname",
                        "recordid",
                        "iteminternalid",
                        "$select",
                        "$filter",
                        "$expand",
                    ):
                        if not k_clean.startswith("@") and not k_clean.startswith("$"):
                            modified_fields.add(k_clean)

                # 2. From body dict (Logic Apps PATCH/POST operations)
                if isinstance(body, dict):
                    for b_key in body.keys():
                        bk_clean = b_key.lower()
                        if not bk_clean.startswith("@") and not bk_clean.startswith("$"):
                            modified_fields.add(bk_clean)

                # 3. From item/record at top of inputs
                for top_key in ("item", "record"):
                    if top_key in inputs and isinstance(inputs[top_key], dict):
                        for ik in inputs[top_key].keys():
                            modified_fields.add(ik.lower())

                record_expr = str(params.get("recordId") or params.get("itemInternalId") or "")
                if not record_expr and "(" in path and ")" in path:
                    record_expr = path

                flow_obj.modifies_attributes.update(modified_fields)
                flow_obj.action_catalog[act_name]["targets_entity"] = True
                flow_obj.action_details.append(
                    {
                        "action_name": act_name,
                        "operation": act_type,
                        "entity": act_ent or norm_target,
                        "record_id_expr": record_expr,
                        "modified_fields": list(modified_fields),
                    }
                )


def parse_condition_clauses(
    filter_rule: str,
    trigger_conditions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Extract individual field-level condition clauses from OData filter expressions
    and Power Automate trigger condition expressions.
    Handles single and multi-field conditions, with operators: eq, ne, gt, ge, lt, le.
    Returns list of dicts with keys: col, field, op, target, val, source.
    """
    clauses: List[Dict[str, Any]] = []

    # 1. Parse OData filter rule (e.g. 'nuit_reinstateoffer eq true and statuscode eq 1')
    if filter_rule and filter_rule.strip():
        odata_pattern = re.compile(
            r"([a-zA-Z0-9_]+)\s+(eq|ne|gt|ge|lt|le)\s+('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^\s()]+)",
            re.IGNORECASE,
        )
        for m in odata_pattern.finditer(filter_rule):
            col = m.group(1).lower().strip()
            op = m.group(2).lower().strip()
            val = m.group(3).strip()
            clean_val: Any = val
            if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                clean_val = val[1:-1]
            elif val.lower() == "true":
                clean_val = True
            elif val.lower() == "false":
                clean_val = False
            elif val.isdigit():
                clean_val = int(val)
            else:
                try:
                    clean_val = float(val)
                except ValueError:
                    clean_val = val

            if col and not col.startswith("@"):
                col_clean = col[1:-6] if col.startswith("_") and col.endswith("_value") else col
                clauses.append({"col": col_clean, "field": col_clean, "raw_col": col, "op": op, "target": val, "val": clean_val, "source": "filter_rule"})

    # 2. Parse Power Automate trigger conditions
    # (e.g. "@equals(triggerOutputs()?['body/nuit_reinstateoffer'], true)")
    conds_to_check = list(trigger_conditions or [])
    if filter_rule and ("@" in filter_rule or "equals(" in filter_rule.lower()):
        conds_to_check.append(filter_rule)

    if conds_to_check:
        pa_pattern = re.compile(
            r"(equals|notequals|greater|greaterorequals|less|lessorequals|empty)\s*\(\s*(?:triggerOutputs\(\)\?\[['\"]body/([a-zA-Z0-9_]+)['\"]\]|triggerBody\(\)\?\[['\"]([a-zA-Z0-9_]+)['\"]\]|body\(['\"][^'\"]+['\"]\)\?\[['\"]([a-zA-Z0-9_]+)['\"]\])(?:\s*,\s*([^)]+))?",
            re.IGNORECASE,
        )
        op_map = {
            "equals": "eq", "notequals": "ne",
            "greater": "gt", "greaterorequals": "ge",
            "less": "lt", "lessorequals": "le",
            "empty": "eq",
        }
        for cond in conds_to_check:
            cond_str = str(cond or "").strip()
            if not cond_str:
                continue
            for m in pa_pattern.finditer(cond_str):
                fn_name = m.group(1).lower()
                col = (m.group(2) or m.group(3) or m.group(4) or "").lower().strip()
                val = (m.group(5) or ("null" if fn_name == "empty" else "")).strip()
                clean_val = val
                if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                    clean_val = val[1:-1]
                elif val.lower() == "true":
                    clean_val = True
                elif val.lower() == "false":
                    clean_val = False
                elif val.isdigit():
                    clean_val = int(val)
                else:
                    try:
                        clean_val = float(val)
                    except ValueError:
                        clean_val = val
                op = op_map.get(fn_name, "eq")
                if col:
                    col_clean = col[1:-6] if col.startswith("_") and col.endswith("_value") else col
                    clauses.append({"col": col_clean, "field": col_clean, "raw_col": col, "op": op, "target": val, "val": clean_val, "source": "trigger_condition"})

    return clauses


def evaluate_condition_rules(
    filter_rule: str,
    trigger_conditions: Optional[List[str]],
    changed_fields: Set[str],
    new_values: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Evaluates trigger condition clauses against mutated fields and new values.
    Returns whether the condition was evaluated, satisfied, violated, or conditional.
    """
    clauses = parse_condition_clauses(filter_rule, trigger_conditions)
    if not clauses:
        return {
            "has_rules": False,
            "evaluated": False,
            "is_satisfied": True,
            "is_partially_satisfied": False,
            "is_violated": False,
            "clauses": [],
            "referenced_fields": [],
            "touches_mutation_fields": False,
            "unmutated_requirements": [],
            "evaluation_summary": "",
        }

    ref_fields = sorted(list({c["col"] for c in clauses}))
    # Touches mutation if either logical field or raw field name is in changed_fields
    touches_mutation = bool(set(ref_fields).intersection(changed_fields))
    if not touches_mutation:
        for c in clauses:
            raw_c = c.get("raw_col")
            if raw_c and raw_c in changed_fields:
                touches_mutation = True
                break

    evaluated_clauses = []
    has_violations = False
    all_satisfied = True
    unmutated_reqs = []
    clause_summaries = []

    for c in clauses:
        col = c["col"]
        raw_col = c.get("raw_col", col)
        op = c["op"]
        target = c["target"]
        target_clean = str(target).strip("'\" ").lower()

        is_in_mutation = (col in changed_fields or raw_col in changed_fields or f"_{col}_value" in changed_fields)

        if not is_in_mutation:
            # Column was not changed in this mutation -> depends on existing state in Dataverse
            unmutated_reqs.append(f"`{col} {op} {target}`")
            evaluated_clauses.append({
                "col": col,
                "field": col,
                "op": op,
                "target": target,
                "in_mutation": False,
                "matched": None,
                "summary": f"Requires existing state `{col} {op} {target}`",
            })
            clause_summaries.append(f"`{col}`: requires {op} {target}")
            continue

        actual_raw = new_values.get(col)
        if actual_raw is None:
            actual_raw = new_values.get(raw_col)
        if actual_raw is None:
            actual_raw = new_values.get(f"_{col}_value")
        if isinstance(actual_raw, dict):
            if "Value" in actual_raw:
                actual_raw = actual_raw["Value"]
            elif "Id" in actual_raw:
                actual_raw = actual_raw["Id"]

        # Evaluate comparison
        matched = False
        # 1. Boolean check
        if target_clean in ("true", "false"):
            target_b = (target_clean == "true")
            actual_b = actual_raw in (True, "true", "True", 1, "1") if actual_raw is not None else False
            matched = (actual_b == target_b) if op == "eq" else (actual_b != target_b)
        # 2. Null check
        elif target_clean in ("null", "none"):
            is_null = (actual_raw is None or actual_raw == "" or str(actual_raw).lower() in ("null", "none"))
            matched = is_null if op == "eq" else not is_null
        # 3. GUID check
        elif clean_guid(target_clean) and actual_raw is not None and clean_guid(str(actual_raw)):
            g_target = clean_guid(target_clean)
            g_actual = clean_guid(str(actual_raw))
            matched = (g_actual == g_target) if op == "eq" else (g_actual != g_target)
        # 4. Numeric check
        else:
            try:
                t_num = float(target_clean)
                a_num = float(actual_raw)
                if op == "eq": matched = (a_num == t_num)
                elif op == "ne": matched = (a_num != t_num)
                elif op == "gt": matched = (a_num > t_num)
                elif op == "ge": matched = (a_num >= t_num)
                elif op == "lt": matched = (a_num < t_num)
                elif op == "le": matched = (a_num <= t_num)
                else: matched = (a_num == t_num)
            except (ValueError, TypeError):
                # 5. String comparison
                a_str = str(actual_raw).strip("'\" ").lower() if actual_raw is not None else ""
                if op == "eq": matched = (a_str == target_clean)
                elif op == "ne": matched = (a_str != target_clean)
                else: matched = (a_str == target_clean)

        verdict_icon = "✓" if matched else "✗"
        clause_summaries.append(f"`{col}`: {actual_raw} ({'Satisfied ' + verdict_icon if matched else 'Violated ' + verdict_icon})")

        evaluated_clauses.append({
            "col": col,
            "field": col,
            "op": op,
            "target": target,
            "in_mutation": True,
            "actual_value": actual_raw,
            "matched": matched,
            "summary": f"`{col}` = {actual_raw} ({'Satisfied ✓' if matched else 'Violated ✗'})",
        })

        if not matched:
            has_violations = True
            all_satisfied = False

    return {
        "has_rules": True,
        "evaluated": True,
        "is_satisfied": (all_satisfied and not has_violations and not unmutated_reqs),
        "is_partially_satisfied": (not has_violations and len(unmutated_reqs) > 0 and touches_mutation),
        "is_violated": has_violations,
        "clauses": evaluated_clauses,
        "referenced_fields": ref_fields,
        "touches_mutation_fields": touches_mutation,
        "unmutated_requirements": unmutated_reqs,
        "evaluation_summary": " | ".join(clause_summaries),
    }


class AuditProcessCorrelator:
    """Coordinates static flow definitions, dynamic run history, and audit records."""


    def __init__(
        self,
        environment_id: Optional[str] = None,
        org_url: str = "",
    ) -> None:
        self.environment_id = clean_guid(environment_id) if environment_id else ""
        self.org_url = org_url.rstrip("/")
        self.verified_runs: List[Dict[str, Any]] = []
        self.verified_runs_cache: Dict[str, Optional[bool]] = {}
        self.verified_actions_cache: Dict[str, Dict[str, Any]] = {}
        self.logic_app_diagnostics: List[str] = []
        self.executions: List[FlowRunExecution] = []
        self.candidate_flows: List[CorrelatedFlow] = []

    def prepare_execution_context(
        self,
        raw_workflows: List[Dict[str, Any]],
        flow_runs: List[Dict[str, Any]],
        flow_sessions: List[Dict[str, Any]],
        async_operations: List[Dict[str, Any]],
        target_entity_logical: str,
        raw_audit_details: Optional[List[Dict[str, Any]]] = None,
        tolerance_seconds: int = 45,
        logic_runs: Optional[List[Dict[str, Any]]] = None,
        portal_sessions: Optional[List[Dict[str, Any]]] = None,
        portal_forms: Optional[List[Dict[str, Any]]] = None,
        ignore_name_patterns: Optional[List[str]] = None,
    ) -> Tuple[List[CorrelatedFlow], List[FlowRunExecution]]:
        """Parses flow definitions, portal forms, and runs into indexed candidate_flows and executions without downloading payloads."""
        norm_target = target_entity_logical.strip().lower()

        clean_patterns = [
            p.strip().lower() for p in (ignore_name_patterns or []) if p and p.strip()
        ]
        if clean_patterns:
            raw_workflows = [
                w for w in raw_workflows
                if not any(pat in str(w.get("name") or "").lower() for pat in clean_patterns)
            ]

        # 1. Parse workflows into candidate flows
        candidate_flows: List[CorrelatedFlow] = []
        flow_by_id: Dict[str, CorrelatedFlow] = {}

        for wf in raw_workflows:
            parsed = extract_flow_definition(wf, norm_target)
            candidate_flows.append(parsed)
            if parsed.workflow_id:
                wid_val = str(parsed.workflow_id)
                flow_by_id[wid_val] = parsed
                flow_by_id[wid_val.lower()] = parsed
                c_wid = clean_guid(wid_val)
                if c_wid:
                    flow_by_id[c_wid] = parsed
                    flow_by_id[c_wid.lower()] = parsed
            if parsed.name:
                flow_by_id[parsed.name] = parsed
                flow_by_id[parsed.name.lower()] = parsed

        # Ingest Power Pages / Portal Forms into candidate_flows
        for pf in (portal_forms or []):
            fid = clean_guid(pf.get("form_id") or "")
            fname = pf.get("name") or "Portal Form"
            fmode = pf.get("mode")  # 100000000=Insert, 100000001=Edit, 100000002=ReadOnly
            mode_str = "Insert" if fmode == 100000000 else ("Edit" if fmode == 100000001 else "Form")
            ftype = pf.get("form_type") or "Form"
            c_flow = CorrelatedFlow(
                workflow_id=fid,
                name=f"Power Pages {ftype}: {fname} ({mode_str})",
                category=7,
                category_label="Power Pages / Portal Form",
                state_code=1,
                primary_entity=norm_target,
                description=f"Power Pages {ftype} in {mode_str} mode on {norm_target}",
                modifies_attributes=set(pf.get("metadata_attributes") or []),
                raw_definition=pf,
            )
            candidate_flows.append(c_flow)
            if fid:
                flow_by_id[fid] = c_flow
                flow_by_id[fid.lower()] = c_flow
            flow_by_id[c_flow.name] = c_flow
            flow_by_id[c_flow.name.lower()] = c_flow
        self.candidate_flows = list(candidate_flows)

        # 2. Establish global audit temporal bracket to prune the history execution pool
        audit_dts: List[datetime] = []
        for detail in (raw_audit_details or []):
            audit_record = detail.get("AuditRecord") or {}
            raw_date = audit_record.get("createdon")
            if raw_date:
                try:
                    d_parsed = dateutil.parser.isoparse(raw_date)
                    audit_dts.append(d_parsed if d_parsed.tzinfo else d_parsed.replace(tzinfo=timezone.utc))
                except Exception:
                    pass

        tol_delta = timedelta(seconds=tolerance_seconds)
        # Pre-window buffer: allow up to 10 minutes prior to earliest audit event to capture long processing times
        pre_window = max(tol_delta, timedelta(minutes=10))
        post_drift = max(tol_delta, timedelta(minutes=2))
        pool_min_dt: Optional[datetime] = (min(audit_dts) - pre_window) if audit_dts else None
        pool_max_dt: Optional[datetime] = (max(audit_dts) + post_drift) if audit_dts else None

        def _is_within_bracket(s: datetime) -> bool:
            if not pool_min_dt or not pool_max_dt:
                return True
            s_norm = s if s.tzinfo else s.replace(tzinfo=timezone.utc)
            return pool_min_dt <= s_norm <= pool_max_dt

        # 3. Build indexed executions (pruned by global temporal bracket)
        executions: List[FlowRunExecution] = []

        # From modern flowruns elastic table
        for fr in flow_runs:
            rid = fr.get("flowrunid") or ""
            wid = clean_guid(fr.get("flowid") or fr.get("workflowid") or fr.get("_flow_id"))
            start_str = fr.get("starttime")
            if not start_str:
                continue
            try:
                s_dt = dateutil.parser.parse(str(start_str))
            except Exception:
                continue
            if not _is_within_bracket(s_dt):
                continue
            e_str = fr.get("endtime")
            e_dt = dateutil.parser.parse(str(e_str)) if e_str else None

            fname = fr.get("flowname") or fr.get("Flow Name") or (flow_by_id[wid].name if wid in flow_by_id else "Cloud Flow")
            dur_str = ""
            if e_dt and s_dt:
                sec = (e_dt - s_dt).total_seconds()
                if sec < 60:
                    dur_str = f"{sec:.1f}s" if sec >= 1 else "<1s"
                else:
                    dur_str = f"{int(sec // 60)}m {int(sec % 60)}s"

            maker_url = fr.get("maker_url") or ""
            env = fr.get("_env_id") or self.environment_id or "default"
            if not maker_url and wid and rid:
                maker_url = f"https://make.powerautomate.com/environments/{env}/flows/{wid}/runs/{rid}"

            executions.append(
                FlowRunExecution(
                    run_id=rid,
                    workflow_id=wid,
                    flow_name=fname,
                    started_on=s_dt,
                    completed_on=e_dt,
                    status=fr.get("status") or "Succeeded",
                    source="flowruns",
                    duration_str=dur_str,
                    error_message=fr.get("errormessage") or fr.get("Error Message") or "",
                    maker_url=maker_url,
                )
            )

        # From flowsessions table
        for fs in flow_sessions:
            sid = fs.get("flowsessionid") or fs.get("flowrunid") or ""
            wid = clean_guid(fs.get("_regardingobjectid_value") or fs.get("workflowid") or fs.get("_flow_id"))
            start_str = fs.get("startedon") or fs.get("starttime")
            if not start_str:
                continue
            try:
                s_dt = dateutil.parser.parse(str(start_str))
            except Exception:
                continue
            if not _is_within_bracket(s_dt):
                continue
            e_str = fs.get("completedon") or fs.get("endtime")
            e_dt = dateutil.parser.parse(str(e_str)) if e_str else None

            fname = fs.get("flowname") or fs.get("Flow Name") or (flow_by_id[wid].name if wid in flow_by_id else "Cloud Flow")
            status_code = fs.get("statuscode")
            status_label = fs.get("status") or ("Succeeded" if status_code == 4 else str(status_code or "Unknown"))

            dur_str = ""
            if e_dt and s_dt:
                sec = (e_dt - s_dt).total_seconds()
                if sec < 60:
                    dur_str = f"{sec:.1f}s" if sec >= 1 else "<1s"
                else:
                    dur_str = f"{int(sec // 60)}m {int(sec % 60)}s"

            maker_url = fs.get("maker_url") or ""
            env = fs.get("_env_id") or self.environment_id or "default"
            if not maker_url:
                frid = fs.get("flowrunid")
                if wid and frid and frid != sid:
                    maker_url = f"https://make.powerautomate.com/environments/{env}/flows/{wid}/runs/{frid}"
                elif wid:
                    maker_url = f"https://make.powerautomate.com/environments/{env}/flows/{wid}/details"

            executions.append(
                FlowRunExecution(
                    run_id=sid,
                    workflow_id=wid,
                    flow_name=fname,
                    started_on=s_dt,
                    completed_on=e_dt,
                    status=status_label,
                    source="flowsessions",
                    duration_str=dur_str,
                    maker_url=maker_url,
                )
            )

        # From asyncoperations (Classic Workflows & System Jobs)
        for ao in async_operations:
            aoid = ao.get("asyncoperationid") or ""
            wid = clean_guid(ao.get("_workflowactivationid_value"))
            start_str = ao.get("startedon")
            if not start_str:
                continue
            try:
                s_dt = dateutil.parser.parse(str(start_str))
            except Exception:
                continue
            if not _is_within_bracket(s_dt):
                continue
            e_str = ao.get("completedon")
            e_dt = dateutil.parser.parse(str(e_str)) if e_str else None

            dur_str = ""
            if e_dt and s_dt:
                sec = (e_dt - s_dt).total_seconds()
                if sec < 60:
                    dur_str = f"{sec:.1f}s" if sec >= 1 else "<1s"
                else:
                    dur_str = f"{int(sec // 60)}m {int(sec % 60)}s"

            state_code = ao.get("statecode")
            status_code = ao.get("statuscode")
            status_str = "Succeeded" if (state_code == 3 and status_code == 30) else f"Status {status_code}"

            executions.append(
                FlowRunExecution(
                    run_id=aoid,
                    workflow_id=wid,
                    flow_name=ao.get("name") or "System Job / Background Workflow",
                    started_on=s_dt,
                    completed_on=e_dt,
                    status=status_str,
                    source="asyncoperations",
                    duration_str=dur_str,
                    error_message=ao.get("message") or ao.get("friendlymessage") or "",
                )
            )

        # From Azure Logic Apps
        combined_logic_runs = list(logic_runs or [])
        for fr in flow_runs:
            if fr.get("source") == "logicapps" or fr.get("_source") == "logicapps":
                combined_logic_runs.append(fr)

        for lr in combined_logic_runs:
            rid = lr.get("id") or lr.get("name") or lr.get("run_id") or lr.get("flowrunid") or ""
            wid = clean_guid(lr.get("workflowid") or lr.get("workflow_id") or lr.get("_flow_id") or lr.get("flowid"))
            if not wid and "workflows/" in rid:
                try:
                    wid = rid.split("workflows/")[1].split("/")[0]
                except Exception:
                    wid = ""
            fname = lr.get("flowname") or lr.get("logic_app_name") or lr.get("Flow Name") or (flow_by_id[wid].name if wid in flow_by_id else "Azure Logic App")
            props = lr.get("properties") or {}
            start_str = props.get("startTime") or lr.get("starttime") or lr.get("startTime")
            if not start_str:
                continue
            try:
                s_dt = dateutil.parser.parse(str(start_str))
            except Exception:
                continue
            if not _is_within_bracket(s_dt):
                continue
            e_str = props.get("endTime") or lr.get("endtime") or lr.get("endTime")
            e_dt = dateutil.parser.parse(str(e_str)) if e_str else None
            status_val = props.get("status") or lr.get("status") or "Succeeded"

            maker_url = lr.get("maker_url") or lr.get("portal_link") or ""
            if not maker_url and rid:
                enc_id = urllib.parse.quote(rid, safe="")
                maker_url = f"https://portal.azure.com/#view/Microsoft_Azure_EMA/LogicAppsMonitorBlade/runid/{enc_id}"

            dur_str = ""
            if e_dt and s_dt:
                sec = (e_dt - s_dt).total_seconds()
                dur_str = f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m {int(sec % 60)}s"

            arm_id = lr.get("arm_resource_id") or lr.get("arm_id") or ""
            if not arm_id and str(rid).startswith("/subscriptions/"):
                arm_id = rid
            sub_id = lr.get("subscription_id")
            rg = lr.get("resource_group")
            app_name = lr.get("logic_app_name") or lr.get("flowname")
            if not arm_id and sub_id and rg and app_name and rid:
                arm_id = f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows/{app_name}/runs/{rid}"

            executions.append(
                FlowRunExecution(
                    run_id=rid,
                    workflow_id=wid,
                    flow_name=fname,
                    started_on=s_dt,
                    completed_on=e_dt,
                    status=status_val,
                    source="logicapps",
                    duration_str=dur_str,
                    error_message=props.get("error", {}).get("message", "") if isinstance(props.get("error"), dict) else "",
                    maker_url=maker_url,
                    arm_resource_id=arm_id,
                    trigger_data=props.get("trigger", {}) if isinstance(props, dict) and isinstance(props.get("trigger"), dict) else {},
                )
            )

        # From Power Pages / Dynamics 365 Portals
        for ps in (portal_sessions or []):
            s_id = str(
                ps.get("adx_webformsessionid")
                or ps.get("mspp_webformsessionid")
                or ps.get("webformsessionid")
                or ""
            )
            c_on = ps.get("createdon")
            m_on = ps.get("modifiedon") or c_on
            st_dt = None
            end_dt = None
            if c_on:
                try:
                    st_dt = dateutil.parser.parse(str(c_on))
                except Exception:
                    pass
            if m_on:
                try:
                    end_dt = dateutil.parser.parse(str(m_on))
                except Exception:
                    pass
            if not st_dt:
                continue
            if not _is_within_bracket(st_dt):
                continue

            w_id = clean_guid(
                ps.get("_adx_webform_value")
                or ps.get("_mspp_webform_value")
                or ""
            )
            step_id = clean_guid(
                ps.get("_adx_webformstep_value")
                or ps.get("_mspp_webformstep_value")
                or ""
            )
            contact_name = (
                ps.get("_adx_contact_value@OData.Community.Display.V1.FormattedValue")
                or ps.get("_mspp_contact_value@OData.Community.Display.V1.FormattedValue")
                or ps.get("_adx_contact_value")
                or ps.get("_mspp_contact_value")
                or "Portal Contact"
            )
            wf_name = (
                ps.get("_adx_webform_value@OData.Community.Display.V1.FormattedValue")
                or ps.get("_mspp_webform_value@OData.Community.Display.V1.FormattedValue")
                or "Multistep Form"
            )
            step_name = (
                ps.get("_adx_webformstep_value@OData.Community.Display.V1.FormattedValue")
                or ps.get("_mspp_webformstep_value@OData.Community.Display.V1.FormattedValue")
                or "Step"
            )
            flow_disp_name = f"Power Pages: {wf_name}"
            p_url = ""
            if self.org_url and s_id:
                p_url = f"{self.org_url}/main.aspx?etn=adx_webformsession&pagetype=entityrecord&id={s_id}"

            dur_str = ""
            if end_dt and st_dt:
                sec = (end_dt - st_dt).total_seconds()
                dur_str = f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m {int(sec % 60)}s"

            executions.append(
                FlowRunExecution(
                    run_id=s_id,
                    workflow_id=w_id or step_id,
                    flow_name=flow_disp_name,
                    started_on=st_dt,
                    completed_on=end_dt,
                    status="Completed",
                    source="portals",
                    duration_str=dur_str,
                    maker_url=p_url,
                    trigger_data={
                        "contact": contact_name,
                        "webform": wf_name,
                        "step": step_name,
                        "step_id": step_id,
                        "session_id": s_id,
                        "primary_record_id": (
                            ps.get("adx_primaryrecordid")
                            or ps.get("mspp_primaryrecordid")
                            or ""
                        ),
                    },
                )
            )

        # Pre-sort executions chronologically by start time
        executions.sort(key=lambda x: x.started_on)
        self.executions = list(executions)
        return self.candidate_flows, self.executions

    def correlate(
        self,
        raw_audit_details: List[Dict[str, Any]],
        raw_workflows: List[Dict[str, Any]],
        flow_runs: List[Dict[str, Any]],
        flow_sessions: List[Dict[str, Any]],
        async_operations: List[Dict[str, Any]],
        target_entity_logical: str,
        attr_metadata: Dict[str, str],
        tolerance_seconds: int = 45,
        only_automations: bool = False,
        target_guids: Optional[Set[str]] = None,
        pa_client: Optional[Any] = None,
        logic_runs: Optional[List[Dict[str, Any]]] = None,
        logic_client: Optional[Any] = None,
        portal_sessions: Optional[List[Dict[str, Any]]] = None,
        portal_forms: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cached_verified_runs: Optional[Dict[str, Optional[bool]]] = None,
        cached_verified_actions: Optional[Dict[str, Dict[str, Any]]] = None,
        max_workers: int = 20,
        cloud_flow_workers: int = 4,
    ) -> List[AuditCorrelationItem]:
        """Correlate each audit mutation entry against flows, logic apps, portal forms, and runs with target record verification."""
        candidate_flows, executions = self.prepare_execution_context(
            raw_workflows=raw_workflows,
            flow_runs=flow_runs,
            flow_sessions=flow_sessions,
            async_operations=async_operations,
            target_entity_logical=target_entity_logical,
            raw_audit_details=raw_audit_details,
            tolerance_seconds=tolerance_seconds,
            logic_runs=logic_runs,
            portal_sessions=portal_sessions,
            portal_forms=portal_forms,
        )

        flow_by_id: Dict[str, CorrelatedFlow] = {}
        for cf in candidate_flows:
            if cf.workflow_id:
                wid_val = str(cf.workflow_id)
                flow_by_id[wid_val] = cf
                flow_by_id[wid_val.lower()] = cf
                c_wid = clean_guid(wid_val)
                if c_wid:
                    flow_by_id[c_wid] = cf
                    flow_by_id[c_wid.lower()] = cf
            if cf.name:
                flow_by_id[cf.name] = cf
                flow_by_id[cf.name.lower()] = cf

        execution_start_times = [x.started_on for x in executions]
        tol_delta = timedelta(seconds=tolerance_seconds)

        # 4. Parse and correlate audit records
        results: List[AuditCorrelationItem] = []
        norm_target_guids: Set[str] = set()
        for g in (target_guids or set()):
            cg = clean_guid(g).lower()
            if cg:
                norm_target_guids.add(cg)
                norm_target_guids.add(cg.replace("-", ""))
        verified_runs_cache: Dict[str, Optional[bool]] = dict(self.verified_runs_cache)
        if cached_verified_runs:
            verified_runs_cache.update(cached_verified_runs)

        verified_actions_cache: Dict[str, Dict[str, Any]] = dict(self.verified_actions_cache)
        if cached_verified_actions:
            verified_actions_cache.update(cached_verified_actions)

        cache_lock = threading.Lock()
        target_matcher = _TargetMatcher(norm_target_guids)
        blob_fetcher_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        blob_fetcher = _BlobFetcher(blob_fetcher_executor)

        def _verify_run_belongs_to_target(r_obj: FlowRunExecution) -> Optional[bool]:
            if not norm_target_guids:
                return None
            rid = r_obj.run_id
            with cache_lock:
                if rid in verified_runs_cache:
                    return verified_runs_cache[rid]

            if r_obj.source == "portals":
                trig_d = getattr(r_obj, "trigger_data", {}) or {}
                step_name = trig_d.get("step") or "Step"
                contact_name = trig_d.get("contact") or "Portal Contact"
                act_label = f"Web Form Step '{step_name}'"
                if contact_name and contact_name != "Portal Contact":
                    act_label += f" by {contact_name}"
                with cache_lock:
                    verified_runs_cache[rid] = True
                    verified_actions_cache[rid] = {
                        "action_name": act_label,
                        "updated_fields": [],
                    }
                return True

            if r_obj.source == "asyncoperations":
                # Asyncoperations were queried specifically for regardingobjectid
                with cache_lock:
                    verified_runs_cache[rid] = True
                    verified_actions_cache[rid] = {
                        "action_name": "Async Operation / System Job",
                        "updated_fields": [],
                    }
                return True

            if r_obj.source == "logicapps" and logic_client and rid:
                target_arm_id = getattr(r_obj, "arm_resource_id", "") or rid
                if not target_arm_id.startswith("/subscriptions/") and r_obj.workflow_id and r_obj.workflow_id.startswith("/subscriptions/"):
                    target_arm_id = f"{r_obj.workflow_id.rstrip('/')}/runs/{rid}"

                run_short = target_arm_id.split("/")[-1] if "/" in target_arm_id else target_arm_id
                start_label = r_obj.started_on.strftime("%H:%M:%S") if hasattr(r_obj.started_on, "strftime") else str(r_obj.started_on)
                diag_tag = f"[{r_obj.flow_name}] Run `{run_short}` ({start_label})"
                self.logic_app_diagnostics.append(f"🔎 {diag_tag}: Querying Azure ARM...")
                print(f"[AuditCorrelation] 🔎 {diag_tag}: Querying Azure ARM ({target_arm_id})...")

                try:
                    trig = getattr(r_obj, "trigger_data", None) or {}
                    if not trig and hasattr(logic_client, "get_run_details"):
                        run_details = logic_client.get_run_details(target_arm_id)
                        props_detail = run_details.get("properties", {}) if isinstance(run_details, dict) else {}
                        trig = props_detail.get("trigger", {}) if isinstance(props_detail, dict) else {}

                    try:
                        actions = logic_client.get_run_actions(target_arm_id, include_child_actions=False)
                    except TypeError:
                        actions = logic_client.get_run_actions(target_arm_id)
                    except Exception:
                        actions = []

                    self.logic_app_diagnostics.append(f"📦 {diag_tag}: Retrieved {len(actions)} action(s) from Azure.")
                    print(f"[AuditCorrelation] 📦 {diag_tag}: Retrieved {len(actions)} action(s).")

                    fl_def = flow_by_id.get(r_obj.workflow_id) or flow_by_id.get(clean_guid(r_obj.workflow_id))
                    flow_cat = fl_def.action_catalog if (fl_def and fl_def.action_catalog) else None

                    fetch_fn = getattr(logic_client, "get_content_from_link", None) or getattr(logic_client, "get_sas_blob_content", lambda u: None)
                    res = _inspect_run_payloads(
                        trigger=trig,
                        actions=actions,
                        fetch_fn=fetch_fn,
                        fetcher=blob_fetcher,
                        matcher=target_matcher,
                        catalog=flow_cat,
                    )

                    if res.get("trigger_matched"):
                        self.logic_app_diagnostics.append(f"🎯 {diag_tag}: Target record identifier MATCHED in trigger payload!")
                        print(f"[AuditCorrelation] 🎯 {diag_tag}: Target identifier MATCHED in trigger payload!")

                    if res["status"] is True:
                        matched_act = res.get("action_name") or "Trigger"
                        self.logic_app_diagnostics.append(f"✅ {diag_tag}: MATCH CONFIRMED on action '{matched_act}'!")
                        print(f"[AuditCorrelation] ✅ {diag_tag}: MATCH CONFIRMED on action '{matched_act}'!")
                        with cache_lock:
                            verified_runs_cache[rid] = True
                            verified_actions_cache[rid] = {
                                "action_name": res.get("action_name") or "Trigger Payload",
                                "updated_fields": list(res.get("updated_fields") or []),
                            }
                        return True
                    elif res["status"] is False:
                        self.logic_app_diagnostics.append(f"⚪ {diag_tag}: Inspected {len(actions)} actions & payloads, target record identifier not found.")
                        print(f"[AuditCorrelation] ⚪ {diag_tag}: Target identifier not found in run.")
                        with cache_lock:
                            verified_runs_cache[rid] = False
                        return False
                    else:
                        with cache_lock:
                            verified_runs_cache[rid] = None
                        return None
                except Exception as ex:
                    err_text = f"⚠️ {diag_tag}: Verification error: {ex}"
                    self.logic_app_diagnostics.append(err_text)
                    print(f"[AuditCorrelation] {err_text}")
                    logging.debug("Logic App run verification failed for %s: %s", target_arm_id, ex)
                    with cache_lock:
                        verified_runs_cache[rid] = None
                    return None

            if pa_client and self.environment_id and r_obj.workflow_id and rid:
                try:
                    trig = {}
                    if hasattr(pa_client, "get_flow_run_details"):
                        details = pa_client.get_flow_run_details(self.environment_id, r_obj.workflow_id, rid)
                        if isinstance(details, dict):
                            trig = details.get("properties", {}).get("trigger", {})

                    actions = []
                    if hasattr(pa_client, "get_flow_run_actions"):
                        actions = pa_client.get_flow_run_actions(self.environment_id, r_obj.workflow_id, rid) or []

                    fl_def = flow_by_id.get(r_obj.workflow_id) or flow_by_id.get(clean_guid(r_obj.workflow_id))
                    flow_cat = fl_def.action_catalog if (fl_def and fl_def.action_catalog) else None

                    fetch_fn = getattr(pa_client, "get_content_from_link", None) or getattr(pa_client, "get_sas_blob_content", lambda u: None)
                    res = _inspect_run_payloads(
                        trigger=trig,
                        actions=actions,
                        fetch_fn=fetch_fn,
                        fetcher=blob_fetcher,
                        matcher=target_matcher,
                        catalog=flow_cat,
                    )

                    if res["status"] is True:
                        with cache_lock:
                            verified_runs_cache[rid] = True
                            verified_actions_cache[rid] = {
                                "action_name": res.get("action_name") or "Trigger Payload",
                                "updated_fields": list(res.get("updated_fields") or []),
                            }
                        return True
                    elif res["status"] is False:
                        with cache_lock:
                            verified_runs_cache[rid] = False
                        return False
                    else:
                        with cache_lock:
                            verified_runs_cache[rid] = None
                        return None
                except Exception as ex:
                    logging.debug("Run verification failed for %s: %s", rid, ex)
                    with cache_lock:
                        verified_runs_cache[rid] = None
                    return None

            with cache_lock:
                verified_runs_cache[rid] = None
            return None

        # Step 3.1: Pre-pass target record verification across candidate runs in the pool
        # Collect all valid audit event timestamps to limit expensive payload network fetches
        audit_event_dts: List[datetime] = []
        for detail in raw_audit_details:
            a_rec = detail.get("AuditRecord") or {}
            c_on = a_rec.get("createdon")
            if c_on:
                try:
                    p_dt = dateutil.parser.isoparse(c_on)
                    if getattr(p_dt, "tzinfo", None) is not None:
                        p_dt = p_dt.replace(tzinfo=None)
                    audit_event_dts.append(p_dt)
                except Exception:
                    pass

        # Candidate runs must be within temporal proximity of at least one audit record
        # (e.g. within tolerance_seconds or 10-20 minutes for Logic Apps / asynchronous jobs)
        sorted_audit_dts = sorted(audit_event_dts)
        max_tol = max(tolerance_seconds, 600)
        la_tol = max(tolerance_seconds, 1200)

        def _is_temporally_proximate_to_any_audit(r: FlowRunExecution) -> bool:
            if not sorted_audit_dts:
                return True
            eff_tol_sec = la_tol if r.source == "logicapps" else max_tol
            eff_delta = timedelta(seconds=eff_tol_sec)
            r_st = r.started_on
            if getattr(r_st, "tzinfo", None) is not None:
                r_st = r_st.replace(tzinfo=None)

            idx = bisect_left(sorted_audit_dts, r_st - eff_delta)
            if idx < len(sorted_audit_dts) and sorted_audit_dts[idx] <= r_st + eff_delta:
                return True

            if r.completed_on:
                r_et = r.completed_on
                if getattr(r_et, "tzinfo", None) is not None:
                    r_et = r_et.replace(tzinfo=None)
                idx_et = bisect_left(sorted_audit_dts, r_et - eff_delta)
                if idx_et < len(sorted_audit_dts) and sorted_audit_dts[idx_et] <= r_et + eff_delta:
                    return True
            return False

        candidate_runs = [r for r in executions if _is_temporally_proximate_to_any_audit(r)]

        verified_runs_list: List[Dict[str, Any]] = []
        if norm_target_guids and candidate_runs:
            la_candidates = [r for r in candidate_runs if r.source == "logicapps"]
            cf_candidates = [r for r in candidate_runs if r.source != "logicapps"]
            total_cand = len(candidate_runs)

            def _process_single_candidate(r_obj: FlowRunExecution):
                try:
                    res = _verify_run_belongs_to_target(r_obj)
                    with cache_lock:
                        act_info = verified_actions_cache.get(r_obj.run_id, {})
                    return r_obj, res, act_info
                except Exception as ex:
                    logging.debug("Worker candidate verification error for %s: %s", r_obj.run_id, ex)
                    return r_obj, None, {}

            overall_completed = 0

            all_la_runs = [r for r in executions if r.source == "logicapps"]
            if not la_candidates:
                if all_la_runs:
                    la_starts = [r.started_on for r in all_la_runs if r.started_on]
                    min_la_str = min(la_starts).strftime("%d/%m/%Y %H:%M:%S") if la_starts else "-"
                    max_la_str = max(la_starts).strftime("%d/%m/%Y %H:%M:%S") if la_starts else "-"
                    msg = (
                        f"ℹ️ [Logic Apps] Found {len(all_la_runs)} run(s) across the 31-day pool "
                        f"(from {min_la_str} to {max_la_str}), but NONE executed within 10 minutes "
                        f"of any audit change event."
                    )
                    self.logic_app_diagnostics.append(msg)
                    print(f"[AuditCorrelation] {msg}")

                    la_by_name: Dict[str, list] = {}
                    for r in all_la_runs:
                        la_by_name.setdefault(r.flow_name, []).append(r)
                    for la_n, r_list in la_by_name.items():
                        st_list = [r.started_on for r in r_list if r.started_on]
                        mn = min(st_list).strftime("%d/%m/%Y %H:%M") if st_list else "-"
                        mx = max(st_list).strftime("%d/%m/%Y %H:%M") if st_list else "-"
                        sub_msg = f"  • {la_n}: {len(r_list)} run(s) (range: {mn} to {mx})"
                        self.logic_app_diagnostics.append(sub_msg)
                        print(f"[AuditCorrelation] {sub_msg}")
                else:
                    msg = "ℹ️ [Logic Apps] No Logic App runs were retrieved in Step 2."
                    self.logic_app_diagnostics.append(msg)
                    print(f"[AuditCorrelation] {msg}")

            # Phase 1: Azure Logic Apps FIRST (for easy monitoring)
            if la_candidates:
                la_total = len(la_candidates)
                la_completed = 0
                la_matches = 0
                msg = f"🚀 [Phase 1/2: Azure Logic Apps] Evaluating {la_total} Logic App candidate run(s) (within 10m of audit events)..."
                self.logic_app_diagnostics.append(msg)
                print(f"[AuditCorrelation] {msg}")
                if progress_callback:
                    progress_callback(
                        overall_completed,
                        total_cand,
                        f"3️⃣.1 [Phase 1/2: Azure Logic Apps] Starting download & verification for {la_total} Logic App run(s)..."
                    )
                la_workers = max(1, max_workers)
                with concurrent.futures.ThreadPoolExecutor(max_workers=la_workers) as executor:
                    futures = {executor.submit(_process_single_candidate, r): r for r in la_candidates}
                    for fut in concurrent.futures.as_completed(futures):
                        r_obj, status, act_info = fut.result()
                        la_completed += 1
                        overall_completed += 1
                        if status is True:
                            la_matches += 1
                            with cache_lock:
                                verified_runs_list.append({
                                    "run_id": r_obj.run_id,
                                    "workflow_id": r_obj.workflow_id,
                                    "flow_name": r_obj.flow_name,
                                    "source": r_obj.source,
                                    "started_on": r_obj.started_on,
                                    "completed_on": r_obj.completed_on,
                                    "action_name": act_info.get("action_name", ""),
                                    "updated_fields": act_info.get("updated_fields", []),
                                    "maker_url": r_obj.maker_url,
                                })
                        if progress_callback and (la_completed % 2 == 0 or la_completed == la_total):
                            progress_callback(
                                overall_completed,
                                total_cand,
                                f"3️⃣.1 [Phase 1/2: Azure Logic Apps] Checked {la_completed}/{la_total} ({la_matches} target match(es), {la_workers} workers)..."
                            )

            # Phase 2: Cloud Flows & Process Matching (conservatively 4 workers to respect API throttling)
            if cf_candidates:
                cf_total = len(cf_candidates)
                cf_completed = 0
                cf_matches = 0
                cf_workers = max(1, cloud_flow_workers)
                if progress_callback:
                    progress_callback(
                        overall_completed,
                        total_cand,
                        f"3️⃣.1 [Phase 2/2: Cloud Flows] Starting download & verification for {cf_total} Cloud Flow / Process run(s)..."
                    )
                with concurrent.futures.ThreadPoolExecutor(max_workers=cf_workers) as executor:
                    futures = {executor.submit(_process_single_candidate, r): r for r in cf_candidates}
                    for fut in concurrent.futures.as_completed(futures):
                        r_obj, status, act_info = fut.result()
                        cf_completed += 1
                        overall_completed += 1
                        if status is True:
                            cf_matches += 1
                            with cache_lock:
                                verified_runs_list.append({
                                    "run_id": r_obj.run_id,
                                    "workflow_id": r_obj.workflow_id,
                                    "flow_name": r_obj.flow_name,
                                    "source": r_obj.source,
                                    "started_on": r_obj.started_on,
                                    "completed_on": r_obj.completed_on,
                                    "action_name": act_info.get("action_name", ""),
                                    "updated_fields": act_info.get("updated_fields", []),
                                    "maker_url": r_obj.maker_url,
                                })
                        if progress_callback and (cf_completed % 5 == 0 or cf_completed == cf_total):
                            progress_callback(
                                overall_completed,
                                total_cand,
                                f"3️⃣.1 [Phase 2/2: Cloud Flows] Checked {cf_completed}/{cf_total} ({cf_matches} target match(es), {cf_workers} workers)..."
                            )

        verified_runs_list.sort(key=lambda x: x["started_on"] or datetime.min)
        self.verified_runs = verified_runs_list
        with cache_lock:
            self.verified_runs_cache = dict(verified_runs_cache)
            self.verified_actions_cache = dict(verified_actions_cache)

        # Pre-calculate total field updates to correlate
        total_field_updates = 0
        for detail in raw_audit_details:
            audit_record = detail.get("AuditRecord") or {}
            raw_date = audit_record.get("createdon")
            if not raw_date:
                continue
            try:
                dateutil.parser.isoparse(raw_date)
            except Exception:
                continue
            user_val = (
                audit_record.get("_userid_value@OData.Community.Display.V1.FormattedValue")
                or audit_record.get("_userid_value")
                or "System"
            )
            is_auto = is_automation_user(str(user_val))
            if only_automations and not is_auto:
                continue
            old_vals_cnt: dict = detail.get("OldValue") or {}
            new_vals_cnt: dict = detail.get("NewValue") or {}
            all_fields_cnt = [
                f for f in set(list(old_vals_cnt.keys()) + list(new_vals_cnt.keys()))
                if not f.startswith("@") and not f.startswith("_")
            ]
            total_field_updates += len(all_fields_cnt) if all_fields_cnt else 1

        checked_count = 0

        for detail in raw_audit_details:
            audit_record = detail.get("AuditRecord") or {}
            raw_date = audit_record.get("createdon")
            if not raw_date:
                continue

            try:
                dt = dateutil.parser.isoparse(raw_date)
            except Exception:
                continue

            changed_date_str = dt.strftime("%d/%m/%Y %H:%M:%S")

            user_val = (
                audit_record.get("_userid_value@OData.Community.Display.V1.FormattedValue")
                or audit_record.get("_userid_value")
                or "System"
            )
            calling_user = audit_record.get("_callinguserid_value@OData.Community.Display.V1.FormattedValue")
            display_user = f"{user_val} (via {calling_user})" if calling_user and calling_user != user_val else str(user_val)

            is_auto = is_automation_user(str(user_val))
            if only_automations and not is_auto:
                continue

            action_code = audit_record.get("action")
            event_type = ACTION_MAP.get(action_code, f"Action {action_code}")
            tx_id = audit_record.get("transactionid") or "-"
            extra_info = audit_record.get("useradditionalinfo") or "-"

            old_vals: dict = detail.get("OldValue") or {}
            new_vals: dict = detail.get("NewValue") or {}
            all_fields = [
                f for f in set(list(old_vals.keys()) + list(new_vals.keys()))
                if not f.startswith("@") and not f.startswith("_")
            ]

            field_items = all_fields if all_fields else ["-"]

            base_overlapping_runs: List[FlowRunExecution] = []
            aligned_dt = dt
            if is_auto and executions:
                ref_st = execution_start_times[0]
                ref_has_tz = getattr(ref_st, "tzinfo", None) is not None
                dt_has_tz = getattr(dt, "tzinfo", None) is not None
                if ref_has_tz and not dt_has_tz:
                    aligned_dt = dt.replace(tzinfo=ref_st.tzinfo or timezone.utc)
                elif not ref_has_tz and dt_has_tz:
                    aligned_dt = dt.replace(tzinfo=None)

                pre_window = max(tol_delta, timedelta(minutes=20))
                post_drift = max(tol_delta, timedelta(minutes=5))
                t_min = aligned_dt - pre_window
                t_max = aligned_dt + post_drift
                idx_start = bisect_left(execution_start_times, t_min)
                idx_end = bisect_right(execution_start_times, t_max)

                def _compl_ge(c_on: Optional[datetime], cutoff: datetime) -> bool:
                    if c_on is None:
                        return True
                    if ref_has_tz and getattr(c_on, "tzinfo", None) is None:
                        c_on = c_on.replace(tzinfo=ref_st.tzinfo or timezone.utc)
                    elif not ref_has_tz and getattr(c_on, "tzinfo", None) is not None:
                        c_on = c_on.replace(tzinfo=None)
                    return c_on >= cutoff

                base_overlapping_runs = [
                    r for r in executions[idx_start:idx_end]
                    if _compl_ge(r.completed_on, aligned_dt - tol_delta)
                ]

            for f_name in field_items:
                checked_count += 1
                f_logical = f_name.lower()
                f_display = attr_metadata.get(f_name, f_name) if f_name != "-" else "(No field changes)"
                if progress_callback and total_field_updates > 0:
                    try:
                        progress_callback(
                            checked_count,
                            total_field_updates,
                            f"Checking field {checked_count}/{total_field_updates}: {f_display} ({changed_date_str})"
                        )
                    except Exception:
                        pass
                old_v = old_vals.get(f_name, "-")
                new_v = new_vals.get(f_name, "-")

                # Match candidate flows whose action definitions modify this attribute
                matching_flows_for_attr = [
                    flow for flow in candidate_flows
                    if f_logical in flow.modifies_attributes
                ]

                # Match runs within tolerance window
                matched_run: Optional[FlowRunExecution] = None
                matched_flow: Optional[CorrelatedFlow] = None
                confidence = "Uncorrelated"
                badge = "⚪"
                summary_lines = []

                if not is_auto:
                    confidence = "User Action"
                    badge = "👤"
                    summary_lines.append(f"Direct update performed by user '{user_val}'")
                else:
                    # 1. Search for temporal run match:
                    # Sort prioritizing runs belonging to flows known to modify this attribute!
                    attr_wf_ids = {fl.workflow_id for fl in matching_flows_for_attr if fl.workflow_id}
                    attr_wf_names = {fl.name.lower() for fl in matching_flows_for_attr if fl.name}
                    overlapping_runs = sorted(
                        base_overlapping_runs,
                        key=lambda r: (
                            0 if (r.workflow_id in attr_wf_ids or (r.flow_name and r.flow_name.lower() in attr_wf_names)) else 1,
                            abs((r.started_on - aligned_dt).total_seconds()),
                        )
                    )

                    best_run = None
                    matched_flow = None
                    verified_match = False
                    matched_act_name = ""
                    unverified_field_run = None
                    unverified_field_flow = None
                    unverified_field_act_name = ""
                    fallback_run = None
                    fallback_flow = None

                    for r in overlapping_runs:
                        fl = (
                            flow_by_id.get(r.workflow_id)
                            or (flow_by_id.get(clean_guid(r.workflow_id)) if r.workflow_id else None)
                            or (flow_by_id.get(r.workflow_id.lower()) if r.workflow_id else None)
                            or (flow_by_id.get(r.flow_name) if r.flow_name else None)
                            or (flow_by_id.get(r.flow_name.lower()) if r.flow_name else None)
                        )
                        ver_status = _verify_run_belongs_to_target(r)

                        # If this run was inspected and belongs to a different record, reject immediately!
                        if ver_status is False:
                            continue

                        act_info = verified_actions_cache.get(r.run_id, {})
                        act_fields = act_info.get("updated_fields", [])
                        act_name = act_info.get("action_name", "")
                        fl_modifies = (fl.modifies_attributes if fl else set()) | set(act_fields)

                        if ver_status is True:
                            # Highest priority: Target GUID verified AND modifies this exact field
                            if f_logical in fl_modifies:
                                best_run = r
                                matched_flow = fl
                                verified_match = True
                                matched_act_name = act_name
                                break  # Fully verified against record GUID and exact field!
                            elif not best_run and not verified_match:
                                best_run = r
                                matched_flow = fl
                                verified_match = True
                                matched_act_name = act_name

                        elif ver_status is None:
                            # Inconclusive payload verification (no direct payload link or links expired)
                            if fl and f_logical in fl.modifies_attributes:
                                if not unverified_field_run:
                                    unverified_field_run = r
                                    unverified_field_flow = fl
                                    unverified_field_act_name = act_name
                                # Do NOT break! Continue checking to see if another run has verified payload!
                            elif not fallback_run:
                                fallback_run = r
                                fallback_flow = fl

                    if not best_run and unverified_field_run:
                        best_run = unverified_field_run
                        matched_flow = unverified_field_flow
                        matched_act_name = unverified_field_act_name
                    elif not best_run and fallback_run:
                        best_run = fallback_run
                        matched_flow = fallback_flow

                    if best_run:
                        matched_run = best_run
                        best_act_info = verified_actions_cache.get(best_run.run_id, {})
                        best_act_fields = set(best_act_info.get("updated_fields", []))
                        fl_modifies = (matched_flow.modifies_attributes if matched_flow else set()) | best_act_fields
                        act_suffix = f" action '{matched_act_name}'" if matched_act_name else ""
                        app_type = "Power Pages Form" if best_run.source == "portals" else ("Azure Logic App" if best_run.source == "logicapps" else "Flow")
                        if verified_match and best_run.source == "portals":
                            confidence = "Exact Run Match"
                            badge = "🟢"
                            trig_d = getattr(best_run, "trigger_data", {}) or {}
                            contact_label = f" by portal contact '{trig_d.get('contact')}'" if trig_d.get("contact") and trig_d.get("contact") != "Portal Contact" else ""
                            step_label = f" (Step: '{trig_d.get('step')}')" if trig_d.get("step") else ""
                            summary_lines.append(
                                f"{app_type} '{best_run.flow_name}'{step_label}{contact_label} recorded web form session targeting record GUID at "
                                f"{best_run.started_on.strftime('%H:%M:%S')}"
                            )
                        elif verified_match and f_logical in fl_modifies:
                            confidence = "Exact Run Match"
                            badge = "🟢"
                            via_info = " (verified via incoming JSON payload)" if (f_logical in best_act_fields and (not matched_flow or f_logical not in matched_flow.modifies_attributes)) else ""
                            summary_lines.append(
                                f"{app_type} '{best_run.flow_name}' executed{act_suffix} targeting record GUID at "
                                f"{best_run.started_on.strftime('%H:%M:%S')} ({best_run.status}) and updated variable '{f_display}'{via_info}"
                            )
                        elif verified_match:
                            confidence = "Exact Run Match"
                            badge = "🟢"
                            summary_lines.append(
                                f"{app_type} '{best_run.flow_name}' executed{act_suffix} targeting record GUID at "
                                f"{best_run.started_on.strftime('%H:%M:%S')} ({best_run.status})"
                            )
                        elif matched_flow and f_logical in matched_flow.modifies_attributes:
                            confidence = "Exact Run Match"
                            badge = "🟢"
                            summary_lines.append(
                                f"{app_type} '{best_run.flow_name}' executed at {best_run.started_on.strftime('%H:%M:%S')} "
                                f"({best_run.status}) and updates field '{f_display}'"
                            )
                        else:
                            confidence = "Probable Session Match"
                            badge = "🟡"
                            summary_lines.append(
                                f"{app_type} '{best_run.flow_name}' executed at {best_run.started_on.strftime('%H:%M:%S')} "
                                f"({best_run.status}) during this change"
                            )
                            if matching_flows_for_attr:
                                cand_names = [f"'{cf.name}'" for cf in matching_flows_for_attr[:3]]
                                summary_lines.append(
                                    f"Configured candidate automations for '{f_display}': {', '.join(cand_names)}"
                                )
                    elif matching_flows_for_attr:
                        # Flow definition matches the changed field, but no verified run was found in retention window
                        matched_flow = matching_flows_for_attr[0]
                        confidence = "Candidate Flow"
                        badge = "🔵"
                        app_type = "Power Pages Form" if matched_flow.category == 7 else ("Azure Logic App" if matched_flow.category == 6 else "Flow")
                        if len(matching_flows_for_attr) == 1:
                            summary_lines.append(
                                f"{app_type} '{matched_flow.name}' is configured with action updating variable '{f_display}' (no active run in logs)"
                            )
                        else:
                            cand_names = [
                                f"'{cf.name}' ({'Power Pages' if cf.category == 7 else ('Logic App' if cf.category == 6 else 'Flow')})"
                                for cf in matching_flows_for_attr
                            ]
                            summary_lines.append(
                                f"Configured across {len(matching_flows_for_attr)} candidate automations: {', '.join(cand_names)} (no active runs in logs)"
                            )
                    else:
                        # Check if a Power Pages / Portal form matches table operation mode (Insert on Create, Edit on Update)
                        portal_cands = [
                            fl for fl in candidate_flows
                            if fl.category == 7 and (
                                (action_code == 1 and (fl.raw_definition.get("mode") == 100000000 or "insert" in fl.name.lower()))
                                or (action_code == 2 and (fl.raw_definition.get("mode") == 100000001 or "edit" in fl.name.lower()))
                                or (fl.raw_definition.get("mode") is None)
                            )
                        ]
                        user_str = str(user_val).lower()
                        if portal_cands and any(k in user_str for k in ("portal", "power pages", "powerpages", "crm portals")):
                            matched_flow = portal_cands[0]
                            confidence = "Probable Portal Form Match"
                            badge = "🟡"
                            mode_desc = "Insert / New Record" if action_code == 1 else "Edit / Update"
                            summary_lines.append(
                                f"Power Pages Form '{matched_flow.name}' ({mode_desc}) matches table operation (submitted via '{display_user}')"
                            )
                        elif portal_cands and is_auto:
                            matched_flow = portal_cands[0]
                            confidence = "Candidate Portal Form"
                            badge = "🔵"
                            mode_desc = "Insert" if action_code == 1 else "Edit"
                            summary_lines.append(
                                f"Configured Power Pages Form '{matched_flow.name}' ({mode_desc} mode) available for this table ({len(portal_cands)} form(s))"
                            )
                        else:
                            confidence = "Uncorrelated Automation"
                            badge = "⚠️"
                            summary_lines.append(
                                f"Changed by {user_val}, but no matching active flow was identified for field '{f_display}'"
                            )

                process_url = ""
                process_link_label = ""
                flow_history_url = ""
                flow_history_label = ""

                env = self.environment_id or "default"

                if matched_run:
                    if matched_run.source == "portals":
                        process_url = matched_run.maker_url
                        process_link_label = "Open Web Form Session in Dynamics ↗"
                        flow_history_url = matched_run.maker_url
                        flow_history_label = "Web Form Session in Dynamics ↗"
                    elif matched_run.source == "logicapps":
                        process_url = matched_run.maker_url
                        process_link_label = "Open Run in Azure Portal ↗"
                        flow_history_url = matched_run.maker_url
                        flow_history_label = "Azure Portal Run ↗"
                    elif matched_run.maker_url:
                        process_url = matched_run.maker_url
                        process_link_label = "Open Run History ↗"
                    elif matched_run.source == "asyncoperations" and self.org_url and matched_run.run_id:
                        process_url = f"{self.org_url}/main.aspx?etn=asyncoperation&pagetype=entityrecord&id={matched_run.run_id}"
                        process_link_label = "Open System Job in Dynamics ↗"

                    # Also link to the full 28-day flow history
                    if matched_run.source not in ("logicapps", "portals"):
                        wid = matched_run.workflow_id or (matched_flow.workflow_id if matched_flow else "")
                        if wid:
                            flow_history_url = f"https://make.powerautomate.com/environments/{env}/flows/{wid}/details"
                            flow_history_label = "Flow History (28d) ↗"
                elif matched_flow:
                    if matched_flow.category == 7:
                        raw_def = matched_flow.raw_definition or {}
                        src_tbl = raw_def.get("source_table") or "adx_entityform"
                        wid = matched_flow.workflow_id
                        if self.org_url and wid:
                            process_url = f"{self.org_url}/main.aspx?etn={src_tbl}&pagetype=entityrecord&id={wid}"
                            process_link_label = "Open Portal Form in Dynamics ↗"
                            flow_history_url = process_url
                            flow_history_label = "Portal Form in Dynamics ↗"
                    elif matched_flow.category == 6:
                        p_url = matched_flow.raw_definition.get("portal_url") or ""
                        if not p_url and matched_flow.workflow_id:
                            p_url = f"https://portal.azure.com/#@/resource{matched_flow.workflow_id}"
                        process_url = p_url
                        process_link_label = "Open Logic App in Azure Portal ↗"
                        flow_history_url = p_url
                        flow_history_label = "Logic App in Azure Portal ↗"
                    elif matched_flow.category == 5 and matched_flow.workflow_id:
                        process_url = f"https://make.powerautomate.com/environments/{env}/flows/{matched_flow.workflow_id}/details"
                        process_link_label = "Open Flow History ↗"
                        flow_history_url = process_url
                        flow_history_label = "Flow History (28d) ↗"
                    elif matched_flow.category == 0 and self.org_url and matched_flow.workflow_id:
                        process_url = f"{self.org_url}/main.aspx?etn=workflow&pagetype=entityrecord&id={matched_flow.workflow_id}"
                        process_link_label = "Open Workflow in Dynamics ↗"
                        flow_history_url = process_url
                        flow_history_label = "Workflow in Dynamics ↗"

                results.append(
                    AuditCorrelationItem(
                        audit_id=audit_record.get("auditid") or "",
                        timestamp=dt,
                        changed_date_str=changed_date_str,
                        user_name=display_user,
                        is_automation=is_auto,
                        event=event_type,
                        changed_field=f_display,
                        changed_field_logical=f_name,
                        old_value=old_v,
                        new_value=new_v,
                        transaction_id=tx_id,
                        extra_info=extra_info,
                        matched_flow=matched_flow,
                        matched_run=matched_run,
                        candidate_flows=matching_flows_for_attr,
                        confidence=confidence,
                        confidence_badge=badge,
                        attribution_summary="; ".join(summary_lines),
                        process_url=process_url,
                        process_link_label=process_link_label,
                        flow_history_url=flow_history_url,
                        flow_history_label=flow_history_label,
                    )
                )

        blob_fetcher_executor.shutdown(wait=False)
        return results

    def correlate_outbound(
        self,
        raw_audit_details: List[Dict[str, Any]],
        candidate_flows: Optional[List[CorrelatedFlow]] = None,
        executions: Optional[List[FlowRunExecution]] = None,
        target_entity_logical: str = "",
        attr_metadata: Optional[Dict[str, str]] = None,
        tolerance_seconds: int = 60,
        raw_workflows: Optional[List[Dict[str, Any]]] = None,
        focus_workflow_ids: Optional[Set[str]] = None,
        focus_keywords: Optional[List[str]] = None,
        ignore_name_patterns: Optional[List[str]] = None,
    ) -> List[OutboundTriggerItem]:
        """
        Evaluate downstream automations (Cloud Flows, Classic Workflows, Logic Apps)
        that were triggered (or eligible to be triggered) by each audit mutation.
        Performs multi-field condition and OData expression evaluation against mutated values.
        Identifies active flows with matching runs, qualified candidates, and
        alerts on TURNED-OFF (statecode=0) flows that should have run.
        """
        norm_target = target_entity_logical.strip().lower()

        clean_patterns = [
            p.strip().lower() for p in (ignore_name_patterns or []) if p and p.strip()
        ]
        if clean_patterns and raw_workflows:
            raw_workflows = [
                w for w in raw_workflows
                if not any(pat in str(w.get("name") or "").lower() for pat in clean_patterns)
            ]

        if candidate_flows is None:
            candidate_flows = self.candidate_flows
        if (not candidate_flows) and raw_workflows:
            candidate_flows = [extract_flow_definition(w, norm_target) for w in raw_workflows]

        if clean_patterns and candidate_flows:
            candidate_flows = [
                cf for cf in candidate_flows
                if not any(pat in (cf.name or "").lower() for pat in clean_patterns)
            ]

        if executions is None:
            executions = self.executions

        # 1. Group audit details by mutation/transaction to prevent duplicate rows per save
        mutations: Dict[str, Dict[str, Any]] = {}
        for d in raw_audit_details:
            audit_record = d.get("AuditRecord") or {}
            audit_id = clean_guid(audit_record.get("auditid") or d.get("auditid") or "")
            tx_id = clean_guid(audit_record.get("transactionid") or d.get("transactionid") or "")
            raw_dt = audit_record.get("createdon") or d.get("createdon")
            if not raw_dt:
                continue
            try:
                dt = dateutil.parser.isoparse(str(raw_dt))
                dt_norm = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            user_name = (
                audit_record.get("userid_formatted")
                or d.get("ChangedBy")
                or d.get("user_name")
                or (audit_record.get("_userid_value@OData.Community.Display.V1.FormattedValue"))
                or "System / User"
            )
            act_code = audit_record.get("action") or d.get("action")
            act_label = (
                ACTION_MAP.get(act_code, "Update")
                if isinstance(act_code, int)
                else str(d.get("event") or d.get("Event") or "Update")
            )

            # Group key: transaction ID if available, else timestamp truncated to the second + user
            if tx_id:
                m_key = f"tx_{tx_id}"
            else:
                m_key = f"time_{dt_norm.strftime('%Y%m%d%H%M%S')}_{user_name}"

            if m_key not in mutations:
                mutations[m_key] = {
                    "audit_id": audit_id,
                    "transaction_id": tx_id,
                    "timestamp": dt_norm,
                    "changed_date_str": dt_norm.strftime("%d/%m/%Y %H:%M:%S"),
                    "user_name": str(user_name),
                    "event": act_label,
                    "changed_fields": [],
                    "changed_field_labels": [],
                    "old_values": {},
                    "new_values": {},
                }

            # Support both raw Dataverse AuditDetail dicts (with OldValue / NewValue dicts)
            # and flattened rows (with ChangedField / old_value / new_value)
            old_vals_dict = d.get("OldValue") if isinstance(d.get("OldValue"), dict) else {}
            new_vals_dict = d.get("NewValue") if isinstance(d.get("NewValue"), dict) else {}

            field_keys = set()
            for k in list(old_vals_dict.keys()) + list(new_vals_dict.keys()):
                k_str = str(k).strip()
                if not k_str or "@" in k_str:
                    continue
                # Normalize lookup attributes like _parentcustomerid_value -> parentcustomerid
                if k_str.startswith("_") and k_str.endswith("_value"):
                    clean_k = k_str[1:-6].lower()
                    field_keys.add(clean_k)
                    field_keys.add(k_str.lower())
                else:
                    field_keys.add(k_str.lower())

            flat_f = str(d.get("ChangedField") or d.get("changed_field_logical") or d.get("Field") or "").strip().lower()
            if flat_f and "@" not in flat_f:
                if flat_f.startswith("_") and flat_f.endswith("_value"):
                    clean_flat = flat_f[1:-6]
                    field_keys.add(clean_flat)
                    field_keys.add(flat_f)
                else:
                    field_keys.add(flat_f)

            for f_log in sorted(field_keys):
                if f_log not in mutations[m_key]["changed_fields"]:
                    mutations[m_key]["changed_fields"].append(f_log)
                    clean_col = f_log[1:-6] if f_log.startswith("_") and f_log.endswith("_value") else f_log
                    f_disp = (
                        (attr_metadata.get(f_log) if attr_metadata else None)
                        or (attr_metadata.get(clean_col) if attr_metadata else None)
                        or d.get("changed_field")
                        or clean_col
                    )
                    mutations[m_key]["changed_field_labels"].append(str(f_disp))

                # Old values (support both raw key and lookup key)
                val_o = old_vals_dict.get(f_log)
                if val_o is None and f_log.startswith("_") and f_log.endswith("_value"):
                    val_o = old_vals_dict.get(f_log[1:-6])
                elif val_o is None and not f_log.startswith("_"):
                    val_o = old_vals_dict.get(f"_{f_log}_value")
                if val_o is None and f_log in (flat_f, flat_f.replace("_value", "").lstrip("_")):
                    val_o = d.get("old_value") if d.get("old_value") is not None else d.get("OldValue")
                if val_o is not None:
                    mutations[m_key]["old_values"][f_log] = val_o

                # New values
                val_n = new_vals_dict.get(f_log)
                if val_n is None and f_log.startswith("_") and f_log.endswith("_value"):
                    val_n = new_vals_dict.get(f_log[1:-6])
                elif val_n is None and not f_log.startswith("_"):
                    val_n = new_vals_dict.get(f"_{f_log}_value")
                if val_n is None and f_log in (flat_f, flat_f.replace("_value", "").lstrip("_")):
                    val_n = d.get("new_value") if d.get("new_value") is not None else d.get("NewValue")
                if val_n is not None:
                    mutations[m_key]["new_values"][f_log] = val_n

        outbound_items: List[OutboundTriggerItem] = []

        # Filter candidate flows that have automated triggers for this entity (exclude purely inbound forms and non-trigger processes)
        norm_focus_kw = [k.lower().strip() for k in (focus_keywords or []) if k.strip()]
        clean_focus_ids = {clean_guid(fid) for fid in (focus_workflow_ids or []) if fid}

        target_flows = []
        for f in candidate_flows:
            if f.category == 7:
                continue
            f_wid = clean_guid(f.workflow_id)
            f_name_lower = (f.name or "").lower()
            is_focused = bool(clean_focus_ids and f_wid in clean_focus_ids) or any(k in f_name_lower for k in norm_focus_kw)
            if f.triggers_on_entity or is_focused:
                target_flows.append(f)

        # 2. Evaluate each mutation against candidate flows
        for m_key, mut in mutations.items():
            t_audit = mut["timestamp"]
            mut_event = mut["event"].lower()
            mut_fields = set(mut["changed_fields"])

            for flow in target_flows:
                f_wid = clean_guid(flow.workflow_id)
                f_name_lower = (flow.name or "").lower()
                is_focused = bool(clean_focus_ids and f_wid in clean_focus_ids) or any(k in f_name_lower for k in norm_focus_kw)

                # 2a. Event matching
                if flow.trigger_events:
                    flow_events_lower = [e.lower() for e in flow.trigger_events]
                    is_flow_update = any(
                        e in fe for fe in flow_events_lower for e in ("update", "modified", "addedormodified", "neworupdated", "3", "4")
                    )
                    is_flow_create = any(
                        e in fe for fe in flow_events_lower for e in ("create", "added", "addedormodified", "neworupdated", "1", "4")
                    )
                    is_flow_delete = any(
                        e in fe for fe in flow_events_lower for e in ("delete", "deleted", "2")
                    )

                    ev_matched = False
                    if mut_event in ("update", "deactivate", "activate", "assign", "share", "unshare", "win", "lose", "attribute change", "status change"):
                        ev_matched = is_flow_update
                    elif mut_event in ("create", "insert", "added"):
                        ev_matched = is_flow_create
                    elif mut_event in ("delete", "deleted"):
                        ev_matched = is_flow_delete
                    else:
                        ev_matched = any(mut_event in fe or fe in mut_event for fe in flow_events_lower)

                    if not ev_matched and not is_focused:
                        continue

                # 2b. Evaluate condition rules (OData filter rows & PA trigger conditions)
                cond_eval = evaluate_condition_rules(
                    flow.trigger_filter_rule,
                    flow.trigger_conditions,
                    mut_fields,
                    mut["new_values"],
                )

                # If this mutation directly violates a required condition, the flow CANNOT trigger
                if cond_eval["is_violated"]:
                    continue

                # 2c. Column filter matching ("Select columns")
                has_select_cols = bool(flow.trigger_attributes)
                if has_select_cols:
                    flow_attrs = [a.lower().strip() for a in flow.trigger_attributes if a.strip()]
                    matched_cols = []
                    for a in flow_attrs:
                        clean_a = a[1:-6] if a.startswith("_") and a.endswith("_value") else a
                        if a in mut_fields or clean_a in mut_fields or f"_{clean_a}_value" in mut_fields:
                            matched_cols.append(a)
                    if not matched_cols:
                        # Specified column filter did not include any of the updated fields
                        continue
                else:
                    # Flow has NO select columns -> fires on ANY column update
                    if cond_eval["has_rules"]:
                        if cond_eval["is_satisfied"]:
                            matched_cols = [f for f in cond_eval["referenced_fields"] if f in mut_fields] or list(mut["changed_fields"])
                        elif cond_eval["touches_mutation_fields"]:
                            matched_cols = [f for f in cond_eval["referenced_fields"] if f in mut_fields]
                        else:
                            # Rule was not touched in this mutation, but flow fires on any update
                            # Condition requires existing Dataverse row state
                            matched_cols = list(mut["changed_fields"])
                    else:
                        # Truly unconditional trigger: fires on ANY column update
                        matched_cols = list(mut["changed_fields"])

                # 2d. Check Active vs. Turned Off
                is_active = (flow.state_code == 1)
                state_label = "Active" if is_active else "🔴 Turned Off / Draft"

                # URLs
                process_url = ""
                flow_history_url = ""
                if flow.category == 5 and flow.workflow_id and self.environment_id:
                    process_url = f"https://make.powerautomate.com/environments/{self.environment_id}/flows/{flow.workflow_id}/details"
                    flow_history_url = f"https://make.powerautomate.com/environments/{self.environment_id}/flows/{flow.workflow_id}/runs"
                elif flow.category == 0 and flow.workflow_id and self.org_url:
                    process_url = f"{self.org_url}/main.aspx?pagetype=entityrecord&etn=workflow&id={flow.workflow_id}"
                elif flow.category == 6:
                    process_url = flow.raw_definition.get("portal_url") or ""

                matched_cols_str = ", ".join(f"`{c}`" for c in matched_cols[:4])
                if len(matched_cols) > 4:
                    matched_cols_str += f" (+{len(matched_cols)-4} more)"

                if not is_active:
                    # Turned off flow alert!
                    # Only triggered because all conditions/columns were satisfied or conditional
                    qualification_status = "🔴 Suppressed (Turned Off)"
                    status_badge = "🔴"
                    if cond_eval["has_rules"] and cond_eval["is_satisfied"]:
                        cond_note = f"Conditions met: {cond_eval['evaluation_summary']}"
                    elif cond_eval["is_partially_satisfied"]:
                        cond_note = f"Conditions: {cond_eval['evaluation_summary']}"
                    elif not has_select_cols and not cond_eval["has_rules"]:
                        cond_note = "Unrestricted trigger (fires on any field update)"
                    else:
                        cond_note = f"Columns matched: {matched_cols_str}"
                    status_reason = f"Flow is Turned Off (statecode=0). Eligible to trigger: {cond_note}"
                    matched_run = None
                    run_id = ""
                    run_status = "Turned Off"
                    started_on = None
                    dur_str = ""
                else:
                    # Active flow: check for downstream run in executions
                    matching_runs = []
                    for ex in executions:
                        ex_wid = clean_guid(ex.workflow_id)
                        f_wid = clean_guid(flow.workflow_id)
                        wid_matches = bool(ex_wid and f_wid and ex_wid == f_wid)
                        name_matches = bool(ex.flow_name and ex.flow_name.lower().strip() == flow.name.lower().strip())
                        arm_matches = bool(flow.category == 6 and ex.workflow_id and flow.workflow_id and ex.workflow_id.lower().strip() == flow.workflow_id.lower().strip())

                        if wid_matches or name_matches or arm_matches:
                            if ex.started_on:
                                ex_start = ex.started_on if ex.started_on.tzinfo else ex.started_on.replace(tzinfo=timezone.utc)
                                diff_sec = (ex_start - t_audit).total_seconds()
                                if -5 <= diff_sec <= tolerance_seconds:
                                    matching_runs.append((diff_sec, ex))

                    if matching_runs:
                        matching_runs.sort(key=lambda x: abs(x[0]))
                        matched_run = matching_runs[0][1]
                        qualification_status = "🟢 Confirmed Triggered"
                        status_badge = "🟢"
                        run_id = matched_run.run_id
                        run_status = matched_run.status
                        started_on = matched_run.started_on
                        dur_str = matched_run.duration_str
                        status_reason = f"Downstream run confirmed: {run_id} ({run_status}, duration: {dur_str or '—'})"
                    else:
                        matched_run = None
                        run_id = ""
                        started_on = None
                        dur_str = ""
                        if cond_eval["has_rules"] and cond_eval["is_satisfied"]:
                            qualification_status = "🟢 Condition Satisfied"
                            status_badge = "🟢"
                            run_status = "Condition Met"
                            status_reason = f"Trigger condition satisfied: {cond_eval['evaluation_summary']}"
                        elif cond_eval["is_partially_satisfied"] or cond_eval["unmutated_requirements"]:
                            qualification_status = "🟡 Conditional Candidate"
                            status_badge = "🟡"
                            run_status = "Requires Condition"
                            status_reason = f"Requires state: {cond_eval['evaluation_summary']}"
                        elif cond_eval["has_rules"]:
                            qualification_status = "🟡 Conditional Candidate"
                            status_badge = "🟡"
                            run_status = "Requires Condition"
                            status_reason = f"Rule: {flow.trigger_filter_rule or '; '.join(flow.trigger_conditions[:1])}"
                        else:
                            qualification_status = "🔵 Qualified to Trigger"
                            status_badge = "🔵"
                            run_status = "Qualified"
                            if not has_select_cols:
                                status_reason = f"Unrestricted trigger (fires on any field update, no run logged within {tolerance_seconds}s)"
                            else:
                                status_reason = f"Eligible on columns: {matched_cols_str} (No run logged within {tolerance_seconds}s)"

                outbound_items.append(
                    OutboundTriggerItem(
                        audit_id=mut["audit_id"],
                        timestamp=mut["timestamp"],
                        changed_date_str=mut["changed_date_str"],
                        user_name=mut["user_name"],
                        event=mut["event"],
                        changed_fields=mut["changed_fields"],
                        changed_field_labels=mut["changed_field_labels"],
                        old_values=mut["old_values"],
                        new_values=mut["new_values"],
                        transaction_id=mut["transaction_id"],
                        flow_id=flow.workflow_id,
                        flow_name=flow.name,
                        category=flow.category,
                        category_label=flow.category_label,
                        is_active=is_active,
                        state_label=state_label,
                        modified_on=flow.modified_on,
                        trigger_events=flow.trigger_events,
                        trigger_attributes=flow.trigger_attributes,
                        matched_columns=matched_cols,
                        filter_expression=flow.trigger_filter_rule,
                        trigger_conditions=flow.trigger_conditions,
                        qualification_status=qualification_status,
                        status_badge=status_badge,
                        status_reason=status_reason,
                        matched_run=matched_run,
                        run_status=run_status,
                        run_id=run_id,
                        started_on=started_on,
                        duration_str=dur_str,
                        process_url=process_url,
                        flow_history_url=flow_history_url,
                    )
                )

        return outbound_items




def group_correlations(items: List[AuditCorrelationItem]) -> List[GroupedAuditExecution]:
    """
    Group correlated audit items by each history record of Attributed Flow / Process.
    Combines all variables modified together in the same flow run, system job, or user action.
    """
    groups: Dict[str, List[AuditCorrelationItem]] = {}

    for item in items:
        # Group by each history record of Attributed Flow / Process
        # Multi-variable mutations caused by the SAME flow execution run or transaction stay grouped together.
        if item.matched_run and item.matched_run.run_id:
            # Exact execution run history record (Cloud Flow run / Flow session / Async operation)
            # All fields modified together in this single run execution stay in ONE row!
            gid = f"run_{item.matched_run.run_id}"
        elif item.matched_flow:
            # Candidate workflow without run log: group by flow ID + audit transaction/timestamp
            # so fields modified in the same transaction stay in ONE row, but separate trigger events are split
            tx_key = item.transaction_id if (item.transaction_id and item.transaction_id not in ("-", "")) else item.changed_date_str
            gid = f"flow_{item.matched_flow.workflow_id}_{tx_key}"
        elif item.transaction_id and item.transaction_id not in ("-", ""):
            gid = f"tx_{item.transaction_id}_{item.changed_date_str}_{item.user_name}"
        else:
            audit_key = item.audit_id or item.changed_date_str
            gid = f"aud_{audit_key}_{item.user_name}"

        if gid not in groups:
            groups[gid] = []
        groups[gid].append(item)

    grouped_results: List[GroupedAuditExecution] = []

    for gid, group_items in groups.items():
        first = group_items[0]
        fields_changed = []
        changes_parts = []

        for it in group_items:
            # Safely stringify old and new values to prevent PyArrow serialization errors
            if it.old_value is None:
                o_str = ""
            elif isinstance(it.old_value, (bytes, bytearray)):
                try:
                    o_str = it.old_value.decode("utf-8", errors="replace")
                except Exception:
                    o_str = str(it.old_value)
            elif isinstance(it.old_value, bool):
                o_str = "Yes" if it.old_value else "No"
            else:
                o_str = str(it.old_value)

            if it.new_value is None:
                n_str = ""
            elif isinstance(it.new_value, (bytes, bytearray)):
                try:
                    n_str = it.new_value.decode("utf-8", errors="replace")
                except Exception:
                    n_str = str(it.new_value)
            elif isinstance(it.new_value, bool):
                n_str = "Yes" if it.new_value else "No"
            else:
                n_str = str(it.new_value)

            fields_changed.append(
                {
                    "Field": str(it.changed_field or "-"),
                    "Logical Field": str(it.changed_field_logical or "-"),
                    "Old Value": o_str,
                    "New Value": n_str,
                }
            )
            old_disp = o_str if o_str != "" else "(blank)"
            new_disp = n_str if n_str != "" else "(blank)"
            changes_parts.append(f"{it.changed_field}: {old_disp} ➔ {new_disp}")

        changes_summary = "\n".join(changes_parts)

        flow_name = "-"
        if first.matched_run:
            flow_name = f"⚡ {first.matched_run.flow_name}"
        elif first.matched_flow:
            flow_name = f"📋 {first.matched_flow.name}"
        elif first.is_automation:
            flow_name = "⚠️ Uncorrelated Automation"
        else:
            flow_name = f"👤 {first.user_name}"

        run_status = (
            first.matched_run.status
            if first.matched_run
            else ("Candidate Flow" if first.matched_flow else ("Uncorrelated Automation" if first.is_automation else "User Action"))
        )
        duration_str = first.matched_run.duration_str if first.matched_run else ""
        run_id = first.matched_run.run_id if first.matched_run else ""
        started_on = first.matched_run.started_on if first.matched_run else first.timestamp
        completed_on = first.matched_run.completed_on if first.matched_run else None

        field_lines = "\n".join([str(it.changed_field) for it in group_items])
        old_lines = "\n".join([str(f["Old Value"]) if f["Old Value"] != "" else "-" for f in fields_changed])
        new_lines = "\n".join([str(f["New Value"]) if f["New Value"] != "" else "-" for f in fields_changed])

        # Carry over flow history links
        flow_hist_url = first.flow_history_url
        flow_hist_label = first.flow_history_label
        if not flow_hist_url:
            for it in group_items:
                if it.flow_history_url:
                    flow_hist_url = it.flow_history_url
                    flow_hist_label = it.flow_history_label
                    break

        grouped_results.append(
            GroupedAuditExecution(
                group_id=gid,
                timestamp=first.timestamp,
                changed_date_str=first.changed_date_str,
                user_name=first.user_name,
                is_automation=first.is_automation,
                event=first.event,
                transaction_id=first.transaction_id,
                flow_name=flow_name,
                process_url=first.process_url,
                process_link_label=first.process_link_label,
                flow_history_url=flow_hist_url,
                flow_history_label=flow_hist_label,
                run_id=run_id,
                started_on=started_on,
                completed_on=completed_on,
                confidence=first.confidence,
                confidence_badge=first.confidence_badge,
                run_status=run_status,
                duration_str=duration_str,
                attribution_summary=first.attribution_summary,
                fields_changed=fields_changed,
                changed_field_lines=field_lines,
                old_value_lines=old_lines,
                new_value_lines=new_lines,
                changes_summary=changes_summary,
                field_count=len(fields_changed),
                matched_flow=first.matched_flow,
                matched_run=first.matched_run,
            )
        )

    def _safe_sort_key(x: GroupedAuditExecution) -> datetime:
        dt = x.started_on or x.timestamp
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    # Sort groups in descending chronological order
    grouped_results.sort(key=_safe_sort_key, reverse=True)

    # If any flow was triggered more than once across the audit history,
    # annotate each trigger instance with (Trigger #1 of N) in chronological sequence
    flow_key_to_groups: Dict[str, List[GroupedAuditExecution]] = {}
    for g in grouped_results:
        key = None
        if g.matched_run and g.matched_run.workflow_id:
            key = f"wf_{g.matched_run.workflow_id}"
        elif g.matched_flow and g.matched_flow.workflow_id:
            key = f"wf_{g.matched_flow.workflow_id}"
        if key:
            flow_key_to_groups.setdefault(key, []).append(g)

    for key, g_list in flow_key_to_groups.items():
        if len(g_list) > 1:
            # Sort chronologically so the earliest trigger is #1
            g_list_chrono = sorted(g_list, key=_safe_sort_key)
            for seq, g in enumerate(g_list_chrono, start=1):
                g.flow_name = f"{g.flow_name} (Trigger #{seq} of {len(g_list)})"

    return grouped_results


def render_dynamics_audit_table_html(
    grouped_items: List[GroupedAuditExecution],
    show_flow_columns: bool = True,
    title: str = "Audit History",
    filter_label: str = "All Fields",
) -> str:
    """
    Renders an HTML table that visually replicates the Microsoft Dynamics 365
    Model-Driven App Audit History grid (like the attachment), including stacked
    variable rows via rowspan and clickable Flow Run History links.
    """
    import html

    rows_html = []
    for g in grouped_items:
        is_auto = is_automation_user(g.user_name)
        # In Dynamics 365 Model-Driven App, # PowerAutomate and Event names are teal (#008272)
        user_color = "#008272" if is_auto else "#0078d4"
        event_color = "#008272" if is_auto else "#0078d4"

        fields = g.fields_changed if g.fields_changed else [{"Field": "-", "Old Value": "", "New Value": ""}]
        n_fields = len(fields)

        # Flow attribution cell
        flow_col = ""
        link_col = ""
        if show_flow_columns:
            if g.flow_name and g.flow_name != "-":
                flow_badge_bg = "#f0f6ff" if "⚡" in g.flow_name else "#f3f2f1"
                flow_badge_border = "#c7e0f4" if "⚡" in g.flow_name else "#edebe9"
                flow_color = "#005a9e" if "⚡" in g.flow_name else "#323130"
                flow_col = (
                    f'<div style="display: inline-block; background: {flow_badge_bg}; color: {flow_color}; '
                    f'border: 1px solid {flow_badge_border}; border-radius: 3px; padding: 2px 7px; font-weight: 600; font-size: 12px; margin-bottom: 3px;">'
                    f'{html.escape(g.flow_name)}</div>'
                    f'<div style="font-size: 11px; color: #605e5c;">{g.confidence_badge} {html.escape(g.confidence)}</div>'
                )
            else:
                flow_col = '<span style="color: #a19f9d; font-size: 12px;">—</span>'

            # Link of the history of the flows
            link_items = []
            if g.process_url:
                if "/runs/" in g.process_url:
                    link_items.append(
                        f'<a href="{html.escape(g.process_url)}" target="_blank" rel="noopener noreferrer" '
                        f'style="display: inline-flex; align-items: center; gap: 4px; color: #0078d4; text-decoration: none; '
                        f'font-weight: 600; font-size: 12px; padding: 3px 8px; background: #f0f6ff; border: 1px solid #c7e0f4; border-radius: 3px;">'
                        f'⚡ Open Run History ↗</a>'
                    )
                else:
                    link_items.append(
                        f'<a href="{html.escape(g.process_url)}" target="_blank" rel="noopener noreferrer" '
                        f'style="display: inline-flex; align-items: center; gap: 4px; color: #0078d4; text-decoration: none; '
                        f'font-weight: 600; font-size: 12px; padding: 3px 8px; background: #f0f6ff; border: 1px solid #c7e0f4; border-radius: 3px;">'
                        f'🔗 {html.escape(g.process_link_label or "Open Flow ↗")}</a>'
                    )

            if g.flow_history_url and g.flow_history_url != g.process_url:
                link_items.append(
                    f'<a href="{html.escape(g.flow_history_url)}" target="_blank" rel="noopener noreferrer" '
                    f'style="display: inline-flex; align-items: center; gap: 4px; color: #005a9e; text-decoration: none; '
                    f'font-weight: 500; font-size: 11px; padding: 2px 7px; background: #ffffff; border: 1px solid #d2d0ce; border-radius: 3px; margin-top: 3px;">'
                    f'📋 {html.escape(g.flow_history_label or "Flow History (28d) ↗")}</a>'
                )

            if link_items:
                link_col = '<div style="display: flex; flex-direction: column; gap: 3px;">' + "".join(link_items) + "</div>"
            else:
                link_col = '<span style="color: #a19f9d; font-size: 12px;">—</span>'

        flow_td = f'<td rowspan="{n_fields}" style="padding: 8px 12px; vertical-align: top; border-bottom: 1px solid #d2d0ce; border-right: 1px solid #edebe9;">{flow_col}</td>' if show_flow_columns else ""
        link_td = f'<td rowspan="{n_fields}" style="padding: 8px 12px; vertical-align: top; border-bottom: 1px solid #d2d0ce;">{link_col}</td>' if show_flow_columns else ""

        # First row of the transaction group
        f0 = fields[0]
        f0_label = html.escape(str(f0.get("Field") or "-"))
        f0_old = html.escape(str(f0.get("Old Value") if f0.get("Old Value") is not None and f0.get("Old Value") != "" else ""))
        f0_new = html.escape(str(f0.get("New Value") if f0.get("New Value") is not None and f0.get("New Value") != "" else ""))
        f0_bottom = "border-bottom: 1px solid #d2d0ce;" if n_fields == 1 else "border-bottom: 1px solid #f3f2f1;"

        first_tr = (
            f'<tr style="background: #ffffff;">'
            f'<td rowspan="{n_fields}" style="padding: 8px 8px; vertical-align: top; text-align: center; color: #a19f9d; font-size: 11px; border-bottom: 1px solid #d2d0ce; border-right: 1px solid #edebe9; width: 28px;">⬜</td>'
            f'<td rowspan="{n_fields}" style="padding: 8px 12px; vertical-align: top; white-space: nowrap; font-size: 13px; color: #201f1e; border-bottom: 1px solid #d2d0ce; border-right: 1px solid #edebe9;">{html.escape(g.changed_date_str)}</td>'
            f'<td rowspan="{n_fields}" style="padding: 8px 12px; vertical-align: top; white-space: nowrap; font-size: 13px; color: {user_color}; font-weight: 500; cursor: pointer; border-bottom: 1px solid #d2d0ce; border-right: 1px solid #edebe9;">{html.escape(g.user_name)}</td>'
            f'<td rowspan="{n_fields}" style="padding: 8px 12px; vertical-align: top; white-space: nowrap; font-size: 13px; color: {event_color}; font-weight: 500; cursor: pointer; border-bottom: 1px solid #d2d0ce; border-right: 1px solid #edebe9;">{html.escape(g.event)}</td>'
            f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #201f1e; {f0_bottom} border-right: 1px solid #edebe9;">{f0_label}</td>'
            f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #605e5c; {f0_bottom} border-right: 1px solid #edebe9;">{f0_old}</td>'
            f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #201f1e; font-weight: 500; {f0_bottom} border-right: 1px solid #edebe9;">{f0_new}</td>'
            f'{flow_td}'
            f'{link_td}'
            f'</tr>'
        )
        rows_html.append(first_tr)

        # Subsequent rows for multi-variable changes within this transaction
        for idx in range(1, n_fields):
            fi = fields[idx]
            fi_label = html.escape(str(fi.get("Field") or "-"))
            fi_old = html.escape(str(fi.get("Old Value") if fi.get("Old Value") is not None and fi.get("Old Value") != "" else ""))
            fi_new = html.escape(str(fi.get("New Value") if fi.get("New Value") is not None and fi.get("New Value") != "" else ""))
            fi_bottom = "border-bottom: 1px solid #d2d0ce;" if idx == (n_fields - 1) else "border-bottom: 1px solid #f3f2f1;"

            sub_tr = (
                f'<tr style="background: #ffffff;">'
                f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #201f1e; {fi_bottom} border-right: 1px solid #edebe9;">{fi_label}</td>'
                f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #605e5c; {fi_bottom} border-right: 1px solid #edebe9;">{fi_old}</td>'
                f'<td style="padding: 6px 12px; vertical-align: top; font-size: 13px; color: #201f1e; font-weight: 500; {fi_bottom} border-right: 1px solid #edebe9;">{fi_new}</td>'
                f'</tr>'
            )
            rows_html.append(sub_tr)

    flow_th = '<th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 170px;"><span style="color: #a19f9d; margin-right: 6px;">|</span>Attributed Flow / Process</th>' if show_flow_columns else ""
    link_th = '<th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; min-width: 170px;"><span style="color: #a19f9d; margin-right: 6px;">|</span>Flow History Link</th>' if show_flow_columns else ""

    table_html = f"""
    <div style="background: #ffffff; border: 1px solid #d2d0ce; border-radius: 4px; box-shadow: 0 1.6px 3.6px 0 rgba(0,0,0,0.13), 0 0.3px 0.9px 0 rgba(0,0,0,0.11); font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; margin: 12px 0 24px 0; overflow: hidden;">
        <!-- Dynamics 365 Toolbar / Header matching attachment -->
        <div style="padding: 16px 20px 10px 20px; border-bottom: 1px solid #edebe9; background: #ffffff;">
            <div style="font-size: 22px; font-weight: 600; color: #201f1e; margin-bottom: 10px; letter-spacing: -0.2px;">{html.escape(title)}</div>
            <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
                <div style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: #605e5c;">
                    <span>Filter on:</span>
                    <span style="border: 1px solid #8a8886; padding: 3px 14px; background: #ffffff; color: #201f1e; border-radius: 2px; font-size: 13px; font-weight: 500; display: inline-flex; align-items: center; gap: 12px;">
                        {html.escape(filter_label)} <span style="font-size: 9px; color: #605e5c;">▼</span>
                    </span>
                </div>
                <div style="font-size: 12px; color: #605e5c;">
                    Total <strong>{len(grouped_items)}</strong> transaction(s) recorded
                </div>
            </div>
            <div style="margin-top: 10px; font-size: 12px; color: #605e5c; display: flex; align-items: center; gap: 6px;">
                <span style="color: #605e5c; font-size: 13px;">🗑️</span>
                <span style="color: #605e5c; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;">DELETE CHANGE HISTORY</span>
            </div>
        </div>

        <!-- Dynamics 365 Grid -->
        <div style="overflow-x: auto; max-width: 100%;">
            <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left;">
                <thead>
                    <tr style="background: #faf9f8; height: 38px;">
                        <th style="padding: 8px 8px; width: 28px; text-align: center; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9;">
                            <span style="font-size: 12px; opacity: 0.6;">⬜</span>
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 130px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>Changed Date
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 140px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>Changed By
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; width: 85px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>Event
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 180px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>Changed Field
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 150px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>Old Value
                        </th>
                        <th style="padding: 8px 12px; text-align: left; font-weight: 600; color: #605e5c; border-bottom: 2px solid #edebe9; border-right: 1px solid #edebe9; min-width: 160px; white-space: nowrap;">
                            <span style="color: #a19f9d; margin-right: 6px;">|</span>New Value
                        </th>
                        {flow_th}
                        {link_th}
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>
        </div>
    </div>
    """
    return table_html


def render_flow_history_table_html(grouped_items: List[GroupedAuditExecution]) -> str:
    """
    Renders an HTML table matching the Cloud Flow History plugin styling,
    where each variable modification is presented on its own line (next line),
    with pastel row tinting, status badges, and clickable Open Run links.
    """
    import html
    import hashlib

    if not grouped_items:
        return '<div style="padding: 16px; color: #8a8886; font-style: italic;">No process execution records match the current filter.</div>'

    rows_html = []
    for g in grouped_items:
        # Pastel background based on flow name matching Cloud Flow History plugin
        clean_name = str(g.flow_name or "").replace("⚡", "").replace("📋", "").strip()
        hue = int(hashlib.md5(clean_name.encode()).hexdigest(), 16) % 360 if clean_name else 210
        row_bg = f"hsla({hue}, 35%, 30%, 0.22)"

        # Status styling badge
        s_lower = str(g.run_status or "").lower()
        if "succeeded" in s_lower:
            status_badge = '<span style="background: rgba(40,167,69,0.3); color: #4ade80; border: 1px solid rgba(40,167,69,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">Succeeded</span>'
        elif "failed" in s_lower:
            status_badge = '<span style="background: rgba(220,53,69,0.3); color: #f87171; border: 1px solid rgba(220,53,69,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">Failed</span>'
        elif any(k in s_lower for k in ("cancelled", "timedout", "running")):
            status_badge = f'<span style="background: rgba(255,193,7,0.3); color: #fbbf24; border: 1px solid rgba(255,193,7,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">{html.escape(g.run_status)}</span>'
        elif "candidate" in s_lower:
            status_badge = '<span style="background: rgba(14,165,233,0.25); color: #38bdf8; border: 1px solid rgba(14,165,233,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">Candidate Flow</span>'
        else:
            status_badge = f'<span style="background: rgba(148,163,184,0.25); color: #cbd5e1; border: 1px solid rgba(148,163,184,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">{html.escape(g.run_status)}</span>'

        # Started date
        start_display = g.started_on.strftime("%d/%m/%Y %H:%M:%S") if g.started_on else g.changed_date_str

        # Duration
        dur_display = g.duration_str
        if not dur_display and g.started_on and g.completed_on:
            secs = (g.completed_on - g.started_on).total_seconds()
            dur_display = f"{secs:.1f}s" if secs >= 1 else "<1s"
        elif not dur_display:
            dur_display = "—"

        # Multi-line Variables Modified list: each variable on its OWN line
        vars_lines = []
        if g.fields_changed:
            for f in g.fields_changed:
                raw_field = str(f.get("Field") or "-")
                clean_field = raw_field.replace("@OData.Community.Display.V1.FormattedValue", "").strip()
                f_name = html.escape(clean_field)
                f_old = html.escape(str(f.get("Old Value") if f.get("Old Value") is not None and f.get("Old Value") != "" else "(blank)"))
                f_new = html.escape(str(f.get("New Value") if f.get("New Value") is not None and f.get("New Value") != "" else "(blank)"))
                vars_lines.append(
                    f'<div style="margin: 3px 0; padding: 3px 8px; background: rgba(0,0,0,0.25); border-left: 3px solid #38bdf8; border-radius: 3px; line-height: 1.5; word-break: break-word;">'
                    f'<strong style="color: #93c5fd;">{f_name}:</strong> '
                    f'<span style="color: #94a3b8;">{f_old}</span> '
                    f'<span style="color: #f59e0b; font-weight: bold; margin: 0 4px;">➔</span> '
                    f'<span style="color: #f1f5f9; font-weight: 500;">{f_new}</span>'
                    f'</div>'
                )
        else:
            vars_lines.append('<span style="color: #94a3b8;">—</span>')

        vars_cell_html = "".join(vars_lines)

        # Open Run link
        history_link = g.process_url or g.flow_history_url or ""
        if history_link:
            open_run_html = f'<a href="{html.escape(history_link)}" target="_blank" rel="noopener noreferrer" style="display: inline-block; color: #60a5fa; text-decoration: underline; font-weight: 600; padding: 2px 6px; border-radius: 3px; background: rgba(59,130,246,0.15);">View</a>'
        else:
            open_run_html = '<span style="color: #64748b;">—</span>'

        row = (
            f'<tr style="background: {row_bg}; border-bottom: 1px solid rgba(148,163,184,0.15);">'
            f'<td style="padding: 10px 12px; font-weight: 600; color: #f8fafc; vertical-align: top; min-width: 180px;">{html.escape(g.flow_name)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{status_badge}</td>'
            f'<td style="padding: 10px 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; color: #cbd5e1; vertical-align: top; white-space: nowrap;">{html.escape(start_display)}</td>'
            f'<td style="padding: 10px 12px; color: #cbd5e1; vertical-align: top; white-space: nowrap;">{html.escape(dur_display)}</td>'
            f'<td style="padding: 10px 12px; color: #cbd5e1; vertical-align: top; font-size: 12px; min-width: 140px;">{html.escape(g.user_name)}</td>'
            f'<td style="padding: 8px 12px; vertical-align: top; min-width: 320px;">{vars_cell_html}</td>'
            f'<td style="padding: 10px 12px; color: #94a3b8; vertical-align: top; white-space: nowrap; font-size: 12px;">{g.field_count} var(s)</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; text-align: center; white-space: nowrap;">{open_run_html}</td>'
            f'</tr>'
        )
        rows_html.append(row)

    table_html = f"""
    <div style="width: 100%; overflow-x: auto; border: 1px solid rgba(148,163,184,0.25); border-radius: 6px; margin: 12px 0 20px 0; background: #0f172a; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <thead>
                <tr style="background: #1e293b; border-bottom: 2px solid rgba(148,163,184,0.3); height: 40px;">
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Flow / Process Name</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Status</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Started</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Duration</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Changed By</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Variables Modified (Old ➔ New)</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Vars</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; text-align: center; white-space: nowrap;">Open Run</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </div>
    """
    return table_html



@dataclass
class FlowOutboundSummary:
    flow_id: str
    flow_name: str
    category_label: str
    is_active: bool
    state_label: str
    qualification_status: str
    status_badge: str
    status_reason: str
    filter_expression: str
    trigger_conditions: List[str]
    trigger_attributes: List[str]
    all_matched_columns: List[str]
    process_url: str
    flow_history_url: str
    mutation_count: int
    first_seen: str
    last_seen: str
    sample_mutations: List[str]
    runs: List[Any]
    timestamps: List[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return self.mutation_count


def group_outbound_by_flow(outbound_items: List[OutboundTriggerItem]) -> List[FlowOutboundSummary]:
    """
    Deduplicates outbound trigger items by Flow/Automation so each unique flow is shown ONCE.
    Aggregates all timestamps/mutations where it was triggered or suppressed.
    """
    by_flow: Dict[str, List[OutboundTriggerItem]] = {}
    for it in outbound_items:
        f_key = it.flow_id or it.flow_name
        if f_key not in by_flow:
            by_flow[f_key] = []
        by_flow[f_key].append(it)

    results: List[FlowOutboundSummary] = []
    for f_key, items in by_flow.items():
        first = items[0]
        # Sort items chronologically safely
        def _item_dt(it: OutboundTriggerItem) -> datetime:
            dt = it.timestamp
            if dt is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        items_sorted = sorted(items, key=_item_dt, reverse=True)
        first_dt_str = items_sorted[-1].changed_date_str
        last_dt_str = items_sorted[0].changed_date_str

        # Combine matched columns
        all_cols_set = set()
        for it in items:
            for c in it.matched_columns:
                all_cols_set.add(c)
        all_cols = sorted(list(all_cols_set))

        # Check for confirmed runs
        runs = [it.matched_run for it in items if it.matched_run]

        # Highest priority status
        # Order: Confirmed Triggered > Suppressed (Turned Off) > Condition Satisfied > Conditional Candidate > Qualified
        statuses = [it.qualification_status for it in items]
        if any("Confirmed Triggered" in s for s in statuses):
            best_status = "🟢 Confirmed Triggered"
            best_badge = "🟢"
        elif any("Suppressed" in s for s in statuses) or not first.is_active:
            best_status = "🔴 Suppressed (Turned Off)"
            best_badge = "🔴"
        elif any("Condition Satisfied" in s for s in statuses):
            best_status = "🟢 Condition Satisfied"
            best_badge = "🟢"
        elif any("Conditional" in s for s in statuses):
            best_status = "🟡 Conditional Candidate"
            best_badge = "🟡"
        else:
            best_status = first.qualification_status
            best_badge = first.status_badge

        # Select representative item matching best_status for accurate status_reason and URLs
        rep_item = first
        for it in items:
            if best_status == "🟢 Confirmed Triggered" and ("Confirmed Triggered" in it.qualification_status or it.matched_run):
                rep_item = it
                break
            elif best_status == "🔴 Suppressed (Turned Off)" and ("Suppressed" in it.qualification_status or not it.is_active):
                rep_item = it
                break
            elif best_status == "🟢 Condition Satisfied" and "Condition Satisfied" in it.qualification_status:
                rep_item = it
                break
            elif best_status == "🟡 Conditional Candidate" and "Conditional" in it.qualification_status:
                rep_item = it
                break

        best_proc_url = rep_item.process_url or first.process_url or ""
        best_hist_url = rep_item.flow_history_url or first.flow_history_url or ""
        for it in items:
            if it.matched_run and getattr(it.matched_run, "run_url", None):
                best_proc_url = it.matched_run.run_url
                break

        # Sample mutation descriptions
        sample_muts = []
        seen_dates = set()
        for it in items_sorted:
            if it.changed_date_str not in seen_dates:
                seen_dates.add(it.changed_date_str)
                f_desc = ", ".join(it.changed_field_labels[:2])
                sample_muts.append(f"{it.changed_date_str} ({f_desc})")
                if len(sample_muts) >= 3:
                    break

        all_timestamps = [it.changed_date_str for it in items_sorted]

        results.append(
            FlowOutboundSummary(
                flow_id=first.flow_id,
                flow_name=first.flow_name,
                category_label=first.category_label,
                is_active=first.is_active,
                state_label=first.state_label,
                qualification_status=best_status,
                status_badge=best_badge,
                status_reason=rep_item.status_reason,
                filter_expression=first.filter_expression,
                trigger_conditions=first.trigger_conditions,
                trigger_attributes=first.trigger_attributes,
                all_matched_columns=all_cols,
                process_url=best_proc_url,
                flow_history_url=best_hist_url,
                mutation_count=len(items),
                first_seen=first_dt_str,
                last_seen=last_dt_str,
                sample_mutations=sample_muts,
                runs=runs,
                timestamps=all_timestamps,
            )
        )

    # Sort results: Turned Off and Confirmed first
    def _flow_sort_key(f: FlowOutboundSummary):
        prio = 50
        if not f.is_active or "Suppressed" in f.qualification_status:
            prio = 10
        elif "Confirmed Triggered" in f.qualification_status:
            prio = 20
        elif "Condition Satisfied" in f.qualification_status:
            prio = 30
        elif "Conditional" in f.qualification_status:
            prio = 40
        return (prio, f.flow_name.lower())

    results.sort(key=_flow_sort_key)
    return results


def group_outbound_triggers(outbound_items: List[OutboundTriggerItem]) -> List[GroupedOutboundExecution]:
    """
    Group outbound trigger items by audit mutation/transaction.
    Merges all audit records sharing the same transaction ID into a single mutation event.
    Compiles summary counters (triggered, qualified, turned off suppressed, conditional).
    """
    grouped_map: Dict[str, List[OutboundTriggerItem]] = {}
    for item in outbound_items:
        tx_key = item.transaction_id if (item.transaction_id and item.transaction_id not in ("-", "")) else ""
        if tx_key:
            gid = f"tx_{tx_key}"
        elif item.changed_date_str:
            gid = f"time_{item.changed_date_str}_{item.user_name}"
        else:
            gid = item.audit_id or "tx_unknown"

        if gid not in grouped_map:
            grouped_map[gid] = []
        grouped_map[gid].append(item)

    results: List[GroupedOutboundExecution] = []
    for gid, items in grouped_map.items():
        first = items[0]
        fields_changed = []
        for idx, f_log in enumerate(first.changed_fields):
            f_disp = first.changed_field_labels[idx] if idx < len(first.changed_field_labels) else f_log
            fields_changed.append({
                "Field": f_disp,
                "Logical Field": f_log,
                "Old Value": first.old_values.get(f_log, ""),
                "New Value": first.new_values.get(f_log, ""),
            })

        summary_parts = []
        for f in fields_changed:
            o_d = f["Old Value"] if f["Old Value"] != "" else "(blank)"
            n_d = f["New Value"] if f["New Value"] != "" else "(blank)"
            summary_parts.append(f"{f['Field']}: {o_d} ➔ {n_d}")

        trig_cnt = sum(1 for it in items if "Confirmed Triggered" in it.qualification_status)
        qual_cnt = sum(1 for it in items if "Qualified to Trigger" in it.qualification_status)
        off_cnt = sum(1 for it in items if "Suppressed" in it.qualification_status or not it.is_active)
        cond_cnt = sum(1 for it in items if "Conditional" in it.qualification_status)

        results.append(
            GroupedOutboundExecution(
                group_id=gid,
                timestamp=first.timestamp,
                changed_date_str=first.changed_date_str,
                user_name=first.user_name,
                event=first.event,
                transaction_id=first.transaction_id,
                changed_fields_summary="\n".join(summary_parts),
                field_count=len(fields_changed),
                fields_changed=fields_changed,
                triggers=items,
                triggered_count=trig_cnt,
                qualified_count=qual_cnt,
                suppressed_turned_off_count=off_cnt,
                conditional_count=cond_cnt,
                has_turned_off_alert=(off_cnt > 0),
            )
        )

    def _safe_group_dt(g: GroupedOutboundExecution) -> datetime:
        dt = g.timestamp
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    results.sort(key=_safe_group_dt, reverse=True)
    return results


def render_outbound_triggers_table_html(
    items: List[Any],
    limit: Optional[int] = None,
    offset: int = 0,
) -> str:
    """
    Render a modern dark-mode table for Outbound Trigger Impact analysis.
    Supports rendering FlowOutboundSummary (deduplicated by flow),
    GroupedOutboundExecution, or OutboundTriggerItem.
    """
    import html

    if not items:
        return '<div style="padding: 16px; color: #94a3b8; font-style: italic;">No outbound automations matched the target table for this audit selection.</div>'

    # Check if items are FlowOutboundSummary (Deduplicated Flow View)
    if isinstance(items[0], FlowOutboundSummary):
        flow_items: List[FlowOutboundSummary] = list(items)
        if offset > 0:
            flow_items = flow_items[offset:]
        if limit is not None and limit > 0:
            flow_items = flow_items[:limit]

        rows_html = []
        for f in flow_items:
            # Background tint: red alert for turned-off flows, green for confirmed
            if not f.is_active:
                row_bg = "rgba(239, 68, 68, 0.12)"
            elif "Confirmed Triggered" in f.qualification_status:
                row_bg = "rgba(16, 185, 129, 0.10)"
            elif "Condition Satisfied" in f.qualification_status:
                row_bg = "rgba(16, 185, 129, 0.07)"
            else:
                row_bg = "#1e293b"

            # Status badge
            if "Confirmed Triggered" in f.qualification_status:
                status_badge = '<span style="background: rgba(16,185,129,0.3); color: #34d399; border: 1px solid rgba(16,185,129,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟢 Confirmed Triggered</span>'
            elif "Suppressed" in f.qualification_status or not f.is_active:
                status_badge = '<span style="background: rgba(239,68,68,0.3); color: #f87171; border: 1px solid rgba(239,68,68,0.5); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🔴 Suppressed (Turned Off)</span>'
            elif "Condition Satisfied" in f.qualification_status:
                status_badge = '<span style="background: rgba(16,185,129,0.25); color: #34d399; border: 1px solid rgba(16,185,129,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟢 Condition Satisfied</span>'
            elif "Conditional" in f.qualification_status:
                status_badge = '<span style="background: rgba(245,158,11,0.25); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟡 Conditional Candidate</span>'
            else:
                status_badge = '<span style="background: rgba(14,165,233,0.25); color: #38bdf8; border: 1px solid rgba(14,165,233,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🔵 Qualified to Trigger</span>'

            # State label
            state_html = (
                '<span style="color: #34d399; font-weight: 600;">🟢 Active</span>'
                if f.is_active
                else '<span style="color: #f87171; font-weight: 700;">🔴 Turned Off</span>'
            )

            # Trigger Columns
            if f.trigger_attributes:
                cols_text = ", ".join(f"`{c}`" for c in f.trigger_attributes[:4])
                if len(f.trigger_attributes) > 4:
                    cols_text += f" (+{len(f.trigger_attributes)-4} more)"
            else:
                cols_text = '<span style="color: #94a3b8; font-style: italic;">(All columns / Unrestricted)</span>'

            # Filter rules / conditions
            cond_html = ""
            if f.filter_expression:
                cond_html += f'<div style="font-size: 11px; color: #94a3b8; margin-bottom: 2px;">Filter: <code style="color: #cbd5e1; background: rgba(0,0,0,0.3); padding: 1px 4px; border-radius: 3px;">{html.escape(f.filter_expression)}</code></div>'
            if f.trigger_conditions:
                cond_html += f'<div style="font-size: 11px; color: #94a3b8;">Condition: <code style="color: #cbd5e1; background: rgba(0,0,0,0.3); padding: 1px 4px; border-radius: 3px;">{html.escape(f.trigger_conditions[0])}</code></div>'
            if not cond_html:
                cond_html = '<span style="color: #64748b; font-size: 11px;">None (Unconditional)</span>'

            # Impact summary
            if f.mutation_count == 1:
                events_summary = f'<span style="color: #f8fafc; font-weight: 600;">1 event</span><br><span style="font-size: 11px; color: #94a3b8;">{html.escape(f.first_seen)}</span>'
            else:
                events_summary = f'<span style="color: #f8fafc; font-weight: 600;">{f.mutation_count} events</span><br><span style="font-size: 11px; color: #94a3b8;">{html.escape(f.first_seen)} → {html.escape(f.last_seen)}</span>'

            # Reason note
            reason_html = f'<div style="font-size: 11px; color: #cbd5e1; line-height: 1.4;">{html.escape(f.status_reason)}</div>'

            # Action link
            action_link = f.process_url or f.flow_history_url or ""
            if action_link:
                link_html = f'<a href="{html.escape(action_link)}" target="_blank" rel="noopener noreferrer" style="color: #60a5fa; text-decoration: underline; font-weight: 600; font-size: 11px; padding: 2px 6px; border-radius: 3px; background: rgba(59,130,246,0.15);">Open ↗</a>'
            else:
                link_html = '<span style="color: #64748b;">—</span>'

            rows_html.append(
                f'<tr style="background: {row_bg}; border-bottom: 1px solid rgba(148,163,184,0.15);">'
                f'<td style="padding: 10px 12px; font-weight: 600; color: #f8fafc; vertical-align: top; min-width: 180px;">{html.escape(f.flow_name)}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{status_badge}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{state_html}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; color: #cbd5e1; font-size: 12px; white-space: nowrap;">{html.escape(f.category_label)}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; font-family: ui-monospace, monospace; font-size: 11px; color: #cbd5e1;">{cols_text}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; max-width: 250px;">{cond_html}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; max-width: 250px;">{reason_html}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{events_summary}</td>'
                f'<td style="padding: 10px 12px; vertical-align: top; text-align: center; white-space: nowrap;">{link_html}</td>'
                f'</tr>'
            )

        table_html = f"""
        <div style="width: 100%; overflow-x: auto; border: 1px solid rgba(148,163,184,0.25); border-radius: 6px; margin: 12px 0 20px 0; background: #0f172a; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
            <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                <thead>
                    <tr style="background: #1e293b; border-bottom: 2px solid rgba(148,163,184,0.3); height: 40px;">
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Flow / Process Name</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Trigger Status</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Flow State</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Category</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Trigger Select Columns</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Condition Rules</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Evaluation Reason</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Impacted Mutations</th>
                        <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; text-align: center; white-space: nowrap;">Link</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>
        </div>
        """
        return table_html

    # Otherwise render by mutation event (OutboundTriggerItem / GroupedOutboundExecution)
    flat_items: List[OutboundTriggerItem] = []
    for x in items:
        if isinstance(x, GroupedOutboundExecution):
            flat_items.extend(x.triggers)
        elif isinstance(x, OutboundTriggerItem):
            flat_items.append(x)

    if not flat_items:
        return '<div style="padding: 16px; color: #94a3b8; font-style: italic;">No outbound automations matched the target table for this audit selection.</div>'

    total_count = len(flat_items)
    if offset > 0:
        flat_items = flat_items[offset:]
    if limit is not None and limit > 0:
        flat_items = flat_items[:limit]

    rows_html = []
    for it in flat_items:
        # Background: alert tint for turned-off flows
        if not it.is_active:
            row_bg = "rgba(239, 68, 68, 0.12)"
        elif "Confirmed Triggered" in it.qualification_status:
            row_bg = "rgba(16, 185, 129, 0.10)"
        elif "Condition Satisfied" in it.qualification_status:
            row_bg = "rgba(16, 185, 129, 0.07)"
        else:
            row_bg = "#1e293b"

        # Status badge
        if "Confirmed Triggered" in it.qualification_status:
            status_badge = '<span style="background: rgba(16,185,129,0.3); color: #34d399; border: 1px solid rgba(16,185,129,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟢 Confirmed Triggered</span>'
        elif "Suppressed" in it.qualification_status or not it.is_active:
            status_badge = '<span style="background: rgba(239,68,68,0.3); color: #f87171; border: 1px solid rgba(239,68,68,0.5); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🔴 Suppressed (Turned Off)</span>'
        elif "Condition Satisfied" in it.qualification_status:
            status_badge = '<span style="background: rgba(16,185,129,0.25); color: #34d399; border: 1px solid rgba(16,185,129,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟢 Condition Satisfied</span>'
        elif "Conditional" in it.qualification_status:
            status_badge = '<span style="background: rgba(245,158,11,0.25); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🟡 Conditional Candidate</span>'
        else:
            status_badge = '<span style="background: rgba(14,165,233,0.25); color: #38bdf8; border: 1px solid rgba(14,165,233,0.4); border-radius: 3px; padding: 2px 8px; font-weight: 600; font-size: 11px;">🔵 Qualified to Trigger</span>'

        # Active state label
        state_html = (
            '<span style="color: #34d399; font-weight: 500;">🟢 Active</span>'
            if it.is_active
            else '<span style="color: #f87171; font-weight: 700;">🔴 Turned Off</span>'
        )

        # Matched columns list
        if it.matched_columns:
            cols_text = ", ".join(f"`{c}`" for c in it.matched_columns[:4])
            if len(it.matched_columns) > 4:
                cols_text += f" (+{len(it.matched_columns)-4} more)"
        else:
            cols_text = '<span style="color: #94a3b8; font-style: italic;">(All columns / Unrestricted)</span>'

        # Conditions
        cond_html = ""
        if it.filter_expression:
            cond_html += f'<div style="font-size: 11px; color: #94a3b8; margin-bottom: 2px;">Filter: <code style="color: #cbd5e1; background: rgba(0,0,0,0.3); padding: 1px 4px; border-radius: 3px;">{html.escape(it.filter_expression)}</code></div>'
        if it.trigger_conditions:
            cond_html += f'<div style="font-size: 11px; color: #94a3b8;">Rule: <code style="color: #cbd5e1; background: rgba(0,0,0,0.3); padding: 1px 4px; border-radius: 3px;">{html.escape(it.trigger_conditions[0])}</code></div>'
        if not cond_html:
            cond_html = '<span style="color: #64748b; font-size: 11px;">None (Unconditional)</span>'

        # Run history info
        run_info = "—"
        if it.matched_run:
            run_info = f"{it.matched_run.status} ({it.matched_run.duration_str or '<1s'})"

        # Action Link
        action_link = it.process_url or it.flow_history_url or ""
        if action_link:
            link_html = f'<a href="{html.escape(action_link)}" target="_blank" rel="noopener noreferrer" style="color: #60a5fa; text-decoration: underline; font-weight: 600; font-size: 11px; padding: 2px 6px; border-radius: 3px; background: rgba(59,130,246,0.15);">Open ↗</a>'
        else:
            link_html = '<span style="color: #64748b;">—</span>'

        # Changed fields summary with values
        field_parts = []
        for idx, f_log in enumerate(it.changed_fields[:3]):
            f_label = it.changed_field_labels[idx] if idx < len(it.changed_field_labels) else f_log
            n_val = it.new_values.get(f_log)
            if n_val is not None:
                field_parts.append(f"{f_label}: {n_val}")
            else:
                field_parts.append(f_label)
        fields_summary = ", ".join(field_parts)
        if len(it.changed_fields) > 3:
            fields_summary += f" (+{len(it.changed_fields)-3})"

        # Status reason
        reason_html = f'<div style="font-size: 11px; color: #cbd5e1;">{html.escape(it.status_reason)}</div>'

        rows_html.append(
            f'<tr style="background: {row_bg}; border-bottom: 1px solid rgba(148,163,184,0.15);">'
            f'<td style="padding: 10px 12px; font-weight: 600; color: #f8fafc; vertical-align: top; min-width: 170px;">{html.escape(it.flow_name)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{status_badge}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; white-space: nowrap;">{state_html}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; color: #cbd5e1; font-size: 12px; white-space: nowrap;">{html.escape(it.category_label)}</td>'
            f'<td style="padding: 10px 12px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; color: #cbd5e1; vertical-align: top; white-space: nowrap;">{html.escape(it.changed_date_str)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; color: #93c5fd; font-size: 12px; max-width: 220px;">{html.escape(fields_summary)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; font-family: ui-monospace, monospace; font-size: 11px; color: #cbd5e1;">{html.escape(cols_text)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; max-width: 240px;">{cond_html}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; max-width: 240px;">{reason_html}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; color: #cbd5e1; font-size: 12px; white-space: nowrap;">{html.escape(run_info)}</td>'
            f'<td style="padding: 10px 12px; vertical-align: top; text-align: center; white-space: nowrap;">{link_html}</td>'
            f'</tr>'
        )

    table_html = f"""
    <div style="width: 100%; overflow-x: auto; border: 1px solid rgba(148,163,184,0.25); border-radius: 6px; margin: 12px 0 20px 0; background: #0f172a; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <thead>
                <tr style="background: #1e293b; border-bottom: 2px solid rgba(148,163,184,0.3); height: 40px;">
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Flow / Process Name</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Trigger Status</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Flow State</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Category</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Mutation Time</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Mutated Fields & Values</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Matched Trigger Columns</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Condition Rules</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Evaluation Reason</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; white-space: nowrap;">Execution Run</th>
                    <th style="padding: 10px 12px; color: #cbd5e1; font-weight: 600; text-align: center; white-space: nowrap;">Link</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </div>
    """
    return table_html






