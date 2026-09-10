import threading
import streamlit as st
import pandas as pd
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from .client import TableInspectorClient
from core.context import AppContext

def render_ui(ctx: AppContext):
    st.title("🧩 Table Inspector")
    st.caption("Inspect entity attributes, form placement, and data usage statistics.")

    # Initialize client
    client = TableInspectorClient(ctx.org_url, ctx.auth.get_token(ctx.org_url))

    # --- Header: Entity Selection ---
    # Load entities
    if "ti_entities" not in st.session_state:
        with st.spinner("Loading entities list..."):
            st.session_state["ti_entities"] = client.get_entities()
    
    entities = st.session_state["ti_entities"]
    
    # Entity search and dropdown on main page
    st.write("### Select a Table")
    col_sel, col_empty = st.columns([2, 1])
    with col_sel:
        # We use a selectbox with all names. Streamlit's selectbox has built-in search.
        entity_options = [f"{e['DisplayName']} ({e['LogicalName']})" for e in entities]
        
        # Check if we have a saved selection to persist across reruns
        default_idx = 0
        if "ti_selected_entity_label" in st.session_state:
            try:
                default_idx = entity_options.index(st.session_state["ti_selected_entity_label"])
            except ValueError:
                default_idx = 0

        selected_entity_label = st.selectbox(
            "Choose a table to inspect",
            options=entity_options,
            index=default_idx,
            key="ti_selector"
        )
        st.session_state["ti_selected_entity_label"] = selected_entity_label

    selected_entity = None
    if selected_entity_label:
        logical_name = selected_entity_label.split("(")[-1].rstrip(")")
        selected_entity = next((e for e in entities if e["LogicalName"] == logical_name), None)

    if not selected_entity:
        st.info("Please select an entity to begin.")
        return

    st.divider()

    # --- Main Content Area ---
    # Toolbar
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("🔄 Refresh Metadata", use_container_width=True):
            _lname = selected_entity['LogicalName']
            for _key in [
                f"ti_attrs_{_lname}", f"ti_usage_{_lname}",
                f"ti_usage_source_{_lname}",
                f"ti_full_total_{_lname}", f"ti_diag_{_lname}",
            ]:
                st.session_state.pop(_key, None)
            st.rerun()
    
    # Body
    st.subheader(f"Data: {selected_entity['DisplayName']}")
    
    # Load attributes
    cache_key = f"ti_attrs_{selected_entity['LogicalName']}"
    if cache_key not in st.session_state:
        with st.spinner(f"Fetching metadata for {selected_entity['LogicalName']}..."):
            attrs = client.get_attributes(selected_entity["LogicalName"])
            
            # Fetch form info
            form_mapping = client.get_forms_containing_attributes(selected_entity["LogicalName"])
            
            processed_attrs = []
            for a in attrs:
                l_name = a["LogicalName"]
                on_forms = form_mapping.get(l_name, [])
                processed_attrs.append({
                    "Display Name": a["DisplayName"],
                    "Logical Name": l_name,
                    "Attribute Type": a["AttributeType"],
                    "On Form(s)": ", ".join(on_forms) if on_forms else "No",
                    "Is Managed": a["IsManaged"]
                })
            
            st.session_state[cache_key] = pd.DataFrame(processed_attrs)

    df = st.session_state[cache_key].copy()

    # Data Usage Calculation (Improved logic)
    usage_cache_key = f"ti_usage_{selected_entity['LogicalName']}"
    
    with col2:
        if st.button("🔍 Full Scan Table", use_container_width=True,
                     help="Exact field usage across ALL records. Slow for large tables."):
            _fl = selected_entity['LogicalName']
            _progress_text = "Full table scan..."
            _progress_bar = st.progress(0, text=_progress_text)

            _st_ctx = get_script_run_ctx()

            def _full_table_progress(records_seen, _total):
                # Background threads don't inherit the Streamlit session context;
                # re-attach it so that st.progress() can enqueue to the right session.
                add_script_run_ctx(threading.current_thread(), _st_ctx)
                if _total:
                    frac = min(0.99, records_seen / _total)
                    label = f"{_progress_text} ({records_seen:,} / {_total:,} records)"
                else:
                    import math
                    frac = min(0.95, 1 - math.exp(-records_seen / 10000))
                    label = f"{_progress_text} ({records_seen:,} records scanned)"
                _progress_bar.progress(frac, text=label)

            with st.spinner("Paging through Dataverse (full table)..."):
                _full_attrs = st.session_state[cache_key]["Logical Name"].tolist()
                full_stats, full_total, full_diag = client.get_usage_statistics_accurate(
                    _fl,
                    selected_entity["EntitySetName"],
                    _full_attrs,
                    progress_callback=_full_table_progress,
                )
            st.session_state[usage_cache_key] = full_stats
            st.session_state[f"ti_usage_source_{_fl}"] = {a: "full" for a in _full_attrs}
            st.session_state[f"ti_full_total_{_fl}"] = full_total
            st.session_state[f"ti_diag_{_fl}"] = full_diag
            _progress_bar.empty()
            st.rerun()

    lname = selected_entity['LogicalName']
    if usage_cache_key in st.session_state:
        usage_data = st.session_state[usage_cache_key]
        source_data = st.session_state.get(f"ti_usage_source_{lname}", {})
        df["Data Usage"] = df["Logical Name"].map(lambda x: usage_data.get(x, 0.0))
        df["Scan Type"] = df["Logical Name"].map(lambda x: source_data.get(x, "").capitalize())
        full_total = st.session_state.get(f"ti_full_total_{lname}")
        full_count = sum(1 for v in source_data.values() if v == "full")
        if full_count and full_total is not None:
            calc_info = f"Full scan ({full_total:,} records) — exact values for {full_count} field(s)."
        else:
            calc_info = "Statistics calculated."
    else:
        df["Data Usage"] = 0.0
        df["Scan Type"] = ""
        calc_info = "Click '🔍 Full Scan Table' to calculate exact field usage."

    # ── Column filters ────────────────────────────────────────────────────────
    with st.expander("🔽 Filter columns", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)

        all_attr_types = sorted(df["Attribute Type"].dropna().unique().tolist())
        with fc1:
            sel_attr_types = st.multiselect(
                "Attribute Type",
                options=all_attr_types,
                default=[],
                placeholder="Show all",
                key=f"ti_filter_type_{lname}",
            )

        all_on_forms = ["Yes", "No"]
        with fc2:
            sel_on_forms = st.multiselect(
                "On Form(s)",
                options=all_on_forms,
                default=[],
                placeholder="Show all",
                key=f"ti_filter_form_{lname}",
            )

        all_managed = ["True", "False"]
        with fc3:
            sel_managed = st.multiselect(
                "Is Managed",
                options=all_managed,
                default=[],
                placeholder="Show all",
                key=f"ti_filter_managed_{lname}",
            )

        all_scan_types = sorted(df["Scan Type"].dropna().unique().tolist())
        with fc4:
            sel_scan_types = st.multiselect(
                "Scan Type",
                options=all_scan_types if all_scan_types else ["Sample", "Full"],
                default=[],
                placeholder="Show all",
                key=f"ti_filter_scan_{lname}",
            )

    # Apply filters — empty selection means "show all" for that column.
    df_display = df.copy()
    if sel_attr_types:
        df_display = df_display[~df_display["Attribute Type"].isin(sel_attr_types)]
    if sel_on_forms:
        if "Yes" in sel_on_forms and "No" not in sel_on_forms:
            df_display = df_display[df_display["On Form(s)"] == "No"]
        elif "No" in sel_on_forms and "Yes" not in sel_on_forms:
            df_display = df_display[df_display["On Form(s)"] != "No"]
        # both selected → hide nothing for this filter
    if sel_managed:
        hide_true = "True" in sel_managed
        hide_false = "False" in sel_managed
        if hide_true and not hide_false:
            df_display = df_display[df_display["Is Managed"] != True]
        elif hide_false and not hide_true:
            df_display = df_display[df_display["Is Managed"] != False]
        # both selected → hide nothing
    if sel_scan_types:
        df_display = df_display[~df_display["Scan Type"].isin(sel_scan_types)]

    # Display Table — row selection enabled so users can pick fields for full scan
    table_event = st.dataframe(
        df_display,
        column_config={
            "Data Usage": st.column_config.ProgressColumn(
                "Data Usage (%)",
                help="Percentage of records where this field is not empty.",
                format="%.2f",
                min_value=0.0,
                max_value=1.0,
            ),
            "On Form(s)": st.column_config.TextColumn("On Form(s)"),
            "Scan Type": st.column_config.TextColumn(
                "Scan Type",
                help="'Sample' = estimated from up to 5,000 records; 'Full' = exact value from all records.",
            ),
        },
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key=f"ti_table_{lname}",
    )

    st.caption(calc_info)

    # --- Full Scan section — driven by row selection in the table above ---
    # selected_row_indices are positions within df_display (the filtered view).
    # Map them back to actual Logical Names via df_display's own rows.
    selected_row_indices = table_event.selection.rows if table_event and table_event.selection else []
    source_data = st.session_state.get(f"ti_usage_source_{lname}", {})
    rows_for_full = [
        df_display.iloc[i]["Logical Name"]
        for i in selected_row_indices
        if i < len(df_display) and source_data.get(df_display.iloc[i]["Logical Name"]) != "full"
    ]

    if usage_cache_key in st.session_state and selected_row_indices:
        st.divider()
        _sel_count = len(selected_row_indices)
        _full_count = _sel_count - len(rows_for_full)
        if rows_for_full:
            st.caption(
                f"{_sel_count} row(s) selected — {len(rows_for_full)} need full scan"
                + (f", {_full_count} already fully scanned." if _full_count else ".")
            )
            if st.button(f"🔬 Full Scan {len(rows_for_full)} Selected Field(s)",
                         key=f"ti_full_btn_{lname}"):
                _progress_text = "Running full scan on selected fields..."
                _progress_bar = st.progress(0, text=_progress_text)
                _st_ctx_sel = get_script_run_ctx()

                def _update_full_progress(records_seen, _total):
                    add_script_run_ctx(threading.current_thread(), _st_ctx_sel)
                    if _total:
                        frac = min(0.99, records_seen / _total)
                        label = f"{_progress_text} ({records_seen:,} / {_total:,} records)"
                    else:
                        import math
                        frac = min(0.95, 1 - math.exp(-records_seen / 10000))
                        label = f"{_progress_text} ({records_seen:,} records scanned)"
                    _progress_bar.progress(frac, text=label)

                with st.spinner("Paging through Dataverse..."):
                    full_stats, full_total, full_diag = client.get_usage_statistics_accurate(
                        lname,
                        selected_entity["EntitySetName"],
                        rows_for_full,
                        progress_callback=_update_full_progress,
                    )
                merged_usage = dict(st.session_state.get(usage_cache_key, {}))
                merged_usage.update(full_stats)
                st.session_state[usage_cache_key] = merged_usage
                merged_source = dict(st.session_state.get(f"ti_usage_source_{lname}", {}))
                for _a in rows_for_full:
                    merged_source[_a] = "full"
                st.session_state[f"ti_usage_source_{lname}"] = merged_source
                st.session_state[f"ti_full_total_{lname}"] = full_total
                st.session_state[f"ti_diag_{lname}"] = full_diag
                _progress_bar.empty()
                st.rerun()
        else:
            st.caption(f"{_sel_count} row(s) selected — all already fully scanned.")

    # Debug log
    diag_key = f"ti_diag_{lname}"
    if diag_key in st.session_state:
        with st.expander("🔍 Debug log (last usage scan)", expanded=False):
            st.code("\n".join(st.session_state[diag_key]), language="text")

    # Export to Excel
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Export results to CSV",
        data=csv,
        file_name=f"{selected_entity['LogicalName']}_inspector.csv",
        mime='text/csv',
    )
