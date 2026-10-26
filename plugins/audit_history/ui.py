"""Streamlit UI for the Audit History plugin.

All session_state keys are prefixed with ``ah_`` to avoid collisions with
other plugins or the core shell.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from core.context import AppContext
from plugins.audit_history.client import DataverseClient
from plugins.audit_history.correlator import (
    AuditProcessCorrelator,
    AuditCorrelationItem,
    CorrelatedFlow,
    GroupedAuditExecution,
    OutboundTriggerItem,
    GroupedOutboundExecution,
    FlowOutboundSummary,
    clean_guid,
    extract_flow_definition,
    group_correlations,
    group_outbound_triggers,
    group_outbound_by_flow,
    is_automation_user,
    render_flow_history_table_html,
    render_outbound_triggers_table_html,
)
from plugins.logic_app.client import LogicAppClient
from plugins.logic_app_entity_inspector.cache import load_cache as load_logic_app_cache
from plugins.audit_history import persistence


def _get_arm_token(ctx: AppContext) -> str | None:
    """Acquire or retrieve cached Azure Resource Manager OAuth token."""
    token = st.session_state.get("la_arm_token") or st.session_state.get("lei_arm_token")
    if token:
        return token
    if hasattr(ctx, "auth"):
        try:
            t = ctx.auth.get_token("https://management.azure.com/")
            if t:
                st.session_state["la_arm_token"] = t
                return t
        except Exception:
            pass
        try:
            res = ctx.auth.acquire_token_silent(["https://management.azure.com/user_impersonation"])
            if not res:
                res = ctx.auth.acquire_token_silent(["https://management.azure.com/.default"])
            if res and "access_token" in res:
                st.session_state["la_arm_token"] = res["access_token"]
                return res["access_token"]
        except Exception:
            pass
    return None


def _detect_current_env_tag(ctx: AppContext) -> str:
    """Detect whether connected environment is PROD, DEV, or QA based on org URL / environment name."""
    env_str = f"{getattr(ctx, 'environment_name', '')} {getattr(ctx, 'org_url', '')} {st.session_state.get('env_name', '')}".lower()
    if any(k in env_str for k in ("dev", "development", "sandbox")):
        return "DEV"
    if any(k in env_str for k in ("qa", "test", "uat")):
        return "QA"
    return "PROD"


def _get_available_resource_groups(ctx: AppContext) -> tuple[list[str], dict[str, int]]:
    """
    Discover available Azure Resource Groups for Logic Apps dynamically
    from the local cache and session state, matching the pattern in other Logic App plugins.
    Returns (sorted_rg_names, {rg_name: count_of_logic_apps}).
    """
    counts: dict[str, int] = {}

    # 1. Inspect local Logic App inspector cache
    try:
        cache = load_logic_app_cache()
        for wf in cache.get("workflows", {}).values():
            rg = wf.get("resource_group")
            if rg and isinstance(rg, str):
                rg_clean = rg.strip()
                if rg_clean:
                    counts[rg_clean] = counts.get(rg_clean, 0) + 1
    except Exception as exc:
        logging.debug("Could not inspect Logic App cache for resource groups: %s", exc)

    # 2. Inspect session state for resource groups already fetched by other plugins (e.g. lei, lad, la)
    for k, v in list(st.session_state.items()):
        if any(k.startswith(prefix) for prefix in ("la_rgs_", "lei_rgs_", "lad_rgs_")) and isinstance(v, list):
            for item in v:
                rg_name = item.get("name") if isinstance(item, dict) else str(item)
                if rg_name and rg_name not in counts:
                    counts[rg_name] = 0

    sorted_rgs = sorted(counts.keys())
    return sorted_rgs, counts


def _match_resource_group_for_env(ctx: AppContext, available_rgs: list[str]) -> str | None:
    """
    Match the active Dataverse environment to the corresponding Logic App Resource Group.
    Avoids hardcoding by dynamically discovering which Resource Group aligns with
    the current core login context (e.g. QA, DEV, or PROD).
    """
    if not available_rgs:
        return None

    # Derive environment string from context
    env_tag = _detect_current_env_tag(ctx)
    env_str = f"{getattr(ctx, 'environment_name', '')} {getattr(ctx, 'org_url', '')} {st.session_state.get('env_name', '')}".lower()

    # Prioritize matching based on detected tag
    if env_tag == "QA":
        for rg in available_rgs:
            rg_l = rg.lower()
            if "qa" in rg_l or "test" in rg_l or "uat" in rg_l:
                return rg
    elif env_tag == "DEV":
        for rg in available_rgs:
            rg_l = rg.lower()
            if "dev" in rg_l or "sandbox" in rg_l:
                return rg
    else:  # PROD / default
        for rg in available_rgs:
            rg_l = rg.lower()
            if "prod" in rg_l:
                return rg

    # Fallback: check substring match from core env_str
    for rg in available_rgs:
        clean_rg = re.sub(r"[-_]+", " ", rg).lower()
        rg_tokens = clean_rg.split()
        if any(tk in env_str for tk in rg_tokens if len(tk) >= 3 and tk not in ("rg", "dyn", "integrations")):
            return rg

    return available_rgs[0] if available_rgs else None


def _render_la_diagnostics(diags: list[str], default_expanded: bool = False) -> None:
    """Render Azure Logic Apps verification and download diagnostic log events persistently."""
    if not diags:
        return
    has_warnings = any("❌" in d or "⚠️" in d or "NONE executed" in d for d in diags)
    with st.expander(
        f"📋 Azure Logic Apps Verification & Download Diagnostics ({len(diags)} log events)",
        expanded=default_expanded or has_warnings,
    ):
        for log_entry in diags:
            if "❌" in log_entry or "[ERROR]" in log_entry or "⚠️" in log_entry:
                st.warning(log_entry)
            elif "✅" in log_entry or "🎯" in log_entry:
                st.success(log_entry)
            else:
                st.caption(log_entry)


def _clean_str(val: Any) -> str:
    """Format and stringify values safely to prevent PyArrow serialization errors."""
    if val is None or pd.isna(val) or val == "":
        return "-"
    if isinstance(val, (bytes, bytearray)):
        try:
            return val.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(val)} bytes>"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (dict, list)):
        try:
            return json.dumps(val)
        except Exception:
            return str(val)
    return str(val)


def _flow_color(flow_name: str | None) -> str:
    """Returns a consistent pastel background color per flow name, matching Cloud Flow History plugin."""
    if not flow_name or flow_name in ("-", "Unknown", "User Action"):
        return ""
    clean = str(flow_name).replace("⚡", "").replace("📋", "").strip()
    hue = int(hashlib.md5(clean.encode()).hexdigest(), 16) % 360
    return f"background-color: hsla({hue}, 70%, 90%, 0.55)"



# Action code -> human-readable label
_ACTION_MAP: dict[int, str] = {
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


def _restore_session_from_dict(data: dict) -> None:
    """Restore an audit analysis session from a dictionary into st.session_state."""
    entity = data.get("entity_logical_name", "")
    rec_id = data.get("record_id", "")
    rec_label = data.get("record_label", rec_id)

    st.session_state["ah_selected_entity"] = entity
    st.session_state["ah_last_entity"] = entity
    st.session_state["ah_selected_record_id"] = rec_id
    st.session_state["ah_selected_record_label"] = rec_label
    st.session_state["ah_filter_config"] = data.get("filter_config", [])
    if data.get("metadata"):
        st.session_state[f"ah_attrs_{entity}"] = data["metadata"]
        st.session_state["ah_fresh_metadata"] = data["metadata"]
    st.session_state["ah_audit_data"] = data.get("audit_data", [])
    st.session_state["ah_raw_audit_details"] = data.get("raw_audit_details", [])

    corr = data.get("correlation_results", [])
    if corr:
        st.session_state["ah_correlation_results"] = corr
    else:
        st.session_state.pop("ah_correlation_results", None)

    outbound = data.get("outbound_results", [])
    if outbound:
        st.session_state["ah_outbound_results"] = outbound
    else:
        st.session_state.pop("ah_outbound_results", None)

    st.session_state["ah_logic_app_diagnostics"] = data.get("logic_app_diagnostics", [])
    st.session_state["ah_raw_workflows"] = data.get("raw_workflows", [])
    st.session_state["ah_env_id"] = data.get("env_id", "")
    st.session_state["ah_step5_duration"] = data.get("step5_duration")

    search_res = data.get("search_results") or []
    if not search_res and rec_id and entity:
        search_res = [{f"{entity}id": rec_id, "name": rec_label}]
    st.session_state["ah_search_results"] = search_res


def _build_current_session_dict() -> dict:
    """Pack active audit analysis session into a dictionary for caching or history saving."""
    entity = st.session_state.get("ah_selected_entity", "")
    return {
        "entity_logical_name": entity,
        "entity_display_name": st.session_state.get("ah_selected_entity_display", entity),
        "record_id": st.session_state.get("ah_selected_record_id", ""),
        "record_label": st.session_state.get("ah_selected_record_label", ""),
        "filter_config": st.session_state.get("ah_filter_config", []),
        "search_results": st.session_state.get("ah_search_results", []),
        "metadata": st.session_state.get("ah_fresh_metadata", {}),
        "audit_data": st.session_state.get("ah_audit_data", []),
        "raw_audit_details": st.session_state.get("ah_raw_audit_details", []),
        "correlation_results": st.session_state.get("ah_correlation_results", []),
        "outbound_results": st.session_state.get("ah_outbound_results", []),
        "logic_app_diagnostics": st.session_state.get("ah_logic_app_diagnostics", []),
        "raw_workflows": st.session_state.get("ah_raw_workflows", []),
        "env_id": st.session_state.get("ah_env_id", ""),
        "step5_duration": st.session_state.get("ah_step5_duration"),
    }


def _maybe_show_restore_banner() -> None:
    """Show banner offering to restore the last session if present in cache."""
    if st.session_state.get("ah_restore_checked"):
        return
    st.session_state["ah_restore_checked"] = True
    if st.session_state.get("ah_audit_data"):
        return
    cached = persistence.load_results()
    if cached and cached.get("audit_data"):
        rec_name = cached.get("record_label") or cached.get("record_id") or "record"
        table_name = cached.get("entity_display_name") or cached.get("entity_logical_name") or "table"
        changes_cnt = len(cached.get("audit_data", []))
        corr_cnt = len(cached.get("correlation_results", []))
        corr_msg = f" and {corr_cnt} correlated flow run(s)" if corr_cnt else ""

        col_msg, col_btn, col_clr = st.columns([4, 1, 1])
        col_msg.info(
            f"💾 **Previous session available**: {changes_cnt} audit change(s){corr_msg} for `{table_name}` ({rec_name}). "
            "Restore without re-querying Dataverse?"
        )
        if col_btn.button("↩️ Restore", key="ah_restore_cache_btn", type="primary"):
            _restore_session_from_dict(cached)
            st.rerun()
        if col_clr.button("✖ Dismiss", key="ah_dismiss_cache_btn"):
            persistence.clear_results()
            st.rerun()


def _render_history_manager() -> None:
    """Render saved history management view."""
    records = persistence.list_history()
    if not records:
        st.info("No saved audit history records found. Run an audit and click '💾 Save' to store it here.")
        return

    df_hist = pd.DataFrame(records)
    cols_to_show = ["name", "table", "record_label", "timestamp", "remark", "audit_count", "correlation_count"]
    rename_cols = {
        "name": "Name",
        "table": "Table",
        "record_label": "Record",
        "timestamp": "Saved At",
        "remark": "Remark",
        "audit_count": "Audit Changes",
        "correlation_count": "Correlated Runs",
    }
    if "step5_duration" in df_hist.columns:
        cols_to_show.append("step5_duration")
        rename_cols["step5_duration"] = "Analysis Duration"

    st.dataframe(
        df_hist[cols_to_show].rename(columns=rename_cols),
        width="stretch",
        hide_index=True,
    )

    filenames = [r["filename"] for r in records]
    labels = [
        f"{r['name']} ({r['table']}) — {r['timestamp'][:16].replace('T', ' ')}"
        for r in records
    ]
    col_sel, col_load, col_del = st.columns([3, 1, 1])
    sel_idx = col_sel.selectbox(
        "Select saved analysis record",
        options=range(len(records)),
        format_func=lambda i: labels[i],
        key="ah_history_select",
    )

    if col_load.button("↩️ Load Analysis", key="ah_history_load_btn", type="primary", width="stretch"):
        try:
            remark, data = persistence.load_history(filenames[sel_idx])
            _restore_session_from_dict(data)
            persistence.save_results(data)
            st.success(
                f"✅ Loaded saved analysis for **{data.get('entity_display_name', data.get('entity_logical_name'))}** "
                f"({data.get('record_label', data.get('record_id'))})."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Load failed: {exc}")

    if col_del.button("🗑️ Delete", key="ah_history_del_btn", width="stretch"):
        if persistence.delete_history(filenames[sel_idx]):
            st.success("Deleted.")
            st.rerun()

    col_rem_input, col_rem_btn = st.columns([4, 1])
    with col_rem_input:
        new_remark = st.text_input(
            "Update remark for selected record",
            value=records[sel_idx].get("remark", ""),
            key="ah_hist_new_remark",
        )
    with col_rem_btn:
        st.write("")
        st.write("")
        if st.button("✏️ Update Remark", key="ah_hist_update_remark_btn", width="stretch"):
            if persistence.update_history_remark(filenames[sel_idx], new_remark):
                st.success("Remark updated.")
                st.rerun()


def render_page(ctx: AppContext, client: DataverseClient) -> None:
    st.title("📜 Dataverse Audit History")

    # Banner for auto-save cache restoration
    _maybe_show_restore_banner()

    # Saved History expander
    with st.expander("📁 Saved History & Previous Analyses", expanded=False):
        _render_history_manager()

    # ------------------------------------------------------------------ #
    # Step 1 – Table selection                                            #
    # ------------------------------------------------------------------ #
    st.header("1. Select Table")

    if "ah_all_entities" not in st.session_state:
        with st.spinner("Fetching tables…"):
            st.session_state["ah_all_entities"] = client.get_entities()

    all_entities: list[dict] = st.session_state["ah_all_entities"]

    if not all_entities:
        st.error("No audit-enabled tables found. Verify your Dataverse permissions.")
        return

    entity_options = {
        e["logical_name"]: f"{e['display_name']} ({e['logical_name']})"
        for e in all_entities
    }

    current_entity = st.session_state.get("ah_selected_entity") or st.session_state.get("ah_last_entity")
    entity_keys = list(entity_options.keys())
    table_index = entity_keys.index(current_entity) if current_entity in entity_keys else 0

    selected_logical_name: str = st.selectbox(
        "Table",
        options=entity_keys,
        index=table_index,
        format_func=lambda x: entity_options[x],
        key="ah_table_selector",
    )

    selected_entity = next(
        e for e in all_entities if e["logical_name"] == selected_logical_name
    )
    entity_set_name: str = selected_entity["entity_set_name"]
    st.session_state["ah_selected_entity_display"] = selected_entity["display_name"]

    # ------------------------------------------------------------------ #
    # Step 2 – Filter builder                                             #
    # ------------------------------------------------------------------ #
    st.markdown("---")
    st.header("2. Filter Records")

    # Lazy-load attribute metadata per entity
    meta_key = f"ah_attrs_{selected_logical_name}"
    if meta_key not in st.session_state:
        with st.spinner(f"Fetching fields for {selected_entity['display_name']}…"):
            st.session_state[meta_key] = client.get_attribute_metadata(
                selected_logical_name
            )

    metadata: dict[str, str] = st.session_state[meta_key]
    sorted_fields = sorted(metadata.keys())

    # Reset filter config ONLY when the entity genuinely changes
    if st.session_state.get("ah_last_entity") != selected_logical_name:
        st.session_state["ah_last_entity"] = selected_logical_name
        st.session_state["ah_selected_entity"] = selected_logical_name
        primary_field = next(
            (k for k in sorted_fields if k.lower() in {"name", "fullname", "subject"}),
            sorted_fields[0] if sorted_fields else "",
        )
        st.session_state["ah_filter_config"] = (
            [{"field": primary_field, "value": ""}] if primary_field else []
        )
        st.session_state.pop("ah_search_results", None)
        st.session_state.pop("ah_audit_data", None)
        st.session_state.pop("ah_raw_audit_details", None)
        st.session_state.pop("ah_correlation_results", None)
        st.session_state.pop("ah_selected_record_id", None)
        st.session_state.pop("ah_selected_record_label", None)
    else:
        st.session_state["ah_selected_entity"] = selected_logical_name

    filter_config: list[dict] = st.session_state.get("ah_filter_config", [])

    for i, item in enumerate(filter_config):
        col_f, col_v, col_rm = st.columns([2, 2, 0.5])
        with col_f:
            filter_config[i]["field"] = st.selectbox(
                f"Field {i + 1}",
                options=sorted_fields,
                index=sorted_fields.index(item["field"]) if item["field"] in sorted_fields else 0,
                key=f"ah_field_{i}_{selected_logical_name}",
            )
        with col_v:
            filter_config[i]["value"] = st.text_input(
                f"Value {i + 1}",
                value=item["value"],
                key=f"ah_val_{i}_{selected_logical_name}",
            )
        with col_rm:
            st.write("")
            st.write("")
            if st.button("❌", key=f"ah_rm_{i}_{selected_logical_name}"):
                filter_config.pop(i)
                st.rerun()

    if st.button("➕ Add Filter"):
        filter_config.append({"field": sorted_fields[0] if sorted_fields else "", "value": ""})
        st.rerun()

    st.write("")
    if st.button("🔍 Search Records", type="primary"):
        with st.spinner("Searching…"):
            active_filters = {
                f["field"]: f["value"]
                for f in filter_config
                if f.get("value")
            }
            records = client.search_records(entity_set_name, filters=active_filters)
            st.session_state["ah_search_results"] = records
            st.session_state.pop("ah_audit_data", None)
            if not records:
                st.warning("No records found matching the filters.")

    # ------------------------------------------------------------------ #
    # Step 3 – Record selection                                           #
    # ------------------------------------------------------------------ #
    search_results: list[dict] = st.session_state.get("ah_search_results", [])
    has_audit_data = bool(st.session_state.get("ah_audit_data"))

    if not search_results and not has_audit_data:
        return

    st.markdown("---")
    st.header("3. Select Record")

    df_results = pd.DataFrame(search_results) if search_results else pd.DataFrame()
    if not df_results.empty:
        display_cols = list(
            dict.fromkeys(
                [f["field"] for f in filter_config if f.get("field") and f["field"] in df_results.columns]
                + [f"{selected_logical_name}id"]
            )
        )
        display_cols = [c for c in display_cols if c in df_results.columns]
        rename_map = {c: metadata.get(c, c) for c in display_cols}
        st.dataframe(
            df_results[display_cols].rename(columns=rename_map),
            width="stretch",
            hide_index=True,
        )

    id_field = f"{selected_logical_name}id"
    name_field = filter_config[0]["field"] if filter_config else id_field
    options = {}
    if not df_results.empty and id_field in df_results.columns:
        for r in search_results:
            rid = r.get(id_field)
            label = r.get(name_field) or rid
            options[rid] = f"{label} ({rid})"

    # If an existing record was loaded / restored from cache, preserve it in options
    saved_rec_id = st.session_state.get("ah_selected_record_id")
    if saved_rec_id and saved_rec_id not in options:
        saved_label = st.session_state.get("ah_selected_record_label") or saved_rec_id
        options[saved_rec_id] = (
            f"{saved_label} ({saved_rec_id})"
            if not str(saved_label).endswith(f"({saved_rec_id})")
            else str(saved_label)
        )

    if not options:
        st.info("Could not determine a record ID field. Check table permissions or search for records above.")
        return

    opt_keys = list(options.keys())
    rec_index = opt_keys.index(saved_rec_id) if saved_rec_id in opt_keys else 0

    selected_record_id: str = st.selectbox(
        "Record to audit",
        options=opt_keys,
        index=rec_index,
        format_func=lambda x: options[x],
        key=f"ah_record_select_{selected_logical_name}",
    )
    st.session_state["ah_selected_record_id"] = selected_record_id
    st.session_state["ah_selected_record_label"] = options.get(selected_record_id, selected_record_id)

    if st.button("🚀 Fetch Audit History", type="primary"):
        with st.spinner("Retrieving audit logs…"):
            try:
                audit_details = client.get_audit_history(entity_set_name, selected_record_id)
                fresh_metadata = client.get_attribute_metadata(selected_logical_name)

                processed: list[dict] = []
                for detail in audit_details:
                    audit_record = detail.get("AuditRecord", {})
                    raw_date = audit_record.get("createdon")
                    changed_on = (
                        pd.to_datetime(raw_date).strftime("%d/%m/%Y %H:%M")
                        if raw_date
                        else "-"
                    )
                    action_code = audit_record.get("action")
                    event_type = _ACTION_MAP.get(action_code, f"Action {action_code}")
                    transaction_id = audit_record.get("transactionid", "-")
                    extra_info = audit_record.get("useradditionalinfo", "-")

                    user_val = (
                        audit_record.get("_userid_value@OData.Community.Display.V1.FormattedValue")
                        or audit_record.get("_userid_value")
                        or "System"
                    )
                    calling_val = audit_record.get(
                        "_callinguserid_value@OData.Community.Display.V1.FormattedValue"
                    )
                    changed_by = (
                        f"{user_val} (via {calling_val})"
                        if calling_val and calling_val != user_val
                        else str(user_val)
                    )

                    old_vals: dict = detail.get("OldValue") or {}
                    new_vals: dict = detail.get("NewValue") or {}
                    all_fields = [
                        f
                        for f in set(list(old_vals.keys()) + list(new_vals.keys()))
                        if not f.startswith("@") and not f.startswith("_")
                    ]

                    if not all_fields:
                        processed.append(
                            {
                                "Changed Date": changed_on,
                                "Changed By": changed_by,
                                "Event": event_type,
                                "Changed Field": "-",
                                "Old Value": "-",
                                "New Value": "-",
                                "Transaction ID": transaction_id,
                                "Extra Info": extra_info,
                            }
                        )
                    else:
                        for field in all_fields:
                            old_val = old_vals.get(field, "-")
                            new_val = new_vals.get(field, "-")
                            if old_val == new_val:
                                continue
                            processed.append(
                                {
                                    "Changed Date": str(changed_on),
                                    "Changed By": str(changed_by),
                                    "Event": str(event_type),
                                    "Changed Field": str(fresh_metadata.get(field, field)),
                                    "Old Value": _clean_str(old_val),
                                    "New Value": _clean_str(new_val),
                                    "Transaction ID": str(transaction_id),
                                    "Extra Info": str(extra_info),
                                }
                            )

                st.session_state["ah_audit_data"] = processed
                st.session_state["ah_raw_audit_details"] = audit_details
                st.session_state["ah_selected_entity"] = selected_logical_name
                st.session_state["ah_selected_record_id"] = selected_record_id
                st.session_state["ah_selected_record_label"] = options.get(selected_record_id, selected_record_id)
                st.session_state["ah_fresh_metadata"] = fresh_metadata
                st.session_state.pop("ah_correlation_results", None)
                # Auto-save session cache
                persistence.save_results(_build_current_session_dict())
                st.success(f"Found {len(processed)} change(s).")
            except Exception as exc:
                st.error(f"Error fetching audit history: {exc}")

    # ------------------------------------------------------------------ #
    # Step 4 – Results                                                    #
    # ------------------------------------------------------------------ #
    audit_data: list[dict] = st.session_state.get("ah_audit_data", [])
    if not audit_data:
        return

    st.divider()
    st.header("4. Audit History Results")

    df = pd.DataFrame(audit_data)
    for col in df.columns:
        df[col] = df[col].apply(_clean_str).astype(str)

    display_cols = [
        "Changed Date",
        "Changed By",
        "Event",
        "Changed Field",
        "Old Value",
        "New Value",
    ]
    st.dataframe(df[display_cols], width="stretch", hide_index=True)

    with st.expander("🛠️ Technical Transaction Details"):
        tech_cols = ["Changed Date", "Transaction ID", "Extra Info"]
        st.dataframe(
            df[tech_cols].drop_duplicates(), width="stretch", hide_index=True
        )

    # Save results to named history section
    st.markdown("---")
    st.subheader("💾 Save Analysis to History")
    st.caption(
        "Save this audit & automation analysis session to history so you can reload and inspect it anytime without waiting for analysis."
    )
    col_sn, col_sr, col_sb = st.columns([3, 3, 1])
    with col_sn:
        default_name = f"{selected_entity.get('display_name', selected_logical_name)} - {st.session_state.get('ah_selected_record_label', selected_record_id)}"
        save_name = st.text_input("Analysis Record Name", value=default_name, key="ah_save_hist_name")
    with col_sr:
        save_remark = st.text_input("Remark (optional)", placeholder="e.g. Investigating unexpected field update by Flow", key="ah_save_hist_remark")
    with col_sb:
        st.write("")
        st.write("")
        if st.button("💾 Save", key="ah_save_hist_btn", type="primary", width="stretch"):
            fname = persistence.save_history(_build_current_session_dict(), name=save_name, remark=save_remark)
            if fname:
                st.success(f"✅ Saved analysis as `{fname}`")
            else:
                st.error("❌ Save failed.")

    # ------------------------------------------------------------------ #
    # Step 5 – Correlate Cloud Flows & Processes                          #
    # ------------------------------------------------------------------ #
    st.divider()
    st.header("5. ⚡ Identify Automation Sources (Cloud Flows & Processes)")
    st.caption(
        "Dataverse audit records log `# PowerAutomate` or service accounts without specifying "
        "which exact flow triggered the update. This step correlates changed fields, timestamps, "
        "flow action steps (`clientdata`), and execution history (`flowruns` / `flowsessions` / `asyncoperations`)."
    )

    _PA_SCOPE = "https://service.flow.microsoft.com//.default"
    _ARM_SCOPE = "https://management.azure.com/user_impersonation"
    pa_connected = bool(st.session_state.get("cf_pa_token"))
    arm_token = _get_arm_token(ctx)
    arm_connected = bool(arm_token)

    with st.expander(
        "⚡ API Connections (Power Automate & Azure Resource Manager for exact run & payload matching)",
        expanded=not (pa_connected and arm_connected),
    ):
        c_pa_col, c_arm_col = st.columns(2)
        with c_pa_col:
            st.markdown("##### ⚡ Power Automate API")
            if pa_connected:
                st.success("✅ Connected to Power Automate API.")
                c_env_info, c_pa_disc = st.columns([2.5, 1])
                with c_env_info:
                    active_env = st.session_state.get("cf_pa_env_select") or st.session_state.get("cf_pa_env_id") or "Auto-detected"
                    st.caption(f"Active Environment: `{active_env}`")
                with c_pa_disc:
                    if st.button("Disconnect PA", key="ah_pa_disconnect"):
                        for k in ("cf_pa_token", "cf_pa_environments", "cf_pa_env_id", "cf_pa_env_select"):
                            st.session_state.pop(k, None)
                        st.rerun()
            else:
                st.info(
                    "Enables deep inspection of flow trigger outputs and action inputs for the target record GUID."
                )
                if st.button("🔗 Authorize Power Automate API", key="ah_pa_auth_btn"):
                    with st.spinner("Authorizing Power Automate..."):
                        try:
                            res = ctx.auth.acquire_token_silent([_PA_SCOPE])
                            if not res:
                                res = ctx.auth.login(scopes=[_PA_SCOPE])
                            if res and "access_token" in res:
                                st.session_state["cf_pa_token"] = res["access_token"]
                                st.success("Connected to Power Automate API!")
                                st.rerun()
                            else:
                                st.error("Authorization failed — no token returned.")
                        except Exception as exc:
                            st.error(f"Authorization error: {exc}")

        with c_arm_col:
            st.markdown("##### ☁️ Azure Resource Manager (Logic Apps)")
            if arm_connected:
                st.success("✅ Connected to Azure Resource Manager.")
                c_arm_info, c_arm_disc = st.columns([2.5, 1])
                with c_arm_info:
                    st.caption("Active ARM token loaded for Logic App run inspection.")
                with c_arm_disc:
                    if st.button("Disconnect ARM", key="ah_arm_disconnect"):
                        for k in ("la_arm_token", "lei_arm_token"):
                            st.session_state.pop(k, None)
                        st.rerun()
            else:
                st.info(
                    "Enables querying Azure Logic App runs in the 5-minute pre-update execution window and verifying payloads."
                )
                if st.button("🔗 Authorize Azure ARM", key="ah_arm_auth_btn"):
                    with st.spinner("Authorizing ARM..."):
                        try:
                            res = ctx.auth.acquire_token_silent([_ARM_SCOPE])
                            if not res:
                                res = ctx.auth.login(scopes=[_ARM_SCOPE])
                            if res and "access_token" in res:
                                st.session_state["la_arm_token"] = res["access_token"]
                                st.success("Connected to Azure Resource Manager!")
                                st.rerun()
                            else:
                                st.error("Authorization failed — no token returned.")
                        except Exception as exc:
                            st.error(f"Authorization error: {exc}")

    col_dir1, col_dir2 = st.columns([2.2, 1.8])
    with col_dir1:
        analysis_direction = st.radio(
            "🧭 Analysis Direction:",
            [
                "🔄 Both (Inbound Cause & Outbound Trigger Impact)",
                "⬅️ Inbound Only (What caused this record's updates?)",
                "➡️ Outbound Only (What automations did these updates trigger?)",
            ],
            index=0,
            horizontal=False,
            key="ah_analysis_direction",
            help=(
                "• Inbound: Discovers which flow, process, portal, or logic app execution caused the field change.\n"
                "• Outbound: Evaluates what automations triggered (or qualified to trigger) as a result of these updated values.\n"
                "• Both: Simultaneously correlates causes and triggered downstream impacts."
            ),
        )
    with col_dir2:
        include_inactive = st.checkbox(
            "🚨 Include Inactive / Turned-Off Flows",
            value=st.session_state.get("ah_chk_inactive", True),
            key="ah_chk_inactive",
            help=(
                "Check for flows that are currently Draft / Inactive (statecode=0) "
                "whose trigger conditions/column filters matched the record update, "
                "alerting you to automations that were supposed to run but were turned off."
            ),
        )
        if include_inactive:
            st.caption("⚠️ Inactive flow detection enabled: turned-off flows matching column triggers will be flagged as 🔴 Suppressed Automations.")

    col_opt1, col_opt2 = st.columns([1.5, 2])
    with col_opt1:
        tolerance_sec = st.slider(
            "⏱️ Execution Window Tolerance (seconds)",
            min_value=15,
            max_value=180,
            value=45,
            step=15,
            help="Tolerance window around audit timestamp to match flow execution start/completion.",
        )
    with col_opt2:
        only_auto = st.checkbox(
            "Only correlate automated changes (# PowerAutomate / System)",
            value=True,
            help="Skip user-initiated manual changes when searching flow execution runs.",
        )
        st.markdown("**Automations to Include:**")
        chk_c1, chk_c2, chk_c3, chk_c4 = st.columns(4)
        with chk_c1:
            include_cloud_flows = st.checkbox(
                "⚡ Cloud Flows",
                value=st.session_state.get("ah_chk_cf", True),
                help="Correlate Power Automate Cloud Flows.",
                key="ah_chk_cf",
            )
        with chk_c2:
            include_processes = st.checkbox(
                "⚙️ Processes",
                value=st.session_state.get("ah_chk_proc", True),
                help="Correlate Classic Workflows & System Jobs.",
                key="ah_chk_proc",
            )
        with chk_c3:
            include_logic_apps = st.checkbox(
                "☁️ Logic Apps",
                value=st.session_state.get("ah_chk_la", True),
                help="Correlate Azure Logic Apps (ARM).",
                key="ah_chk_la",
            )
        with chk_c4:
            include_portals = st.checkbox(
                "🌐 Portals / Pages",
                value=st.session_state.get("ah_chk_portals", True),
                help="Correlate Power Pages & Dynamics 365 Portal forms (Basic Forms, Multistep Forms, Web Form Sessions).",
                key="ah_chk_portals",
            )

        target_rg_scope: str | None = None
        if include_logic_apps:
            available_rgs, rg_counts = _get_available_resource_groups(ctx)
            auto_matched_rg = _match_resource_group_for_env(ctx, available_rgs)

            rg_options = []
            if auto_matched_rg:
                matched_cnt = rg_counts.get(auto_matched_rg, 0)
                rg_options.append(f"⚡ Auto: {auto_matched_rg} ({matched_cnt})")

            for rg in available_rgs:
                cnt = rg_counts.get(rg, 0)
                rg_options.append(f"{rg} ({cnt})")

            rg_options.append("(All Resource Groups)")

            selected_rg_choice = st.selectbox(
                "Logic App Resource Group Scope",
                options=rg_options,
                index=0,
                help=(
                    "Filter Logic Apps by Azure Resource Group. "
                    "Auto-detected based on the active Dataverse environment you logged into at the core of the app."
                ),
                key="ah_la_rg_scope",
            )

            if selected_rg_choice.startswith("⚡ Auto:"):
                target_rg_scope = auto_matched_rg
            elif selected_rg_choice == "(All Resource Groups)":
                target_rg_scope = None
            else:
                target_rg_scope = selected_rg_choice.split(" (")[0].strip()

    has_any_source = include_cloud_flows or include_processes or include_logic_apps
    if not has_any_source:
        st.warning("⚠️ Please select at least one automation source (Cloud Flows, Classic Processes, or Logic Apps) to correlate.")

    if st.button("⚡ Correlate Flows & Processes", type="primary", disabled=not has_any_source):
        st.session_state.pop("ah_correlation_results", None)
        st.session_state.pop("ah_logic_app_diagnostics", None)
        st.session_state.pop("ah_step5_duration", None)
        step5_start_time = time.perf_counter()
        prog_placeholder = st.empty()
        prog_bar = prog_placeholder.progress(0.05, text="1️⃣ Identifying selected automations...")
        with st.status("🔍 Analyzing automations and matching runs...", expanded=True) as status:
            try:
                st.write("1️⃣ Fetching Power Automate environment and active workflows...")
                
                # Resolve Power Automate environment identifier referencing Cloud Flow History plugin
                dv_org_id = client.get_instance_id() or ""
                pa_env = (
                    st.session_state.get("cf_pa_env_select")
                    or st.session_state.get("cf_pa_env_id")
                    or st.session_state.get("cff_env_id")
                    or dv_org_id
                    or (ctx.env_id.strip() if getattr(ctx, "env_id", None) else "")
                )
                pa_token = st.session_state.get("cf_pa_token")
                if not pa_token and hasattr(ctx, "auth"):
                    try:
                        res = ctx.auth.acquire_token_silent(["https://service.flow.microsoft.com//.default"])
                        if res and "access_token" in res:
                            pa_token = res["access_token"]
                            st.session_state["cf_pa_token"] = pa_token
                    except Exception:
                        pass
                if pa_token:
                    try:
                        import requests
                        pa_res = requests.get(
                            "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments?api-version=2016-11-01",
                            headers={"Authorization": f"Bearer {pa_token}", "Accept": "application/json"},
                            timeout=10,
                        )
                        if pa_res.status_code == 200:
                            envs = pa_res.json().get("value", [])
                            for e in envs:
                                e_name = e.get("name", "")
                                e_props = e.get("properties", {})
                                linked = e_props.get("linkedEnvironmentMetadata", {})
                                if (dv_org_id and e_name.lower() == dv_org_id.lower()) or \
                                   (dv_org_id and linked.get("instanceId", "").lower() == dv_org_id.lower()) or \
                                   (ctx.org_url and linked.get("instanceUrl", "").rstrip("/").lower() == ctx.org_url.rstrip("/").lower()):
                                    pa_env = e_name
                                    st.session_state["cf_pa_env_id"] = e_name
                                    break
                    except Exception:
                        pass

                if not pa_env:
                    pa_env = dv_org_id or "default"

                # ------------------------------------------------------------------ #
                # 1️⃣ Step 1: Discover Cloud Flows & Logic Apps touching target table #
                # ------------------------------------------------------------------ #
                prog_bar.progress(0.15, text="1️⃣ Identifying flows touching target table...")
                st.write("1️⃣ Identifying Cloud Flows and Azure Logic Apps that touch the target table...")

                raw_workflows = []
                wf_by_id = {}
                if include_cloud_flows or include_processes:
                    cached_wfs = st.session_state.get("ah_all_workflows_cache")
                    cached_inactive = st.session_state.get("ah_workflows_cached_with_inactive")
                    if cached_wfs and len(cached_wfs) > 200 and cached_inactive == include_inactive:
                        raw_workflows = list(cached_wfs)
                    else:
                        raw_workflows = client.get_workflows_for_entity(selected_logical_name, include_inactive=include_inactive)
                        if raw_workflows:
                            st.session_state["ah_all_workflows_cache"] = raw_workflows
                            st.session_state["ah_workflows_cached_with_inactive"] = include_inactive

                    # Targeted FetchXML / OData query to catch flows whose clientdata / primaryentity references the table
                    wf_by_id = {clean_guid(w.get("workflowid")): w for w in raw_workflows if w.get("workflowid")}
                    touching_results = client.get_workflows_touching_entity(selected_logical_name, include_inactive=include_inactive)
                    for tw in touching_results:
                        twid = clean_guid(tw.get("workflowid"))
                        if twid:
                            if twid in wf_by_id:
                                if tw.get("clientdata"):
                                    wf_by_id[twid]["clientdata"] = tw.get("clientdata")
                            else:
                                raw_workflows.append(tw)
                                wf_by_id[twid] = tw

                    # Filter raw_workflows based on selection
                    if not include_cloud_flows:
                        raw_workflows = [w for w in raw_workflows if w.get("category") != 5]
                    if not include_processes:
                        raw_workflows = [w for w in raw_workflows if w.get("category") == 5]
                    wf_by_id = {clean_guid(w.get("workflowid")): w for w in raw_workflows if w.get("workflowid")}

                # Incorporate discovered flows from Cloud Flow History if available
                if include_cloud_flows:
                    cf_flows = st.session_state.get("cf_flows", [])
                    if cf_flows:
                        for cfw in cf_flows:
                            wid = clean_guid(cfw.get("workflowid"))
                            if wid and wid not in wf_by_id:
                                raw_workflows.append(cfw)
                                wf_by_id[wid] = cfw

                # Incorporate Logic Apps from inspector cache if enabled
                if include_logic_apps:
                    try:
                        la_cache = load_logic_app_cache()
                        cached_las = la_cache.get("workflows", {})

                        # Determine effective target RG scope from selection
                        sel_choice = st.session_state.get("ah_la_rg_scope", "")
                        effective_rg: str | None = target_rg_scope
                        if effective_rg is None and sel_choice:
                            if sel_choice.startswith("⚡ Auto:"):
                                available_rgs, _ = _get_available_resource_groups(ctx)
                                effective_rg = _match_resource_group_for_env(ctx, available_rgs)
                            elif sel_choice != "(All Resource Groups)":
                                effective_rg = sel_choice.split(" (")[0].strip()

                        for la_id, la in cached_las.items():
                            la_rg = str(la.get("resource_group") or "").strip()

                            # Filter strictly by the selected Resource Group scope
                            if effective_rg and la_rg.lower() != effective_rg.lower():
                                continue

                            clean_la_id = clean_guid(la.get("id") or la_id)
                            if clean_la_id and clean_la_id not in wf_by_id:
                                la_row = {
                                    "workflowid": la.get("id") or la_id,
                                    "name": la.get("name") or "Unnamed Logic App",
                                    "category": 6,
                                    "category_label": "Azure Logic App",
                                    "statecode": 1 if la.get("state") == "Enabled" else 0,
                                    "primaryentity": "",
                                    "raw_definition": la.get("raw_definition") or la,
                                    "properties": la.get("raw_definition", {}).get("properties") if isinstance(la.get("raw_definition"), dict) else {},
                                    "subscription_id": la.get("subscription_id"),
                                    "resource_group": la.get("resource_group"),
                                    "portal_url": la.get("portal_url"),
                                }
                                raw_workflows.append(la_row)
                                wf_by_id[clean_la_id] = la_row
                    except Exception as la_c_exc:
                        logging.debug("Could not load Logic App cache: %s", la_c_exc)

                st.session_state["ah_all_workflows_cache"] = raw_workflows

                # Load persistent clientdata disk cache
                disk_cd_cache = persistence.load_clientdata_cache()
                if "ah_clientdata_cache" not in st.session_state:
                    st.session_state["ah_clientdata_cache"] = {}
                st.session_state["ah_clientdata_cache"].update(disk_cd_cache)
                client._workflow_cache.update(st.session_state["ah_clientdata_cache"])

                # Hydrate any cached clientdata into wf_by_id
                for wid, cd in client._workflow_cache.items():
                    if wid in wf_by_id and not wf_by_id[wid].get("clientdata"):
                        wf_by_id[wid]["clientdata"] = cd

                # For all Cloud Flows (category 5), verify trigger action by inspecting its flow definition
                # We do NOT filter flows by name — we inspect every Cloud Flow definition directly!
                cloud_flow_wids = [
                    clean_guid(w.get("workflowid"))
                    for w in raw_workflows
                    if w.get("category") == 5 and clean_guid(w.get("workflowid"))
                ]
                needed_wids = [
                    wid for wid in cloud_flow_wids
                    if wid in wf_by_id and not wf_by_id[wid].get("clientdata")
                ]

                if needed_wids:
                    prog_bar.progress(0.20, text=f"1️⃣ Inspecting trigger actions across {len(cloud_flow_wids)} Cloud Flow(s)...")
                    fetched_batch = client.get_workflows_clientdata_batch(needed_wids, max_workers=10)
                    st.session_state["ah_clientdata_cache"].update(fetched_batch)
                    persistence.save_clientdata_cache(st.session_state["ah_clientdata_cache"])
                    for wid, cd in fetched_batch.items():
                        if wid in wf_by_id:
                            wf_by_id[wid]["clientdata"] = cd

                # Parse definitions and filter flows touching target table based STRICTLY on trigger action & actions
                parsed_flows: list[CorrelatedFlow] = []
                table_touching_flows: list[CorrelatedFlow] = []
                for w in raw_workflows:
                    pf = extract_flow_definition(w, selected_logical_name)
                    parsed_flows.append(pf)
                    # A flow touches this table if its trigger action fires on this entity or an action modifies it
                    if pf.triggers_on_entity or pf.modifies_attributes or pf.action_details:
                        table_touching_flows.append(pf)

                st.session_state["ah_table_touching_flows"] = table_touching_flows
                cf_touching = [tf for tf in table_touching_flows if tf.category not in (6, 7)]
                la_touching = [tf for tf in table_touching_flows if tf.category == 6]
                portal_touching = [tf for tf in table_touching_flows if tf.category == 7]

                p_touch_msg = f" and **{len(portal_touching)}** Power Pages form(s)" if portal_touching else ""
                st.write(
                    f"   Found **{len(cf_touching)}** Cloud Flow(s), **{len(la_touching)}** Azure Logic App(s){p_touch_msg} touching table `'{selected_logical_name}'` "
                    f"(out of {len(raw_workflows)} total automations evaluated)."
                )
                if table_touching_flows:
                    with st.expander(f"📋 View {len(table_touching_flows)} Automations Touching '{selected_logical_name}' & Modified Fields", expanded=True):
                        for tf in table_touching_flows:
                            mod_fields = [f"`{col}`" for col in sorted(tf.modifies_attributes)]
                            mod_text = ", ".join(mod_fields) if mod_fields else "_Triggers only (no direct field update action detected)_"
                            trig_text = f" [Triggers: {', '.join(tf.trigger_events)}]" if tf.trigger_events else ""
                            st.markdown(f"- **{tf.name}** ({tf.category_label}){trig_text} → **Touches**: {mod_text}")

                # ------------------------------------------------------------------ #
                # 2️⃣ Step 2: Query candidate execution history in time window       #
                # ------------------------------------------------------------------ #
                # Derive time range from audit records
                raw_details = st.session_state.get("ah_raw_audit_details", [])
                date_times = []
                for d in raw_details:
                    c_on = (d.get("AuditRecord") or {}).get("createdon")
                    if c_on:
                        try:
                            date_times.append(pd.to_datetime(c_on))
                        except Exception:
                            pass

                target_guid_clean = clean_guid(selected_record_id).lower() if selected_record_id else ""
                clean_target_guids = {target_guid_clean} if target_guid_clean else set()
                # If analyzing a Contact, also allow matching on numeric Student / Banner ID (e.g. 140375569)
                # Ensure Application IDs or other lookup GUIDs are NEVER mixed into the contact target pool
                if (selected_logical_name or "").lower() == "contact":
                    for d in raw_details:
                        f_name = str(d.get("ChangedField") or "").lower()
                        if any(k in f_name for k in ("student", "banner", "id")):
                            for val_key in ("OldValue", "NewValue"):
                                v = str(d.get(val_key) or "").strip()
                                if v and v.isdigit() and 6 <= len(v) <= 12:
                                    clean_target_guids.add(v.lower())

                pa_client = None
                if pa_token:
                    try:
                        from plugins.cloud_flow.client import PowerAutomateClient
                        pa_client = PowerAutomateClient(pa_token)
                    except Exception:
                        pa_client = None

                logic_client = None
                arm_tok = _get_arm_token(ctx)
                if arm_tok:
                    try:
                        logic_client = LogicAppClient(arm_tok)
                    except Exception:
                        logic_client = None

                flow_runs = []
                flow_sessions = []
                async_ops = []
                logic_runs = []
                portal_sessions = []
                portal_forms = []

                GENERIC_FLOW_NAMES = {"cloud flow", "unnamed flow", "unknown", "flow", "logic app", "azure logic app", ""}
                touching_cf_ids = {clean_guid(tf.workflow_id) for tf in cf_touching if clean_guid(tf.workflow_id)}
                touching_cf_names = {
                    tf.name.lower().strip() for tf in cf_touching
                    if tf.name and tf.name.lower().strip() not in GENERIC_FLOW_NAMES
                }

                def _parse_run_dt(val):
                    if val is None or val == "":
                        return None
                    try:
                        dt = pd.to_datetime(val)
                        if getattr(dt, "tzinfo", None) is not None:
                            try:
                                return dt.tz_convert(None)
                            except TypeError:
                                return dt.tz_localize(None)
                        return dt
                    except Exception:
                        return None

                def _is_touching_cf_run(r: dict) -> bool:
                    if not touching_cf_ids and not touching_cf_names:
                        return False
                    r_wid = clean_guid(r.get("flowid") or r.get("workflowid") or r.get("_flow_id") or r.get("_regardingobjectid_value"))
                    if r_wid and r_wid in touching_cf_ids:
                        return True
                    r_name = str(r.get("flowname") or r.get("Flow Name") or "").lower().strip()
                    if r_name and r_name not in GENERIC_FLOW_NAMES and r_name in touching_cf_names:
                        return True
                    return False

                # 2.1 Earliest audit start time - 10 minutes
                # 2.2 Latest audit start time + 2 minutes
                if date_times:
                    raw_min = min(date_times) - pd.Timedelta(minutes=10)
                    raw_max = max(date_times) + pd.Timedelta(minutes=2)
                    min_dt = _parse_run_dt(raw_min)
                    max_dt = _parse_run_dt(raw_max)
                    start_iso = min_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if min_dt else ""
                    end_iso = max_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if max_dt else ""
                    st.write(
                        f"2️⃣ Querying execution history between **{start_iso}** and **{end_iso}** "
                        f"(Window: Earliest audit time - 10 min to Latest audit time + 2 min)..."
                    )

                    parsed_audit_dts = [_parse_run_dt(dt) for dt in date_times if _parse_run_dt(dt) is not None]
                    window_tol_sec = max(tolerance_sec, 600)  # at least 10 minutes window around each audit event

                    def _is_run_in_window(st_val) -> bool:
                        if not st_val or not parsed_audit_dts:
                            return True
                        p_dt = _parse_run_dt(st_val)
                        if p_dt is None:
                            return False
                        # Must fall within the overall bounding window AND be proximate to an actual audit update
                        if min_dt and max_dt and not (min_dt <= p_dt <= max_dt):
                            return False
                        for a_dt in parsed_audit_dts:
                            if abs((p_dt - a_dt).total_seconds()) <= window_tol_sec:
                                return True
                        return False

                    if include_cloud_flows:
                        for r in client.get_flow_runs(start_iso, end_iso):
                            if _is_touching_cf_run(r) and _is_run_in_window(r.get("starttime")):
                                flow_runs.append(r)

                        for s in client.get_flow_sessions(start_iso, end_iso):
                            if _is_touching_cf_run(s) and _is_run_in_window(s.get("startedon")):
                                flow_sessions.append(s)

                    if include_processes:
                        for gid in clean_target_guids:
                            ops = client.get_async_operations(
                                gid, start_iso=start_iso, end_iso=end_iso
                            )
                            for op in ops:
                                if op.get("asyncoperationid") not in {o.get("asyncoperationid") for o in async_ops}:
                                    async_ops.append(op)

                    if include_portals:
                        for gid in clean_target_guids:
                            ps_rows = client.get_portal_webform_sessions(
                                gid, start_iso=start_iso, end_iso=end_iso
                            )
                            for ps in ps_rows:
                                ps_id = ps.get("adx_webformsessionid") or ps.get("mspp_webformsessionid") or ps.get("webformsessionid")
                                if ps_id and ps_id not in {p.get("adx_webformsessionid") or p.get("mspp_webformsessionid") or p.get("webformsessionid") for p in portal_sessions}:
                                    portal_sessions.append(ps)
                        portal_forms = client.get_portal_forms_for_entity(selected_logical_name)
                else:
                    min_dt = None
                    max_dt = None
                    start_iso = None
                    end_iso = None
                    st.write("2️⃣ Querying execution history...")

                    def _is_run_in_window(st_val) -> bool:
                        return True

                    if include_portals:
                        for gid in clean_target_guids:
                            ps_rows = client.get_portal_webform_sessions(gid)
                            for ps in ps_rows:
                                ps_id = ps.get("adx_webformsessionid") or ps.get("mspp_webformsessionid") or ps.get("webformsessionid")
                                if ps_id and ps_id not in {p.get("adx_webformsessionid") or p.get("mspp_webformsessionid") or p.get("webformsessionid") for p in portal_sessions}:
                                    portal_sessions.append(ps)
                        portal_forms = client.get_portal_forms_for_entity(selected_logical_name)

                # Merge cached / active runs from Cloud Flow History (strictly filtered to touching flows & window)
                if include_cloud_flows:
                    cf_cached = st.session_state.get("cf_date_runs")
                    if not cf_cached:
                        try:
                            from plugins.cloud_flow.persistence import load_results as load_cf_results
                            cf_cached = load_cf_results()
                        except Exception:
                            cf_cached = []

                    if cf_cached:
                        existing_run_ids = {r.get("flowrunid") for r in flow_runs if r.get("flowrunid")}
                        for cr in cf_cached:
                            c_rid = cr.get("flowrunid")
                            if c_rid and c_rid in existing_run_ids:
                                continue
                            if not _is_touching_cf_run(cr):
                                continue
                            st_str = cr.get("starttime") or cr.get("startedon")
                            if not _is_run_in_window(st_str):
                                continue
                            flow_runs.append(cr)
                            if c_rid:
                                existing_run_ids.add(c_rid)

                # If PA token is available, query Power Automate API specifically for table-touching flows!
                if include_cloud_flows and pa_client and pa_env and pa_env != "default" and date_times:
                    try:
                        touching_wf_ids = {tf.workflow_id for tf in table_touching_flows if tf.category == 5 and tf.workflow_id}
                        target_wfs = [w for w in raw_workflows if clean_guid(w.get("workflowid")) in touching_wf_ids]
                        if not target_wfs:
                            target_wfs = [w for w in raw_workflows if w.get("category") == 5][:25]

                        import concurrent.futures
                        def _fetch_pa_runs(wf_item):
                            w_id = wf_item.get("workflowid")
                            if not w_id:
                                return []
                            try:
                                return pa_client.get_flow_runs(
                                    pa_env, w_id, top=50, start_date=start_iso, end_date=end_iso
                                )
                            except Exception:
                                return []

                        existing_run_ids = {r.get("flowrunid") for r in flow_runs if r.get("flowrunid")}
                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, max(1, len(target_wfs)))) as executor:
                            futures = {executor.submit(_fetch_pa_runs, wf): wf for wf in target_wfs}
                            for fut in concurrent.futures.as_completed(futures):
                                wf_obj = futures[fut]
                                w_id = wf_obj.get("workflowid")
                                w_name = wf_obj.get("name") or "Cloud Flow"
                                try:
                                    for r in fut.result():
                                        r_id = r.get("name") or r.get("flowrunid")
                                        if r_id and r_id in existing_run_ids:
                                            continue
                                        r_start_str = r.get("properties", {}).get("startTime")
                                        if not _is_run_in_window(r_start_str):
                                            continue
                                        flow_runs.append({
                                            "flowrunid": r_id,
                                            "starttime": r.get("properties", {}).get("startTime"),
                                            "endtime": r.get("properties", {}).get("endTime"),
                                            "status": r.get("properties", {}).get("status"),
                                            "workflowid": w_id,
                                            "Flow Name": w_name,
                                            "_env_id": pa_env,
                                            "_pa_run": True,
                                        })
                                        if r_id:
                                            existing_run_ids.add(r_id)
                                except Exception:
                                    pass
                    except Exception as pa_exc:
                        logging.debug("Direct PA flow run fetch skipped: %s", pa_exc)

                # If ARM token is available, query Logic Apps API specifically for table-touching Logic Apps!
                if include_logic_apps and logic_client and date_times and la_touching:
                    prog_bar.progress(0.40, text=f"2️⃣ Fetching execution runs for {len(la_touching)} Logic App(s)...")
                    try:
                        import concurrent.futures
                        start_bound = _parse_run_dt(min_dt)
                        end_bound = _parse_run_dt(max_dt)

                        def _fetch_single_la_runs(la_item: CorrelatedFlow):
                            raw_def = la_item.raw_definition or {}
                            sub_id = raw_def.get("subscription_id")
                            rg = raw_def.get("resource_group")
                            app_name = la_item.name
                            if not sub_id or not rg:
                                w_id_str = str(la_item.workflow_id or "")
                                m_sub = re.search(r"/subscriptions/([^/]+)/", w_id_str, re.IGNORECASE)
                                m_rg = re.search(r"/resourceGroups/([^/]+)/", w_id_str, re.IGNORECASE)
                                sub_id = m_sub.group(1) if m_sub else sub_id
                                rg = m_rg.group(1) if m_rg else rg

                            if not sub_id or not rg or not app_name:
                                return []
                            try:
                                return logic_client.get_runs(
                                    sub_id,
                                    rg,
                                    app_name,
                                    top=100,
                                    max_runs=2000,
                                    since_dt=start_bound,
                                    until_dt=end_bound,
                                )
                            except Exception:
                                return []

                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, max(1, len(la_touching)))) as executor:
                            futures = {executor.submit(_fetch_single_la_runs, tf): tf for tf in la_touching}
                            for fut in concurrent.futures.as_completed(futures):
                                la_tf = futures[fut]
                                try:
                                    raw_runs = fut.result()
                                    for r in raw_runs:
                                        props = r.get("properties", {})
                                        r_start_str = props.get("startTime")
                                        if not r_start_str:
                                            continue
                                        r_start = _parse_run_dt(r_start_str)
                                        if r_start is None:
                                            continue
                                        if (start_bound and r_start < start_bound) or (end_bound and r_start > end_bound):
                                            continue
                                        r_id = r.get("id")
                                        if not r_id or not str(r_id).startswith("/subscriptions/"):
                                            w_clean = str(la_tf.workflow_id or "").rstrip("/")
                                            r_id = f"{w_clean}/runs/{r.get('name')}" if w_clean.startswith("/subscriptions/") else (r.get("name") or "")
                                        logic_runs.append({
                                            "id": r_id,
                                            "name": r.get("name"),
                                            "workflow_id": la_tf.workflow_id,
                                            "flow_name": la_tf.name,
                                            "properties": props,
                                            "maker_url": f"https://portal.azure.com/#view/Microsoft_Azure_EMA/LogicAppsMonitorBlade/runid/{urllib.parse.quote(r_id, safe='')}" if r_id else "",
                                        })
                                except Exception:
                                    pass
                    except Exception as la_run_exc:
                        logging.debug("Direct Logic App run fetch skipped: %s", la_run_exc)

                port_msg = f", and **{len(portal_sessions)}** Portal session(s)" if include_portals else ""
                st.write(
                    f"   Retrieved **{len(flow_runs)}** candidate Cloud Flow run(s) (filtered to {len(cf_touching)} touching flows in time window), "
                    f"**{len(logic_runs)}** Logic App run(s) (for {len(la_touching)} touching Logic Apps), "
                    f"**{len(flow_sessions)}** flow session(s), **{len(async_ops)}** system job(s){port_msg}. "
                    f"*(Target Record GUID verification will be performed in Step 3.1)*"
                )

                # Ensure clientdata is loaded for any flow appearing in execution history
                candidate_wids = set()
                for fr in flow_runs:
                    cwid = clean_guid(fr.get("flowid") or fr.get("workflowid") or fr.get("_flow_id"))
                    if cwid:
                        candidate_wids.add(cwid)
                for fs in flow_sessions:
                    cwid = clean_guid(fs.get("_regardingobjectid_value") or fs.get("workflowid") or fs.get("_flow_id"))
                    if cwid:
                        candidate_wids.add(cwid)

                missing_meta_wids = [cwid for cwid in candidate_wids if not wf_by_id.get(cwid)]
                for cwid in missing_meta_wids:
                    fetched_wf = client.get_workflow(cwid)
                    if fetched_wf:
                        raw_workflows.append(fetched_wf)
                        wf_by_id[cwid] = fetched_wf

                missing_cd_wids = [
                    cwid for cwid in candidate_wids
                    if wf_by_id.get(cwid) and not wf_by_id[cwid].get("clientdata") and wf_by_id[cwid].get("category") != 6
                ]
                if missing_cd_wids:
                    fetched_batch = client.get_workflows_clientdata_batch(missing_cd_wids, max_workers=8)
                    if "ah_clientdata_cache" in st.session_state:
                        st.session_state["ah_clientdata_cache"].update(fetched_batch)
                    for wid, cd in fetched_batch.items():
                        if wid in wf_by_id:
                            wf_by_id[wid]["clientdata"] = cd

                # ------------------------------------------------------------------ #
                # 3️⃣ Step 3: Correlate filtered history flows targeting record GUID  #
                # ------------------------------------------------------------------ #
                prog_bar.progress(0.50, text="3️⃣ Correlating field changes with flow execution history...")
                st.write("3️⃣ Correlating field changes with flow definitions, temporal window, and target record GUID...")

                def _on_correlate_progress(cur: int, tot: int, msg: str):
                    pct = 0.50 + 0.49 * (cur / max(1, tot))
                    prog_bar.progress(min(pct, 0.99), text=f"3️⃣ {msg}")

                do_inbound = "Inbound" in analysis_direction or "Both" in analysis_direction
                do_outbound = "Outbound" in analysis_direction or "Both" in analysis_direction

                fresh_meta = st.session_state.get("ah_fresh_metadata", {})
                cached_v_runs = st.session_state.get("ah_verified_runs_cache")
                cached_v_acts = st.session_state.get("ah_verified_actions_cache")
                correlator = AuditProcessCorrelator(
                    environment_id=pa_env, org_url=client.org_url
                )

                correlated_items = []
                if do_inbound:
                    correlated_items = correlator.correlate(
                        raw_audit_details=raw_details,
                        raw_workflows=raw_workflows,
                        flow_runs=flow_runs,
                        flow_sessions=flow_sessions,
                        async_operations=async_ops,
                        target_entity_logical=selected_logical_name,
                        attr_metadata=fresh_meta,
                        tolerance_seconds=tolerance_sec,
                        only_automations=only_auto,
                        target_guids=clean_target_guids,
                        pa_client=pa_client,
                        logic_runs=logic_runs,
                        logic_client=logic_client,
                        portal_sessions=portal_sessions,
                        portal_forms=portal_forms,
                        progress_callback=_on_correlate_progress,
                        cached_verified_runs=cached_v_runs,
                        cached_verified_actions=cached_v_acts,
                        max_workers=20,
                        cloud_flow_workers=4,
                    )
                    st.session_state["ah_correlation_results"] = correlated_items
                    st.write(f"   📊 **Step 3.2 Inbound Attribution**: Correlated **{len(correlated_items)}** audit field update(s) locally against verified runs and flow definitions.")
                else:
                    # Parse workflows into correlator.candidate_flows and executions for outbound only (fast path)
                    correlator.prepare_execution_context(
                        raw_workflows=raw_workflows,
                        flow_runs=flow_runs,
                        flow_sessions=flow_sessions,
                        async_operations=async_ops,
                        target_entity_logical=selected_logical_name,
                        raw_audit_details=raw_details,
                        tolerance_seconds=tolerance_sec,
                        logic_runs=logic_runs,
                        portal_sessions=portal_sessions,
                        portal_forms=portal_forms,
                    )
                    st.session_state.pop("ah_correlation_results", None)

                # Report Step 3.1 Target GUID verification results
                if do_inbound:
                    if getattr(correlator, "verified_runs", None):
                        st.write(
                            f"   🎯 **Step 3.1 Target GUID Verification**: Found **{len(correlator.verified_runs)}** execution run(s) "
                            f"directly operating on target record `{target_guid_clean}`:"
                        )
                        for vr in correlator.verified_runs:
                            f_src = "Power Pages Form" if vr["source"] == "portals" else ("Azure Logic App" if vr["source"] == "logicapps" else ("Cloud Flow" if vr["source"] == "flowruns" else "Process"))
                            time_str = vr["started_on"].strftime("%H:%M:%S") if hasattr(vr["started_on"], "strftime") else str(vr["started_on"])
                            act_str = f" → Action: `{vr['action_name']}`" if vr["action_name"] else ""
                            st.write(f"     • **{vr['flow_name']}** ({f_src}) at `{time_str}`{act_str}")
                    else:
                        st.write(f"   ℹ️ **Step 3.1 Target GUID Verification**: No candidate runs in this window directly referenced target record `{target_guid_clean}`.")

                outbound_items = []
                if do_outbound:
                    prog_bar.progress(0.92, text="3️⃣ Evaluating outbound flow triggers and conditions...")
                    st.write("3️⃣ Evaluating outbound flow triggers, column filters, and conditions...")
                    outbound_items = correlator.correlate_outbound(
                        raw_audit_details=raw_details,
                        candidate_flows=correlator.candidate_flows or table_touching_flows,
                        executions=correlator.executions,
                        target_entity_logical=selected_logical_name,
                        attr_metadata=fresh_meta,
                        tolerance_seconds=tolerance_sec,
                        raw_workflows=raw_workflows,
                    )
                    st.session_state["ah_outbound_results"] = outbound_items
                    st.write(f"   🚀 **Step 3.3 Outbound Triggers**: Evaluated **{len(outbound_items)}** potential trigger impact(s) across candidate flow(s).")
                else:
                    st.session_state.pop("ah_outbound_results", None)

                la_diags = getattr(correlator, "logic_app_diagnostics", [])
                st.session_state["ah_logic_app_diagnostics"] = la_diags
                if la_diags:
                    _render_la_diagnostics(la_diags, default_expanded=False)

                step5_elapsed = time.perf_counter() - step5_start_time
                st.session_state["ah_step5_duration"] = step5_elapsed
                dur_str = f"{step5_elapsed:.1f}s" if step5_elapsed < 60 else f"{int(step5_elapsed // 60)}m {int(step5_elapsed % 60)}s"

                summary_status_str = f"✅ Correlation complete in {dur_str}!"
                if do_inbound:
                    summary_status_str += f" {len(correlated_items)} field update(s) checked."
                if do_outbound:
                    summary_status_str += f" {len(outbound_items)} outbound impact(s) evaluated."
                prog_bar.progress(1.0, text=summary_status_str)
                if do_inbound:
                    st.session_state["ah_correlation_results"] = correlated_items
                st.session_state["ah_verified_runs"] = getattr(correlator, "verified_runs", [])
                st.session_state["ah_verified_runs_cache"] = getattr(correlator, "verified_runs_cache", {})
                st.session_state["ah_verified_actions_cache"] = getattr(correlator, "verified_actions_cache", {})
                st.session_state["ah_env_id"] = pa_env
                st.session_state["ah_raw_workflows"] = raw_workflows
                # Auto-save session cache including correlation results and duration
                persistence.save_results(_build_current_session_dict())
                status.update(
                    label=f"✅ Automation correlation complete in {dur_str}!",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:
                if "step5_start_time" in locals():
                    st.session_state["ah_step5_duration"] = time.perf_counter() - step5_start_time
                if "correlator" in locals():
                    st.session_state["ah_logic_app_diagnostics"] = getattr(correlator, "logic_app_diagnostics", [])
                prog_bar.progress(1.0, text=f"❌ Correlation failed: {exc}")
                status.update(label=f"❌ Correlation failed: {exc}", state="error")
                st.exception(exc)


    # ------------------------------------------------------------------ #
    # Render Correlation Results                                          #
    # ------------------------------------------------------------------ #
    corr_results: list[AuditCorrelationItem] = st.session_state.get(
        "ah_correlation_results", []
    )
    outbound_results: list[OutboundTriggerItem] = st.session_state.get(
        "ah_outbound_results", []
    )
    persistent_la_diags = st.session_state.get("ah_logic_app_diagnostics", [])

    if corr_results or outbound_results:
        step5_dur = st.session_state.get("ah_step5_duration")
        if step5_dur is not None:
            dur_display = (
                f"{step5_dur:.1f}s"
                if step5_dur < 60
                else f"{int(step5_dur // 60)}m {int(step5_dur % 60)}s"
            )
            st.caption(f"⚡ Step 5 Automation analysis completed in **{dur_display}**.")

        if persistent_la_diags:
            _render_la_diagnostics(persistent_la_diags, default_expanded=False)

        st.subheader("🎯 Step 5: Process & Flow Automation Results")

        # Save Step 5 result to named history
        with st.expander("💾 Save Step 5 Results to History", expanded=False):
            col_sn, col_sr, col_sb = st.columns([3, 3, 1])
            with col_sn:
                default_name = f"{selected_entity.get('display_name', selected_logical_name)} - {st.session_state.get('ah_selected_record_label', selected_record_id)} (Flow Correlation)"
                save_name_step5 = st.text_input("Record Name", value=default_name, key="ah_save_step5_name")
            with col_sr:
                save_remark_step5 = st.text_input("Remark (optional)", placeholder="e.g. Inbound attribution and outbound trigger impact", key="ah_save_step5_remark")
            with col_sb:
                st.write("")
                st.write("")
                if st.button("💾 Save", key="ah_save_step5_btn", type="primary", width="stretch"):
                    fname = persistence.save_history(_build_current_session_dict(), name=save_name_step5, remark=save_remark_step5)
                    if fname:
                        st.success(f"✅ Saved Step 5 analysis to history: `{fname}`")
                    else:
                        st.error("❌ Save failed.")

        outbound_first = bool(outbound_results and not corr_results)
        if outbound_first:
            tab_outbound, tab_inbound = st.tabs([
                f"➡️ Outbound Trigger Impact ({len(outbound_results)} events)",
                f"⬅️ Inbound Attribution ({len(corr_results)} fields)",
            ])
        else:
            tab_inbound, tab_outbound = st.tabs([
                f"⬅️ Inbound Attribution ({len(corr_results)} fields)",
                f"➡️ Outbound Trigger Impact ({len(outbound_results)} events)",
            ])

        with tab_inbound:
            if not corr_results:
                st.info("ℹ️ No Inbound Attribution results available. Select 'Both' or 'Inbound Only' and click '⚡ Correlate Flows & Processes'.")
            else:
                total_mutations = len(corr_results)
                auto_mutations = sum(1 for c in corr_results if c.is_automation)
                exact_matches = sum(1 for c in corr_results if c.confidence == "Exact Run Match")
                probable_matches = sum(
                    1 for c in corr_results if c.confidence in ("Probable Session Match", "Candidate Flow")
                )

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Analyzed Mutations", total_mutations)
                m2.metric("Automated (# PowerAutomate)", auto_mutations)
                m3.metric("🟢 Exact Run Matches", exact_matches)
                m4.metric("🟡/🔵 Candidate Matches", probable_matches)

                grouped_items = group_correlations(corr_results)

                def _get_sort_dt(item: GroupedAuditExecution) -> datetime:
                    dt = item.started_on or item.timestamp
                    if dt is None:
                        return datetime.min.replace(tzinfo=timezone.utc)
                    if dt.tzinfo is None:
                        return dt.replace(tzinfo=timezone.utc)
                    return dt

                tab_cf, tab_cards = st.tabs([
                    "⚡ Cloud Flow History Runs (Grouped by Process Execution)",
                    "🔎 Grouped Execution Cards & Drill-down",
                ])

                with tab_cf:
                    st.caption(
                        "Grouped by each execution history record of the Attributed Flow / Process (styled like the Cloud Flow History plugin), "
                        "with run status, duration, consolidated variable mutations, and direct links to Power Automate run history."
                    )

                    # Sort and layout controls
                    ctrl_c1, ctrl_c2 = st.columns([1.5, 2])
                    with ctrl_c1:
                        sort_order = st.radio(
                            "Chronological Sort:",
                            ["⬇️ Newest First (Descending)", "⬆️ Oldest First (Ascending)"],
                            horizontal=True,
                            key="ah_sort_order",
                        )
                    with ctrl_c2:
                        view_mode = st.radio(
                            "Display Layout:",
                            ["📋 Multi-line Table (Each Variable on Next Line)", "📊 Data Grid"],
                            horizontal=True,
                            key="ah_view_mode",
                        )

                    # Filter controls matching Cloud Flow History plugin
                    all_statuses = sorted(list({g.run_status or "Unknown" for g in grouped_items}))
                    all_flows = sorted(list({g.flow_name for g in grouped_items if g.flow_name}))

                    with st.expander("🎯 Filter Results", expanded=False):
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            sel_status = st.multiselect("Filter Status", options=all_statuses, key="ah_f_status")
                        with fc2:
                            sel_flows = st.multiselect("Filter Flow Name", options=all_flows, key="ah_f_flow")
                        with fc3:
                            g_search = st.text_input("Search all columns", placeholder="Type to search…", key="ah_f_global")

                    filtered_groups: list[GroupedAuditExecution] = []
                    for g in grouped_items:
                        st_val = g.run_status or "Unknown"
                        if sel_status and st_val not in sel_status:
                            continue
                        if sel_flows and g.flow_name not in sel_flows:
                            continue
                        if g_search:
                            search_str = f"{g.flow_name} {g.run_status} {g.user_name} {g.changed_date_str} {g.changes_summary}".lower()
                            if g_search.lower() not in search_str:
                                continue
                        filtered_groups.append(g)

                    is_desc = "Newest" in sort_order
                    filtered_groups.sort(key=_get_sort_dt, reverse=is_desc)

                    if len(filtered_groups) != len(grouped_items):
                        st.info(f"Showing {len(filtered_groups)} of {len(grouped_items)} flow execution records.")

                    if view_mode == "📋 Multi-line Table (Each Variable on Next Line)":
                        if not filtered_groups:
                            st.info("No flow execution records match the current filter.")
                        else:
                            st.html(render_flow_history_table_html(filtered_groups))
                    else:
                        run_rows = []
                        for g in filtered_groups:
                            dur_display = g.duration_str
                            if not dur_display and g.started_on and g.completed_on:
                                secs = int((g.completed_on - g.started_on).total_seconds())
                                dur_display = f"{secs}s" if secs >= 1 else "<1s"
                            elif not dur_display:
                                dur_display = "—"

                            start_dt = _get_sort_dt(g)
                            history_link = g.process_url or g.flow_history_url or ""
                            flow_28d_link = g.flow_history_url if (g.flow_history_url and g.flow_history_url != g.process_url) else ""

                            run_rows.append(
                                {
                                    "Flow Name": g.flow_name,
                                    "Status": g.run_status or "Unknown",
                                    "Started": start_dt,
                                    "Duration": dur_display,
                                    "Changed By": g.user_name,
                                    "Variables Modified (Old ➔ New)": g.changes_summary,
                                    "Vars": f"{g.field_count} var(s)",
                                    "Confidence": f"{g.confidence_badge} {g.confidence}",
                                    "Open Run": history_link,
                                    "Flow History": flow_28d_link,
                                }
                            )

                        df_runs = pd.DataFrame(run_rows)
                        if not df_runs.empty:
                            col_pos = {col: i for i, col in enumerate(df_runs.columns)}

                            def _style_history_row(row: pd.Series) -> list[str]:
                                styles = [""] * len(row)
                                if "Flow Name" in col_pos:
                                    styles[col_pos["Flow Name"]] = _flow_color(row.get("Flow Name"))
                                if "Status" in col_pos:
                                    s = str(row.get("Status", "")).lower()
                                    idx = col_pos["Status"]
                                    if "succeeded" in s:
                                        styles[idx] = "background-color: rgba(40,167,69,0.35); color: #155724; font-weight: bold"
                                    elif "failed" in s:
                                        styles[idx] = "background-color: rgba(220,53,69,0.35); color: #721c24; font-weight: bold"
                                    elif any(x in s for x in ("running", "cancelled", "timedout")):
                                        styles[idx] = "background-color: rgba(255,193,7,0.35); color: #856404; font-weight: bold"
                                    elif "candidate" in s or "match" in s:
                                        styles[idx] = "background-color: rgba(0,123,255,0.25); color: #004085; font-weight: bold"
                                return styles

                            styled = df_runs.style.apply(_style_history_row, axis=1)
                            col_cfg: dict = {
                                "Flow Name": st.column_config.TextColumn("Flow / Process Name", width="medium"),
                                "Status": st.column_config.TextColumn("Status", width="small"),
                                "Started": st.column_config.DatetimeColumn("Started", format="DD/MM/YYYY HH:mm:ss", width="medium"),
                                "Duration": st.column_config.Column("Duration", width="small"),
                                "Changed By": st.column_config.TextColumn("Changed By", width="medium"),
                                "Variables Modified (Old ➔ New)": st.column_config.TextColumn(
                                    "Variables Modified (Old ➔ New)",
                                    help="All variables/fields modified together in this single process execution",
                                    width="large",
                                ),
                                "Vars": st.column_config.TextColumn("Vars", width="small"),
                                "Confidence": st.column_config.TextColumn("Confidence", width="small"),
                                "Open Run": st.column_config.LinkColumn(
                                    "Open Run",
                                    display_text="View",
                                    help="Direct link to open the flow execution run history in Power Automate",
                                ),
                                "Flow History": st.column_config.LinkColumn(
                                    "Flow History",
                                    display_text="28d Runs",
                                    help="Direct link to open the 28-day flow run history in Power Automate Maker Portal",
                                ),
                            }
                            st.dataframe(styled, width="stretch", hide_index=True, column_config=col_cfg)

                    export_rows = []
                    for g in filtered_groups:
                        s_display = g.started_on.strftime("%d/%m/%Y %H:%M:%S") if g.started_on else g.changed_date_str
                        export_rows.append(
                            {
                                "Flow / Process Name": g.flow_name,
                                "Status": g.run_status,
                                "Started": s_display,
                                "Duration": g.duration_str,
                                "Changed By": g.user_name,
                                "Variables Modified": g.changes_summary,
                                "Vars Count": g.field_count,
                                "Confidence": f"{g.confidence_badge} {g.confidence}",
                                "Run Link": g.process_url,
                                "28d History Link": g.flow_history_url,
                            }
                        )
                    df_export = pd.DataFrame(export_rows)
                    csv_data = df_export.to_csv(index=False).encode("utf-8")

                    col_exp_csv, col_exp_xlsx = st.columns([1, 1])
                    with col_exp_csv:
                        st.download_button(
                            "⬇️ Export Inbound Table (CSV)",
                            data=csv_data,
                            file_name=f"step5_inbound_flows_{selected_logical_name}_{str(selected_record_id)[:8]}.csv",
                            mime="text/csv",
                            key="ah_export_csv",
                            width="stretch",
                        )
                    with col_exp_xlsx:
                        excel_buffer = io.BytesIO()
                        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                            df_export.to_excel(writer, index=False, sheet_name="Attributed Flows")
                            det_rows = []
                            for c in corr_results:
                                f_name = c.matched_flow.name if c.matched_flow else (c.candidate_flows[0].name if c.candidate_flows else "-")
                                r_id = c.matched_run.run_id if c.matched_run else "-"
                                det_rows.append({
                                    "Changed Date": c.changed_date_str,
                                    "Changed By": c.user_name,
                                    "Event": c.event,
                                    "Changed Field": c.changed_field,
                                    "Field Logical": c.changed_field_logical,
                                    "Old Value": str(c.old_value) if c.old_value is not None else "",
                                    "New Value": str(c.new_value) if c.new_value is not None else "",
                                    "Attributed Automation": f_name,
                                    "Run ID": r_id,
                                    "Confidence": f"{c.confidence_badge} {c.confidence}",
                                    "Attribution Details": c.attribution_summary,
                                })
                            if det_rows:
                                pd.DataFrame(det_rows).to_excel(writer, index=False, sheet_name="Field Mutations")
                        st.download_button(
                            "📊 Export Inbound Table (Excel .xlsx)",
                            data=excel_buffer.getvalue(),
                            file_name=f"step5_inbound_flows_{selected_logical_name}_{str(selected_record_id)[:8]}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="ah_export_excel",
                            width="stretch",
                        )

                with tab_cards:
                    cards_c1, cards_c2 = st.columns([1.5, 2])
                    with cards_c1:
                        cards_sort = st.radio(
                            "Order Cards by Started Date/Time:",
                            ["⬇️ Newest First (Descending)", "⬆️ Oldest First (Ascending)"],
                            horizontal=True,
                            key="ah_cards_sort_order",
                        )

                    is_cards_desc = "Newest" in cards_sort
                    sorted_cards = sorted(grouped_items, key=_get_sort_dt, reverse=is_cards_desc)

                    for g in sorted_cards:
                        start_label = g.started_on.strftime("%d/%m/%Y %H:%M:%S") if g.started_on else g.changed_date_str
                        header = f"{g.flow_name} — Started: {start_label} ({g.field_count} variable(s) changed by {g.user_name})"
                        with st.container(border=True):
                            st.markdown(f"##### {header}")
                            c1, c2 = st.columns([2, 1])
                            with c1:
                                st.markdown(f"**Confidence:** {g.confidence_badge} `{g.confidence}`")
                                st.markdown(
                                    f"**Status:** `{g.run_status}` | **Started:** `{start_label}`"
                                    + (f" | **Duration:** `{g.duration_str}`" if g.duration_str else "")
                                )
                                if g.attribution_summary:
                                    st.caption(g.attribution_summary)
                            with c2:
                                if g.process_url:
                                    st.markdown(f"[🔗 {g.process_link_label or 'Open Run History in Power Automate ↗'}]({g.process_url})")
                                if g.flow_history_url and g.flow_history_url != g.process_url:
                                    st.markdown(f"[📋 {g.flow_history_label or 'Open 28-day Flow History ↗'}]({g.flow_history_url})")

                            df_vars = pd.DataFrame(g.fields_changed)
                            for col in df_vars.columns:
                                df_vars[col] = df_vars[col].apply(_clean_str).astype(str)
                            st.dataframe(df_vars, width="stretch", hide_index=True)

                st.subheader("🔍 Detailed Inbound Run Evidence")
                unique_runs = {}
                for c in corr_results:
                    if c.matched_run and c.matched_run.run_id not in unique_runs:
                        unique_runs[c.matched_run.run_id] = (c.matched_run, c.matched_flow)

                if unique_runs:
                    sorted_run_items = sorted(
                        unique_runs.values(),
                        key=lambda x: x[0].started_on or datetime.min.replace(tzinfo=timezone.utc),
                        reverse=True,
                    )
                    for run, flow in sorted_run_items:
                        rid = run.run_id
                        start_label = run.started_on.strftime("%d/%m/%Y %H:%M:%S") if run.started_on else "—"
                        with st.expander(
                            f"⚡ {run.flow_name} — Run `{rid[:12]}…` ({run.status}) at {start_label}",
                            expanded=True,
                        ):
                            rc1, rc2 = st.columns(2)
                            with rc1:
                                st.markdown(f"**Flow Name:** `{run.flow_name}`")
                                st.markdown(f"**Status:** `{run.status}`")
                                st.markdown(f"**Execution Source:** `{run.source}`")
                                if run.duration_str:
                                    st.markdown(f"**Duration:** `{run.duration_str}`")
                            with rc2:
                                st.markdown(f"**Started At:** `{run.started_on.strftime('%Y-%m-%d %H:%M:%S UTC')}`")
                                if run.completed_on:
                                    st.markdown(f"**Completed At:** `{run.completed_on.strftime('%Y-%m-%d %H:%M:%S UTC')}`")
                                if run.maker_url:
                                    st.markdown(f"[🔗 Open Run in Power Automate ↗]({run.maker_url})")

                            if flow and flow.modifies_attributes:
                                st.info(
                                    f"📌 **Configured Action Attributes**: Modifies {len(flow.modifies_attributes)} field(s) on `{selected_logical_name}`: "
                                    f"`{', '.join(sorted(flow.modifies_attributes))}`"
                                )
                else:
                    st.info("No specific runtime execution sessions were captured within the time window tolerance.")

        with tab_outbound:
            if not outbound_results:
                st.info("ℹ️ No Outbound Trigger Impact results available. Select 'Both' or 'Outbound Only' and click '⚡ Correlate Flows & Processes'.")
            else:
                flow_summaries = group_outbound_by_flow(outbound_results)
                grouped_outbound = group_outbound_triggers(outbound_results)

                suppressed_turned_off = [
                    f for f in flow_summaries if not f.is_active or "Suppressed" in f.qualification_status
                ]
                if suppressed_turned_off:
                    st.error(
                        f"🚨 **Missed Automations Alert**: Found **{len(suppressed_turned_off)}** automation(s) that were **TURNED OFF / INACTIVE** (Draft)! "
                        "These flows were qualified to trigger on the modified fields or conditions, but were suppressed because the flow is turned off.",
                        icon="🚨",
                    )

                ob_total = len(outbound_results)
                ob_confirmed = sum(1 for f in flow_summaries if "Confirmed Triggered" in f.qualification_status)
                ob_satisfied = sum(1 for f in flow_summaries if "Condition Satisfied" in f.qualification_status)
                ob_conditional = sum(1 for f in flow_summaries if "Conditional" in f.qualification_status)
                ob_turned_off = len(suppressed_turned_off)

                om1, om2, om3, om4, om5 = st.columns(5)
                om1.metric("Unique Flows", len(flow_summaries), help="Distinct automations eligible across the audit window")
                om2.metric("🟢 Confirmed Runs", ob_confirmed, help="Active flows with verified downstream execution runs")
                om3.metric("🟢 Condition Met", ob_satisfied, help="Flows where all trigger condition rules were satisfied")
                om4.metric("🟡 Conditional", ob_conditional, help="Flows requiring existing unmutated record state")
                om5.metric("🔴 Turned Off", ob_turned_off, help="Turned off flows that were eligible to trigger")

                def _get_ob_sort_dt(item: GroupedOutboundExecution) -> datetime:
                    dt = item.timestamp
                    if dt is None:
                        return datetime.min.replace(tzinfo=timezone.utc)
                    if dt.tzinfo is None:
                        return dt.replace(tzinfo=timezone.utc)
                    return dt

                tab_ob_flows, tab_ob_runs, tab_ob_cards = st.tabs([
                    f"⚡ Deduplicated Automations ({len(flow_summaries)} Unique Flows)",
                    f"🕒 Mutation Timeline ({len(grouped_outbound)} Transactions)",
                    "🔎 Grouped Mutation Cards",
                ])

                with tab_ob_flows:
                    st.caption(
                        "Deduplicated executive view: Each automation appears exactly once, consolidating all mutation triggers, evaluated conditions, and suppressed alerts across the selected audit window."
                    )
                    fc1, fc2 = st.columns([1.5, 2])
                    with fc1:
                        f_view_mode = st.radio(
                            "Flow View Layout:",
                            ["📋 Deduplicated Table", "📊 Data Grid"],
                            horizontal=True,
                            key="ah_ob_flow_view_mode",
                        )
                    with fc2:
                        f_search = st.text_input("Search flows or conditions", placeholder="Type to search…", key="ah_ob_flow_search")

                    all_flow_statuses = sorted(list({f.qualification_status for f in flow_summaries}))
                    f_status_filter = st.multiselect("Filter by Flow Status:", options=all_flow_statuses, key="ah_ob_flow_status_filter")

                    filtered_flows = []
                    for f in flow_summaries:
                        if f_status_filter and f.qualification_status not in f_status_filter:
                            continue
                        if f_search:
                            haystack = f"{f.flow_name} {f.qualification_status} {f.status_reason} {f.filter_expression} {' '.join(f.trigger_conditions)} {' '.join(f.trigger_attributes)}".lower()
                            if f_search.lower() not in haystack:
                                continue
                        filtered_flows.append(f)

                    if not filtered_flows:
                        st.info("No automations match the current filter.")
                    elif f_view_mode == "📋 Deduplicated Table":
                        st.html(render_outbound_triggers_table_html(filtered_flows))
                    else:
                        grid_data = []
                        for f in filtered_flows:
                            grid_data.append({
                                "Flow Name": f.flow_name,
                                "Status": f.qualification_status,
                                "State": "🟢 Active" if f.is_active else "🔴 Turned Off",
                                "Category": f.category_label,
                                "Trigger Select Columns": ", ".join(f.trigger_attributes) if f.trigger_attributes else "(All columns / Unrestricted)",
                                "Trigger Filter Rows": f.filter_expression or "(None)",
                                "Trigger Conditions": "; ".join(f.trigger_conditions) if f.trigger_conditions else "(None)",
                                "Evaluation Reason": f.status_reason,
                                "Impacted Mutations": f"{f.mutation_count} event(s) ({f.first_seen} → {f.last_seen})" if f.mutation_count > 1 else f"1 event ({f.first_seen})",
                                "Open Flow": f.process_url or "",
                                "Flow History": f.flow_history_url or "",
                            })
                        df_flow_grid = pd.DataFrame(grid_data)
                        f_col_cfg: dict = {
                            "Flow Name": st.column_config.TextColumn("Flow / Workflow Name", width="medium"),
                            "Status": st.column_config.TextColumn("Status", width="small"),
                            "State": st.column_config.TextColumn("State", width="small"),
                            "Category": st.column_config.TextColumn("Category", width="small"),
                            "Trigger Select Columns": st.column_config.TextColumn("Select Columns", width="medium"),
                            "Trigger Filter Rows": st.column_config.TextColumn("Filter Rows", width="medium"),
                            "Trigger Conditions": st.column_config.TextColumn("Conditions", width="medium"),
                            "Evaluation Reason": st.column_config.TextColumn("Evaluation Reason", width="large"),
                            "Impacted Mutations": st.column_config.TextColumn("Impacted Mutations", width="medium"),
                            "Open Flow": st.column_config.LinkColumn("Open Flow", display_text="Open ↗"),
                            "Flow History": st.column_config.LinkColumn("Flow History", display_text="Runs ↗"),
                        }
                        st.dataframe(df_flow_grid, width="stretch", hide_index=True, column_config=f_col_cfg)

                with tab_ob_runs:
                    st.caption(
                        "Evaluates downstream Cloud Flows, Classic Workflows, and Logic Apps triggered by each audit mutation. "
                        "Tracks active flows with confirmed runs, qualified column triggers, and alerts on turned-off flows."
                    )

                    ob_ctrl1, ob_ctrl2 = st.columns([1.5, 2])
                    with ob_ctrl1:
                        ob_sort_order = st.radio(
                            "Chronological Sort:",
                            ["⬇️ Newest First (Descending)", "⬆️ Oldest First (Ascending)"],
                            horizontal=True,
                            key="ah_ob_sort_order",
                        )
                    with ob_ctrl2:
                        ob_view_mode = st.radio(
                            "Display Layout:",
                            ["📋 Multi-line Table (Each Variable on Next Line)", "📊 Data Grid"],
                            horizontal=True,
                            key="ah_ob_view_mode",
                        )

                    all_ob_statuses = sorted(list({trig.qualification_status for g in grouped_outbound for trig in g.triggers}))
                    all_ob_flows = sorted(list({trig.flow_name for g in grouped_outbound for trig in g.triggers if trig.flow_name}))

                    with st.expander("🎯 Filter Outbound Results", expanded=False):
                        ofc1, ofc2, ofc3 = st.columns(3)
                        with ofc1:
                            sel_ob_status = st.multiselect("Filter Status", options=all_ob_statuses, key="ah_ob_f_status")
                        with ofc2:
                            sel_ob_flows = st.multiselect("Filter Flow Name", options=all_ob_flows, key="ah_ob_f_flow")
                        with ofc3:
                            ob_search = st.text_input("Search outbound events", placeholder="Type to search…", key="ah_ob_f_search")

                    filtered_ob_groups: list[GroupedOutboundExecution] = []
                    for g in grouped_outbound:
                        kept_triggers = []
                        for tr in g.triggers:
                            if sel_ob_status and tr.qualification_status not in sel_ob_status:
                                continue
                            if sel_ob_flows and tr.flow_name not in sel_ob_flows:
                                continue
                            if ob_search:
                                s_haystack = f"{tr.flow_name} {tr.qualification_status} {tr.status_reason} {tr.run_status} {g.user_name} {g.changes_summary}".lower()
                                if ob_search.lower() not in s_haystack:
                                    continue
                            kept_triggers.append(tr)

                        if kept_triggers:
                            g_copy = GroupedOutboundExecution(
                                group_id=g.group_id,
                                timestamp=g.timestamp,
                                changed_date_str=g.changed_date_str,
                                user_name=g.user_name,
                                event=g.event,
                                transaction_id=g.transaction_id,
                                changed_fields_summary=g.changed_fields_summary,
                                changes_summary=g.changes_summary,
                                field_count=g.field_count,
                                fields_changed=g.fields_changed,
                                triggers=kept_triggers,
                                triggered_count=sum(1 for t in kept_triggers if "Triggered" in t.qualification_status),
                                suppressed_turned_off_count=sum(1 for t in kept_triggers if "Suppressed" in t.qualification_status),
                                qualified_count=sum(1 for t in kept_triggers if "Qualified" in t.qualification_status),
                                conditional_count=sum(1 for t in kept_triggers if "Conditional" in t.qualification_status),
                                has_turned_off_alert=any("Suppressed" in t.qualification_status for t in kept_triggers),
                            )
                            filtered_ob_groups.append(g_copy)

                    is_ob_desc = "Newest" in ob_sort_order
                    filtered_ob_groups.sort(key=_get_ob_sort_dt, reverse=is_ob_desc)

                    if len(filtered_ob_groups) != len(grouped_outbound):
                        st.info(f"Showing {len(filtered_ob_groups)} of {len(grouped_outbound)} mutation events.")

                    if ob_view_mode == "📋 Multi-line Table (Each Variable on Next Line)":
                        if not filtered_ob_groups:
                            st.info("No outbound trigger events match the current filter.")
                        else:
                            # Flatten items to check total rows
                            all_triggers_count = sum(len(g.triggers) for g in filtered_ob_groups)
                            page_size = 50
                            if all_triggers_count > page_size:
                                total_pages = (all_triggers_count + page_size - 1) // page_size
                                p_col1, p_col2 = st.columns([1, 3])
                                with p_col1:
                                    current_page = st.number_input(
                                        f"Page (1 to {total_pages})",
                                        min_value=1,
                                        max_value=total_pages,
                                        value=1,
                                        step=1,
                                        key="ah_ob_table_page",
                                    )
                                with p_col2:
                                    st.caption(f"Showing items {(current_page - 1) * page_size + 1} to {min(current_page * page_size, all_triggers_count)} of {all_triggers_count} total outbound trigger events.")
                                offset = (current_page - 1) * page_size
                                st.html(render_outbound_triggers_table_html(filtered_ob_groups, limit=page_size, offset=offset))
                            else:
                                st.html(render_outbound_triggers_table_html(filtered_ob_groups))
                    else:
                        ob_grid_rows = []
                        for g in filtered_ob_groups:
                            for tr in g.triggers:
                                ob_grid_rows.append(
                                    {
                                        "Mutation Time": tr.changed_date_str,
                                        "Changed By": tr.user_name,
                                        "Event": tr.event,
                                        "Flow Name": tr.flow_name,
                                        "State": "🟢 Active" if tr.is_active else "🔴 Turned Off",
                                        "Status": tr.qualification_status,
                                        "Status Reason": tr.status_reason,
                                        "Trigger Columns": ", ".join(tr.trigger_attributes) if tr.trigger_attributes else "(Any column)",
                                        "Matched Columns": ", ".join(tr.matched_columns),
                                        "Run ID": tr.run_id or "—",
                                        "Run Status": tr.run_status or "—",
                                        "Open Flow": tr.process_url or "",
                                        "Run History": tr.flow_history_url or "",
                                    }
                                )
                        df_ob = pd.DataFrame(ob_grid_rows)
                        if not df_ob.empty:
                            ob_col_cfg: dict = {
                                "Mutation Time": st.column_config.TextColumn("Mutation Time", width="medium"),
                                "Changed By": st.column_config.TextColumn("Changed By", width="small"),
                                "Event": st.column_config.TextColumn("Event", width="small"),
                                "Flow Name": st.column_config.TextColumn("Flow / Workflow Name", width="medium"),
                                "State": st.column_config.TextColumn("State", width="small"),
                                "Status": st.column_config.TextColumn("Status", width="medium"),
                                "Status Reason": st.column_config.TextColumn("Status Reason", width="large"),
                                "Trigger Columns": st.column_config.TextColumn("Trigger Columns", width="medium"),
                                "Matched Columns": st.column_config.TextColumn("Matched Columns", width="medium"),
                                "Run ID": st.column_config.TextColumn("Run ID", width="small"),
                                "Run Status": st.column_config.TextColumn("Run Status", width="small"),
                                "Open Flow": st.column_config.LinkColumn("Open Flow", display_text="Details"),
                                "Run History": st.column_config.LinkColumn("Run History", display_text="Runs"),
                            }
                            st.dataframe(df_ob, width="stretch", hide_index=True, column_config=ob_col_cfg)

                    ob_export_rows = []
                    for g in filtered_ob_groups:
                        for tr in g.triggers:
                            ob_export_rows.append(
                                {
                                    "Mutation Timestamp": tr.changed_date_str,
                                    "Changed By": tr.user_name,
                                    "Audit Event": tr.event,
                                    "Variables Changed": g.changes_summary,
                                    "Flow Name": tr.flow_name,
                                    "Category": tr.category_label,
                                    "Flow State": "Active" if tr.is_active else "Turned Off / Draft",
                                    "Qualification Status": tr.qualification_status,
                                    "Status Reason": tr.status_reason,
                                    "Trigger Columns Filter": ", ".join(tr.trigger_attributes) if tr.trigger_attributes else "(Any Column)",
                                    "Matched Changed Columns": ", ".join(tr.matched_columns),
                                    "Trigger Filter Expression": tr.filter_expression or "",
                                    "Trigger Conditions": "; ".join(tr.trigger_conditions) if tr.trigger_conditions else "",
                                    "Run ID": tr.run_id or "",
                                    "Run Status": tr.run_status or "",
                                    "Run Started On": tr.started_on.strftime("%d/%m/%Y %H:%M:%S") if tr.started_on else "",
                                    "Run Duration": tr.duration_str or "",
                                    "Flow URL": tr.process_url or "",
                                    "Run History URL": tr.flow_history_url or "",
                                }
                            )
                    if ob_export_rows:
                        df_ob_export = pd.DataFrame(ob_export_rows)
                        ob_csv_data = df_ob_export.to_csv(index=False).encode("utf-8")
                        c_ob_csv, c_ob_xlsx = st.columns([1, 1])
                        with c_ob_csv:
                            st.download_button(
                                "⬇️ Export Outbound Triggers (CSV)",
                                data=ob_csv_data,
                                file_name=f"step5_outbound_triggers_{selected_logical_name}_{str(selected_record_id)[:8]}.csv",
                                mime="text/csv",
                                key="ah_ob_export_csv",
                                width="stretch",
                            )
                        with c_ob_xlsx:
                            ob_excel_buf = io.BytesIO()
                            with pd.ExcelWriter(ob_excel_buf, engine="openpyxl") as writer:
                                df_ob_export.to_excel(writer, index=False, sheet_name="Outbound Triggers")
                            st.download_button(
                                "📊 Export Outbound Triggers (Excel .xlsx)",
                                data=ob_excel_buf.getvalue(),
                                file_name=f"step5_outbound_triggers_{selected_logical_name}_{str(selected_record_id)[:8]}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="ah_ob_export_excel",
                                width="stretch",
                            )

                with tab_ob_cards:
                    st.caption("Drill-down into each record mutation and review the qualification state of candidate automations.")
                    cards_ob_sort = st.radio(
                        "Order Cards by Mutation Timestamp:",
                        ["⬇️ Newest First (Descending)", "⬆️ Oldest First (Ascending)"],
                        horizontal=True,
                        key="ah_ob_cards_sort",
                    )
                    is_ob_cards_desc = "Newest" in cards_ob_sort
                    sorted_ob_cards = sorted(grouped_outbound, key=_get_ob_sort_dt, reverse=is_ob_cards_desc)

                    # Paginate cards if there are more than 15 mutation events
                    cards_per_page = 15
                    if len(sorted_ob_cards) > cards_per_page:
                        total_c_pages = (len(sorted_ob_cards) + cards_per_page - 1) // cards_per_page
                        cp_col1, cp_col2 = st.columns([1, 3])
                        with cp_col1:
                            cur_c_page = st.number_input(
                                f"Mutation Group Page (1 to {total_c_pages})",
                                min_value=1,
                                max_value=total_c_pages,
                                value=1,
                                step=1,
                                key="ah_ob_cards_page",
                            )
                        with cp_col2:
                            st.caption(f"Showing mutations {(cur_c_page - 1) * cards_per_page + 1} to {min(cur_c_page * cards_per_page, len(sorted_ob_cards))} of {len(sorted_ob_cards)}.")
                        c_offset = (cur_c_page - 1) * cards_per_page
                        sorted_ob_cards = sorted_ob_cards[c_offset : c_offset + cards_per_page]

                    for g in sorted_ob_cards:
                        header = f"Mutation at {g.changed_date_str} by {g.user_name} ({g.field_count} variable(s) changed) — {len(g.triggers)} Flow(s) Evaluated"
                        with st.container(border=True):
                            st.markdown(f"##### {header}")
                            sc1, sc2, sc3 = st.columns(3)
                            sc1.metric("🟢 Confirmed Triggered", g.triggered_count)
                            sc2.metric("🔵 Qualified to Trigger", g.qualified_count)
                            sc3.metric("🔴 Suppressed / Inactive", g.suppressed_turned_off_count)

                            st.markdown("**Variables Changed in this Mutation:**")
                            df_mut_vars = pd.DataFrame(g.fields_changed)
                            for col in df_mut_vars.columns:
                                df_mut_vars[col] = df_mut_vars[col].apply(_clean_str).astype(str)
                            st.dataframe(df_mut_vars, width="stretch", hide_index=True)

                            st.markdown("**Evaluated Automations:**")
                            for tr in g.triggers:
                                with st.expander(f"{tr.status_badge} **{tr.flow_name}** — `{tr.qualification_status}`", expanded=("Suppressed" in tr.qualification_status or "Triggered" in tr.qualification_status)):
                                    tc1, tc2 = st.columns([2, 1])
                                    with tc1:
                                        st.markdown(f"**Status Reason:** {tr.status_reason}")
                                        st.markdown(f"**Flow State:** `{tr.state_label}` | **Category:** `{tr.category_label}`")
                                        if tr.matched_columns:
                                            st.markdown(f"**Matched Columns:** `{', '.join(tr.matched_columns)}`")
                                        if tr.filter_expression:
                                            st.markdown(f"**Filter Expression:** `{tr.filter_expression}`")
                                        if tr.trigger_conditions:
                                            st.markdown(f"**Trigger Conditions:** `{'; '.join(tr.trigger_conditions)}`")
                                        if tr.run_id:
                                            st.markdown(f"**Run ID:** `{tr.run_id}` | **Run Status:** `{tr.run_status}`" + (f" | **Duration:** `{tr.duration_str}`" if tr.duration_str else ""))
                                    with tc2:
                                        if tr.process_url:
                                            st.markdown(f"[🔗 Open Flow Details ↗]({tr.process_url})")
                                        if tr.flow_history_url:
                                            st.markdown(f"[📋 Open Run History ↗]({tr.flow_history_url})")

        # Discovered Flows catalog for this entity
        raw_wfs = st.session_state.get("ah_raw_workflows", [])
        if raw_wfs:
            with st.expander(f"📂 All Discovered Workflows for Table '{selected_logical_name}' ({len(raw_wfs)})"):
                wf_catalog = []
                for w in raw_wfs:
                    wf_catalog.append(
                        {
                            "Name": w.get("name"),
                            "Category": "Modern Cloud Flow" if w.get("category") == 5 else "Classic Workflow",
                            "State": "🟢 Active" if w.get("statecode") == 1 else "🔴 Draft",
                            "Primary Entity": w.get("primaryentity") or "-",
                            "Modified On": w.get("modifiedon"),
                        }
                    )
                st.dataframe(pd.DataFrame(wf_catalog), width="stretch", hide_index=True)
    elif persistent_la_diags:
        st.info("ℹ️ Automation correlation completed, but no matching runs were attributed to audit records.")
        _render_la_diagnostics(persistent_la_diags, default_expanded=True)

    if st.button("🗑️ Clear Results"):
        persistence.clear_results()
        for k in (
            "ah_audit_data",
            "ah_raw_audit_details",
            "ah_correlation_results",
            "ah_outbound_results",
            "ah_logic_app_diagnostics",
            "ah_verified_runs",
            "ah_verified_runs_cache",
            "ah_verified_actions_cache",
            "ah_selected_record_id",
            "ah_selected_record_label",
            "ah_step5_duration",
        ):
            st.session_state.pop(k, None)
        st.rerun()

