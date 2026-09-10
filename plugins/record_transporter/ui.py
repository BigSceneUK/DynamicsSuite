import streamlit as st
import pandas as pd
import logging
from typing import List, Dict, Any, Tuple
from core.context import AppContext
from .client import RecordTransporterClient

# Custom styling for premium appearance
st.markdown(
    """
    <style>
    .stAlert {
        border-radius: 8px;
    }
    .status-card {
        background-color: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 15px;
    }
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        color: #1f77b4;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

def render_ui(ctx: AppContext) -> None:
    st.title("🔄 Record Transporter")
    st.markdown(
        "Copy records between Dataverse environments and dynamically resolve missing lookup dependencies."
    )

    if not ctx.org_url:
        st.warning("Please configure your source Dataverse URL during login or in the sidebar settings.")
        st.stop()

    # ------------------------------------------------------------------
    # State Initialization
    # ------------------------------------------------------------------
    if "rt_target_url" not in st.session_state:
        st.session_state["rt_target_url"] = ""
    if "rt_target_token" not in st.session_state:
        st.session_state["rt_target_token"] = ""
    if "rt_entities" not in st.session_state:
        st.session_state["rt_entities"] = []
    if "rt_attributes" not in st.session_state:
        st.session_state["rt_attributes"] = []
    if "rt_source_records" not in st.session_state:
        st.session_state["rt_source_records"] = []
    if "rt_missing_lookups" not in st.session_state:
        st.session_state["rt_missing_lookups"] = []
    if "rt_available_envs" not in st.session_state:
        st.session_state["rt_available_envs"] = []

    # Sync target URL from discovered list selection (must happen before st.text_input is instantiated)
    if st.session_state.get("rt_discovered_env_select"):
        st.session_state["rt_target_url"] = st.session_state["rt_discovered_env_select"]
        st.session_state["rt_discovered_env_select"] = None

    # Initialize source client
    try:
        source_token = ctx.auth.get_token(ctx.org_url)
        source_client = RecordTransporterClient(ctx.org_url, source_token)
    except Exception as exc:
        st.error(f"Failed to authenticate with source environment: {exc}")
        st.stop()

    # ------------------------------------------------------------------
    # Step 1: Environment Settings & Connection
    # ------------------------------------------------------------------
    st.subheader("🌍 Environment Connections")
    col1, col2 = st.columns(2)

    with col1:
        st.text_input("Source Environment URL", value=ctx.org_url, disabled=True)
        st.success("✅ Connected as Source")

    with col2:
        target_url = st.text_input(
            "Target Environment URL",
            key="rt_target_url",
            placeholder="https://myorg.crm.dynamics.com",
            help="Enter the destination Dataverse environment URL",
        )

        # Environment discovery helper
        if st.button("🔍 Discover Environments"):
            with st.spinner("Discovering environments..."):
                try:
                    bap_token = ctx.auth.get_token("https://api.bap.microsoft.com/")
                    envs = source_client.get_environments(bap_token)
                    st.session_state["rt_available_envs"] = envs
                    if envs:
                        st.success(f"Found {len(envs)} environments!")
                    else:
                        st.warning("No environments returned by Discovery API.")
                except Exception as exc:
                    st.warning(f"Could not discover environments: {exc}")

        if st.session_state["rt_available_envs"]:
            envs = st.session_state["rt_available_envs"]
            env_options = {
                e["properties"].get("linkedEnvironmentMetadata", {}).get("instanceUrl", "").rstrip("/"): 
                f"{e['properties'].get('displayName', 'Unnamed')} ({e['properties'].get('linkedEnvironmentMetadata', {}).get('instanceUrl', '')})"
                for e in envs if e["properties"].get("linkedEnvironmentMetadata")
            }
            selected_url = st.selectbox(
                "Select Target from Discovered List:",
                options=list(env_options.keys()),
                format_func=lambda x: env_options[x],
                index=None,
                key="rt_discovered_env_select"
            )
            # Rerun when selected to let the top synchronization block update the text input
            if selected_url:
                st.rerun()

        # Connect target environment
        if target_url:
            connected = st.session_state["rt_target_token"] != ""
            btn_label = "🔌 Reconnect Target" if connected else "🔌 Connect Target"
            if st.button(btn_label, type="primary"):
                with st.spinner("Connecting to target environment..."):
                    try:
                        # Interactive popup to request target token
                        target_token = ctx.auth.acquire_token_for_resource(target_url)
                        st.session_state["rt_target_token"] = target_token
                        st.success("Connected to target environment successfully!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to connect to target environment: {exc}")
                        st.session_state["rt_target_token"] = ""
        else:
            st.info("Enter target environment URL to start connection.")

    if not st.session_state["rt_target_token"] or not st.session_state["rt_target_url"]:
        st.stop()

    # Initialize target client
    target_client = RecordTransporterClient(
        st.session_state["rt_target_url"],
        st.session_state["rt_target_token"]
    )

    st.divider()

    # ------------------------------------------------------------------
    # Step 2: Entity & Attribute Selection
    # ------------------------------------------------------------------
    st.subheader("📦 Entity & Attribute Configuration")
    
    # Fetch source entities list once
    if not st.session_state["rt_entities"]:
        with st.spinner("Fetching entities..."):
            try:
                st.session_state["rt_entities"] = source_client.get_entities()
            except Exception as e:
                st.error(f"Failed to fetch entities: {e}")
                st.stop()

    entities = st.session_state["rt_entities"]
    entity_options = {e["LogicalName"]: f"{e['DisplayName']} ({e['LogicalName']})" for e in entities}
    
    selected_logical_name = st.selectbox(
        "Select Entity to Transport",
        options=list(entity_options.keys()),
        format_func=lambda x: entity_options[x],
        key="rt_selected_entity",
    )

    if not selected_logical_name:
        st.stop()

    entity_meta = next(e for e in entities if e["LogicalName"] == selected_logical_name)
    entity_set_name = entity_meta["EntitySetName"]
    pk_attr = entity_meta["PrimaryIdAttribute"]
    primary_name_attr = entity_meta["PrimaryNameAttribute"]

    # Fetch attributes for selected entity
    # We clear the cached attributes list if the entity changed
    if "rt_last_entity" not in st.session_state or st.session_state["rt_last_entity"] != selected_logical_name:
        st.session_state["rt_last_entity"] = selected_logical_name
        with st.spinner("Fetching attributes..."):
            try:
                st.session_state["rt_attributes"] = source_client.get_attributes(selected_logical_name)
                # Clear preview records and lookups
                st.session_state["rt_source_records"] = []
                st.session_state["rt_missing_lookups"] = []
            except Exception as e:
                st.error(f"Failed to fetch attributes: {e}")
                st.stop()

    attributes = st.session_state["rt_attributes"]

    col_attrs_sel, col_attrs_list = st.columns([1, 2])

    with col_attrs_sel:
        st.markdown("**Attribute Filters**")
        attr_search = st.text_input("Search attributes:", placeholder="Type to filter...")
        
        # Checkboxes for selecting all/unselecting all
        select_all = st.checkbox("Select / Unselect All Attributes", value=True)

    with col_attrs_list:
        with st.expander("Available Attributes List", expanded=True):
            # Display scrollable container with checkboxes
            selected_attrs = []
            filtered_attrs = attributes
            if attr_search:
                filtered_attrs = [
                    a for a in attributes 
                    if attr_search.lower() in a["DisplayName"].lower() or attr_search.lower() in a["LogicalName"].lower()
                ]

            for attr in filtered_attrs:
                # Pre-check standard/custom writable fields
                default_checked = select_all
                # Don't check system read-only fields by default
                if attr["LogicalName"] in ("createdon", "modifiedon", "versionnumber", "createdby", "modifiedby", "owninguser", "owningteam", "owningbusinessunit"):
                    default_checked = False

                checked = st.checkbox(
                    f"{attr['DisplayName']} ({attr['LogicalName']}) — {attr['AttributeType']}",
                    value=default_checked,
                    key=f"attr_chk_{attr['LogicalName']}"
                )
                if checked:
                    selected_attrs.append(attr["LogicalName"])

    st.markdown(f"Selected **{len(selected_attrs)}** attribute(s) to copy.")
    st.divider()

    # ------------------------------------------------------------------
    # Step 3: Record Retrieval & Selection
    # ------------------------------------------------------------------
    st.subheader("🔍 Retrieve & Select Records")
    
    retrieve_mode = st.radio("Retrieval Method", ["Quick Fetch (Top N)", "FetchXML Filter"], horizontal=True, key="rt_retrieve_mode")

    fetch_xml = None
    top_n = 50
    if retrieve_mode == "Quick Fetch (Top N)":
        top_n = st.number_input("Top N Records", min_value=1, max_value=5000, value=50, step=50, key="rt_top_n_input")
    else:
        fetch_xml = st.text_area(
            "FetchXML Query", 
            placeholder="<fetch>\n  <entity name=\"...\">\n    ...\n  </entity>\n</fetch>",
            height=200,
            help="Provide custom FetchXML query. Make sure the entity node matches the selected entity.",
            key="rt_fetch_xml_input"
        )

    if st.button("⚡ Retrieve Source Records"):
        with st.spinner("Retrieving records..."):
            try:
                st.session_state["rt_source_records"] = source_client.fetch_records(
                    selected_logical_name,
                    entity_set_name,
                    selected_attrs,
                    fetch_xml=fetch_xml,
                    top=top_n
                )
                st.session_state["rt_missing_lookups"] = []  # Reset lookups on fresh fetch
                if not st.session_state["rt_source_records"]:
                    st.warning("No records found in source environment.")
            except Exception as e:
                st.error(f"Failed to fetch records: {e}")

    source_records = st.session_state["rt_source_records"]
    if not source_records:
        st.stop()

    st.markdown(f"Found **{len(source_records)}** record(s). Select the ones you want to transfer:")
    
    # We construct a table editor with a checkbox selector
    preview_rows = []
    for rec in source_records:
        row = {
            "Select": True,
            "GUID": rec.get(pk_attr),
        }
        if primary_name_attr:
            row["Name"] = rec.get(primary_name_attr) or "(No Name)"
        
        # Add values of other key attributes for preview
        for attr in selected_attrs[:5]:  # Preview up to 5 attributes
            if attr != pk_attr and attr != primary_name_attr:
                val = rec.get(attr)
                if val is None:
                    val = rec.get(f"_{attr}_value")
                row[attr] = str(val) if val is not None else ""
        preview_rows.append(row)

    df_preview = pd.DataFrame(preview_rows)
    edited_df = st.data_editor(df_preview, width='stretch', hide_index=True, key="rt_record_editor")
    
    # Identify which records are selected
    selected_indices = edited_df.index[edited_df["Select"] == True].tolist()
    records_to_transfer = [source_records[i] for i in selected_indices]

    st.markdown(f"**{len(records_to_transfer)}** record(s) selected for transfer.")
    st.divider()

    # ------------------------------------------------------------------
    # Step 4: Missing Lookup Resolution
    # ------------------------------------------------------------------
    st.subheader("🔗 Dependency & Lookup Verification")

    if st.button("🔍 Check for Missing Lookup Records"):
        with st.spinner("Analyzing dependencies and querying target environment..."):
            try:
                # 1. Fetch lookup metadata from source
                lookup_metadata = source_client.get_lookup_metadata(selected_logical_name)
                
                # Build mapping of LogicalName -> EntitySetName
                entity_set_mapping = {e["LogicalName"]: e["EntitySetName"] for e in entities}
                primary_name_mapping = {e["LogicalName"]: e["PrimaryNameAttribute"] for e in entities}
                
                # 2. Extract referenced lookup IDs from selected records
                lookups_to_verify = []  # list of (target_entity_set, guid, target_entity, source_field)
                for rec in records_to_transfer:
                    for attr in selected_attrs:
                        if attr in lookup_metadata:
                            val = rec.get(f"_{attr}_value")
                            if val:
                                # Get target entity name
                                target_entity = rec.get(f"_{attr}_value@Microsoft.Dynamics.CRM.targetentityname")
                                if not target_entity:
                                    targets = lookup_metadata[attr].get("Targets", [])
                                    target_entity = targets[0] if targets else None
                                
                                if target_entity:
                                    target_set = entity_set_mapping.get(target_entity)
                                    if target_set:
                                        lookups_to_verify.append((target_set, val, target_entity, attr))

                # Deduplicate referenced lookups
                unique_lookups = list(set((s, g, e) for s, g, e, f in lookups_to_verify))
                
                # 3. Check target environment for existence of these records
                target_check_keys = [(s, g) for s, g, e in unique_lookups]
                existence_results = target_client.check_records_exist_in_target(target_check_keys)
                
                # 4. Filter to missing lookups and fetch their display names from source
                missing_lookups = []
                for set_name, guid, entity_name in unique_lookups:
                    # Ignore systemuser and organization since they are environment-specific and cannot be created/copied directly
                    if entity_name in ("systemuser", "organization"):
                        continue
                        
                    exists = existence_results.get((set_name, guid), False)
                    if not exists:
                        # Fetch display name from source environment
                        name_attr = primary_name_mapping.get(entity_name, "")
                        disp_name = source_client.fetch_record_display_name(set_name, guid, name_attr)
                        missing_lookups.append({
                            "Select": True,
                            "Entity": entity_name,
                            "EntitySetName": set_name,
                            "GUID": guid,
                            "Name": disp_name
                        })
                
                st.session_state["rt_missing_lookups"] = missing_lookups
                if not missing_lookups:
                    st.success("🎉 All referenced lookup records already exist in the target environment!")
                else:
                    st.warning(f"⚠️ Found {len(missing_lookups)} missing referenced lookup record(s) in target environment.")
            except Exception as e:
                st.error(f"Failed to check lookups: {e}")

    missing_lookups = st.session_state["rt_missing_lookups"]
    lookups_to_transfer = []
    if missing_lookups:
        df_missing = pd.DataFrame(missing_lookups)
        edited_missing_df = st.data_editor(
            df_missing, 
            width='stretch', 
            hide_index=True, 
            key="rt_missing_lookups_editor",
            column_config={
                "EntitySetName": None # Hide entity set name column
            }
        )
        
        # Filter selected missing lookups to transfer
        selected_lookup_indices = edited_missing_df.index[edited_missing_df["Select"] == True].tolist()
        lookups_to_transfer = [missing_lookups[i] for i in selected_lookup_indices]
        
        st.info(f"**{len(lookups_to_transfer)}** missing lookup record(s) will be created in target before the main records transfer.")

    st.divider()

    # ------------------------------------------------------------------
    # Step 5: Transfer Options & Execution
    # ------------------------------------------------------------------
    st.subheader("⚙️ Transfer Configuration & Run")
    
    c_opts1, c_opts2 = st.columns(2)
    with c_opts1:
        create_new = st.checkbox("Create Missing Records", value=True, help="Create records in target if they do not exist.")
        update_existing = st.checkbox("Update Existing Records", value=True, help="Update records in target if they already exist (matches on GUID).")
    with c_opts2:
        auto_map_bu = st.checkbox("Auto-Map Business Unit", value=True, help="Automatically map business unit lookups to target root business unit.")
        auto_map_currency = st.checkbox("Auto-Map Default Currency", value=True, help="Automatically map transactioncurrency lookups to target default currency.")
        auto_map_users = st.checkbox("Auto-Map System Users", value=True, help="Automatically map owner/createdby/modifiedby system users matching by email address.")

    if st.button("🚀 Start Transfer Operation", type="primary", disabled=len(records_to_transfer) == 0):
        # Execution logs container
        log_container = st.empty()
        status_bar = st.progress(0)
        logs = []

        def append_log(action: str, entity: str, name: str, status: str, detail: str):
            logs.append({
                "Action": action,
                "Entity": entity,
                "Name": name,
                "Status": status,
                "Detail": detail
            })
            # Update container with a scrollable report of the last 10 actions
            recent_logs = logs[-10:]
            log_text = "\n".join(f"[{l['Status']}] {l['Action']} {l['Entity']} '{l['Name']}': {l['Detail']}" for l in recent_logs)
            log_container.code(log_text)

        with st.spinner("Processing transfer..."):
            try:
                entity_set_mapping = {e["LogicalName"]: e["EntitySetName"] for e in entities}
                primary_name_mapping = {e["LogicalName"]: e["PrimaryNameAttribute"] for e in entities}
                
                # Pre-fetch root BU and base currency if auto-mapping enabled
                target_bu_id = target_client.get_root_business_unit() if auto_map_bu else None
                target_currency_id = target_client.get_base_currency() if auto_map_currency else None
                target_current_user_id = target_client.get_current_user_id() if auto_map_users else None
                target_org_id = target_client.get_organization_id()
                
                # Build target user email mapping if auto-mapping users enabled
                target_user_mapping = {}
                if auto_map_users:
                    # Find all unique user references in lookups to transfer and main records
                    user_guids = set()
                    
                    # 1. User references from missing lookups
                    for l in lookups_to_transfer:
                        if l["Entity"] == "systemuser":
                            user_guids.add(l["GUID"])
                            
                    # 2. User references from main records
                    lookup_metadata = source_client.get_lookup_metadata(selected_logical_name)
                    for rec in records_to_transfer:
                        for attr in selected_attrs:
                            if attr in lookup_metadata:
                                val = rec.get(f"_{attr}_value")
                                target_entity = rec.get(f"_{attr}_value@Microsoft.Dynamics.CRM.targetentityname")
                                if not target_entity:
                                    targets = lookup_metadata[attr].get("Targets", [])
                                    target_entity = targets[0] if targets else None
                                if val and target_entity == "systemuser":
                                    user_guids.add(val)
                                    
                    # Query user emails from source and resolve in target
                    for src_uid in user_guids:
                        email = source_client.get_system_user_email(src_uid)
                        if email:
                            tgt_uid = target_client.get_system_user_by_email(email)
                            if tgt_uid:
                                target_user_mapping[src_uid] = tgt_uid
                                append_log("Map User", "systemuser", email, "INFO", f"Mapped source {src_uid[:8]} -> target {tgt_uid[:8]}")
                            else:
                                append_log("Map User", "systemuser", email, "WARNING", f"User email '{email}' not found in target environment. Will default/skip.")

                # Total steps: transfer selected missing lookups + selected main records
                total_steps = len(lookups_to_transfer) + len(records_to_transfer)
                completed_steps = 0

                # A. Transfer missing lookup records first
                append_log("Start", "Dependency", "All", "INFO", f"Transferring {len(lookups_to_transfer)} missing dependencies...")
                for idx, lookup in enumerate(lookups_to_transfer):
                    entity_name = lookup["Entity"]
                    set_name = lookup["EntitySetName"]
                    guid = lookup["GUID"]
                    name = lookup["Name"]
                    
                    try:
                        # Fetch full missing record details from source
                        src_rec_details = source_client.fetch_record_details(set_name, guid)
                        
                        # We copy all fields from source record details
                        # Build a list of all fields except read-only system ones
                        lookup_attrs = list(src_rec_details.keys())
                        
                        # Fetch lookup metadata for this dependent entity to map its lookups
                        dep_lookup_meta = source_client.get_lookup_metadata(entity_name)
                        
                        # Construct payload
                        payload = {}
                        for k, v in src_rec_details.items():
                            if k.startswith("_") or k in (
                                "createdon", "modifiedon", "versionnumber", "createdby", "modifiedby", 
                                "owninguser", "owningteam", "owningbusinessunit", f"{entity_name}id"
                            ):
                                continue
                            
                            # Standard fields
                            if v is not None:
                                payload[k] = v
                                
                        # Handle lookups on the dependent record
                        for k in list(src_rec_details.keys()):
                            if k.startswith("_") and k.endswith("_value") and not k.endswith("@odata.bind"):
                                attr_name = k[1:-6] # extract field name from _field_value
                                val = src_rec_details[k]
                                if val:
                                    target_entity_type = src_rec_details.get(f"{k}@Microsoft.Dynamics.CRM.targetentityname")
                                    if not target_entity_type and attr_name in dep_lookup_meta:
                                        targets = dep_lookup_meta[attr_name].get("Targets", [])
                                        target_entity_type = targets[0] if targets else None
                                    
                                    if target_entity_type:
                                        target_set_name = entity_set_mapping.get(target_entity_type)
                                        
                                        # Resolve navigation property from ManyToOneRelationships
                                        nav_properties = dep_lookup_meta.get(attr_name, {}).get("NavigationProperties", {})
                                        nav_prop = nav_properties.get(target_entity_type)
                                        if not nav_prop:
                                            nav_prop = src_rec_details.get(f"{k}@Microsoft.Dynamics.CRM.associatednavigationproperty") or attr_name
                                            
                                        if target_set_name and nav_prop:
                                            # Apply mappings
                                            mapped_guid = val
                                            if auto_map_bu and target_entity_type == "businessunit" and target_bu_id:
                                                mapped_guid = target_bu_id
                                            elif auto_map_currency and target_entity_type == "transactioncurrency" and target_currency_id:
                                                mapped_guid = target_currency_id
                                            elif target_entity_type == "organization" and target_org_id:
                                                mapped_guid = target_org_id
                                            elif auto_map_users and target_entity_type == "systemuser":
                                                mapped_guid = target_user_mapping.get(mapped_guid, target_current_user_id or mapped_guid)
                                                
                                            payload[f"{nav_prop}@odata.bind"] = f"/{target_set_name}({mapped_guid})"

                        # Upsert dependent record
                        success, detail = target_client.upsert_record(
                            set_name, 
                            guid, 
                            payload, 
                            create_new=create_new, 
                            update_existing=update_existing
                        )
                        if success:
                            append_log("Copy Lookup", entity_name, name, "SUCCESS", "Created/Updated in target.")
                        else:
                            append_log("Copy Lookup", entity_name, name, "FAILED", f"Error: {detail}")
                            
                    except Exception as err:
                        append_log("Copy Lookup", entity_name, name, "ERROR", str(err))
                        
                    completed_steps += 1
                    status_bar.progress(completed_steps / total_steps)

                # B. Transfer main records
                append_log("Start", selected_logical_name, "All", "INFO", f"Transferring {len(records_to_transfer)} main records...")
                lookup_metadata = source_client.get_lookup_metadata(selected_logical_name)
                
                for idx, rec in enumerate(records_to_transfer):
                    guid = rec[pk_attr]
                    name = rec.get(primary_name_attr) or guid
                    
                    try:
                        # Fetch full record details from source to ensure we get all selected attributes
                        src_rec_details = source_client.fetch_record_details(entity_set_name, guid)
                        
                        # Build upsert payload
                        payload = {}
                        for attr in selected_attrs:
                            if attr == pk_attr:
                                continue
                            if attr in (
                                "createdon", "modifiedon", "versionnumber", "createdby", "modifiedby", 
                                "owninguser", "owningteam", "owningbusinessunit"
                            ):
                                continue
                            
                            is_lookup = attr in lookup_metadata
                            if is_lookup:
                                val = src_rec_details.get(f"_{attr}_value")
                                if val:
                                    target_entity_type = src_rec_details.get(f"_{attr}_value@Microsoft.Dynamics.CRM.targetentityname")
                                    if not target_entity_type:
                                        targets = lookup_metadata[attr].get("Targets", [])
                                        target_entity_type = targets[0] if targets else None
                                        
                                    if target_entity_type:
                                        target_set_name = entity_set_mapping.get(target_entity_type)
                                        
                                        # Resolve navigation property from ManyToOneRelationships
                                        nav_properties = lookup_metadata.get(attr, {}).get("NavigationProperties", {})
                                        nav_prop = nav_properties.get(target_entity_type)
                                        if not nav_prop:
                                            nav_prop = src_rec_details.get(f"_{attr}_value@Microsoft.Dynamics.CRM.associatednavigationproperty") or attr
                                            
                                        if target_set_name and nav_prop:
                                            # Apply mappings
                                            mapped_guid = val
                                            if auto_map_bu and target_entity_type == "businessunit" and target_bu_id:
                                                mapped_guid = target_bu_id
                                            elif auto_map_currency and target_entity_type == "transactioncurrency" and target_currency_id:
                                                mapped_guid = target_currency_id
                                            elif target_entity_type == "organization" and target_org_id:
                                                mapped_guid = target_org_id
                                            elif auto_map_users and target_entity_type == "systemuser":
                                                mapped_guid = target_user_mapping.get(mapped_guid, target_current_user_id or mapped_guid)
                                                
                                            payload[f"{nav_prop}@odata.bind"] = f"/{target_set_name}({mapped_guid})"
                            else:
                                val = src_rec_details.get(attr)
                                if val is not None:
                                    payload[attr] = val

                        # Log generated payload attributes for debugging
                        payload_keys = [k for k in payload.keys()]
                        append_log("Prepare Main", selected_logical_name, name, "INFO", f"Payload fields: {payload_keys}")

                        # Upsert main record
                        success, detail = target_client.upsert_record(
                            entity_set_name, 
                            guid, 
                            payload, 
                            create_new=create_new, 
                            update_existing=update_existing
                        )
                        if success:
                            append_log("Copy Main", selected_logical_name, name, "SUCCESS", "Created/Updated in target.")
                        else:
                            append_log("Copy Main", selected_logical_name, name, "FAILED", f"Error: {detail}")
                            
                    except Exception as err:
                        append_log("Copy Main", selected_logical_name, name, "ERROR", str(err))
                        
                    completed_steps += 1
                    status_bar.progress(completed_steps / total_steps)

                # Final completion log
                append_log("Finish", "All", "All", "INFO", "Transfer process completed.")
                
            except Exception as e:
                st.error(f"An unexpected error occurred during transfer: {e}")

        # Show final report
        st.success("Transfer Completed!")
        
        df_report = pd.DataFrame(logs)
        st.subheader("📊 Transfer Report")
        
        # Display summary stats
        col_success, col_failed, col_total = st.columns(3)
        success_count = len(df_report[df_report["Status"] == "SUCCESS"])
        failed_count = len(df_report[df_report["Status"].isin(["FAILED", "ERROR"])])
        
        with col_success:
            st.metric("Success Records", success_count)
        with col_failed:
            st.metric("Failed/Error Records", failed_count)
        with col_total:
            st.metric("Total Executed", len(df_report[df_report["Status"] != "INFO"]))
            
        st.dataframe(df_report, width='stretch', hide_index=True)
