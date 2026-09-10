"""Streamlit UI for the Cloud Flow plugin.

All session-state keys are prefixed with ``cf_`` to avoid collisions.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json

import numpy as np
import pandas as pd
import streamlit as st

from core.context import AppContext
from plugins.cloud_flow import persistence
from plugins.cloud_flow.client import (
    DataverseClient,
    PowerAutomateClient,
    _PA_SCOPE,
    _STATUS_ICON,
)

_DEFAULT_DAYS_BACK = 1

# Columns always shown in the results table
_DEFAULT_COLS = ["\U0001f4cb Flow Name", "status", "starttime", "endtime", "duration", "\U0001f517 Run"]
# Friendly "Flow Name" key (no emoji prefix, matches data dict)
_FLOW_NAME_COL = "Flow Name"

# Default fields that "Fetch Details" may populate
_DEFAULT_DETAIL_FIELDS = [
    "Operation",
    "Fetch XML",
    "Update Action",
    "Update Payload",
]


def _get_configured_detail_fields() -> list[str]:
    """Retrieve configured detail fields from the plugin config or fallback to defaults."""
    plugin = st.session_state.get("cf_plugin_instance")
    if plugin is not None and hasattr(plugin, "get_config"):
        try:
            cfg = plugin.get_config()
            if isinstance(cfg, dict) and "detail_fields" in cfg:
                fields = cfg["detail_fields"]
                if isinstance(fields, list):
                    return [str(f).strip() for f in fields if str(f).strip()]
        except Exception:
            pass
    return list(_DEFAULT_DETAIL_FIELDS)

_NOISE_KEYS = frozenset({
    "@odata.context",
    "@odata.type",
    "@odata.id",
    "@odata.etag",
    "ItemInternalId",
})

_RUN_LINK_COL = "\U0001f517 Run"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_icon(status: str | None) -> str:
    if not status:
        return ""
    return _STATUS_ICON.get(status, "")


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return ""
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    rem = secs % 60
    return f"{mins}m {rem:.0f}s"


def _make_pa_run_url(env_id: str, flow_id: str, run_id: str) -> str:
    return (
        f"https://make.powerautomate.com/environments/{env_id}"
        f"/flows/{flow_id}/runs/{run_id}"
    )


def _flow_color(flow_name: str | None) -> str:
    if not flow_name:
        return ""
    hue = int(hashlib.md5(str(flow_name).encode()).hexdigest(), 16) % 360
    return f"background-color: hsla({hue}, 70%, 90%, 0.5)"


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------


def render(ctx: AppContext, plugin: "PluginBase" | None = None) -> None:
    if plugin is not None:
        st.session_state["cf_plugin_instance"] = plugin
    st.title("\u26a1 Cloud Flow History")
    st.markdown(
        "Browse execution history for solution-aware Cloud Flows in your Dataverse environment."
    )

    try:
        dv_token = ctx.auth.get_token(ctx.org_url)
    except Exception as exc:
        st.error(f"Failed to acquire Dataverse token: {exc}")
        return

    client = DataverseClient(ctx.org_url, dv_token)

    _maybe_show_restore_banner()

    pa_connected = bool(st.session_state.get("cf_pa_token"))
    with st.expander(
        "\u26a1 Power Automate API (optional \u2014 more detailed runs)",
        expanded=pa_connected,
    ):
        _render_pa_section(ctx, client)

    st.divider()

    tab_flow, tab_date = st.tabs(["\U0001f4cb Per-Flow History", "\U0001f4c5 Date-Range Search"])

    with tab_flow:
        _render_per_flow_tab(ctx, client)

    with tab_date:
        _render_date_range_tab(ctx, client)


# ---------------------------------------------------------------------------
# Restore banner
# ---------------------------------------------------------------------------


def _maybe_show_restore_banner() -> None:
    if st.session_state.get("cf_restore_checked"):
        return
    st.session_state["cf_restore_checked"] = True
    cached = persistence.load_results()
    if cached:
        col_msg, col_btn, col_clr = st.columns([4, 1, 1])
        col_msg.info(
            f"\U0001f4be **{len(cached)} runs** from your last session are available. "
            "Restore them without searching again?"
        )
        if col_btn.button("\u21a9\ufe0f Restore", key="cf_restore_cache"):
            st.session_state["cf_date_runs"] = cached
            st.rerun()
        if col_clr.button("\u2716 Dismiss", key="cf_dismiss_cache"):
            persistence.clear_results()
            st.rerun()


# ---------------------------------------------------------------------------
# Power Automate section
# ---------------------------------------------------------------------------


def _render_pa_section(ctx: AppContext, client: DataverseClient) -> None:
    pa_token = st.session_state.get("cf_pa_token")

    if pa_token:
        st.success("\u2705 Connected to Power Automate API.")
        if st.button("Disconnect", key="cf_pa_disconnect"):
            for k in ("cf_pa_token", "cf_pa_environments", "cf_pa_env_id", "cf_pa_env_select"):
                st.session_state.pop(k, None)
            st.rerun()
        _render_pa_env_selector(ctx, client)
    else:
        st.info(
            "Connect to the Power Automate API to fetch runs directly from "
            "`api.flow.microsoft.com`. This provides richer run details and "
            "works even when the Dataverse `flowsessions` table is sparse."
        )
        if st.button("\U0001f517 Authorize Power Automate", key="cf_pa_auth"):
            with st.spinner("Requesting token\u2026"):
                try:
                    result = ctx.auth.acquire_token_silent([_PA_SCOPE])
                    if not result:
                        result = ctx.auth.login(scopes=[_PA_SCOPE])
                    if result:
                        st.session_state["cf_pa_token"] = result["access_token"]
                        st.rerun()
                    else:
                        st.error("Authorization failed \u2014 no token returned.")
                except Exception as exc:
                    st.error(f"Authorization error: {exc}")


def _get_pa_env_id() -> str | None:
    return st.session_state.get("cf_pa_env_select") or st.session_state.get("cf_pa_env_id")


def _render_pa_env_selector(ctx: AppContext, client: DataverseClient) -> None:
    pa_token = st.session_state.get("cf_pa_token")
    if not pa_token:
        return

    if "cf_pa_environments" not in st.session_state:
        with st.spinner("Fetching PA environments\u2026"):
            pa_client = PowerAutomateClient(pa_token)
            st.session_state["cf_pa_environments"] = pa_client.get_environments()

    envs: list[dict] = st.session_state["cf_pa_environments"]
    if not envs:
        st.warning("No Power Automate environments found.")
        return

    env_options = {e["name"]: e["properties"]["displayName"] for e in envs}

    if "cf_pa_env_id" not in st.session_state:
        try:
            dv_org_id = client.get_instance_id()
            if dv_org_id:
                for env_name in env_options:
                    if env_name.lower() == dv_org_id.lower():
                        st.session_state["cf_pa_env_id"] = env_name
                        st.success(f"\u2705 Auto-linked to: {env_options[env_name]}")
                        break
        except Exception:
            pass

    default_idx = 0
    saved = st.session_state.get("cf_pa_env_id")
    keys = list(env_options.keys())
    if saved and saved in keys:
        default_idx = keys.index(saved)

    selected = st.selectbox(
        "Power Automate Environment",
        options=keys,
        index=default_idx,
        format_func=lambda x: env_options[x],
        key="cf_pa_env_select",
    )
    st.session_state["cf_pa_env_id"] = selected


# ---------------------------------------------------------------------------
# Per-Flow tab
# ---------------------------------------------------------------------------


def _render_per_flow_tab(ctx: AppContext, client: DataverseClient) -> None:
    if st.button("\U0001f504 Load / Refresh Flows", key="cf_load_flows"):
        st.session_state.pop("cf_flows", None)

    if "cf_flows" not in st.session_state:
        with st.spinner("Fetching Cloud Flows\u2026"):
            try:
                st.session_state["cf_flows"] = client.get_modern_flows()
            except Exception as exc:
                st.error(f"Failed to fetch flows: {exc}")
                return

    flows: list[dict] = st.session_state["cf_flows"]
    if not flows:
        st.info("No solution-aware Cloud Flows found in this environment.")
        return

    st.caption(f"{len(flows)} flows found.")

    name_filter = st.text_input(
        "Filter flows by name", placeholder="Type to filter\u2026", key="cf_flow_filter"
    )
    filtered = [f for f in flows if name_filter.lower() in f["name"].lower()]

    flow_options = {f["workflowid"]: f["name"] for f in filtered}
    if not flow_options:
        st.warning("No flows match the filter.")
        return

    selected_id: str = st.selectbox(
        f"Select Flow ({len(filtered)} shown)",
        options=list(flow_options.keys()),
        format_func=lambda x: flow_options[x],
        key="cf_flow_select",
    )

    top_n = st.number_input(
        "Max runs to fetch", min_value=10, max_value=500, value=50, step=10, key="cf_flow_top"
    )

    if st.button("\U0001f4ca Fetch Run History", key="cf_flow_fetch", type="primary"):
        with st.spinner("Fetching run history\u2026"):
            try:
                pa_token = st.session_state.get("cf_pa_token")
                env_id = _get_pa_env_id()

                if pa_token and env_id:
                    pa_client = PowerAutomateClient(pa_token)
                    raw = pa_client.get_flow_runs(env_id, selected_id, top=int(top_n))
                    runs = [
                        {
                            "flowrunid": r.get("name"),
                            "starttime": r.get("properties", {}).get("startTime"),
                            "endtime": r.get("properties", {}).get("endTime"),
                            "status": r.get("properties", {}).get("status"),
                            "durationinmilliseconds": None,
                            "triggername": r.get("properties", {}).get("trigger", {}).get("name"),
                            "Flow Name": flow_options[selected_id],
                            "_pa_run": True,
                            "_env_id": env_id,
                            "_flow_id": selected_id,
                        }
                        for r in raw
                    ]
                else:
                    runs = client.get_flow_runs(selected_id, top=int(top_n))
                    for r in runs:
                        r["_flow_id"] = selected_id
                        r["Flow Name"] = flow_options[selected_id]

                st.session_state["cf_per_flow_runs"] = (selected_id, flow_options[selected_id], runs)
                persistence.save_results(runs)
            except Exception as exc:
                st.error(f"Failed to fetch run history: {exc}")
                return

    if "cf_per_flow_runs" not in st.session_state:
        return

    sel_id, flow_name, run_data = st.session_state["cf_per_flow_runs"]
    if not run_data:
        st.info("No run history found for this flow.")
        return

    st.success(f"**{flow_name}** \u2014 {len(run_data)} runs fetched.")
    _render_results_section(run_data, state_key="pf")


# ---------------------------------------------------------------------------
# Date-Range tab
# ---------------------------------------------------------------------------


def _render_date_range_tab(ctx: AppContext, client: DataverseClient) -> None:
    # Save & Load cases are surfaced before any search so users can restore
    # previously saved history records without re-querying Dataverse/PA.
    st.subheader("\U0001f4be Save & Load Cases")
    with st.expander("\U0001f4c1 Saved History", expanded=False):
        _render_history_manager(key_prefix="dr_top")
    st.divider()

    today = pd.Timestamp.utcnow().date()
    yesterday = today - pd.Timedelta(days=_DEFAULT_DAYS_BACK)

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start Date", value=yesterday, key="cf_start_date")
        start_time = st.text_input("Start Time (HH:MM)", value="00:00", key="cf_start_time")
    with col2:
        end_date = st.date_input("End Date", value=today, key="cf_end_date")
        end_time = st.text_input("End Time (HH:MM)", value="23:59", key="cf_end_time")

    if st.button("\U0001f4c5 Last 7 Days", key="cf_preset_7d"):
        st.session_state["cf_start_date"] = today - pd.Timedelta(days=7)
        st.session_state["cf_end_date"] = today
        st.session_state["cf_start_time"] = "00:00"
        st.session_state["cf_end_time"] = "23:59"
        st.rerun()

    try:
        start_dt = pd.Timestamp(f"{start_date} {start_time}").isoformat() + "Z"
        end_dt = pd.Timestamp(f"{end_date} {end_time}").isoformat() + "Z"
    except Exception:
        st.error("Invalid time format \u2014 use HH:MM (24-hour).")
        return

    failed_only = st.toggle("Show failed/cancelled only", value=False, key="cf_failed_only")
    top_n = st.number_input(
        "Max runs per flow (PA) / sessions (Dataverse)",
        min_value=50, max_value=2000, value=500, step=50, key="cf_date_top",
    )

    pa_token = st.session_state.get("cf_pa_token")
    env_id = _get_pa_env_id()
    use_pa = bool(pa_token and env_id)

    if use_pa:
        st.caption(f"Using **Power Automate API** \u2014 parallel scan across all flows in env `{env_id}`")
    else:
        st.caption(
            "Using **Dataverse API** (`flowsessions`). "
            "Connect Power Automate API above for better coverage."
        )

    col_btn_search, col_btn_stop = st.columns([1, 1])
    with col_btn_search:
        search_clicked = st.button("\U0001f50d Search", key="cf_date_search", type="primary")
    with col_btn_stop:
        stop_clicked = st.button("\u23f9\ufe0f Stop Search", key="cf_date_stop")

    if stop_clicked:
        st.session_state["cf_stop_search_req"] = True

    if search_clicked:
        st.session_state.pop("cf_stop_search_req", None)
        all_runs: list[dict] = []
        flows: list[dict] = st.session_state.get("cf_flows", [])

        if not flows:
            with st.spinner("Loading flows list\u2026"):
                try:
                    flows = client.get_modern_flows()
                    st.session_state["cf_flows"] = flows
                except Exception as exc:
                    st.warning(f"Could not load flows list: {exc}")

        flow_dict = {f["workflowid"]: f["name"] for f in flows}

        if failed_only:
            with st.spinner("Checking high-speed `flowruns` table\u2026"):
                all_runs = client.get_failed_flow_runs(start_dt, end_dt)
            if all_runs:
                st.success(f"High-speed search found {len(all_runs)} non-succeeded runs.")
            else:
                st.info("`flowruns` table unavailable \u2014 falling back to full scan.")

        if not all_runs and use_pa:
            pa_client = PowerAutomateClient(pa_token)
            target_flows = flows
            total_flows = len(target_flows)

            if total_flows == 0:
                st.warning("No flows found to scan.")
            else:
                import threading
                stop_event = threading.Event()
                status_box = st.empty()
                prog_bar = st.progress(0)
                status_box.text(f"Scanning {total_flows} flows via Power Automate API\u2026")

                def _fetch_flow_runs(flow_item: dict) -> tuple:
                    if stop_event.is_set():
                        return flow_item["workflowid"], flow_item["name"], [], None
                    f_id = flow_item["workflowid"]
                    f_name = flow_item["name"]
                    try:
                        if failed_only:
                            r_f = pa_client.get_flow_runs(env_id, f_id, start_date=start_dt, end_date=end_dt, top=int(top_n), status="Failed")
                            r_c = pa_client.get_flow_runs(env_id, f_id, start_date=start_dt, end_date=end_dt, top=int(top_n), status="Cancelled")
                            r_t = pa_client.get_flow_runs(env_id, f_id, start_date=start_dt, end_date=end_dt, top=int(top_n), status="TimedOut")
                            runs = r_f + r_c + r_t
                        else:
                            runs = pa_client.get_flow_runs(env_id, f_id, start_date=start_dt, end_date=end_dt, top=int(top_n))
                        return f_id, f_name, runs, None
                    except Exception as exc:
                        return f_id, f_name, [], str(exc)

                max_threads = min(20, total_flows)
                completed = 0
                stopped_early = False

                with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
                    futures = {executor.submit(_fetch_flow_runs, f): f for f in target_flows}
                    for future in concurrent.futures.as_completed(futures):
                        if st.session_state.get("cf_stop_search_req"):
                            stop_event.set()
                            stopped_early = True
                            for f in futures:
                                f.cancel()
                            break
                        completed += 1
                        try:
                            f_id, f_name, found_runs, _ = future.result()
                            for run in found_runs:
                                run_id = run.get("name") or run.get("flowrunid", "")
                                all_runs.append({
                                    "flowrunid": run_id,
                                    "starttime": run.get("properties", {}).get("startTime"),
                                    "endtime": run.get("properties", {}).get("endTime"),
                                    "status": run.get("properties", {}).get("status"),
                                    "workflowid": f_id,
                                    "Flow Name": f_name,
                                    "durationinmilliseconds": None,
                                    "_pa_run": True,
                                    "_env_id": env_id,
                                    "_flow_id": f_id,
                                })
                        except Exception:
                            pass
                        prog_bar.progress(min(1.0, completed / total_flows))
                        status_box.text(f"Scanned {completed}/{total_flows} \u2014 {len(all_runs)} runs found.")

                prog_bar.empty()
                if stopped_early or st.session_state.get("cf_stop_search_req"):
                    status_box.warning(f"\u23f9\ufe0f Search stopped by user. Scanned {completed}/{total_flows} flows \u2014 {len(all_runs)} runs found.")
                else:
                    status_box.empty()

        if not all_runs and not use_pa:
            with st.spinner("Querying Dataverse `flowsessions` table\u2026"):
                try:
                    all_runs = client.get_sessions_by_date(start_dt, end_dt, top=int(top_n))
                    for r in all_runs:
                        r["Flow Name"] = flow_dict.get(r.get("workflowid"), "Unknown")
                except Exception as exc:
                    st.error(f"Dataverse query failed: {exc}")

        for r in all_runs:
            if "Flow Name" not in r:
                r["Flow Name"] = flow_dict.get(r.get("workflowid"), "Unknown")

        if all_runs:
            df_check = pd.DataFrame(all_runs)
            if "starttime" in df_check.columns:
                df_check["starttime"] = pd.to_datetime(df_check["starttime"], errors="coerce", utc=True)
                s = pd.to_datetime(start_dt, utc=True)
                e = pd.to_datetime(end_dt, utc=True)
                df_check = df_check[(df_check["starttime"] >= s) & (df_check["starttime"] <= e)]
                all_runs = df_check.to_dict("records")

        if failed_only:
            all_runs = [r for r in all_runs if r.get("status") in ("Failed", "Cancelled", "TimedOut")]

        st.session_state["cf_date_runs"] = all_runs
        persistence.save_results(all_runs)

    if "cf_date_runs" not in st.session_state:
        return

    run_data: list[dict] = st.session_state["cf_date_runs"]
    if not run_data:
        st.info("No runs found in the selected date range.")
        return

    total = len(run_data)
    succeeded = sum(1 for r in run_data if r.get("status") == "Succeeded")
    failed = sum(1 for r in run_data if r.get("status") in ("Failed", "failed"))
    cancelled = sum(1 for r in run_data if r.get("status") in ("Cancelled", "cancelled", "TimedOut"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", total)
    m2.metric("\u2705 Succeeded", succeeded)
    m3.metric("\u274c Failed", failed)
    m4.metric("\u26a0\ufe0f Cancelled / Timed Out", cancelled)

    st.divider()
    _render_results_section(run_data, state_key="dr")


# ---------------------------------------------------------------------------
# Full results section
# ---------------------------------------------------------------------------


def _render_results_section(run_data: list[dict], state_key: str) -> None:
    pa_token = st.session_state.get("cf_pa_token")
    env_id = _get_pa_env_id()
    has_pa = bool(pa_token and env_id)
    has_pa_runs = any(r.get("_pa_run") for r in run_data)

    # ── 1. Fetch Details ──────────────────────────────────────────────────
    st.subheader("\U0001f50d Fetch Details")
    if has_pa and has_pa_runs:
        already_fetched = any(r.get("has_details") for r in run_data)
        if already_fetched:
            st.success("\u2705 Details already fetched for this result set.")

        configured_fields = _get_configured_detail_fields()
        fields_input_key = f"cf_{state_key}_target_fields"
        if fields_input_key not in st.session_state:
            st.session_state[fields_input_key] = ", ".join(configured_fields)

        col_inp, col_reset = st.columns([8, 2])
        with col_inp:
            fields_raw = st.text_input(
                "📋 Fields to extract from Trigger Outputs & Actions (comma-separated):",
                key=fields_input_key,
                help="Specify field names from flow trigger outputs or action payloads to extract as columns.",
            )
        with col_reset:
            st.write("")
            st.write("")
            if st.button("↺ Reset Fields", key=f"cf_{state_key}_reset_fields", help="Reset to configured plugin defaults"):
                st.session_state[fields_input_key] = ", ".join(configured_fields)
                st.rerun()

        target_fields = [f.strip() for f in fields_raw.split(",") if f.strip()]
        if not target_fields:
            target_fields = list(configured_fields)

        col_f1, col_f2 = st.columns([2, 1])
        with col_f1:
            fetch_clicked = st.button("📥 Fetch Details (Trigger Outputs)", key=f"cf_{state_key}_fetch")
        with col_f2:
            stop_fetch_clicked = st.button("⏹️ Stop Fetching", key=f"cf_{state_key}_stop_fetch")

        if stop_fetch_clicked:
            st.session_state[f"cf_{state_key}_stop_fetch_req"] = True

        if fetch_clicked:
            st.session_state.pop(f"cf_{state_key}_stop_fetch_req", None)
            pa_client = PowerAutomateClient(pa_token)
            total_items = len(run_data)
            done_count = 0
            success_count = 0
            prog_bar = st.progress(0)
            status_box = st.empty()

            def _fetch_one(run_item: dict) -> tuple:
                r_id = run_item.get("flowrunid", "")
                r_env = run_item.get("_env_id", env_id)
                r_flow = run_item.get("_flow_id", run_item.get("workflowid", ""))
                try:
                    details = pa_client.get_flow_run_details(r_env, r_flow, r_id)
                    actions = pa_client.get_flow_run_actions(r_env, r_flow, r_id)
                    return r_id, details, actions
                except Exception:
                    return r_id, {}, []

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(_fetch_one, r): r for r in run_data}
                for future in concurrent.futures.as_completed(futures):
                    if st.session_state.get(f"cf_{state_key}_stop_fetch_req"):
                        for f in futures:
                            f.cancel()
                        break
                    done_count += 1
                    r_id, details, actions = future.result()
                    if details:
                        success_count += 1
                        _apply_details(run_data, r_id, details, actions, pa_client, detail_fields=target_fields)
                    prog_bar.progress(done_count / total_items)
                    status_box.text(
                        f"Fetching {done_count}/{total_items} \u2014 {success_count} enriched."
                    )

            prog_bar.empty()
            status_box.empty()
            if success_count:
                st.success(f"Fetched details for {success_count} runs.")
                persistence.save_results(run_data)
                st.rerun()
            else:
                st.warning("Could not fetch details for any run.")
    else:
        if not has_pa:
            st.caption("Connect to the Power Automate API above to enable detail fetching.")
        else:
            st.caption("Detail fetching is available for runs fetched via the PA API.")

    st.divider()

    # ── 2. Save to History & History manager ──────────────────────────────
    st.subheader("\U0001f4be Save & Restore")

    with st.expander("\U0001f4c1 Saved History", expanded=False):
        _render_history_manager(key_prefix=state_key)

    col_s1, col_s2, col_s3 = st.columns([3, 3, 1])
    with col_s1:
        save_name = st.text_input(
            "History Record Name", placeholder="e.g. Morning run audit",
            key=f"cf_{state_key}_save_name",
        )
    with col_s2:
        save_remark = st.text_input(
            "Remark (optional)", placeholder="e.g. Investigating issue #42",
            key=f"cf_{state_key}_save_remark",
        )
    with col_s3:
        st.write("")
        st.write("")
        if st.button("\U0001f4be Save", key=f"cf_{state_key}_save_btn", use_container_width=True):
            fname = persistence.save_history(run_data, name=save_name, remark=save_remark)
            if fname:
                st.success(f"Saved: `{fname}`")
            else:
                st.error("Save failed.")

    st.divider()

    # ── 3. Build DataFrame ────────────────────────────────────────────────
    df = _build_dataframe(run_data)

    # ── 4. Column selector for extracted detail fields ────────────────────
    configured_fields = _get_configured_detail_fields()
    raw_user_fields = [f.strip() for f in st.session_state.get(f"cf_{state_key}_target_fields", "").split(",") if f.strip()]
    known_detail_fields = set(raw_user_fields) | set(configured_fields) | set(_DEFAULT_DETAIL_FIELDS)

    base_display = ["Flow Name", "status", "starttime", "endtime", "duration", _RUN_LINK_COL]
    extra_options = [
        c for c in df.columns
        if (c in known_detail_fields or any(r.get(c) is not None for r in run_data if r.get("has_details")))
        and c not in base_display
        and not c.startswith("_")
    ]
    if extra_options:
        selected_extras = st.multiselect(
            "📊 Show extracted detail columns:",
            options=sorted(extra_options),
            default=sorted(extra_options),
            key=f"cf_{state_key}_extras",
        )
    else:
        selected_extras = []

    base_display = ["Flow Name", "status", "starttime", "endtime", "duration", _RUN_LINK_COL]
    base_cols = [c for c in base_display if c in df.columns]
    final_cols = base_cols + [c for c in selected_extras if c in df.columns]

    # ── 5. Filter panel ───────────────────────────────────────────────────
    with st.expander("\U0001f3af Filter Results", expanded=False):
        f_df = df[final_cols].copy()

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            if "status" in f_df.columns:
                sel_status = st.multiselect(
                    "Status",
                    options=sorted(f_df["status"].dropna().unique()),
                    key=f"cf_{state_key}_f_status",
                )
                if sel_status:
                    f_df = f_df[f_df["status"].isin(sel_status)]
        with fc2:
            if "Flow Name" in f_df.columns:
                sel_flows = st.multiselect(
                    "Flow Name",
                    options=sorted(f_df["Flow Name"].dropna().unique()),
                    key=f"cf_{state_key}_f_flow",
                )
                if sel_flows:
                    f_df = f_df[f_df["Flow Name"].isin(sel_flows)]
        with fc3:
            g_search = st.text_input(
                "Search all columns", placeholder="Type to search\u2026",
                key=f"cf_{state_key}_f_global",
            )
            if g_search:
                f_df = f_df[
                    f_df.astype(str).apply(lambda x: x.str.contains(g_search, case=False)).any(axis=1)
                ]

        skip_dyn = {"status", "Flow Name", "starttime", "endtime", "duration", _RUN_LINK_COL}
        dynamic_cols = [c for c in final_cols if c not in skip_dyn]
        if dynamic_cols:
            st.markdown("---")
            st.caption("Extracted field filters:")
            filter_logic = st.radio(
                "Combine filters with:", ["AND", "OR"],
                horizontal=True, key=f"cf_{state_key}_filter_logic",
            )
            adv_cols_ui = st.columns(3)
            filter_conditions: list = []

            for idx, col in enumerate(dynamic_cols):
                with adv_cols_ui[idx % 3]:
                    raw_uv = f_df[col].dropna().unique()
                    uv = sorted({
                        str(x) for x in raw_uv
                        if str(x).strip() not in ("", "nan", "none", "<na>")
                    })
                    if not uv:
                        continue
                    if len(uv) < 20:
                        sel_v = st.multiselect(col, options=uv, key=f"cf_{state_key}_fd_{col}")
                        if sel_v:
                            filter_conditions.append(f_df[col].astype(str).isin(sel_v))
                    else:
                        txt_v = st.text_input(
                            f"Search {col}", placeholder="Comma-separated\u2026",
                            key=f"cf_{state_key}_fd_{col}",
                        )
                        if txt_v:
                            terms = [t.strip() for t in txt_v.split(",") if t.strip()]
                            if terms:
                                col_conds = [
                                    f_df[col].astype(str).str.contains(t, case=False, na=False)
                                    for t in terms
                                ]
                                filter_conditions.append(np.logical_or.reduce(col_conds))

            if filter_conditions:
                combined = (
                    np.logical_and.reduce(filter_conditions)
                    if filter_logic == "AND"
                    else np.logical_or.reduce(filter_conditions)
                )
                f_df = f_df[combined]

    if len(f_df) != len(df):
        st.info(f"Showing {len(f_df)} of {len(df)} runs after filtering.")

    # ── 6. Color-coded styled dataframe ───────────────────────────────────
    col_pos = {col: i for i, col in enumerate(f_df.columns)}

    def _style_row(row: pd.Series) -> list[str]:
        styles = [""] * len(row)
        if "Flow Name" in col_pos:
            styles[col_pos["Flow Name"]] = _flow_color(row.get("Flow Name"))
        if "status" in col_pos:
            s = str(row.get("status", "")).lower()
            i = col_pos["status"]
            if s == "succeeded":
                styles[i] = "background-color: rgba(40,167,69,0.4); color: white; font-weight: bold"
            elif s == "failed":
                styles[i] = "background-color: rgba(220,53,69,0.4); color: white; font-weight: bold"
            elif s in ("running", "cancelled", "timedout"):
                styles[i] = "background-color: rgba(255,193,7,0.4)"
        return styles

    def _dur_fmt(x):
        if pd.isna(x) or x is None:
            return ""
        return _fmt_duration(int(x))

    styled = f_df.style.apply(_style_row, axis=1).format({"duration": _dur_fmt}, na_rep="")
    col_cfg: dict = {
        "starttime": st.column_config.DatetimeColumn("Started", format="D MMM YYYY, h:mm a"),
        "endtime": st.column_config.DatetimeColumn("Completed", format="D MMM YYYY, h:mm a"),
        "duration": st.column_config.Column("Duration"),
        "status": st.column_config.TextColumn("Status"),
        _RUN_LINK_COL: st.column_config.LinkColumn("Open Run", display_text="View"),
    }
    st.dataframe(styled, use_container_width=True, hide_index=True, column_config=col_cfg)

    if "full_fetch_xml" in df.columns:
        with st.expander("\U0001f4c4 View Full Fetch XML"):
            for xml in df["full_fetch_xml"].dropna().unique():
                st.code(xml, language="xml")

    csv_cols = [c for c in f_df.columns if c != _RUN_LINK_COL]
    csv_data = f_df[csv_cols].to_csv(index=False).encode("utf-8")
    st.download_button(
        "\u2b07\ufe0f Export CSV", data=csv_data,
        file_name="cloud_flow_results.csv", mime="text/csv",
        key=f"cf_{state_key}_csv",
    )

    # ── 7. Deep Search Panel ─────────────────────────────────────────────
    _render_deep_search_and_dump_panel(run_data, f_df, df, state_key=state_key)


# ---------------------------------------------------------------------------
# Deep Search Panel
# ---------------------------------------------------------------------------


def _deep_search_payload(
    obj: any,
    query: str,
    current_path: str = "",
    max_matches: int = 50,
) -> list[dict]:
    """Recursively search for query string within nested dicts, lists, and strings."""
    matches: list[dict] = []
    q_lower = query.lower().strip()
    if not q_lower:
        return matches

    def _walk(item: any, path: str):
        if len(matches) >= max_matches:
            return
        if isinstance(item, dict):
            for k, v in item.items():
                new_path = f"{path}.{k}" if path else str(k)
                if q_lower in str(k).lower():
                    matches.append({
                        "path": new_path,
                        "match_type": "key",
                        "value": str(k),
                        "snippet": str(k),
                    })
                _walk(v, new_path)
        elif isinstance(item, list):
            for i, elem in enumerate(item):
                new_path = f"{path}[{i}]"
                _walk(elem, new_path)
        elif isinstance(item, (str, int, float, bool)) and item is not None:
            val_str = str(item)
            if q_lower in val_str.lower():
                idx = val_str.lower().find(q_lower)
                start = max(0, idx - 100)
                end = min(len(val_str), idx + len(query) + 100)
                snippet = val_str[start:end]
                if start > 0:
                    snippet = "…" + snippet
                if end < len(val_str):
                    snippet = snippet + "…"
                matches.append({
                    "path": path,
                    "match_type": "value",
                    "value": val_str,
                    "snippet": snippet,
                })

    _walk(obj, current_path)
    return matches


def _render_deep_search_and_dump_panel(
    run_data: list[dict], f_df: pd.DataFrame, df: pd.DataFrame, state_key: str
) -> None:
    st.markdown("---")
    st.subheader("\U0001f50d Deep Search Payloads")

    pa_token = st.session_state.get("cf_pa_token")
    env_id = _get_pa_env_id()
    if not (pa_token and env_id):
        st.info(
            "\U0001f4a1 To perform deep payload searches inside loops, "
            "please authorize and select an environment in the **Power Automate API** section above."
        )
        return

    active_indices = f_df.index.tolist() if not f_df.empty else df.index.tolist()
    valid_runs: list[dict] = [
        run_data[i] for i in active_indices
        if i < len(run_data) and run_data[i].get("flowrunid")
    ]
    if not valid_runs:
        st.caption("No flow runs available to inspect.")
        return

    def _format_run_label(r: dict) -> str:
        fn = r.get("Flow Name") or r.get("flowname") or "Flow"
        st_time = str(r.get("starttime", ""))[:19].replace("T", " ")
        status = r.get("status", "")
        rid = str(r.get("flowrunid", ""))[:8]
        return f"{fn} | {st_time} | {status} ({rid}\u2026)"

    labels = [_format_run_label(r) for r in valid_runs]

    col_sel, _ = st.columns([3, 1])
    selected_idx = col_sel.selectbox(
        "Select Flow Run to inspect:",
        options=range(len(valid_runs)),
        format_func=lambda i: labels[i],
        key=f"cf_{state_key}_dump_sel_run",
    )
    selected_run = valid_runs[selected_idx]
    r_id = selected_run.get("flowrunid", "")
    r_env = selected_run.get("_env_id") or env_id
    r_flow = selected_run.get("_flow_id") or selected_run.get("workflowid", "")

    st.markdown(
        "Search for any **GUID**, **FetchXML attribute**, or **string** across flow run action inputs, "
        "outputs, and loop iterations."
    )
    search_query = st.text_input(
        "Query / Condition to find:",
        placeholder="e.g. f6ff7545-4919-f111-8342-6045bdc1eeb4 or accountid",
        key=f"cf_{state_key}_deep_search_query",
    )

    st.caption(
        f"🎯 Target run: **{selected_run.get('Flow Name')}** | "
        f"**{str(selected_run.get('starttime', ''))[:19].replace('T', ' ')}** | "
        f"Run ID: `{r_id[:12]}…`"
    )

    col_sc1, col_sc2 = st.columns([3, 2])
    with col_sc1:
        search_scope = st.radio(
            "Search scope:",
            [
                "Selected Run (deepest: inspects all loop repetitions)",
                "All Filtered Runs (scans triggers & top-level actions)",
            ],
            key=f"cf_{state_key}_search_scope",
        )
    with col_sc2:
        st.write("")
        force_refresh = st.checkbox(
            "🔄 Force fresh fetch (bypass cache)",
            value=False,
            key=f"cf_{state_key}_force_search_fresh",
            help="Re-fetches fresh action details and loop iterations from the Power Automate API instead of using cached data.",
        )

    if st.button("\U0001f50d Search Payloads", key=f"cf_{state_key}_btn_deep_search"):
        if not search_query.strip():
            st.warning("Please enter a search query.")
        else:
            pa_client = PowerAutomateClient(pa_token)
            results: list[dict] = []

            if "Selected Run" in search_scope:
                with st.spinner("Analyzing selected run and loop repetitions\u2026"):
                    dump_cache_key = f"cf_{state_key}_dump_data_{r_id}"
                    dump = None if force_refresh else st.session_state.get(dump_cache_key)
                    if not dump:
                        dump = pa_client.get_flow_run_full_dump(
                            environment_id=r_env,
                            flow_id=r_flow,
                            run_id=r_id,
                            include_loop_repetitions=True,
                            max_repetitions=500,
                        )
                        st.session_state[dump_cache_key] = dump

                    act_count = len(dump.get("actions", []))
                    loop_dict = dump.get("loops", {})
                    total_iterations = sum(len(v) for v in loop_dict.values())
                    st.info(
                        f"📊 Scanned: **{act_count} action(s)** and **{total_iterations} loop iteration(s)** across {len(loop_dict)} loop(s)."
                    )
                    if total_iterations == 0:
                        st.warning(
                            "⚠️ 0 loop iterations were returned by the API for this run. "
                            "If the loop ran, check if the Power Automate API requires 'Force fresh fetch' or if the run is still in progress."
                        )

                    matches = _deep_search_payload(dump, search_query.strip())
                    for m in matches:
                        m["run_id"] = r_id
                        m["flow_name"] = selected_run.get("Flow Name")
                        results.append(m)
            else:
                runs_to_scan = valid_runs[:20]
                prog = st.progress(0.0)
                for i, r in enumerate(runs_to_scan):
                    curr_rid = r.get("flowrunid", "")
                    curr_env = r.get("_env_id") or env_id
                    curr_flow = r.get("_flow_id") or r.get("workflowid", "")
                    prog.progress((i + 1) / len(runs_to_scan))
                    try:
                        det = pa_client.get_flow_run_details(curr_env, curr_flow, curr_rid)
                        acts = pa_client.get_flow_run_actions(curr_env, curr_flow, curr_rid)
                        m = _deep_search_payload({"details": det, "actions": acts}, search_query.strip())
                        for match in m:
                            match["run_id"] = curr_rid
                            match["flow_name"] = r.get("Flow Name")
                            results.append(match)
                    except Exception:
                        continue
                prog.empty()

            if results:
                st.success(f"\U0001f389 Found **{len(results)} match(es)** for `{search_query}`!")
                for idx, res in enumerate(results):
                    path = res.get("path", "")
                    loc_desc = path
                    import re
                    rep_match = re.search(r"loops\.([^\.\[]+)\[(\d+)\]", path)
                    if rep_match:
                        loop_name = rep_match.group(1)
                        iteration_num = int(rep_match.group(2)) + 1
                        loc_desc = f"Loop '{loop_name}' \u2192 Iteration #{iteration_num}"

                    with st.expander(
                        f"Match #{idx + 1}: {loc_desc}",
                        expanded=(idx == 0),
                    ):
                        st.caption(f"Run: `{res.get('flow_name')}` ({res.get('run_id')}) | Path: `{path}`")
                        st.markdown(f"**Matched Snippet:**")
                        st.info(res.get("snippet", ""))
                        val = res.get("value", "")
                        if "\n" in val or "<" in val or "{" in val:
                            st.code(val, language="xml" if "<" in val else "json")
                        else:
                            st.text(val)
            else:
                st.warning(f"No occurrences of `{search_query}` found in the analyzed payloads.")

# ---------------------------------------------------------------------------
# History manager widget
# ---------------------------------------------------------------------------


def _render_history_manager(key_prefix: str = "") -> None:
    records = persistence.list_history()
    if not records:
        st.caption("No saved history yet.")
        return

    df_hist = pd.DataFrame(records)
    st.dataframe(
        df_hist[["name", "timestamp", "remark", "Fetched Detail", "count"]].rename(
            columns={"name": "Name", "timestamp": "Saved At", "remark": "Remark", "count": "Runs"}
        ),
        use_container_width=True, hide_index=True,
    )

    filenames = [r["filename"] for r in records]
    labels = [f"{r['name'] or 'unnamed'} \u2014 {r['timestamp'][:16]}" for r in records]
    col_sel, col_load, col_del = st.columns([3, 1, 1])
    sel_idx = col_sel.selectbox(
        "Select record", options=range(len(records)),
        format_func=lambda i: labels[i], key=f"cf_{key_prefix}_hist_select",
    )

    if col_load.button("\u21a9\ufe0f Load", key=f"cf_{key_prefix}_hist_load"):
        try:
            remark, data = persistence.load_history(filenames[sel_idx])
            st.session_state["cf_date_runs"] = data
            persistence.save_results(data)
            _remark_display = remark or "\u2014"
            st.success(f"Loaded {len(data)} runs. Remark: {_remark_display}")
            st.rerun()
        except Exception as exc:
            st.error(f"Load failed: {exc}")

    if col_del.button("\U0001f5d1\ufe0f Delete", key=f"cf_{key_prefix}_hist_delete"):
        if persistence.delete_history(filenames[sel_idx]):
            st.success("Deleted.")
            st.rerun()

    new_remark = st.text_input("Update remark for selected record", key=f"cf_{key_prefix}_hist_remark")
    if st.button("\u270f\ufe0f Update Remark", key=f"cf_{key_prefix}_hist_update_remark"):
        if persistence.update_history_remark(filenames[sel_idx], new_remark):
            st.success("Remark updated.")
            st.rerun()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_dataframe(run_data: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(run_data)
    if df.empty:
        return df

    df["starttime"] = pd.to_datetime(df.get("starttime"), errors="coerce", utc=True)
    df["endtime"] = pd.to_datetime(df.get("endtime"), errors="coerce", utc=True)

    # Calculate duration
    if "durationinmilliseconds" in df.columns:
        # Use existing ms if available, else calculate from start/end
        ms = df["durationinmilliseconds"].fillna(
            (df["endtime"] - df["starttime"]).dt.total_seconds() * 1000
        )
    else:
        ms = (df["endtime"] - df["starttime"]).dt.total_seconds() * 1000

    df["duration"] = ms

    pa_run = "_pa_run" in df.columns and df["_pa_run"].any()

    def _link(row: pd.Series) -> str:
        if pa_run:
            return _make_pa_run_url(
                row.get("_env_id", ""), row.get("_flow_id", ""), row.get("flowrunid", "")
            )
        fid = row.get("_flow_id", "")
        rid = row.get("flowrunid", "")
        return _make_pa_run_url("default", fid, rid) if (fid and rid) else ""

    df[_RUN_LINK_COL] = df.apply(_link, axis=1)
    return df


def _apply_details(
    run_data: list[dict],
    run_id: str,
    details: dict,
    actions: list[dict],
    pa_client: "PowerAutomateClient",
    detail_fields: list[str] | None = None,
) -> None:
    if detail_fields is None:
        detail_fields = _get_configured_detail_fields()

    field_map = {f.lower().strip(): f.strip() for f in detail_fields if f.strip()}

    for r in run_data:
        if r.get("flowrunid") != run_id:
            continue

        props = details.get("properties", {})
        trigger = props.get("trigger", {})

        blobs: list = [
            trigger.get("outputs", {}).get("body"),
            trigger.get("inputs", {}).get("body"),
        ]
        out_link = trigger.get("outputsLink", {}).get("uri")
        if out_link:
            fetched = pa_client.get_content_from_link(out_link)
            if fetched:
                blobs.append(fetched)

        for blob in blobs:
            if not blob:
                continue
            search_items: list = [blob]
            if isinstance(blob, dict) and "body" in blob:
                search_items.append(blob["body"])

            for item in search_items:
                if not isinstance(item, dict):
                    continue
                for k, v in item.items():
                    if k in _NOISE_KEYS:
                        continue
                    k_lower = k.lower()
                    if k_lower in field_map:
                        r[field_map[k_lower]] = v
                    elif k_lower.startswith("_") and k_lower.endswith("_value") and k_lower[1:-6] in field_map:
                        r[field_map[k_lower[1:-6]]] = v
                    elif (
                        "status" in k_lower
                        or "update" in k_lower
                        or "message" in k_lower
                    ):
                        r[k] = v

                for sub_key in ("entity", "value"):
                    sub = item.get(sub_key)
                    if isinstance(sub, dict):
                        for k, v in sub.items():
                            if k in _NOISE_KEYS:
                                continue
                            k_lower = k.lower()
                            if k_lower in field_map:
                                r[field_map[k_lower]] = v
                            elif k_lower.startswith("_") and k_lower.endswith("_value") and k_lower[1:-6] in field_map:
                                r[field_map[k_lower[1:-6]]] = v
                            elif "status" in k_lower:
                                r[k] = v
                    elif isinstance(sub, list) and sub and isinstance(sub[0], dict):
                        for k, v in sub[0].items():
                            if k in _NOISE_KEYS:
                                continue
                            k_lower = k.lower()
                            if k_lower in field_map:
                                r[field_map[k_lower]] = v
                            elif k_lower.startswith("_") and k_lower.endswith("_value") and k_lower[1:-6] in field_map:
                                r[field_map[k_lower[1:-6]]] = v
                            elif "status" in k_lower:
                                r[k] = v

                if "operation" in field_map and not r.get(field_map["operation"]):
                    if "sdkmessage" in item:
                        r[field_map["operation"]] = item["sdkmessage"]
                    elif "message" in item:
                        r[field_map["operation"]] = item["message"]

        for action in actions:
            a_props = action.get("properties", {})
            a_name = action.get("name", "")
            if a_props.get("status") != "Succeeded":
                continue

            inputs = a_props.get("inputs", {})
            outputs = a_props.get("outputs", {})

            if isinstance(inputs, dict):
                for k, v in inputs.items():
                    if isinstance(v, str) and (
                        "fetchxml" in k.lower() or "<fetch" in v.lower()
                    ):
                        if "fetch xml" in field_map:
                            r[field_map["fetch xml"]] = v[:100] + "…"
                        r["full_fetch_xml"] = v
                body_i = inputs.get("body", {})
                if isinstance(body_i, dict) and "fetchXml" in body_i:
                    if "fetch xml" in field_map:
                        r[field_map["fetch xml"]] = body_i["fetchXml"][:100] + "…"
                    r["full_fetch_xml"] = body_i["fetchXml"]

            is_update = any(w in a_name.lower() for w in ("update", "patch", "upsert"))
            if is_update:
                if "update action" in field_map:
                    r[field_map["update action"]] = a_name
                if isinstance(inputs, dict) and "body" in inputs:
                    if "update payload" in field_map:
                        r[field_map["update payload"]] = str(inputs["body"])[:200] + "…"

            for src in (outputs.get("body"), inputs.get("body")):
                if isinstance(src, dict):
                    for k, v in src.items():
                        if k in _NOISE_KEYS:
                            continue
                        k_lower = k.lower()
                        if k_lower in field_map and not r.get(field_map[k_lower]):
                            r[field_map[k_lower]] = v
                    val = src.get("value")
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        for k, v in val[0].items():
                            if k in _NOISE_KEYS:
                                continue
                            k_lower = k.lower()
                            if k_lower in field_map and not r.get(field_map[k_lower]):
                                r[field_map[k_lower]] = v

        r["has_details"] = True
        break
