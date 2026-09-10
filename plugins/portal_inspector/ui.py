"""
Portal Inspector UI — adapted from the standalone PortalInspector app.
Renders inside the DynamicsSuite shell; auth and org_url come from AppContext.
Session-state keys are prefixed with 'pi_' to avoid collisions with other plugins.
"""
from __future__ import annotations

from urllib.parse import unquote, urlparse

import pandas as pd
import streamlit as st

from core.context import AppContext
from plugins.portal_inspector.client import DataverseClient



_TABLE_ENTITY_NAMES: dict[str, str] = {
    "adx_entityforms": "adx_entityform",
    "adx_entityformmetadatas": "adx_entityformmetadata",
    "adx_webpages": "adx_webpage",
    "adx_webtemplates": "adx_webtemplate",
    "adx_contentsnippets": "adx_contentsnippet",
    "adx_contentsnippetlanguages": "adx_contentsnippetlanguage",
    "adx_entitylists": "adx_entitylist",
    "adx_webforms": "adx_webform",
    "adx_webformsteps": "adx_webformstep",
    "adx_webformstepmetadata": "adx_webformstepmetadata",
    "adx_weblinks": "adx_weblink",
    "adx_sitesettings": "adx_sitesetting",
    "adx_pagetemplates": "adx_pagetemplate",
    "mspp_entitylists": "mspp_entitylist",
    "mspp_webpages": "mspp_webpage",
    "mspp_webtemplates": "mspp_webtemplate",
    "mspp_contentsnippets": "mspp_contentsnippet",
    "mspp_entityforms": "mspp_entityform",
    "mspp_entityformmetadatas": "mspp_entityformmetadata",
    "mspp_webforms": "mspp_webform",
    "mspp_webformsteps": "mspp_webformstep",
    "mspp_sitesettings": "mspp_sitesetting",
    "mspp_pagetemplates": "mspp_pagetemplate",
    "systemforms": "systemform",
}

_TYPE_ICONS: dict[str, str] = {
    "webpage": "📄",
    "page_template": "📝",
    "web_template": "🧩",
    "entity_list": "📋",
    "entity_form": "📃",
    "web_form": "📑",
    "content_snippet": "✂️",
    "child_page": "🔗",
    "fetchxml_query": "🔍",
}
_TYPE_LABELS: dict[str, str] = {
    "webpage": "Web Page",
    "page_template": "Page Template",
    "web_template": "Web Template",
    "entity_list": "Entity List",
    "entity_form": "Basic Form",
    "web_form": "Multistep Form",
    "content_snippet": "Content Snippet",
    "child_page": "Child Page",
    "fetchxml_query": "FetchXML Data Query",
}


# ---------------------------------------------------------------------------
# Link helpers
# ---------------------------------------------------------------------------

def _pe_record_link(org_url: str, table_name: str, record_id: str) -> str:
    if not record_id or not org_url:
        return ""
    entity_name = _TABLE_ENTITY_NAMES.get(table_name, table_name)
    meta = st.session_state.get("pi_portal_app_meta", {})
    app_id = meta.get("portal_mgmt_app_id", "")
    if app_id:
        return f"{org_url}/main.aspx?appid={app_id}&pagetype=entityrecord&etn={entity_name}&id={record_id}"
    return f"{org_url}/main.aspx?pagetype=entityrecord&etn={entity_name}&id={record_id}"


def _crm_form_link(entity_name: str, form_id: str, solution_id: str = "") -> str:
    _DEFAULT_SOLUTION_ID = "00000001-0000-0000-0001-00000000009b"
    if not form_id or not entity_name:
        return ""
    meta = st.session_state.get("pi_portal_app_meta", {})
    env_id = meta.get("env_id", "")
    if not env_id:
        return ""
    sid = solution_id or _DEFAULT_SOLUTION_ID
    return (
        f"https://make.powerapps.com/e/{env_id}/s/{sid}"
        f"/entity/{entity_name}/form/edit/{form_id}?source=powerappsportal"
    )



def _parse_portal_url(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        segments = [s for s in unquote(parsed.path).split("/") if s]
        return segments[-1] if segments else ""
    return raw.strip("/")


# ---------------------------------------------------------------------------
# Page Structure Explorer rendering helpers
# ---------------------------------------------------------------------------

def _pe_table_badge(node: dict) -> str:
    t = node.get("_type", "")
    icon = _TYPE_ICONS.get(t, "🔷")
    label = _TYPE_LABELS.get(t, t.replace("_", " ").title())
    return f"{icon} {label}"


def _render_kv(pairs) -> None:
    rows = [(k, v) for k, v in pairs if v not in (None, "", [], {})]
    if rows:
        df = pd.DataFrame(rows, columns=["Field", "Value"])
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_pe_node(node: dict, org_url: str, indent: int = 0) -> None:
    if not isinstance(node, dict):
        return
    if node.get("error"):
        st.error(node["error"])
        return

    node_type = node.get("_type", "node")
    node_name = node.get("name") or node.get("partial_url") or node.get("_id", "")
    badge = _pe_table_badge(node)
    record_id = node.get("_id", "")
    table = node.get("_table", "")

    expander_label = f"{badge}: **{node_name}**"
    if record_id:
        expander_label += f"  `{record_id}`"

    with st.expander(expander_label, expanded=(indent == 0)):
        _render_kv([
            ("Type", _TYPE_LABELS.get(node_type, node_type)),
            ("Table", table),
            ("Record ID", record_id),
            ("Partial URL", node.get("partial_url", "")),
            ("Title", node.get("title", "")),
        ])

        if record_id and org_url:
            pm_link = _pe_record_link(org_url, table, record_id)
            st.markdown(f"[🔗 Open in Portal Management]({pm_link})")

        src = node.get("source_preview", "")
        if src:
            with st.expander("📄 Template Source (preview)", expanded=False):
                st.code(src, language="html")

        cpreview = node.get("content_preview", "")
        if cpreview:
            with st.expander("✂️ Content Value (preview)", expanded=False):
                st.code(cpreview, language="html")
        hrefs = node.get("hrefs", [])
        if hrefs:
            st.markdown("**URLs in snippet:**")
            for h in hrefs:
                st.code(h)

        pt = node.get("page_template")
        wt = node.get("web_template")
        if pt:
            pt_name = pt.get("name", "")
            st.markdown(f"#### 📝 Page Template: **{pt_name}**" if pt_name else "#### 📝 Page Template")
            _render_kv([
                ("Name", pt.get("name", "")),
                ("Record ID", pt.get("_id", "")),
                ("Type", pt.get("type", "")),
                ("Web Template", (wt.get("name") or wt.get("_id", "")) if wt else ""),
            ])

        if wt:
            st.markdown("#### 🧩 Web Template")
            render_pe_node(wt, org_url, indent + 1)

        mf = node.get("multistep_form")
        if mf:
            st.markdown("#### 📑 Multistep Form (page-level link)")
            render_pe_node(mf, org_url, indent + 1)

        bf = node.get("basic_form")
        if bf:
            st.markdown("#### 📃 Basic Form (page-level link)")
            render_pe_node(bf, org_url, indent + 1)

        el = node.get("entity_list")
        if el:
            st.markdown("#### 📋 Entity List (page-level link)")
            render_pe_node(el, org_url, indent + 1)

        buttons = node.get("action_buttons", {})
        if buttons:
            st.markdown("**Action Buttons:**")
            btn_rows = []
            for action, info in buttons.items():
                row: dict = {"Action": action.capitalize(), "Label": info.get("label", ""), "Enabled": "✅"}
                det = info.get("details", {})
                if det:
                    row.update({k.replace("_", " ").title(): v for k, v in det.items()})
                btn_rows.append(row)
            st.dataframe(pd.DataFrame(btn_rows), use_container_width=True, hide_index=True)

        columns = node.get("columns", [])
        if columns:
            st.markdown("**List Columns:**")
            st.dataframe(pd.DataFrame(columns), use_container_width=True, hide_index=True)

        options = node.get("options", [])
        if options:
            with st.expander(f"📌 List Options / View Actions ({len(options)})", expanded=True):
                for i, opt in enumerate(options):
                    if opt.get("enabled") is False:
                        label = opt.get("button_label") or opt.get("name") or f"Option {i + 1}"
                        st.caption(f"*(disabled)* {label}")
                        continue
                    label = opt.get("button_label") or opt.get("name") or f"Option {i + 1}"
                    with st.expander(f"**{label}**", expanded=True):
                        target = opt.get("target_type", "")
                        if target:
                            st.markdown(f"**Target Type:** {target}")
                        if opt.get("entity_form"):
                            st.markdown(f"**Basic Form:** {opt['entity_form']}")
                        if opt.get("webpage"):
                            st.markdown(f"**Redirect to Webpage:** {opt['webpage']}")
                        if opt.get("url"):
                            st.markdown(f"**URL:** `{opt['url']}`")
                        btn_label = opt.get("button_label", "")
                        if btn_label:
                            st.markdown(f"**Button Label:** {btn_label}")
                        fetchxml = opt.get("fetchxml", "")
                        if fetchxml:
                            with st.expander("Filter Criteria (FetchXML)", expanded=False):
                                st.code(fetchxml.strip(), language="xml")
                    if i < len(options) - 1:
                        st.divider()

        field_meta = node.get("field_metadata", [])
        if field_meta:
            st.markdown("**Field Metadata:**")
            st.dataframe(pd.DataFrame(field_meta), use_container_width=True, hide_index=True)

        steps = node.get("steps", [])
        if steps:
            st.markdown("**Form Steps:**")
            summary_cols = ["name", "type", "form", "tab", "table"]
            summary_rows = [{c: s.get(c, "") for c in summary_cols} for s in steps]
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            form_steps = [s for s in steps if s.get("crm_form_structure")]
            if form_steps:
                st.markdown("**Form Step Details:**")
                for step in form_steps:
                    crm_struct = step["crm_form_structure"]
                    step_name = step.get("name", "")
                    crm_entity = crm_struct.get("entity", "")
                    crm_form_name = crm_struct.get("form_name", "")
                    crm_form_id = crm_struct.get("form_id", "")
                    all_subgrids = [
                        sg for t in crm_struct.get("tabs", []) for sg in t.get("subgrids", [])
                    ]
                    header = f"🏛️ **{step_name}** — `{crm_entity}` / `{crm_form_name}`"
                    if all_subgrids:
                        header += f"  ·  {len(all_subgrids)} sub-grid(s)"
                    with st.expander(header, expanded=True):
                        if crm_form_id and org_url:
                            crm_link = _crm_form_link(crm_entity, crm_form_id, crm_struct.get("solution_id", ""))
                            if crm_link:
                                st.markdown(f"[🔗 Open in Power Apps maker portal]({crm_link})")
                        for tab in crm_struct.get("tabs", []):
                            tab_label = tab.get("label") or tab.get("tab_name", "")
                            tab_subgrids = tab.get("subgrids", [])
                            tab_header = f"📑 Tab: **{tab_label}**"
                            if tab_subgrids:
                                tab_header += f"  ·  {len(tab_subgrids)} sub-grid(s)"
                            with st.expander(tab_header, expanded=True):
                                for sec in tab.get("sections", []):
                                    sec_label = sec.get("label") or sec.get("section_name", "")
                                    sec_sgs = sec.get("subgrids", [])
                                    sec_fields = sec.get("fields", [])
                                    if sec_sgs:
                                        st.markdown(f"**Section:** {sec_label or '*(unnamed)*'}")
                                        for sg in sec_sgs:
                                            sg_title = sg.get("title") or sg.get("control_id", "")
                                            st.markdown(f"&nbsp;&nbsp;📋 **Sub-Grid:** `{sg_title}`")
                                            _render_kv([
                                                ("Target Entity", sg.get("target_entity", "")),
                                                ("Relationship", sg.get("relationship", "")),
                                                ("View ID", sg.get("view_id", "")),
                                            ])
                                    if sec_fields:
                                        with st.expander(
                                            f"Fields in section '{sec_label}' ({len(sec_fields)})",
                                            expanded=False,
                                        ):
                                            for f_name in sec_fields[:50]:
                                                st.caption(f"• `{f_name}`")

        step_meta_list = node.get("step_metadata", [])
        if step_meta_list:
            total_entries = sum(len(sm["metadata"]) for sm in step_meta_list)
            with st.expander(
                f"📎 Step Metadata Details ({total_entries} entries across {len(step_meta_list)} step(s))",
                expanded=True,
            ):
                for sm in step_meta_list:
                    st.markdown(f"**Step: {sm['step_name']}**")
                    rows = []
                    for m in sm["metadata"]:
                        row = {"Type": m.get("type", ""), "Attribute / Subgrid": m.get("attribute", "")}
                        if m.get("linked_form"):
                            row["Linked Basic Form"] = m["linked_form"]
                        rows.append(row)
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    st.divider()

        for k, label in [("entity", "Entity"), ("crm_form", "CRM Form"), ("mode", "Mode")]:
            v = node.get(k)
            if v:
                st.caption(f"**{label}:** {v}")

        crm_struct = node.get("crm_form_structure")
        if crm_struct:
            crm_entity = crm_struct.get("entity", "")
            crm_form_name = crm_struct.get("form_name", "")
            crm_form_id = crm_struct.get("form_id", "")
            portal_tab_name = node.get("portal_tab_name", "")
            st.markdown(f"#### 🏛️ CRM Form: `{crm_entity}` → `{crm_form_name}`")
            if portal_tab_name:
                st.caption(f"Portal Tab Name (internal): `{portal_tab_name}`")
            if crm_form_id and org_url:
                crm_link = _crm_form_link(crm_entity, crm_form_id, crm_struct.get("solution_id", ""))
                if crm_link:
                    st.markdown(f"[🔗 Open in Power Apps maker portal]({crm_link})")
            for tab in crm_struct.get("tabs", []):
                tab_label = tab.get("label") or tab.get("tab_name", "")
                tab_subgrids = tab.get("subgrids", [])
                tab_header = f"📑 Tab: **{tab_label}**"
                if tab_subgrids:
                    tab_header += f"  ·  {len(tab_subgrids)} sub-grid(s)"
                with st.expander(tab_header, expanded=True):
                    for sec in tab.get("sections", []):
                        sec_label = sec.get("label") or sec.get("section_name", "")
                        sec_sgs = sec.get("subgrids", [])
                        sec_fields = sec.get("fields", [])
                        if sec_label or sec_sgs:
                            st.markdown(f"**Section:** {sec_label or '*(unnamed)*'}")
                        for sg in sec_sgs:
                            sg_title = sg.get("title") or sg.get("control_id", "")
                            st.markdown(f"&nbsp;&nbsp;📋 **Sub-Grid:** `{sg_title}`")
                            _render_kv([
                                ("Target Entity", sg.get("target_entity", "")),
                                ("Relationship", sg.get("relationship", "")),
                                ("View ID", sg.get("view_id", "")),
                            ])
                        if sec_fields:
                            with st.expander(f"Fields in section ({len(sec_fields)})", expanded=False):
                                for f_name in sec_fields[:30]:
                                    st.caption(f"• `{f_name}`")

        lrefs = node.get("liquid_refs", {})
        if lrefs:
            st.markdown("#### Via Liquid references:")
            for list_node in lrefs.get("entity_lists", []):
                render_pe_node(list_node, org_url, indent + 1)
            for form_node in lrefs.get("entity_forms", []):
                render_pe_node(form_node, org_url, indent + 1)
            for wf_node in lrefs.get("web_forms", []):
                render_pe_node(wf_node, org_url, indent + 1)
            for inc_node in lrefs.get("includes", []):
                render_pe_node(inc_node, org_url, indent + 1)
            for snip_node in lrefs.get("snippets", []):
                render_pe_node(snip_node, org_url, indent + 1)

            fq_list = lrefs.get("fetch_queries", [])
            if fq_list:
                with st.expander(f"🔍 FetchXML Data Queries ({len(fq_list)})", expanded=True):
                    for fq in fq_list:
                        entity = fq.get("entity", "")
                        var_name = fq.get("var_name", "")
                        linked = fq.get("linked_entities", [])
                        attrs = fq.get("attributes", [])
                        order_by = fq.get("order_by", [])
                        filter_attrs = fq.get("filter_attrs", [])
                        usage_snippets = fq.get("usage_snippets", [])
                        xml_body = fq.get("xml_body", "")
                        linked_str = (
                            f"  *(+{len(linked)} linked: {', '.join(f'`{e}`' for e in linked[:3])})*"
                            if linked else ""
                        )
                        st.markdown(f"**`{var_name}`** → `{entity}`{linked_str}")
                        col1, col2 = st.columns(2)
                        with col1:
                            if attrs:
                                st.caption("**Columns selected:**")
                                st.markdown("  \n".join(f"• `{a}`" for a in attrs))
                            if order_by:
                                st.caption("**Order by:**")
                                st.markdown("  \n".join(f"• `{o}`" for o in order_by))
                        with col2:
                            if filter_attrs:
                                st.caption("**Filtered on:**")
                                st.markdown("  \n".join(f"• `{f}`" for f in filter_attrs))
                            if usage_snippets:
                                st.caption("**Used in template as:**")
                                for snip in usage_snippets:
                                    st.code(snip, language="liquid")
                        if xml_body:
                            with st.expander("View raw FetchXML", expanded=False):
                                st.code(xml_body, language="xml")
                        st.divider()

        children = node.get("child_pages", [])
        if children:
            st.markdown("#### 🔗 Child Pages:")
            for ch in children:
                render_pe_node(ch, org_url, indent + 1)


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render_page(ctx: AppContext, client: DataverseClient) -> None:
    org_url = ctx.org_url

    # --- Init session state ---
    for key, default in [
        ("pi_pe_result", None),
        ("pi_pe_partial_url_last", ""),
        ("pi_pe_all_pages", None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # Fetch portal app metadata once per connected org
    if (
        "pi_portal_app_meta" not in st.session_state
        or st.session_state.get("pi_portal_app_meta_org") != org_url
    ):
        st.session_state["pi_portal_app_meta"] = client.get_portal_app_meta()
        st.session_state["pi_portal_app_meta_org"] = org_url

    st.title("🌐 Power Pages Portal Inspector")
    st.markdown(
        "Explore component hierarchies, forms, and templates in Power Pages."
    )
    st.info(f"🔗 Connected to: `{org_url}`")


    # =======================================================================
    # PAGE STRUCTURE EXPLORER
    # =======================================================================
    st.header("🗂️ Page Structure Explorer")
    st.markdown(
        "Paste a **full portal URL** or just the **Partial URL** segment to explore the "
        "component hierarchy of that page.  "
        "The query string (e.g. `?ReturnUrl=…`) is ignored automatically."
    )

    col_pe_url, col_pe_depth, col_pe_btn = st.columns([3, 1, 1])
    with col_pe_url:
        pe_raw_input = st.text_input(
            "Portal URL or Partial URL",
            placeholder="e.g. https://…/SignIn?ReturnUrl=…  or just  SignIn",
            key="pi_pe_partial_url",
        ).strip()
        pe_partial_url = _parse_portal_url(pe_raw_input)
        if pe_raw_input and pe_partial_url:
            st.caption(f"Partial URL extracted: `{pe_partial_url}`")
    with col_pe_depth:
        pe_depth = st.number_input(
            "Depth (1 – 6)",
            min_value=1,
            max_value=6,
            value=2,
            step=1,
            key="pi_pe_depth",
            help=(
                "1 = page + template names only\n"
                "2 = Liquid components (lists, forms, snippets) resolved\n"
                "    + CRM form tab/section/sub-grid structure\n"
                "3 = columns, action buttons, field metadata\n"
                "4+ = deep detail: button URLs, step config, snippet HTML"
            ),
        )
    with col_pe_btn:
        st.write("")
        pe_analyse = st.button("Analyse", use_container_width=True, key="pi_pe_analyse_btn")

    if pe_analyse:
        if not pe_partial_url:
            st.warning("Please enter a portal URL or Partial URL.")
        else:
            with st.spinner(f"Analysing '{pe_partial_url}' to depth {pe_depth}…"):
                try:
                    pe_result = client.get_page_structure(pe_partial_url, max_depth=int(pe_depth))
                    st.session_state["pi_pe_result"] = pe_result
                    st.session_state["pi_pe_partial_url_last"] = pe_partial_url
                except Exception as exc:
                    st.error(f"Analysis failed: {exc}")
                    st.session_state["pi_pe_result"] = None

    if st.button("Browse available pages", key="pi_pe_browse_btn"):
        with st.spinner("Fetching all webpages…"):
            try:
                all_pages = client.list_all_webpages()
                st.session_state["pi_pe_all_pages"] = all_pages
            except Exception as exc:
                st.error(f"Could not fetch page list: {exc}")

    if st.session_state.get("pi_pe_all_pages"):
        all_pages = st.session_state["pi_pe_all_pages"]
        with st.expander(f"📚 All available pages ({len(all_pages)} records)", expanded=True):
            st.caption(
                "⚠️ System pages like **Sign In**, **Register**, and **Profile** are "
                "built into the Power Pages runtime and are **not** stored as Dataverse "
                "records — they will never appear here or in the explorer."
            )
            browse_df = (
                pd.DataFrame([
                    {"Partial URL": p["partial_url"], "Page Name": p["name"], "Schema": p["schema"]}
                    for p in all_pages
                ])
                .drop_duplicates(subset=["Partial URL"])
                .sort_values("Partial URL")
            )
            st.dataframe(browse_df, use_container_width=True, hide_index=True)

    if st.session_state.get("pi_pe_result"):
        pe_result = st.session_state["pi_pe_result"]
        last_url = st.session_state.get("pi_pe_partial_url_last", "")

        if pe_result.get("_type") == "system_page":
            st.subheader(f"⚙️ System Page: {pe_result['name']}")
            st.info(
                "This is a Power Pages **built-in system page** — it has no custom Dataverse "
                "webpage record.  Its visible text and labels are customised through "
                "**Content Snippets** and its behaviour through **Site Settings**, both shown below."
            )
            snippets = pe_result.get("content_snippets", [])
            settings = pe_result.get("site_settings", [])

            st.markdown(f"### ✂️ Content Snippets ({len(snippets)})")
            if snippets:
                for snip in snippets:
                    with st.expander(f"✂️ `{snip['name']}`", expanded=False):
                        col_meta, col_val = st.columns([1, 2])
                        with col_meta:
                            _render_kv([
                                ("Record ID", snip.get("_id", "")),
                                ("Schema / Table", snip.get("schema", "")),
                            ])
                            rid = snip.get("_id", "")
                            table = snip.get("schema", "")
                            if org_url and table and rid:
                                st.markdown(
                                    f"[🔗 Open in Portal Management]({_pe_record_link(org_url, table, rid)})"
                                )
                        with col_val:
                            full_val = snip.get("value", "")
                            if full_val:
                                st.markdown("**Value:**")
                                st.code(full_val, language="html")
                            else:
                                st.caption("*(empty)*")
                        snip_hrefs = snip.get("hrefs", [])
                        if snip_hrefs:
                            st.markdown("**URLs inside this snippet:**")
                            for h in snip_hrefs:
                                st.code(h)
            else:
                st.info("No content snippets found for this system page.")

            st.markdown(f"### ⚙️ Site Settings ({len(settings)})")
            if settings:
                st.dataframe(
                    pd.DataFrame([
                        {"Name": s["name"], "Value": s["value"], "Schema": s["schema"]}
                        for s in settings
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No site settings found for this system page.")

        elif pe_result.get("error"):
            st.error(pe_result["error"])
            st.info(
                "💡 **Tip:** Pages like *Sign In*, *Register*, *Redeem Invitation*, and "
                "*Profile* are Power Pages **system pages** — try entering them directly "
                "(e.g. `SignIn`, `Register`) to explore their content snippets and settings."
            )
            suggestions = pe_result.get("suggestions", [])
            if suggestions:
                st.markdown("**Similar pages found:**")
                sug_df = pd.DataFrame([
                    {"Partial URL": p["partial_url"], "Page Name": p["name"]}
                    for p in suggestions
                ])
                st.dataframe(sug_df, use_container_width=True, hide_index=True)
            elif pe_result.get("all_pages"):
                st.session_state["pi_pe_all_pages"] = pe_result["all_pages"]
                st.rerun()
        else:
            st.subheader(f"Structure: `/{last_url}/`")
            render_pe_node(pe_result, org_url, indent=0)


