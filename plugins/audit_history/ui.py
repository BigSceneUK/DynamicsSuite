"""Streamlit UI for the Audit History plugin.

All session_state keys are prefixed with ``ah_`` to avoid collisions with
other plugins or the core shell.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.context import AppContext
from plugins.audit_history.client import DataverseClient

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


def render_page(ctx: AppContext, client: DataverseClient) -> None:
    st.title("📜 Dataverse Audit History")

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

    selected_logical_name: str = st.selectbox(
        "Table",
        options=list(entity_options.keys()),
        format_func=lambda x: entity_options[x],
    )

    selected_entity = next(
        e for e in all_entities if e["logical_name"] == selected_logical_name
    )
    entity_set_name: str = selected_entity["entity_set_name"]

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

    # Reset filter config when the entity changes
    if st.session_state.get("ah_last_entity") != selected_logical_name:
        st.session_state["ah_last_entity"] = selected_logical_name
        primary_field = next(
            (k for k in sorted_fields if k.lower() in {"name", "fullname", "subject"}),
            sorted_fields[0] if sorted_fields else "",
        )
        st.session_state["ah_filter_config"] = (
            [{"field": primary_field, "value": ""}] if primary_field else []
        )
        st.session_state.pop("ah_search_results", None)
        st.session_state.pop("ah_audit_data", None)

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
            records = client.search_records(
                entity_set_name,
                filters=active_filters,
                entity_logical_name=selected_logical_name,
            )
            st.session_state["ah_search_results"] = records
            st.session_state.pop("ah_audit_data", None)
            if getattr(client, "last_error", None):
                st.error(f"Search failed: {client.last_error}")
            elif not records:
                st.warning("No records found matching the filters.")

    # ------------------------------------------------------------------ #
    # Step 3 – Record selection                                           #
    # ------------------------------------------------------------------ #
    search_results: list[dict] = st.session_state.get("ah_search_results", [])
    if not search_results:
        return

    st.markdown("---")
    st.header("3. Select Record")

    df_results = pd.DataFrame(search_results)
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
        use_container_width=True,
        hide_index=True,
    )

    id_field = f"{selected_logical_name}id"
    name_field = filter_config[0]["field"] if filter_config else id_field
    options = {}
    if id_field in df_results.columns:
        for r in search_results:
            rid = r.get(id_field)
            label = r.get(name_field) or rid
            options[rid] = f"{label} ({rid})"

    if not options:
        st.info("Could not determine a record ID field. Check table permissions.")
        return

    selected_record_id: str = st.selectbox(
        "Record to audit",
        options=list(options.keys()),
        format_func=lambda x: options[x],
    )

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

                    old_vals: dict = detail.get("OldValue") or {}
                    new_vals: dict = detail.get("NewValue") or {}
                    all_fields = [
                        f
                        for f in set(list(old_vals.keys()) + list(new_vals.keys()))
                        if not f.startswith("@")
                    ]

                    if not all_fields:
                        processed.append(
                            {
                                "Changed Date": changed_on,
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
                                    "Changed Date": changed_on,
                                    "Event": event_type,
                                    "Changed Field": fresh_metadata.get(field, field),
                                    "Old Value": str(old_val),
                                    "New Value": str(new_val),
                                    "Transaction ID": transaction_id,
                                    "Extra Info": extra_info,
                                }
                            )

                st.session_state["ah_audit_data"] = processed
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
    display_cols = ["Changed Date", "Event", "Changed Field", "Old Value", "New Value"]

    # ── Filter controls ────────────────────────────────────────────── #
    with st.expander("🔽 Filter Results", expanded=True):
        col_search, col_field, col_event = st.columns([1.5, 1.5, 1])

        with col_search:
            search_query = st.text_input(
                "Search in Values",
                placeholder="Type keyword to filter...",
                key="ah_filter_search",
            )

        all_fields = sorted(
            [f for f in df["Changed Field"].dropna().unique() if f != "-"]
        )
        with col_field:
            selected_fields = st.multiselect(
                "Filter by Changed Field",
                options=all_fields,
                placeholder="All fields",
                key="ah_filter_fields",
            )

        all_events = sorted(df["Event"].dropna().unique().tolist())
        with col_event:
            selected_events = st.multiselect(
                "Filter by Event",
                options=all_events,
                placeholder="All events",
                key="ah_filter_events",
            )

    # Apply filters
    filtered_df = df.copy()
    if selected_fields:
        filtered_df = filtered_df[filtered_df["Changed Field"].isin(selected_fields)]
    if selected_events:
        filtered_df = filtered_df[filtered_df["Event"].isin(selected_events)]
    if search_query.strip():
        q = search_query.strip().lower()
        mask = (
            filtered_df["Changed Field"].astype(str).str.lower().str.contains(q, na=False)
            | filtered_df["Old Value"].astype(str).str.lower().str.contains(q, na=False)
            | filtered_df["New Value"].astype(str).str.lower().str.contains(q, na=False)
            | filtered_df["Event"].astype(str).str.lower().str.contains(q, na=False)
        )
        filtered_df = filtered_df[mask]

    col_count, col_csv = st.columns([4, 1])
    with col_count:
        st.caption(f"Showing **{len(filtered_df)}** of **{len(df)}** change(s)")
    with col_csv:
        csv_data = filtered_df[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Export CSV",
            data=csv_data,
            file_name="audit_history.csv",
            mime="text/csv",
            key="ah_export_csv",
            use_container_width=True,
        )

    st.dataframe(filtered_df[display_cols], use_container_width=True, hide_index=True)

    with st.expander("🛠️ Technical Transaction Details"):
        tech_cols = ["Changed Date", "Transaction ID", "Extra Info"]
        st.dataframe(
            filtered_df[tech_cols].drop_duplicates(), use_container_width=True, hide_index=True
        )

    if st.button("🗑️ Clear Results"):
        st.session_state.pop("ah_audit_data", None)
        st.rerun()
