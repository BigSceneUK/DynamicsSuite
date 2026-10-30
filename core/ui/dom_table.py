"""
DOM-native table components for DynamicsSuite.

Replaces Streamlit's canvas-based Glide Data Grid (`st.dataframe` and `st.data_editor`)
with native DOM elements to resolve pointer event misalignment, dropped click events,
broken column sorting, and widget reconciliation conflicts on macOS/Retina displays.

Features:
- Native DOM rendering with Streamlit columns and HTML flexbox
- Interactive column sorting (numerical, alphabetical, chronological)
- Fast search and multi-column filtering
- Responsive pagination (20, 50, 100, 200, All)
- Row selection checkboxes with direct session_state synchronization
- Special column renderers (Progress bars, links, status badges, code/GUIDs)
"""

from __future__ import annotations

import html
from typing import Any, Callable
import pandas as pd
import streamlit as st


def _make_sort_key(val: Any, descending: bool = False) -> tuple:
    """Generate a 3-tuple sort key that safely compares mixed types with empty values last."""
    if val is None or val == "":
        return (-1 if descending else 2, 0.0, "")

    # Numeric types
    if isinstance(val, (int, float)):
        return (0, float(val), "")

    val_str = str(val).strip()
    # Try cleaning common formatted numbers (e.g. '1,234.50' or '95.5%')
    cleaned = val_str.replace(",", "").rstrip("%")
    try:
        num = float(cleaned)
        return (0, num, "")
    except ValueError:
        return (1, 0.0, val_str.lower())


def render_dom_table(
    data: pd.DataFrame | list[dict[str, Any]],
    key_prefix: str,
    columns: list[str | tuple[str, str] | tuple[str, str, float] | dict[str, Any]] | None = None,
    id_key: str | None = None,
    selectable: bool = False,
    default_select_all: bool = False,
    search_columns: list[str] | None = None,
    default_sort_col: str | None = None,
    default_sort_desc: bool = False,
    page_size_options: list[int | str] | None = None,
    empty_message: str = "No records to display.",
    column_config: dict[str, dict[str, Any] | str] | None = None,
    formatters: dict[str, Callable[[Any], str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Renders a DOM-native table with column sorting, search filtering, and pagination.

    Args:
        data: DataFrame or list of dicts.
        key_prefix: Unique string prefix for Streamlit state.
        columns: List of column definitions:
            - str: field name (label = field name, ratio = 1.0)
            - tuple: (field_name, display_label) or (field_name, display_label, flex_ratio)
            - dict: {"field": str, "label": str, "width": float}
        id_key: Key in each record that uniquely identifies it. If omitted, uses index.
        selectable: Whether to include a selection checkbox column.
        default_select_all: Initial selection state for rows on first load.
        search_columns: Fields to search. Defaults to all visible columns.
        default_sort_col: Initial column to sort by.
        default_sort_desc: Whether default sort is descending.
        page_size_options: Rows per page options (default: [20, 50, 100, 'All']).
        empty_message: Message to show when data is empty.
        column_config: Special display config per column:
            - {"type": "progress", "min": 0.0, "max": 1.0, "format": "%.1f%%"}
            - {"type": "link", "text": "View"}
            - {"type": "badge"}
            - {"type": "code"}
        formatters: Custom formatting functions mapping field -> formatted string.

    Returns:
        If selectable=True, returns list of selected item dicts.
        If selectable=False, returns list of currently filtered and sorted item dicts.
    """
    # Convert DataFrame to records list if necessary
    if isinstance(data, pd.DataFrame):
        if data.empty:
            items: list[dict[str, Any]] = []
        else:
            items = data.to_dict("records")
        default_cols = list(data.columns)
    else:
        items = list(data or [])
        default_cols = list(items[0].keys()) if items else []

    # Auto-generate columns if not explicitly provided
    if not columns:
        columns = [c for c in default_cols if not c.startswith("_")]

    # Normalize column definitions to (field_name, display_label, flex_ratio)
    norm_columns: list[tuple[str, str, float]] = []
    for col in columns:
        if isinstance(col, str):
            norm_columns.append((col, col, 1.0))
        elif isinstance(col, dict):
            norm_columns.append((
                col.get("field", ""),
                col.get("label", col.get("field", "")),
                float(col.get("width", 1.0)),
            ))
        elif isinstance(col, (list, tuple)):
            norm_columns.append((
                str(col[0]),
                str(col[1]) if len(col) > 1 else str(col[0]),
                float(col[2]) if len(col) > 2 else 1.0,
            ))

    col_fields = [c[0] for c in norm_columns]

    # Ensure id_key
    if not id_key:
        id_key = "_dom_row_id"
        for idx, it in enumerate(items):
            if "_dom_row_id" not in it:
                it["_dom_row_id"] = str(idx)

    # State keys
    selected_set_key = f"{key_prefix}_selected_ids"
    fp_key = f"{key_prefix}_items_fingerprint"
    page_state_key = f"{key_prefix}_current_page"
    search_state_key = f"{key_prefix}_search"
    sort_col_key = f"{key_prefix}_sort_col"
    sort_dir_key = f"{key_prefix}_sort_dir"

    current_ids = [str(item.get(id_key)) for item in items if item.get(id_key) is not None]

    if selected_set_key not in st.session_state or st.session_state.get(fp_key) != current_ids:
        st.session_state[fp_key] = current_ids
        st.session_state[selected_set_key] = set(current_ids) if default_select_all else set()
        st.session_state[page_state_key] = 1

    if sort_col_key not in st.session_state:
        st.session_state[sort_col_key] = default_sort_col or "(None)"
    if sort_dir_key not in st.session_state:
        st.session_state[sort_dir_key] = "Descending ▼" if default_sort_desc else "Ascending ▲"

    if not items:
        with st.container(border=True):
            st.info(empty_message)
        return []

    # ------------------------------------------------------------------
    # Top Controls Bar: Search, Column Sorting, Page Size, Selection
    # ------------------------------------------------------------------
    ctrl_cols = st.columns([2.5, 2.0, 1.3, 1.2], vertical_alignment="center")

    with ctrl_cols[0]:
        search_text = st.text_input(
            "Filter records",
            placeholder="🔍 Filter rows...",
            key=search_state_key,
            label_visibility="collapsed",
        ).strip().lower()

    with ctrl_cols[1]:
        sort_options = ["(None)"] + [col[1] for col in norm_columns]
        cur_sort_label = st.session_state.get(sort_col_key, "(None)")
        if cur_sort_label not in sort_options:
            cur_sort_label = "(None)"
        sort_choice = st.selectbox(
            "Sort by",
            options=sort_options,
            index=sort_options.index(cur_sort_label),
            key=sort_col_key,
            label_visibility="collapsed",
        )

    with ctrl_cols[2]:
        sort_dir_choice = st.selectbox(
            "Order",
            options=["Ascending ▲", "Descending ▼"],
            index=0 if "Asc" in st.session_state.get(sort_dir_key, "Asc") else 1,
            key=sort_dir_key,
            label_visibility="collapsed",
        )

    with ctrl_cols[3]:
        page_sizes = page_size_options or [20, 50, 100, 200, "All"]
        page_size_choice = st.selectbox(
            "Rows per page",
            options=page_sizes,
            index=0,
            key=f"{key_prefix}_page_size",
            label_visibility="collapsed",
        )

    # 1. Filter Items
    if search_text:
        target_fields = search_columns or col_fields
        filtered_items = [
            item for item in items
            if any(search_text in str(item.get(f, "") or "").lower() for f in target_fields)
        ]
    else:
        filtered_items = list(items)

    # 2. Sort Items
    active_sort_field = None
    if sort_choice and sort_choice != "(None)":
        for col_f, col_l, _ in norm_columns:
            if col_l == sort_choice:
                active_sort_field = col_f
                break

    is_descending = "Desc" in sort_dir_choice
    if active_sort_field:
        filtered_items.sort(
            key=lambda x: _make_sort_key(x.get(active_sort_field), is_descending),
            reverse=is_descending,
        )

    # 3. Action Buttons (Select All / Clear) if selectable
    if selectable:
        action_col1, action_col2, _ = st.columns([1.5, 1.5, 5], vertical_alignment="center")
        with action_col1:
            sel_btn_lbl = f"Select All ({len(filtered_items)})" if search_text else "Select All"
            if st.button(sel_btn_lbl, key=f"{key_prefix}_btn_sel_all", use_container_width=True):
                for it in filtered_items:
                    iid = str(it.get(id_key))
                    st.session_state[selected_set_key].add(iid)
                    st.session_state[f"{key_prefix}_chk_{iid}"] = True
                st.rerun()
        with action_col2:
            if st.button("Clear", key=f"{key_prefix}_btn_clr_all", use_container_width=True):
                for it in filtered_items:
                    iid = str(it.get(id_key))
                    st.session_state[selected_set_key].discard(iid)
                    st.session_state[f"{key_prefix}_chk_{iid}"] = False
                st.rerun()

    # 4. Pagination Window
    total_items = len(filtered_items)
    if page_size_choice == "All":
        page_size = max(1, total_items)
    else:
        page_size = int(page_size_choice)

    total_pages = max(1, (total_items + page_size - 1) // page_size)
    current_page = st.session_state.get(page_state_key, 1)
    if current_page > total_pages:
        current_page = total_pages
        st.session_state[page_state_key] = current_page
    if current_page < 1:
        current_page = 1
        st.session_state[page_state_key] = current_page

    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, total_items)
    page_items = filtered_items[start_idx:end_idx]

    # Calculate column ratios
    chk_ratio = 0.5
    if selectable:
        col_weights = [chk_ratio] + [col[2] for col in norm_columns]
    else:
        col_weights = [col[2] for col in norm_columns]

    # ------------------------------------------------------------------
    # Main Table Container
    # ------------------------------------------------------------------
    with st.container(border=True):
        # Header bar
        header_html = (
            "<div style='display:flex; align-items:center; background-color: rgba(128, 128, 128, 0.08); "
            "border-radius: 6px; padding: 8px 12px; font-weight: 600; font-size: 0.82rem; "
            "letter-spacing: 0.02em; border-bottom: 1px solid rgba(128, 128, 128, 0.18); "
            "margin-bottom: 6px; gap: 12px;'>"
        )
        if selectable:
            header_html += f"<div style='flex: {chk_ratio}; min-width: 0; text-align: center; color: #64748b;'>✓</div>"

        for col_field, col_label, col_ratio in norm_columns:
            sort_indicator = ""
            if col_field == active_sort_field:
                sort_arrow = "▼" if is_descending else "▲"
                sort_indicator = f" <span style='color: #3b82f6;'>{sort_arrow}</span>"
            header_html += (
                f"<div style='flex: {col_ratio}; min-width: 0; overflow: hidden; "
                f"text-overflow: ellipsis; white-space: nowrap;'>"
                f"{html.escape(str(col_label))}{sort_indicator}</div>"
            )
        header_html += "</div>"
        st.markdown(header_html, unsafe_allow_html=True)

        if not filtered_items:
            st.markdown(
                "<div style='text-align: center; padding: 24px 0; color: #94a3b8; font-style: italic; font-size: 0.88rem;'>"
                "No records match the current filter.</div>",
                unsafe_allow_html=True,
            )
        else:
            def _on_toggle(target_id: str, widget_key: str):
                val = st.session_state.get(widget_key, False)
                if val:
                    st.session_state[selected_set_key].add(target_id)
                else:
                    st.session_state[selected_set_key].discard(target_id)

            for row_idx, item in enumerate(page_items):
                if row_idx > 0:
                    st.markdown(
                        "<div style='border-bottom: 1px solid rgba(128, 128, 128, 0.10); margin: 2px 0;'></div>",
                        unsafe_allow_html=True,
                    )

                iid = str(item.get(id_key))
                row_cols = st.columns(col_weights, vertical_alignment="center")

                offset = 0
                if selectable:
                    offset = 1
                    chk_key = f"{key_prefix}_chk_{iid}"
                    expected_checked = iid in st.session_state[selected_set_key]
                    if chk_key not in st.session_state or st.session_state[chk_key] != expected_checked:
                        st.session_state[chk_key] = expected_checked

                    with row_cols[0]:
                        st.checkbox(
                            "",
                            key=chk_key,
                            on_change=_on_toggle,
                            args=(iid, chk_key),
                            label_visibility="collapsed",
                        )

                for col_idx, (col_field, _, _) in enumerate(norm_columns):
                    with row_cols[col_idx + offset]:
                        raw_val = item.get(col_field)
                        cfg = (column_config or {}).get(col_field)

                        # Formatter function override
                        if formatters and col_field in formatters:
                            cell_str = formatters[col_field](raw_val)
                            st.markdown(
                                f"<span style='font-size: 0.85rem;' title='{html.escape(cell_str)}'>{html.escape(cell_str)}</span>",
                                unsafe_allow_html=True,
                            )
                            continue

                        # Special Progress Bar Column
                        if isinstance(cfg, dict) and cfg.get("type") == "progress":
                            try:
                                num_val = float(raw_val or 0.0)
                                max_v = float(cfg.get("max", 1.0))
                                pct = max(0.0, min(100.0, (num_val / max_v) * 100.0))
                                fmt = cfg.get("format", "%.1f%%")
                                label_txt = fmt % (pct if "%" in fmt else num_val)
                                bar_html = (
                                    f"<div style='display:flex; align-items:center; gap:8px; width:100%;'>"
                                    f"<div style='flex:1; background:rgba(128,128,128,0.2); height:6px; border-radius:3px; overflow:hidden;'>"
                                    f"<div style='width:{pct}%; background:#3b82f6; height:100%;'></div>"
                                    f"</div>"
                                    f"<span style='font-size:0.75rem; color:#64748b; font-variant-numeric:tabular-nums; width:45px; text-align:right;'>{label_txt}</span>"
                                    f"</div>"
                                )
                                st.markdown(bar_html, unsafe_allow_html=True)
                            except Exception:
                                st.write(str(raw_val))
                            continue

                        # Special Badge Column
                        if isinstance(cfg, dict) and cfg.get("type") == "badge":
                            val_str = str(raw_val or "")
                            badge_color = "#3b82f6"
                            badge_bg = "rgba(59, 130, 246, 0.12)"
                            if val_str.lower() in ("yes", "full", "active", "true", "success"):
                                badge_color = "#16a34a"
                                badge_bg = "rgba(22, 163, 74, 0.12)"
                            elif val_str.lower() in ("no", "sample", "inactive", "false", "failed"):
                                badge_color = "#d97706"
                                badge_bg = "rgba(217, 119, 6, 0.12)"

                            st.markdown(
                                f"<span style='font-size:0.75rem; font-weight:600; padding:2px 7px; border-radius:4px; "
                                f"background:{badge_bg}; color:{badge_color};'>{html.escape(val_str)}</span>",
                                unsafe_allow_html=True,
                            )
                            continue

                        # Default text/string/code rendering
                        cell_str = "" if raw_val is None else str(raw_val)
                        if not cell_str:
                            st.markdown(
                                "<span style='color: #94a3b8; font-style: italic; font-size: 0.82rem;'>—</span>",
                                unsafe_allow_html=True,
                            )
                        elif len(cell_str) == 36 and cell_str.count("-") == 4:
                            st.markdown(
                                f"<code style='font-size: 0.76rem; background: rgba(128, 128, 128, 0.08); "
                                f"padding: 2px 5px; border-radius: 4px;'>{html.escape(cell_str)}</code>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                f"<span style='font-size: 0.85rem; line-height: 1.4;' "
                                f"title='{html.escape(cell_str)}'>{html.escape(cell_str)}</span>",
                                unsafe_allow_html=True,
                            )

        # Pagination Navigation Bar
        if total_items > 0:
            st.markdown(
                "<div style='border-top: 1px solid rgba(128, 128, 128, 0.15); margin: 8px 0 6px 0;'></div>",
                unsafe_allow_html=True,
            )
            nav_prev, nav_info, nav_next = st.columns([1, 2.5, 1], vertical_alignment="center")
            with nav_prev:
                if st.button("◀ Previous", key=f"{key_prefix}_btn_prev", disabled=(current_page <= 1), use_container_width=True):
                    st.session_state[page_state_key] = current_page - 1
                    st.rerun()

            with nav_info:
                if selectable:
                    selected_count = len(st.session_state[selected_set_key].intersection({str(it.get(id_key)) for it in items}))
                    sel_text = f" &nbsp;|&nbsp; <b>{selected_count}</b> selected"
                else:
                    sel_text = ""
                st.markdown(
                    f"<div style='text-align: center; font-size: 0.83rem; color: #64748b;'>"
                    f"Page <b>{current_page}</b> of <b>{total_pages}</b> &nbsp;|&nbsp; "
                    f"Showing <b>{start_idx + 1}–{end_idx}</b> of <b>{total_items}</b>"
                    f"{sel_text}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with nav_next:
                if st.button("Next ▶", key=f"{key_prefix}_btn_next", disabled=(current_page >= total_pages), use_container_width=True):
                    st.session_state[page_state_key] = current_page + 1
                    st.rerun()

    if selectable:
        active_selected = st.session_state[selected_set_key]
        return [item for item in items if str(item.get(id_key)) in active_selected]
    return filtered_items


def render_dom_selectable_table(
    items: list[dict[str, Any]],
    id_key: str,
    columns: list[tuple[str, str, float] | dict[str, Any]],
    key_prefix: str,
    default_select_all: bool = True,
    search_columns: list[str] | None = None,
    default_sort_col: str | None = None,
    default_sort_desc: bool = False,
    page_size_options: list[int | str] | None = None,
    empty_message: str = "No records to display.",
    formatters: dict[str, Callable[[Any], str]] | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper for render_dom_table with selectable=True."""
    return render_dom_table(
        data=items,
        key_prefix=key_prefix,
        columns=columns,
        id_key=id_key,
        selectable=True,
        default_select_all=default_select_all,
        search_columns=search_columns,
        default_sort_col=default_sort_col,
        default_sort_desc=default_sort_desc,
        page_size_options=page_size_options,
        empty_message=empty_message,
        formatters=formatters,
    )
