from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from core.context import AppContext
from plugins.portal_webapi_auditor.client import PortalWebApiAuditorClient


def _badge(text: str, bg_color: str, text_color: str = "#ffffff") -> str:
    return (
        f'<span style="background-color:{bg_color};color:{text_color};padding:3px 8px;'
        f'border-radius:12px;font-size:0.82em;font-weight:600;display:inline-block;">{text}</span>'
    )


def _get_script_execution_info(occ: dict, client: PortalWebApiAuditorClient) -> tuple[str, str, str, str]:
    """
    Returns (category, badge_html, label, description)
    category: 'client' | 'server' | 'framework'
    """
    comp_t = occ.get("component_type", "")
    is_gov = client._is_webapi_governed(occ)

    if comp_t in ("Dynamic UI Catalog", "Entity List (OData Feed)", "Portal Lookup Control"):
        badge = _badge("🧩 FRAMEWORK (AUTO-INJECTED WEB API)", "#0d6efd")
        return (
            "framework",
            badge,
            "Framework Dynamic Call",
            "Auto-invoked by Power Pages runtime framework. Governed by Webapi/<table-name>/fields site settings.",
        )
    if is_gov:
        badge = _badge("🌐 CLIENT-SIDE SCRIPT (WEB API)", "#dc3545" if occ.get("is_unprojected") else "#198754")
        return (
            "client",
            badge,
            "Client-Side Script (Browser Web API)",
            "Executes in the user's browser via /_api/. Restricted strictly by Webapi/<table-name>/fields site settings.",
        )
    badge = _badge("⚙️ SERVER-SIDE (LIQUID / FETCHXML)", "#6c757d")
    return (
        "server",
        badge,
        "Server-Side Template (Table Permissions)",
        "Executes on the portal web server. Governed by Table Permissions (NOT restricted by Webapi/fields).",
    )


def _invalidate_all_caches(
    client: PortalWebApiAuditorClient,
    clear_code_scan: bool = False,
    clear_site_settings: bool = False,
) -> None:
    """Wipe all in-memory client and session-state caches to guarantee 100% fresh scan results."""
    client.clear_all_caches()
    st.session_state["pwa_reconciliation"] = None
    if clear_code_scan:
        st.session_state["pwa_code_scan"] = None
    if clear_site_settings:
        st.session_state["pwa_site_settings"] = None
    for k in list(st.session_state.keys()):
        if k.startswith("pwa_input_val_") or k.startswith("pwa_ctx_") or k.startswith("pwa_chk_"):
            del st.session_state[k]


def render_page(ctx: AppContext, client: PortalWebApiAuditorClient) -> None:
    org_url = ctx.org_url

    # --- Session State Initialization ---
    ss_keys = {
        "pwa_websites": None,
        "pwa_selected_website": "ALL",
        "pwa_site_settings": None,
        "pwa_code_scan": None,
        "pwa_reconciliation": None,
        "pwa_last_org": org_url,
    }
    if st.session_state.get("pwa_version_hash") != "v29_custom_webapi_helpers":
        _invalidate_all_caches(client, clear_code_scan=True, clear_site_settings=True)
        st.session_state["pwa_version_hash"] = "v29_custom_webapi_helpers"

    for k, v in ss_keys.items():
        if k not in st.session_state or st.session_state.get("pwa_last_org") != org_url:
            st.session_state[k] = v
    st.session_state["pwa_last_org"] = org_url

    # Header
    st.title("🛡️ Power Pages Web API Auditor & Remediation")
    st.markdown(
        "Audit Power Pages portal code against Dataverse Web API site settings, "
        "ensure strict compliance with Microsoft's upcoming wildcard `*` deprecation, "
        "and generate clean, least-privilege column whitelists."
    )

    # Scope & Deprecation Notice Alert
    with st.expander("ℹ️ About Microsoft's Power Pages Web API Policy Change & Component Scope", expanded=False):
        st.markdown(
            """
            **Deprecation Policy ([Microsoft Learn](https://learn.microsoft.com/en-us/power-pages/important-changes-deprecations)):**
            - Support for wildcard `*` in the `Webapi/<table-name>/fields` site setting is deprecated.
            - **August 2026**: Newly created websites can no longer use wildcard `*`.
            - **September 14, 2026**: Support for `*` is completely removed for **all** websites.
            - Existing sites must explicitly list allowed columns (e.g. `firstname,lastname,emailaddress1`).
            - Any Web API call referencing columns that are not explicitly whitelisted will fail once `*` is disabled.
            - *Note: Model-driven app scripts (`Xrm.WebApi`) are completely unaffected.*

            ---
            **🔍 Scope Clarification (Forms & Lists vs. Web API):**
            - **Standard Basic Forms, Multistep Forms & Entity Lists**: Run **server-side** using Dataverse **Table Permissions** (and Column Security Profiles). They do **NOT** use the client-side Web API and do **NOT** require `Webapi/<table-name>/enabled` or `Webapi/<table-name>/fields`.
            - **Custom Web API Calls (`/_api/...` or PCF `context.webAPI`)**: Used in custom JavaScript/widgets. **ALL columns** referenced in `$select`, `$filter`, or `$orderby` **MUST** be whitelisted in `Webapi/<table-name>/fields`.
            """
        )

    # ------------------------------------------------------------------
    # Top Controls & Website Selector
    # ------------------------------------------------------------------
    if st.session_state["pwa_websites"] is None:
        with st.spinner("Fetching portal websites..."):
            st.session_state["pwa_websites"] = client.get_websites()

    websites: list[dict] = st.session_state["pwa_websites"] or []

    site_options: dict[str, str] = {"ALL": "🌐 All Portal Websites"}
    for w in websites:
        name = w.get("name") or "Unnamed Portal"
        domain = f" ({w.get('domain')})" if w.get("domain") else ""
        site_options[w["id"]] = f"{name}{domain}"

    col_site, col_refresh, col_scan_btn = st.columns([3, 1, 1.2])

    with col_site:
        selected_site_key = st.selectbox(
            "Portal Website",
            options=list(site_options.keys()),
            format_func=lambda k: site_options.get(k, k),
            key="pwa_website_select",
        )
        if selected_site_key != st.session_state["pwa_selected_website"]:
            st.session_state["pwa_selected_website"] = selected_site_key
            _invalidate_all_caches(client, clear_code_scan=True, clear_site_settings=True)
            st.rerun()

    with col_refresh:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True, key="pwa_refresh_btn"):
            _invalidate_all_caches(client, clear_code_scan=True, clear_site_settings=True)
            st.rerun()

    with col_scan_btn:
        st.write("")
        st.write("")
        run_deep_scan = st.button("🔍 Deep Scan Code", type="primary", use_container_width=True, key="pwa_scan_btn")

    target_website_id = None if selected_site_key == "ALL" else selected_site_key

    # ------------------------------------------------------------------
    # Data Loading
    # ------------------------------------------------------------------
    if st.session_state["pwa_site_settings"] is None:
        with st.spinner("Auditing Web API Site Settings..."):
            st.session_state["pwa_site_settings"] = client.get_webapi_site_settings(target_website_id)

    site_settings = st.session_state["pwa_site_settings"] or []

    # Run code scan only on explicit button click
    if run_deep_scan:
        with st.spinner("Deep-scanning portal code and reconciling Web API site settings..."):
            _invalidate_all_caches(client, clear_code_scan=True, clear_site_settings=True)
            st.session_state["pwa_site_settings"] = client.get_webapi_site_settings(target_website_id)
            site_settings = st.session_state["pwa_site_settings"] or []
            known_tbls = [s["table_name"] for s in site_settings]
            tbl_fields_map = {s["table_name"]: s.get("fields_list", []) for s in site_settings}
            code_occurrences = client.scan_portal_code_for_webapi(
                target_website_id, known_tables=known_tbls, table_fields_map=tbl_fields_map
            )
            st.session_state["pwa_code_scan"] = code_occurrences
            curr_mode = st.session_state.get("pwa_suggestion_mode", "forms")
            st.session_state["pwa_reconciliation"] = client.reconcile_webapi_usage(
                site_settings, code_occurrences, suggestion_mode=curr_mode, target_website_id=target_website_id
            )
        st.success(f"✅ Deep scan complete! Found {len(code_occurrences)} code references across {len(site_settings)} tables.")

    c_scan_info, c_mode_sel = st.columns([3, 2.5])
    with c_mode_sel:
        mode_options = {
            "forms": "📋 Include Basic Forms (Recommended)",
            "client": "🎯 Client & Lists Only (Strict)",
            "server": "🌐 All Sources (Include Server FetchXML)",
        }
        curr_mode = st.session_state.get("pwa_suggestion_mode", "forms")
        selected_mode = st.selectbox(
            "Default Whitelist Suggestion Level",
            options=list(mode_options.keys()),
            format_func=lambda k: mode_options[k],
            index=list(mode_options.keys()).index(curr_mode) if curr_mode in mode_options else 0,
            key="pwa_suggestion_mode_select",
            help=(
                "🎯 Client & Lists Only (Strict):\n"
                "  Columns fetched by browser JS (/_api/), safeAjax, PCF controls, Entity List OData feeds, and Portal Lookup Controls.\n"
                "  These are the ONLY calls directly governed by Webapi/<table>/fields — missing columns cause a 403.\n\n"
                "📋 Include Basic Forms (Recommended):\n"
                "  Above + columns from Basic Form / Multistep Form XML layouts.\n"
                "  Standard form rendering is server-side (Table Permissions apply, not Web API fields),\n"
                "  but Portal Lookup Controls on forms DO call /_api/, and custom form JS typically accesses\n"
                "  the same fields shown on the form — so including form layout fields is a safe conservative choice.\n\n"
                "🌐 All Sources (Include Server FetchXML):\n"
                "  Above + columns from {% fetchxml %} Liquid blocks and entity[] lookups.\n"
                "  Server-side FetchXML is governed by Table Permissions (not Web API fields),\n"
                "  but when <all-attributes /> is detected, ALL table columns are added to the whitelist."
            ),
        )
        if selected_mode != st.session_state.get("pwa_suggestion_mode", "forms"):
            st.session_state["pwa_suggestion_mode"] = selected_mode
            # Clear all cached textarea values so Tab 2 shows the new suggestion level,
            # not the stale session-state value that Streamlit would otherwise prioritise
            for k in list(st.session_state.keys()):
                if k.startswith("pwa_input_val_"):
                    del st.session_state[k]
            # Instant in-memory tier update — zero network requests!
            if st.session_state.get("pwa_reconciliation"):
                for r in st.session_state["pwa_reconciliation"]:
                    r["suggestion_mode"] = selected_mode
                    if selected_mode == "client":
                        r["recommended_fields"] = r.get("recommended_fields_strict", "")
                    elif selected_mode == "server":
                        r["recommended_fields"] = r.get("recommended_fields_server", "")
                    else:
                        r["recommended_fields"] = r.get("recommended_fields_forms", "")
            st.rerun()

    with c_scan_info:
        scan_len = len(st.session_state.get("pwa_code_scan") or [])
        if scan_len > 0:
            st.caption(f"📁 Code scan active: **{scan_len}** code references detected across templates, forms & scripts.")
        else:
            st.caption("ℹ️ Click **Deep Scan Code** above to scan Web Templates, Basic Forms, Web Pages, and custom JavaScript.")

    code_occurrences = st.session_state.get("pwa_code_scan") or []

    # Reconcile settings vs code (cached in session state so button clicks are instantaneous)
    if st.session_state.get("pwa_reconciliation") is None:
        with st.spinner("Analyzing site settings and reconciling against portal components..."):
            reconciliation = client.reconcile_webapi_usage(
                site_settings, code_occurrences, suggestion_mode=selected_mode, target_website_id=target_website_id
            )
            st.session_state["pwa_reconciliation"] = reconciliation
    else:
        reconciliation = st.session_state["pwa_reconciliation"]


    # ------------------------------------------------------------------
    # KPI Metrics Bar
    # ------------------------------------------------------------------
    crit_count = sum(1 for r in reconciliation if r["status_level"] == "critical")
    warn_count = sum(1 for r in reconciliation if r["status_level"] == "warning")
    ok_count = sum(1 for r in reconciliation if r["status_level"] == "success")
    fw_count = sum(1 for r in reconciliation if r["status_level"] == "framework")
    unprojected_tables = [r for r in reconciliation if r.get("unprojected_calls_count", 0) > 0]
    unprojected_count = len(unprojected_tables)
    # Count only /_api/-governed calls (excludes FetchXML/Liquid/Basic Form server-side)
    governed_calls = sum(
        1 for occ in code_occurrences
        if PortalWebApiAuditorClient._is_webapi_governed(occ)
    )

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.metric("Web API Tables", len(reconciliation))
    with m2:
        st.metric("🚨 Wildcard '*' Violations", crit_count, delta=f"{crit_count} must fix" if crit_count else None, delta_color="inverse")
    with m3:
        st.metric("⚠️ Unprojected Calls", unprojected_count, delta=f"{unprojected_count} tables missing $select" if unprojected_count else None, delta_color="inverse", help="Tables with Web API queries omitting $select or using *. In Power Pages, these calls will fail with HTTP 403 once wildcard '*' is replaced with a whitelist.")
    with m4:
        st.metric("🔧 Framework & Catalogs", fw_count, help="Tables used by Entity List OData, Portal Lookup Controls, or Dynamic UI Catalogs (e.g. cascading lookups, regex validation schemes). Web API settings ARE required.")
    with m5:
        st.metric("✅ Compliant Tables", ok_count)
    with m6:
        st.metric("📞 Governed API Calls", governed_calls, help="Client-side /_api/ calls, SDK wrappers, PCF controls, and framework lookups — the only calls restricted by Webapi/fields site settings.")

    st.divider()

    # ------------------------------------------------------------------
    # Diagnostics Bar for Wildcards & Settings
    # ------------------------------------------------------------------
    wildcard_tables = [s["table_name"] for s in site_settings if s.get("has_wildcard")]
    with st.expander(f"🔍 Site Settings Diagnostics ({len(wildcard_tables)} tables detected with '*' wildcard)", expanded=False):
        st.markdown(f"**Selected Portal Scope:** `{selected_site_key}` (Target ID: `{target_website_id}`)")
        st.markdown(f"**Total Tables Configured:** `{len(site_settings)}` &nbsp;|&nbsp; **Tables Flagged with `*`:** `{', '.join(wildcard_tables) if wildcard_tables else 'None'}`")
        diag_rows = []
        for s in sorted(site_settings, key=lambda x: (not x.get("has_wildcard"), x["table_name"])):
            dups = s.get("duplicate_fields_settings", [])
            diag_rows.append({
                "Table Name": s.get("table_name"),
                "Has Wildcard (*)": "🚨 YES" if s.get("has_wildcard") else "No",
                "Fields Setting Value": s.get("fields_raw") or "*(not set)*",
                "Web API Enabled": "✅ Yes" if s.get("enabled") else "❌ No",
                "Records in Portal Mgmt": f"⚠️ {len(dups) + 1} records" if dups else "1 record",
                "Setting Table": s.get("setting_table"),
                "Website ID": s.get("website_id") or "(null / global)",
            })
        if diag_rows:
            st.dataframe(pd.DataFrame(diag_rows), width="stretch", hide_index=True)
        else:
            st.info("No site settings found.")

    # Map each logical table name to any raw endpoint names found in JavaScript scanning
    # (e.g., custom_feedbackrecord -> {"custom_feedbackrecords"})
    endpoint_aliases: dict[str, set[str]] = {}
    for occ in code_occurrences:
        l_tbl = occ.get("table_name")
        r_tbl = occ.get("table_raw")
        if l_tbl and r_tbl and l_tbl != r_tbl:
            endpoint_aliases.setdefault(l_tbl, set()).add(r_tbl)

    # ------------------------------------------------------------------
    # Main Tabs
    # ------------------------------------------------------------------
    tab_dash, tab_remediate, tab_drilldown, tab_export = st.tabs([
        "🚨 Deprecation & Compliance Dashboard",
        "🛠️ Remediation & Update Site Settings",
        "🔍 Component Drilldown",
        "📊 Raw Scan & Export",
    ])

    # ==================================================================
    # TAB 1: COMPLIANCE DASHBOARD
    # ==================================================================
    with tab_dash:
        st.subheader("Web API Configuration Status")
        st.caption(
            "Overview of all Dataverse tables configured for Power Pages Web API or referenced in portal client-side scripts."
        )

        if not code_occurrences:
            st.info("💡 **Deep Scan Available:** Click the red **`🔍 Deep Scan Code`** button in the top right to analyze all Web Templates, Pages, Basic Forms, Form Metadata, and Scripts for complete field usage across all tables.")

        unproj_tables = [r for r in reconciliation if r.get("unprojected_calls_count", 0) > 0]
        if unproj_tables:
            total_unproj = sum(r["unprojected_calls_count"] for r in unproj_tables)
            st.error(
                f"🚨 **Client Script Code Updates Required: {total_unproj} Unprojected Web API Call(s) Detected across {len(unproj_tables)} Table(s)**\n\n"
                "**Microsoft Official Rule:** *Any Web API call referencing columns that are not explicitly whitelisted will fail once `*` is disabled.*\n\n"
                "Because unprojected calls (e.g. `/_api/contacts` without `$select` or using `$select=*`) request all attributes of the record, "
                "they will immediately fail with **HTTP 403 Forbidden** once wildcard `*` is retired! "
                "Even though all current table attributes have been added to the recommended whitelist as a safety net, "
                "**client scripts MUST be updated to project only required columns with explicit `$select` clauses**. "
                "Check the **🛠️ Remediation** tab or **🔍 Component Drilldown** tab for copyable suggested query rewrites."
            )

        filter_mode = st.radio(
            "Filter tables by status:",
            options=[
                "All",
                "🚨 Violations (* Wildcard)",
                "⚠️ Unprojected Calls (Missing $select)",
                "⚠️ Missing Fields",
                "🔧 Framework & Dynamic Catalogs",
                "ℹ️ Configured (No Code Found)",
                "✅ Compliant Only",
            ],
            horizontal=True,
            key="pwa_filter_mode",
        )

        filtered_recon = reconciliation
        if filter_mode == "🚨 Violations (* Wildcard)":
            filtered_recon = [r for r in reconciliation if r["has_wildcard"]]
        elif filter_mode == "⚠️ Unprojected Calls (Missing $select)":
            filtered_recon = [r for r in reconciliation if r.get("unprojected_calls_count", 0) > 0]
        elif filter_mode == "⚠️ Missing Fields":
            filtered_recon = [r for r in reconciliation if r["status_level"] == "warning"]
        elif filter_mode == "🔧 Framework & Dynamic Catalogs":
            filtered_recon = [r for r in reconciliation if r["status_level"] == "framework"]
        elif filter_mode == "ℹ️ Configured (No Code Found)":
            filtered_recon = [r for r in reconciliation if r["status_level"] == "info"]
        elif filter_mode == "✅ Compliant Only":
            filtered_recon = [r for r in reconciliation if r["status_level"] == "success"]

        if not filtered_recon:
            st.info("No tables match the selected filter.")
        else:
            table_rows = []
            for r in filtered_recon:
                tbl = r["table_name"]
                status = r["status"]
                fields_raw = r["configured_fields_raw"] or "*(not set)*"
                rec_raw = r.get("recommended_fields", "")
                rec_fields = [f.strip() for f in rec_raw.split(",") if f.strip()]
                rec_count = len(rec_fields)
                rec_str = ", ".join(rec_fields[:6]) + (f" (+{rec_count-6} more)" if rec_count > 6 else "")
                missing_str = ", ".join(r["missing_from_whitelist"]) if r["missing_from_whitelist"] else "None"
                unproj_cnt = r.get("unprojected_calls_count", 0)

                cat_role = r.get("catalog_role")
                if cat_role:
                    role_str = f"{cat_role['badge']}"
                elif r["status_level"] == "framework":
                    role_str = "🔧 Framework Lookup/OData"
                else:
                    role_str = "Standard"

                if r["has_wildcard"] and unproj_cnt > 0:
                    action_required = "🚨 Update Whitelist & Rewrite JS Query"
                elif r["has_wildcard"]:
                    action_required = "🚨 Update Whitelist in Setting"
                elif unproj_cnt > 0:
                    action_required = "⚠️ Rewrite JS Query (Add $select)"
                elif r["missing_from_whitelist"]:
                    action_required = "⚠️ Add Missing Fields to Whitelist"
                elif r["status_level"] == "framework":
                    action_required = "🔧 Maintain Framework Whitelist"
                elif r["status_level"] == "info":
                    action_required = "ℹ️ Verify Usage or Disable"
                else:
                    action_required = "✅ Compliant"

                table_rows.append({
                    "Table Logical Name": tbl,
                    "Compliance Status": status,
                    "Action Required": action_required,
                    "Unprojected Calls": f"🚨 {unproj_cnt} call(s) (Rewrite Required)" if unproj_cnt > 0 else "None",
                    "Architectural Role": role_str,
                    "Current fields Setting": fields_raw,
                    "Suggested Fields (Count)": rec_count,
                    "Suggested Fields": rec_str if rec_str else f"{tbl}id",
                    "Missing from Whitelist": missing_str,
                    "Total Script References": r["total_code_references"],
                })

            df_summary = pd.DataFrame(table_rows)
            st.dataframe(
                df_summary,
                width="stretch",
                hide_index=True,
                column_config={
                    "Table Logical Name": st.column_config.TextColumn("Table Logical Name", width="large"),
                },
            )

            if any(r["has_wildcard"] for r in filtered_recon):
                st.warning(
                    "⚠️ **Action Required**: One or more tables have `Webapi/<table-name>/fields` set to `*`. "
                    "Use the **🛠️ Remediation & Update Site Settings** tab to apply explicit whitelists before September 14, 2026."
                )

    # ==================================================================
    # TAB 2: REMEDIATION & UPDATE SITE SETTINGS
    # ==================================================================
    with tab_remediate:
        st.subheader("1-Click Site Setting Remediation")
        st.markdown(
            "Review recommended column whitelists generated from portal JavaScript/HTML scanning, "
            "make manual adjustments if desired, and apply them directly to Dataverse site settings."
        )

        # Select table to remediate
        table_names = [r["table_name"] for r in reconciliation]
        if not table_names:
            st.info("No Web API tables found to remediate.")
        else:
            # Default to first table with violation if exists
            default_idx = 0
            for i, r in enumerate(reconciliation):
                if r["has_wildcard"] or r["missing_from_whitelist"]:
                    default_idx = i
                    break

            def _format_remediate_tbl(t: str) -> str:
                rec = next((r for r in reconciliation if r["table_name"] == t), None)
                status_str = rec["status"] if rec else ""
                aliases = endpoint_aliases.get(t)
                alias_str = f" [endpoint: {', '.join(sorted(aliases))}]" if aliases else ""
                return f"{t}{alias_str}  —  {status_str}"

            selected_tbl = st.selectbox(
                "Select Table to Remediate:",
                options=table_names,
                index=default_idx,
                format_func=_format_remediate_tbl,
                key="pwa_remediate_tbl_select",
            )

            rec_item = next((r for r in reconciliation if r["table_name"] == selected_tbl), None)
            if rec_item:
                c1, c2 = st.columns([3, 2])

                with c1:
                    st.markdown(f"#### Configuration for `{selected_tbl}`")
                    st.markdown(f"**Status:** {rec_item['status']}")
                    st.caption(rec_item["status_desc"])

                    curr_val = rec_item["configured_fields_raw"]
                    curr_f_list = [f.strip() for f in curr_val.split(",") if f.strip()] if curr_val else []
                    curr_count_str = f" ({len(curr_f_list)} columns)" if (curr_val and curr_val != "*") else (" (wildcard: all columns)" if curr_val == "*" else "")
                    st.text_input(
                        f"Current Dataverse Site Setting Value{curr_count_str}:",
                        value=curr_val if curr_val else "(No site setting found)",
                        disabled=True,
                    )

                    disc_fields = rec_item["discovered_fields"]
                    st.markdown(
                        f"**Fields detected in portal scripts ({len(disc_fields)}):** "
                        + (f"`{', '.join(disc_fields)}`" if disc_fields else "_None detected_")
                    )

                    if rec_item["missing_from_whitelist"]:
                        st.error(
                            f"🚨 **Missing from Whitelist:** `{', '.join(rec_item['missing_from_whitelist'])}` "
                            f"(These calls will fail unless added)."
                        )

                    if rec_item.get("catalog_role"):
                        role = rec_item["catalog_role"]
                        st.info(f"**{role['badge']} ({role['role_title']})**\n\n{role['description']}")
                        if rec_item.get("unused_warning"):
                            st.success(f"🛡️ **Architecture Insight:** {rec_item['unused_warning']}")
                    elif rec_item.get("unused_warning"):
                        st.warning(f"💡 **Security Advisory:** {rec_item['unused_warning']}")

                    st.caption("ℹ️ Whitelist applies to client-side Web API calls (`/_api/...`) for `$select`, `$filter`, and `$orderby`. Standard Forms and Lists rely on Table Permissions.")

                with c2:
                    st.markdown("#### Apply Fix to Dataverse")
                    recommended_val = rec_item["recommended_fields"]

                    if rec_item.get("has_unprojected_calls"):
                        f_count = len([f for f in recommended_val.split(",") if f.strip()])
                        st.warning(
                            f"⚠️ **Unprojected Calls Detected ({rec_item.get('unprojected_calls_count')} call(s)):** "
                            f"Because queries in code omit `$select` or use wildcard `*`, "
                            f"**all {f_count} table fields** have been automatically added to the whitelist below. "
                            "**Note:** You must ALSO update the client-side JavaScript code (see suggested updates below)."
                        )

                    input_key = f"pwa_input_val_{selected_tbl}"

                    strict_val = rec_item.get("recommended_fields_strict", "")
                    form_val = rec_item.get("recommended_fields_forms", rec_item.get("recommended_fields_comprehensive", ""))
                    server_val = rec_item.get("recommended_fields_server", form_val)
                    curr_raw = rec_item.get("configured_fields_raw", "")

                    strict_cnt = len([f for f in strict_val.split(",") if f.strip()])
                    form_cnt = len([f for f in form_val.split(",") if f.strip()])
                    server_cnt = len([f for f in server_val.split(",") if f.strip()])

                    c_b1, c_b2, c_b3, c_b4 = st.columns(4)

                    with c_b1:
                        if st.button(
                            f"🎯 Client & Lists ({strict_cnt} cols)",
                            key=f"btn_strict_{selected_tbl}",
                            use_container_width=True,
                            help="Load columns from client-side Web API calls (/_api/...), safeAjax, PCF, and portal list views only.",
                        ):
                            st.session_state[input_key] = strict_val
                            st.rerun()

                    with c_b2:
                        if st.button(
                            f"📋 + Form XML ({form_cnt} cols)",
                            key=f"btn_comp_{selected_tbl}",
                            use_container_width=True,
                            help="Client calls + columns rendered on Basic Form / Multistep Form layouts (excludes server-side FetchXML).",
                        ):
                            st.session_state[input_key] = form_val
                            st.rerun()

                    with c_b3:
                        server_help = f"Includes all {server_cnt} columns from server-side FetchXML templates and Liquid queries."
                        if server_cnt == form_cnt:
                            server_help += " (Same columns as Form XML for this table)."
                        if st.button(
                            f"🌐 + Server XML ({server_cnt} cols)",
                            key=f"btn_server_{selected_tbl}",
                            use_container_width=True,
                            help=server_help,
                        ):
                            st.session_state[input_key] = server_val
                            st.rerun()

                    with c_b4:
                        if curr_raw and curr_raw != "*":
                            curr_cnt = len([f for f in curr_raw.split(",") if f.strip()])
                            btn_text = f"💾 Current Dataverse ({curr_cnt} cols)"
                            disabled = False
                        else:
                            btn_text = "💾 Current Dataverse (None)"
                            disabled = True
                        if st.button(
                            btn_text,
                            key=f"btn_keep_curr_{selected_tbl}",
                            use_container_width=True,
                            disabled=disabled,
                            help="Revert to existing Dataverse site setting value (ignoring scan suggestions).",
                        ):
                            st.session_state[input_key] = curr_raw
                            st.rerun()

                    if input_key not in st.session_state:
                        st.session_state[input_key] = recommended_val

                    current_raw_val = st.session_state[input_key]
                    current_f_list = [f.strip() for f in current_raw_val.split(",") if f.strip()]
                    field_count = len(current_f_list)

                    new_val_input = st.text_area(
                        f"Fields Whitelist to Save ({field_count} column{'s' if field_count != 1 else ''}, comma-separated):",
                        help="Enter the exact comma-separated logical names of columns to expose via Web API.",
                        key=input_key,
                    ).strip()

                    parsed_save_fields = [f.strip() for f in new_val_input.split(",") if f.strip()]
                    save_count = len(parsed_save_fields)
                    st.caption(f"📊 **Whitelist Count:** `{save_count}` column{'s' if save_count != 1 else ''} will be saved to `Webapi/{selected_tbl}/fields`.")

                    # Internal Dataverse Metadata Schema Verification
                    with st.expander(f"🔍 Internal Check: Verify `{save_count}` Field(s) Against Dataverse Schema", expanded=False):
                        st.markdown(f"Verify whether all suggested/entered columns actually exist in the **`{selected_tbl}`** Dataverse entity schema.")
                        if st.button("🧪 Run Field Verification Check", key=f"btn_verify_fields_{selected_tbl}", use_container_width=True):
                            with st.spinner(f"Querying Dataverse metadata for '{selected_tbl}'..."):
                                chk = client.validate_fields_exist(selected_tbl, parsed_save_fields)
                                st.session_state[f"pwa_chk_{selected_tbl}"] = chk

                        chk_res = st.session_state.get(f"pwa_chk_{selected_tbl}")
                        if chk_res:
                            v_list = chk_res.get("valid", [])
                            iv_list = chk_res.get("invalid", [])
                            all_db_attrs = chk_res.get("all_table_attributes", [])
                            if chk_res.get("is_all_valid"):
                                st.success(f"✅ All {len(v_list)} suggested/entered field(s) exist and are verified in `{selected_tbl}` Dataverse metadata!")
                            else:
                                st.error(f"❌ {len(iv_list)} field(s) NOT found on `{selected_tbl}` in Dataverse metadata:\n\n`{', '.join(iv_list)}`")
                                st.caption("These fields may be typos, belong to another table, or be JavaScript variables.")
                                if st.button(f"🧹 Remove Invalid Fields (Keep {len(v_list)} valid fields)", key=f"btn_clean_invalid_{selected_tbl}"):
                                    st.session_state[input_key] = ",".join(v_list)
                                    del st.session_state[f"pwa_chk_{selected_tbl}"]
                                    st.rerun()

                            with st.expander(f"📋 View All Available Dataverse Columns ({len(all_db_attrs)}) for `{selected_tbl}`"):
                                st.code(", ".join(all_db_attrs), language="text")

                    # Direct Dataverse Update Button
                    st.write("")
                    setting_id = rec_item.get("setting_id")
                    selected_site_obj = next((w for w in websites if w["id"] == target_website_id), None)
                    target_schema = selected_site_obj.get("schema") if selected_site_obj else (client.get_website_schema(target_website_id) if target_website_id else "adx")
                    default_setting_table = f"{target_schema}_sitesettings"
                    setting_table = (rec_item.get("setting_table") if setting_id else None) or default_setting_table
                    site_id = rec_item.get("website_id") or target_website_id or (websites[0]["id"] if websites else "")

                    btn_label = "🚀 Update Site Setting in Dataverse" if setting_id else "✨ Create & Enable Site Setting"
                    
                    if st.button(btn_label, type="primary", use_container_width=True, key=f"pwa_btn_update_{selected_tbl}"):
                        if not new_val_input:
                            st.warning("Please provide a non-empty list of fields.")
                        elif "*" in new_val_input:
                            st.error("Wildcard '*' is deprecated by Microsoft and cannot be saved.")
                        else:
                            try:
                                with st.spinner("Applying changes to Dataverse..."):
                                    if setting_id:
                                        client.update_site_setting_fields(setting_table, setting_id, new_val_input)
                                        st.success(f"✅ Successfully updated `Webapi/{selected_tbl}/fields` to `{new_val_input}`!")
                                    else:
                                        if not site_id:
                                            st.error("Cannot create site setting: No Website ID could be determined.")
                                        else:
                                            ok, msg = client.create_or_enable_webapi_site_settings(
                                                setting_table, site_id, selected_tbl, new_val_input
                                            )
                                            if ok:
                                                st.success(f"✅ {msg}")
                                            else:
                                                st.error(f"❌ {msg}")
                                # Invalidate cache to refresh
                                _invalidate_all_caches(client, clear_code_scan=False, clear_site_settings=True)
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Failed updating site setting: {exc}")

                st.divider()

                # Prominent Code Update Required Section for Unprojected Calls
                if rec_item.get("has_unprojected_calls"):
                    unproj_occs = [o for o in rec_item.get("occurrences", []) if o.get("is_unprojected") and client._is_webapi_governed(o)]
                    st.error(
                        f"🚨 **Client Script Code Updates Required for `{selected_tbl}` ({len(unproj_occs)} unprojected call(s))**\n\n"
                        "**Official Microsoft Power Pages Documentation Rule:**\n"
                        "> *Any Web API call referencing columns that are not explicitly whitelisted will fail once `*` is disabled.*\n\n"
                        "Saving the table whitelist above provides a safety net by including all current table attributes. "
                        "However, **client scripts must still be updated to explicitly project only required columns with `$select`**.\n"
                        "Unprojected calls request every column from Dataverse, degrade portal performance, and will fail if new or system attributes are queried."
                    )

                    with st.expander(f"📝 View & Copy Suggested Code Updates for `{selected_tbl}` ({len(unproj_occs)} location(s))", expanded=True):
                        for u_idx, u_occ in enumerate(unproj_occs, start=1):
                            comp_t = u_occ.get("component_type", "Component")
                            comp_n = u_occ.get("component_name", "Unnamed")
                            line_n = u_occ.get("line_number", 1)
                            raw_ep = u_occ.get("raw_endpoint", "")
                            sug_rew = u_occ.get("suggested_rewrite", "")
                            sug_f = u_occ.get("suggested_fields", [])
                            pm_link = client.get_record_link(u_occ.get("table_source", ""), u_occ.get("component_id", ""))

                            st.markdown(f"##### {u_idx}. {comp_t}: `{comp_n}`" + (f" (Line {line_n})" if line_n > 1 else "") + (f"  ·  [🔗 Edit in Portal Management]({pm_link})" if pm_link else ""))
                            st.caption(f"Reason: {u_occ.get('unprojected_reason')}  |  Original call: `{raw_ep}`")

                            col_u1, col_u2 = st.columns([1, 1])
                            with col_u1:
                                st.markdown("❌ **Current Code (Fails with HTTP 403 once `*` is disabled):**")
                                st.code(u_occ.get("context_snippet", raw_ep), language="javascript")
                            with col_u2:
                                st.markdown("✅ **Suggested Coding Update (Safe Query Rewrite):**")
                                st.code(f"// Rewrite query with explicit $select projection:\n{sug_rew}", language="javascript")
                                st.caption(f"Safe Whitelisted Fields ({len(sug_f)}): `{', '.join(sug_f[:10])}`" + (f" (+{len(sug_f)-10} more)" if len(sug_f) > 10 else ""))
                            st.write("")
                    st.divider()

                # Batch Whitelist Recommendation Box
                pm_loc = client.get_location_breadcrumb(rec_item["setting_table"])
                pm_link = client.get_record_link(rec_item["setting_table"], rec_item.get("setting_id") or "")
                app_title = "Power Pages Management" if "Power Pages" in pm_loc else "Portal Management"
                st.markdown(f"#### Manual {app_title} Path")
                link_md = f" 👉 [Open Record in {app_title}]({pm_link})" if pm_link else ""
                st.info(
                    f"**Location:** `{pm_loc}` → Site Setting: `Webapi/{selected_tbl}/fields`{link_md}\n\n"
                    f"**Recommended Value to copy:** `{new_val_input}`"
                )

    # ==================================================================
    # TAB 3: COMPONENT DRILLDOWN
    # ==================================================================
    with tab_drilldown:
        st.subheader("Component Drilldown")
        st.markdown(
            "Inspect exactly which portal components (Web Templates, Pages, Forms, etc.) "
            "call the Web API, view the code context, and navigate to the Portal Management app."
        )

        all_tables_set = set(r["table_name"] for r in reconciliation)
        all_tables_set.update(occ["table_name"] for occ in code_occurrences)
        all_discovered_tables = sorted(list(all_tables_set))

        # Collect Dynamic Catalog / Framework findings from reconciliation
        all_catalog_occs = []
        for r in reconciliation:
            if r.get("catalog_role"):
                cat_role = r["catalog_role"]
                rec_f_list = [f.strip() for f in r.get("recommended_fields", "").split(",") if f.strip()]
                all_catalog_occs.append({
                    "raw_endpoint": f"Dynamic UI Catalog: {cat_role['badge']}",
                    "table_name": r["table_name"],
                    "table_raw": r["table_name"],
                    "fields": cat_role.get("key_attributes", []),
                    "line_number": 1,
                    "context_snippet": (
                        f"// Framework & Catalog Role: {cat_role['badge']} ({cat_role['role_title']})\n"
                        f"// Classification: {cat_role['description']}\n"
                        f"// Key Whitelist Attributes: {', '.join(cat_role.get('key_attributes', []))}\n"
                        f"// Suggested Whitelist ({len(rec_f_list)} fields): {r.get('recommended_fields', '')}\n"
                        f"// Safety Guidance: {cat_role.get('safety_advice', '')}"
                    ),
                    "component_type": "Dynamic UI Catalog",
                    "component_name": f"{cat_role['role_title']} ({cat_role['badge']})",
                    "component_id": "",
                    "table_source": r.get("setting_table") or "adx_sitesettings",
                    "field_source": "fields",
                    "website_id": r.get("website_id") or "",
                })

        if not all_discovered_tables:
            st.info("No Web API tables found to inspect.")
        else:
            def _format_drilldown_table(t: str) -> str:
                if t == "(All Tables)":
                    return "🌐 (All Tables)"
                aliases = endpoint_aliases.get(t)
                if aliases:
                    return f"{t}  —  endpoint: /_api/{', /_api/'.join(sorted(aliases))}"
                return t

            col_sel_table, col_sel_field = st.columns([2, 2])
            with col_sel_table:
                dd_table = st.selectbox(
                    "Select Table to Inspect:",
                    options=["(All Tables)"] + all_discovered_tables,
                    format_func=_format_drilldown_table,
                    key="pwa_dd_table_select",
                )

            # -------------------------------------------------------------
            # Deep Table Inspection View when a specific table is chosen
            # -------------------------------------------------------------
            if dd_table != "(All Tables)":
                st.markdown(f"### 🔍 Deep Inspection: `{dd_table}`")
                
                cache_key = f"pwa_ctx_{dd_table}_{target_website_id}"
                if cache_key not in st.session_state:
                    with st.spinner(f"Loading 360° portal context for '{dd_table}'..."):
                        st.session_state[cache_key] = client.get_table_deep_context(
                            dd_table,
                            target_website_id,
                            existing_code_occurrences=code_occurrences,
                        )
                tbl_ctx = st.session_state[cache_key]

                r_item = next((r for r in reconciliation if r["table_name"] == dd_table), None)
                cat_role = r_item.get("catalog_role") if r_item else tbl_ctx.get("catalog_role")

                # Dedicated Framework & Dynamic Catalog Finding Card
                if cat_role:
                    rec_f = r_item.get("recommended_fields", "") if r_item else ""
                    all_subroles = cat_role.get("all_roles", [cat_role])
                    subroles_md = ""
                    if len(all_subroles) > 1:
                        subroles_md = "\n\n**Participating Framework Roles:**\n" + "\n".join(
                            f"- **{sr['badge']} ({sr['role_title']}):** {sr['description']}" for sr in all_subroles
                        )
                    st.info(
                        f"#### 🧩 Framework & Dynamic Catalog Finding: {cat_role['badge']}\n\n"
                        f"**Classification:** {cat_role['role_title']}\n\n"
                        f"**Architectural Purpose:** {cat_role['description']}"
                        f"{subroles_md}\n\n"
                        f"**Key Whitelisted Attributes:** `{', '.join(cat_role.get('key_attributes', []))}`\n\n"
                        f"**Suggested Whitelist ({len(rec_f.split(',')) if rec_f else 0} fields):** `{rec_f}`\n\n"
                        f"🛡️ **Safety Guidance:** {cat_role.get('safety_advice', '')}"
                    )

                rec_cnt = len([f for f in r_item.get("recommended_fields", "").split(",") if f.strip()]) if r_item else 0
                c_stat1, c_stat2, c_stat3, c_stat4, c_stat5 = st.columns(5)
                with c_stat1:
                    st.metric("Site Settings", len(tbl_ctx["site_settings"]))
                with c_stat2:
                    st.metric("Architectural Role", cat_role["badge"] if cat_role else "Standard")
                with c_stat3:
                    st.metric("Suggested Fields", rec_cnt)
                with c_stat4:
                    st.metric("Forms & Lists", len(tbl_ctx["basic_forms"]) + len(tbl_ctx["multistep_steps"]) + len(tbl_ctx["entity_lists"]))
                with c_stat5:
                    st.metric("Code References", len(tbl_ctx["code_occurrences"]))

                # 1. Site Settings Section
                with st.expander(f"🛡️ Web API Site Settings ({len(tbl_ctx['site_settings'])} records)", expanded=True):
                    if not tbl_ctx["site_settings"]:
                        st.info(f"No `Webapi/{dd_table}/*` site settings exist on this portal.")
                    else:
                        ss_df = pd.DataFrame([
                            {"Setting Name": s["name"], "Value": s["value"], "Table": s["table"]}
                            for s in tbl_ctx["site_settings"]
                        ])
                        st.dataframe(ss_df, width="stretch", hide_index=True)

                    if tbl_ctx.get("cross_portal_site_settings"):
                        cross_portals = sorted(list(set(s.get("portal_name", s["table"]) for s in tbl_ctx["cross_portal_site_settings"])))
                        st.warning(
                            f"ℹ️ **Cross-Portal Finding:** Detected {len(tbl_ctx['cross_portal_site_settings'])} site setting(s) for `{dd_table}` "
                            f"on another portal ({', '.join(cross_portals)}) in this environment. "
                            f"These do **NOT** apply to the currently selected portal!"
                        )
                        with st.expander("🔍 View other portal site settings for reference", expanded=False):
                            cross_df = pd.DataFrame([
                                {
                                    "Setting Name": s["name"],
                                    "Value": s["value"],
                                    "Table": s["table"],
                                    "Portal / Website": s.get("portal_name", "Other Portal"),
                                }
                                for s in tbl_ctx["cross_portal_site_settings"]
                            ])
                            st.dataframe(cross_df, width="stretch", hide_index=True)

                # 2. Portal Basic Forms & Multistep Form Steps
                total_forms = len(tbl_ctx["basic_forms"]) + len(tbl_ctx["multistep_steps"]) + len(tbl_ctx["entity_lists"])
                with st.expander(f"📋 Portal Forms & Lists ({total_forms} records)", expanded=(total_forms > 0)):
                    if total_forms == 0:
                        st.caption("No Basic Forms, Multistep Steps, or Entity Lists target this table directly.")
                    else:
                        st.info(
                            "💡 **Architecture Note:** Standard Basic Forms, Multistep Forms, and Entity Lists execute "
                            "server-side via **Table Permissions**. They do not consume the Web API unless they contain custom "
                            "client-side JavaScript or PCF controls calling `/_api/...`."
                        )
                        if tbl_ctx["basic_forms"]:
                            st.markdown("**Basic Forms (Entity Forms):**")
                            bf_df = pd.DataFrame([
                                {
                                    "Basic Form Name": bf["name"],
                                    "Mode": bf["mode"],
                                    "CRM Form": bf["form_name"],
                                    "Form XML Rendered Fields": ", ".join(bf.get("form_fields", [])) if bf.get("form_fields") else "*(metadata not loaded)*",
                                }
                                for bf in tbl_ctx["basic_forms"]
                            ])
                            st.dataframe(bf_df, width="stretch", hide_index=True)

                        if tbl_ctx["multistep_steps"]:
                            st.markdown("**Multistep Form Steps:**")
                            wf_df = pd.DataFrame([
                                {
                                    "Step Name": s["name"],
                                    "Step Type": s["type"],
                                    "CRM Form": s["form_name"],
                                    "Form XML Rendered Fields": ", ".join(s.get("form_fields", [])) if s.get("form_fields") else "*(metadata not loaded)*",
                                }
                                for s in tbl_ctx["multistep_steps"]
                            ])
                            st.dataframe(wf_df, width="stretch", hide_index=True)

                        if tbl_ctx["entity_lists"]:
                            st.markdown("**Entity Lists:**")
                            el_df = pd.DataFrame([
                                {"List Name": el["name"]}
                                for el in tbl_ctx["entity_lists"]
                            ])
                            st.dataframe(el_df, width="stretch", hide_index=True)

                # 3. Form Metadata (Attribute rules, pre-populates, PCF controls)
                with st.expander(f"⚙️ Form Metadata & Attribute Rules ({len(tbl_ctx['form_metadata'])} records)", expanded=(len(tbl_ctx['form_metadata']) > 0)):
                    if not tbl_ctx["form_metadata"]:
                        st.caption("No specific attribute metadata records found for this table's forms.")
                    else:
                        fmd_df = pd.DataFrame([
                            {
                                "Attribute Logical Name": m["attribute"],
                                "Label": m["label"],
                                "Control Style / Type": m["control_style"] or m["type"] or "Default",
                                "Description / Tooltip": m["description"],
                            }
                            for m in tbl_ctx["form_metadata"]
                        ])
                        st.dataframe(fmd_df, width="stretch", hide_index=True)

                # 4. Code & Text References
                combined_code_occs = [
                    o for o in code_occurrences
                    if o.get("table_name") == dd_table or o.get("table_raw") == dd_table or o.get("table_name") == dd_table + "s" or (o.get("table_name") or "")[:-1] == dd_table
                ] + tbl_ctx["code_occurrences"]

                # Prepend the Dynamic Catalog finding for this table so it is visible in the references list
                cat_occ = next((o for o in all_catalog_occs if o["table_name"] == dd_table), None)
                if cat_occ and not any(o.get("component_type") == "Dynamic UI Catalog" for o in combined_code_occs):
                    combined_code_occs.insert(0, cat_occ)

                # Deduplicate by snippet & component
                seen_code_keys = set()
                unique_code_occs = []
                for occ in combined_code_occs:
                    k = (occ.get("component_name"), occ.get("line_number"), occ.get("context_snippet")[:50])
                    if k not in seen_code_keys:
                        seen_code_keys.add(k)
                        unique_code_occs.append(occ)

                with st.expander(f"💻 Code & Template References ({len(unique_code_occs)} matches)", expanded=True):
                    if not unique_code_occs:
                        st.caption(
                            f"No script calls or text mentions of `{dd_table}` found in Web Templates, Web Pages, or Web Files."
                        )
                    else:
                        _FRAMEWORK_TYPES = PortalWebApiAuditorClient.FRAMEWORK_COMPONENT_TYPES
                        governed_occs_dd = [o for o in unique_code_occs if PortalWebApiAuditorClient._is_webapi_governed(o)]
                        info_occs_dd = [o for o in unique_code_occs if not PortalWebApiAuditorClient._is_webapi_governed(o)]
                        fw_occs = [o for o in governed_occs_dd if o.get("component_type") in _FRAMEWORK_TYPES]

                        c_subfilter1, c_subfilter2 = st.columns([3, 1])
                        with c_subfilter1:
                            script_filter = st.radio(
                                "Filter References by Execution Environment:",
                                options=[
                                    f"All References ({len(unique_code_occs)})",
                                    f"🌐 Client-Side Scripts (Web API) ({len(governed_occs_dd)})",
                                    f"⚙️ Server-Side (Liquid / FetchXML) ({len(info_occs_dd)})",
                                ],
                                horizontal=True,
                                key=f"pwa_script_filter_{dd_table}",
                            )

                        if "Client-Side" in script_filter:
                            displayed_occs = governed_occs_dd
                        elif "Server-Side" in script_filter:
                            displayed_occs = info_occs_dd
                        else:
                            displayed_occs = unique_code_occs

                        if fw_occs and ("Client-Side" in script_filter or "All" in script_filter):
                            st.info(
                                f"🔧 **{len(fw_occs)} framework & dynamic catalog reference(s)** detected. "
                                "The Power Pages framework auto-issues Web API calls or queries these reference catalogs dynamically — "
                                "no custom JavaScript is required, but explicit `Webapi/fields` site settings ARE needed."
                            )

                        if info_occs_dd and ("Server-Side" in script_filter or "All" in script_filter):
                            st.caption(
                                f"📚 **{len(info_occs_dd)} server-side reference(s)** found (FetchXML, Liquid tags, etc.). "
                                "These run on the server and are governed by **Table Permissions**, not `Webapi/fields`. "
                                "They are shown here for context only and do not affect compliance status."
                            )

                        for idx, occ in enumerate(displayed_occs):
                            cat, badge_html, label_str, desc_str = _get_script_execution_info(occ, client)
                            comp_t = occ.get("component_type", "Component")
                            comp_n = occ.get("component_name", "Unnamed")
                            line_n = occ.get("line_number", 1)
                            raw_ep = occ.get("raw_endpoint", "")
                            f_list = occ.get("fields", [])
                            snip = occ.get("context_snippet", "")
                            pm_link = client.get_record_link(occ.get("table_source", ""), occ.get("component_id", ""))

                            st.markdown(
                                f"#### {badge_html} &nbsp; **{comp_t}: `{comp_n}`**"
                                + (f" *(Line {line_n})*" if line_n > 1 else "")
                                + (f" &nbsp;·&nbsp; [🔗 Open in Portal Mgmt]({pm_link})" if pm_link else ""),
                                unsafe_allow_html=True,
                            )
                            st.caption(f"**Execution Environment:** {desc_str}")
                            if raw_ep:
                                st.caption(f"Endpoint / Call: `{raw_ep}`" + (f" | Fields: `{', '.join(f_list)}`" if f_list else ""))

                            if occ.get("is_unprojected") and client._is_webapi_governed(occ):
                                sug_f = occ.get("suggested_fields", [])
                                sug_rew = occ.get("suggested_rewrite", "")
                                st.error(
                                    f"🚨 **Unprojected / Wildcard Call — Code Update Required ({occ.get('unprojected_reason')})**\n\n"
                                    f"**Microsoft Official Rule:** *Any Web API call referencing columns that are not explicitly whitelisted will fail once `*` is disabled.*\n\n"
                                    f"Because this query omits `$select` (implicit SELECT *), the browser requests all entity attributes from Dataverse. "
                                    f"It will trigger **HTTP 403 Forbidden** errors at runtime once wildcard `*` is disabled unless updated.\n\n"
                                    f"**Suggested Safe Query Rewrite:**\n"
                                    f"```javascript\n{sug_rew}\n```\n"
                                    f"**Whitelisted Fields ({len(sug_f)}):** `{', '.join(sug_f[:15])}`" + (f" *(+{len(sug_f)-15} more)*" if len(sug_f) > 15 else "")
                                )
                            elif occ.get("has_all_attributes"):
                                st.info(
                                    "💡 **Server-Side FetchXML (Table Permissions):** This query uses `<all-attributes />`. "
                                    "Because FetchXML executes server-side on the web server using Table Permissions, it does **not** call `/_api/` and is **not** restricted by Web API site settings. "
                                    "It will not fail when Web API wildcard `*` is deprecated. However, explicitly listing `<attribute>` elements is recommended for query performance."
                                )
                            elif occ.get("is_form_xml"):
                                st.info(
                                    f"📋 **Model-Driven Form Layout:** All `{len(f_list)}` fields configured on this form are automatically "
                                    f"included in the recommended whitelist for `{occ.get('table_name')}`."
                                )

                            st.code(snip, language="javascript")
                            st.write("")

                # 5. Table Permissions
                with st.expander(f"🔐 Table Permissions ({len(tbl_ctx['table_permissions'])} records)", expanded=False):
                    if not tbl_ctx["table_permissions"]:
                        st.warning(f"⚠️ No Table Permission records found for `{dd_table}`.")
                    else:
                        tp_df = pd.DataFrame([
                            {
                                "Permission Name": tp["name"],
                                "Scope": tp.get("scope", "Default"),
                                "Read": "✅" if tp.get("read") else "❌",
                                "Write": "✅" if tp.get("write") else "❌",
                                "Create": "✅" if tp.get("create") else "❌",
                            }
                            for tp in tbl_ctx["table_permissions"]
                        ])
                        st.dataframe(tp_df, width="stretch", hide_index=True)

            # -------------------------------------------------------------
            # Global View across all tables
            # -------------------------------------------------------------
            else:
                combined_all_occs = list(code_occurrences)
                seen_cat_tbls = set(o.get("table_name") for o in combined_all_occs if o.get("component_type") == "Dynamic UI Catalog")
                for c_occ in all_catalog_occs:
                    if c_occ["table_name"] not in seen_cat_tbls:
                        combined_all_occs.append(c_occ)
                        seen_cat_tbls.add(c_occ["table_name"])

                matching_occs = combined_all_occs
                col_filter_type, col_filter_field = st.columns([2, 2])
                with col_filter_type:
                    global_type_filter = st.radio(
                        "Filter by Execution Environment:",
                        options=["All Types", "🌐 Client-Side Scripts (Web API) Only", "⚙️ Server-Side (Liquid / FetchXML) Only"],
                        horizontal=True,
                        key="pwa_global_type_filter",
                    )
                with col_filter_field:
                    available_fields = sorted(list(set(f for o in matching_occs for f in o["fields"])))
                    dd_field = st.selectbox(
                        "Filter by Column / Field Name:",
                        options=["(All Fields)"] + available_fields,
                        key="pwa_dd_field_select",
                    )

                if "Client-Side" in global_type_filter:
                    matching_occs = [o for o in matching_occs if client._is_webapi_governed(o)]
                elif "Server-Side" in global_type_filter:
                    matching_occs = [o for o in matching_occs if not client._is_webapi_governed(o)]

                if dd_field != "(All Fields)":
                    matching_occs = [o for o in matching_occs if dd_field in o["fields"]]

                page_size = 25
                total_items = len(matching_occs)
                total_pages = max(1, (total_items + page_size - 1) // page_size)

                col_info_c, col_page_c = st.columns([3, 1])
                with col_info_c:
                    st.caption(f"Showing **{total_items}** code reference(s) across portal components.")
                with col_page_c:
                    if total_pages > 1:
                        current_page = st.number_input(
                            f"Page (1-{total_pages})", min_value=1, max_value=total_pages, value=1, step=1, key="pwa_drilldown_page"
                        )
                    else:
                        current_page = 1

                start_idx = (current_page - 1) * page_size
                page_occs = matching_occs[start_idx : start_idx + page_size]

                for idx, occ in enumerate(page_occs):
                    cat, badge_html, label_str, desc_str = _get_script_execution_info(occ, client)
                    comp_type = occ.get("component_type", "Component")
                    comp_name = occ.get("component_name", "Unnamed")
                    rec_id = occ.get("component_id", "")
                    table_source = occ.get("table_source", "")
                    field_source = occ.get("field_source", "")
                    line_no = occ.get("line_number", 1)
                    endpoint = occ.get("raw_endpoint", "")
                    fields_list = occ.get("fields", [])

                    pm_link = client.get_record_link(table_source, rec_id)
                    header_title = f"{comp_type}: {comp_name}  (Line {line_no})  ·  Target: {occ['table_name']}"

                    with st.expander(header_title, expanded=(idx < 2)):
                        st.markdown(f"{badge_html} &nbsp; **Execution Environment:** {desc_str}", unsafe_allow_html=True)
                        st.write("")
                        c_info, c_action = st.columns([4, 1])
                        with c_info:
                            st.markdown(f"**Endpoint:** `{endpoint}`")
                            st.markdown(
                                f"**Fields referenced:** "
                                + (", ".join(f"`{f}`" for f in fields_list) if fields_list else "_None extracted_")
                            )
                            st.caption(f"Source Field: `{table_source}.{field_source}`")

                        with c_action:
                            if pm_link:
                                st.markdown(f"[🔗 Open in Portal Mgmt]({pm_link})")

                        if occ.get("is_unprojected") and client._is_webapi_governed(occ):
                            sug_f = occ.get("suggested_fields", [])
                            sug_rew = occ.get("suggested_rewrite", "")
                            st.error(
                                f"🚨 **Unprojected / Wildcard Call — Code Update Required ({occ.get('unprojected_reason')})**\n\n"
                                f"**Microsoft Official Rule:** *Any Web API call referencing columns that are not explicitly whitelisted will fail once `*` is disabled.*\n\n"
                                f"Because this query omits `$select` (implicit SELECT *), the browser requests all entity attributes from Dataverse. "
                                f"It will trigger **HTTP 403 Forbidden** errors at runtime once wildcard `*` is disabled unless updated.\n\n"
                                f"**Suggested Safe Query Rewrite:**\n"
                                f"```javascript\n{sug_rew}\n```\n"
                                f"**Whitelisted Fields ({len(sug_f)}):** `{', '.join(sug_f[:15])}`" + (f" *(+{len(sug_f)-15} more)*" if len(sug_f) > 15 else "")
                            )
                        elif occ.get("has_all_attributes"):
                            st.info(
                                "💡 **Server-Side FetchXML (Table Permissions):** This query uses `<all-attributes />`. "
                                "Because FetchXML executes server-side on the web server using Table Permissions, it does **not** call `/_api/` and is **not** restricted by Web API site settings. "
                                "It will not fail when Web API wildcard `*` is deprecated. However, explicitly listing `<attribute>` elements is recommended for query performance."
                            )

                        st.markdown("**Code Snippet:**")
                        st.code(occ.get("context_snippet", ""), language="javascript")

    # ==================================================================
    # TAB 4: RAW SCAN & EXPORT
    # ==================================================================
    with tab_export:
        st.subheader("Scan Results & Compliance Export")
        st.markdown("Export full audit and code scan results for project governance and compliance documentation.")

        if reconciliation:
            st.markdown("#### Table Reconciliation Summary")
            recon_export_rows = [
                {
                    "Table": r["table_name"],
                    "Status": r["status"],
                    "Action Required": (
                        "🚨 Update Setting & Rewrite Code" if (r["has_wildcard"] and r.get("unprojected_calls_count", 0) > 0)
                        else ("🚨 Update Setting to Whitelist" if r["has_wildcard"]
                        else ("⚠️ Rewrite JS Query ($select)" if r.get("unprojected_calls_count", 0) > 0
                        else ("⚠️ Add Missing Fields" if r["missing_from_whitelist"]
                        else "✅ Compliant")))
                    ),
                    "Has Wildcard (*)": r["has_wildcard"],
                    "Unprojected Calls": r.get("unprojected_calls_count", 0),
                    "Requires Code Update": "YES" if r.get("requires_code_update") else "NO",
                    "Current Configured Fields": r["configured_fields_raw"],
                    "Discovered Fields Count": len(r["discovered_fields"]),
                    "Discovered Fields": ", ".join(r["discovered_fields"]),
                    "Missing from Whitelist": ", ".join(r["missing_from_whitelist"]),
                    "Recommended Setting": r["recommended_fields"],
                    "Total Code Occurrences": r["total_code_references"],
                }
                for r in reconciliation
            ]
            df_recon_export = pd.DataFrame(recon_export_rows)
            st.dataframe(df_recon_export, width="stretch", hide_index=True)

            csv_buf = io.StringIO()
            df_recon_export.to_csv(csv_buf, index=False)
            st.download_button(
                label="📥 Download Compliance Report (CSV)",
                data=csv_buf.getvalue(),
                file_name="power_pages_webapi_compliance_report.csv",
                mime="text/csv",
                key="pwa_dl_report",
            )

        if client.last_errors:
            with st.expander("⚠️ Diagnostic & API Notices", expanded=False):
                for err in client.last_errors:
                    st.caption(err)
