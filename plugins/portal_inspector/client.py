from __future__ import annotations

import html as _html
import json as _json
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests


class DataverseClient:
    """
    Dataverse REST client for the Portal Inspector plugin.
    Covers Power Pages metadata search, page structure exploration,
    and content update operations.
    """

    def __init__(self, org_url: str, token: str) -> None:
        self.org_url = org_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }

    # ------------------------------------------------------------------
    # Portal metadata helpers
    # ------------------------------------------------------------------

    def get_portal_app_meta(self) -> dict:
        """
        Fetch the Portal Management model-driven app GUID and, if possible,
        the Power Platform environment ID.  Used to build deep-links.
        Results are cached on the instance.
        """
        if hasattr(self, "_portal_app_meta"):
            return self._portal_app_meta
        meta: dict = {"portal_mgmt_app_id": "", "env_id": ""}

        data = self._odata_get(
            "appmodules?$filter=contains(name,'Portal Management')"
            "&$select=appmoduleid,name,uniquename&$top=5"
        )
        if data and data.get("value"):
            apps = data["value"]
            exact = next(
                (a for a in apps if a.get("name", "").lower() == "portal management"), None
            )
            meta["portal_mgmt_app_id"] = (exact or apps[0]).get("appmoduleid", "")

        # Resolve the env_id from the Global Discovery Service.
        # Requires a separate token scope — falls back to empty string gracefully.
        orgs = self._odata_get("organizations?$select=organizationid&$top=1")
        org_id = (orgs or {}).get("value", [{}])[0].get("organizationid", "") if orgs else ""
        env_id = self._resolve_env_id_from_discovery(org_id)
        meta["env_id"] = env_id or org_id

        self._portal_app_meta = meta
        return meta

    def _resolve_env_id_from_discovery(self, org_id: str) -> str:
        """
        Attempt to map a Dataverse organizationid to the Power Platform environment ID
        via the Global Discovery Service.  Returns '' on any failure.
        NOTE: CLI token acquisition (subprocess) is intentionally excluded.
        """
        return ""

    def get_solution_id_for_form(self, form_id: str) -> str:
        """
        Return the GUID of the unmanaged solution that owns the given systemform.
        Returns '' if not found.
        """
        if not form_id:
            return ""
        data = self._odata_get(
            f"solutioncomponents?$filter=objectid eq {form_id} and componenttype eq 24"
            f"&$select=_solutionid_value,ismanaged&$top=10"
        )
        if not (data and data.get("value")):
            return ""
        rows = data["value"]
        unmanaged = [r for r in rows if not r.get("ismanaged", True)]
        src = unmanaged[0] if unmanaged else rows[0]
        return src.get("_solutionid_value", "") or ""

    # ------------------------------------------------------------------
    # Content extraction / normalisation utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_content(raw_val: str) -> str:
        """
        Normalise raw Dataverse field values to plain HTML/text.
        Handles:
          - powerpagecomponents.content  → JSON {"source": "<HTML>"}
          - adx_webformsteps.adx_instructions → JSON [{"LCID":1033,"Value":"<HTML>"}]
        """
        if not raw_val or not isinstance(raw_val, str):
            return raw_val or ""
        stripped = raw_val.strip()
        try:
            if stripped.startswith("{"):
                parsed = _json.loads(stripped)
                if "source" in parsed:
                    return parsed["source"]
                if "description" in parsed:
                    desc = parsed["description"]
                    if isinstance(desc, str):
                        try:
                            inner = _json.loads(desc)
                            if isinstance(inner, list):
                                for entry in inner:
                                    if entry.get("LCID") == 1033:
                                        return entry.get("Value", desc)
                                if inner:
                                    return inner[0].get("Value", desc)
                        except Exception:
                            pass
                        return desc
                return raw_val
            if stripped.startswith("["):
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if entry.get("LCID") == 1033:
                            return entry.get("Value", raw_val)
                    if parsed:
                        return parsed[0].get("Value", raw_val)
        except Exception:
            pass
        return raw_val

    @staticmethod
    def _normalise_text_for_search(value) -> str:
        if value is None:
            return ""
        text = str(value)
        text = text.replace("\u2019", "'").replace("\u2018", "'")
        text = _html.unescape(text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    @staticmethod
    def _matches_search(search_text: str, candidate_text: str, match_mode: str = "exact") -> bool:
        needle = DataverseClient._normalise_text_for_search(search_text)
        haystack = DataverseClient._normalise_text_for_search(candidate_text)
        if not needle or not haystack:
            return False
        if needle in haystack:
            return True
        if match_mode == "all_words":
            tokens = [t for t in needle.split() if len(t) >= 3]
            return len(tokens) >= 2 and all(t in haystack for t in tokens)
        return False

    @staticmethod
    def _search_tokens(search_text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9']+", search_text.lower())
        return [t for t in tokens if len(t) >= 3]

    # ------------------------------------------------------------------
    # Debug word probe
    # ------------------------------------------------------------------

    def debug_word_probe(self, search_text: str, max_per_token: int = 20) -> dict:
        tokens = self._search_tokens(search_text)
        if not tokens:
            return {"tokens": [], "per_token": [], "overlap": []}

        probe_fields = [
            {"table": "adx_contentsnippets",      "id": "adx_contentsnippetid",      "name": "adx_name",               "field": "adx_value"},
            {"table": "adx_contentsnippetlanguages","id":"adx_contentsnippetlanguageid","name":"adx_name",               "field": "adx_value"},
            {"table": "powerpagecomponents",       "id": "powerpagecomponentid",       "name": "name",                   "field": "content"},
            {"table": "adx_webformsteps",          "id": "adx_webformstepid",          "name": "adx_name",               "field": "adx_instructions"},
            {"table": "adx_webpages",              "id": "adx_webpageid",              "name": "adx_name",               "field": "adx_copy"},
            {"table": "adx_webpages",              "id": "adx_webpageid",              "name": "adx_name",               "field": "adx_summary"},
            {"table": "adx_webpages",              "id": "adx_webpageid",              "name": "adx_name",               "field": "adx_meta_description"},
            {"table": "adx_webtemplates",          "id": "adx_webtemplateid",          "name": "adx_name",               "field": "adx_source"},
            {"table": "adx_entityforms",           "id": "adx_entityformid",           "name": "adx_name",               "field": "adx_instructions"},
            {"table": "adx_entityforms",           "id": "adx_entityformid",           "name": "adx_name",               "field": "adx_successmessage"},
            {"table": "adx_entityformmetadata",    "id": "adx_entityformmetadataid",   "name": "adx_attributelogicalname","field": "adx_description"},
            {"table": "adx_entityformmetadata",    "id": "adx_entityformmetadataid",   "name": "adx_attributelogicalname","field": "adx_label"},
            {"table": "adx_webformstepmetadata",   "id": "adx_webformstepmetadataid",  "name": "adx_attributelogicalname","field": "adx_description"},
            {"table": "adx_webformstepmetadata",   "id": "adx_webformstepmetadataid",  "name": "adx_attributelogicalname","field": "adx_label"},
            {"table": "adx_weblinks",              "id": "adx_weblinkid",              "name": "adx_name",               "field": "adx_description"},
            {"table": "adx_sitesettings",          "id": "adx_sitesettingid",          "name": "adx_name",               "field": "adx_value"},
        ]

        per_token = []
        overlap_counter: dict = {}

        for token in tokens:
            token_rows: list = []
            for cfg in probe_fields:
                safe = token.replace("'", "''")
                url = (
                    f"{self.org_url}/api/data/v9.2/{cfg['table']}"
                    f"?$filter=contains({cfg['field']}, '{safe}')"
                    f"&$select={cfg['id']},{cfg['name']},{cfg['field']}"
                    f"&$top={max_per_token}"
                )
                res = requests.get(url, headers=self.headers)
                if not res.ok:
                    continue
                for row in res.json().get("value", []):
                    rid = row.get(cfg["id"])
                    rname = row.get(cfg["name"]) or "Unnamed Record"
                    raw_value = row.get(cfg["field"]) or ""
                    extracted = self._extract_content(raw_value)
                    snippet = self._normalise_text_for_search(extracted if extracted else raw_value)[:220]
                    token_rows.append({
                        "table": cfg["table"],
                        "field": cfg["field"],
                        "record_id": rid,
                        "record_name": rname,
                        "snippet": snippet,
                    })
                    key = (cfg["table"], cfg["field"], rid, rname)
                    if key not in overlap_counter:
                        overlap_counter[key] = set()
                    overlap_counter[key].add(token)
            per_token.append({"token": token, "matches": token_rows, "count": len(token_rows)})

        needed = set(tokens)
        overlap = []
        for key, seen_tokens in overlap_counter.items():
            if needed.issubset(seen_tokens):
                table, field, rid, rname = key
                overlap.append({
                    "table": table, "field": field,
                    "record_id": rid, "record_name": rname,
                    "tokens": sorted(list(seen_tokens)),
                })
        return {"tokens": tokens, "per_token": per_token, "overlap": overlap}

    # ------------------------------------------------------------------
    # Portal content search
    # ------------------------------------------------------------------

    def search_portal_content(
        self, search_text: str, match_mode: str = "exact", scan_mode: str = "comprehensive"
    ) -> tuple[list, list]:
        """
        Scan all common Power Pages / Power Portal metadata tables for the given text.
        Returns (results, diagnostics).
        """
        safe_search = search_text.replace("'", "''")

        search_map = {
            "adx_contentsnippets": {
                "logical": "adx_contentsnippet",
                "id": "adx_contentsnippetid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_value"],
            },
            "adx_webformsteps": {
                "logical": "adx_webformstep",
                "id": "adx_webformstepid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_instructions"],
            },
            "adx_webpages": {
                "logical": "adx_webpage",
                "id": "adx_webpageid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_copy", "adx_summary", "adx_meta_description"],
            },
            "adx_webtemplates": {
                "logical": "adx_webtemplate",
                "id": "adx_webtemplateid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_source"],
            },
            "adx_entityforms": {
                "logical": "adx_entityform",
                "id": "adx_entityformid",
                "name": "adx_name",
                "fields": ["adx_instructions", "adx_successmessage"],
            },
            "adx_entityformmetadatas": {
                "logical": "adx_entityformmetadata",
                "id": "adx_entityformmetadataid",
                "name": "adx_attributelogicalname",
                "fields": ["adx_name", "adx_description", "adx_label"],
                "parent_lookup": {"field": "_adx_entityform_value", "label": "Basic Form"},
            },
            "adx_webforms": {
                "logical": "adx_webform",
                "id": "adx_webformid",
                "name": "adx_name",
                "fields": ["adx_name"],
            },
            "adx_weblinks": {
                "logical": "adx_weblink",
                "id": "adx_weblinkid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_description"],
            },
            "adx_sitesettings": {
                "logical": "adx_sitesetting",
                "id": "adx_sitesettingid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_value"],
            },
            "powerpagecomponents": {
                "logical": "powerpagecomponent",
                "id": "powerpagecomponentid",
                "name": "name",
                "fields": ["name", "content"],
            },
            "mspp_webpages": {
                "logical": "mspp_webpage",
                "id": "mspp_webpageid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_copy", "mspp_summary"],
            },
            "mspp_webtemplates": {
                "logical": "mspp_webtemplate",
                "id": "mspp_webtemplateid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_source"],
            },
            "mspp_contentsnippets": {
                "logical": "mspp_contentsnippet",
                "id": "mspp_contentsnippetid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_value"],
            },
            "mspp_entityforms": {
                "logical": "mspp_entityform",
                "id": "mspp_entityformid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_instructions", "mspp_successmessage"],
            },
            "mspp_webforms": {
                "logical": "mspp_webform",
                "id": "mspp_webformid",
                "name": "mspp_name",
                "fields": ["mspp_name"],
            },
            "mspp_webformsteps": {
                "logical": "mspp_webformstep",
                "id": "mspp_webformstepid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_instructions"],
            },
            "mspp_sitesettings": {
                "logical": "mspp_sitesetting",
                "id": "mspp_sitesettingid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_value"],
            },
            "adx_pagetemplates": {
                "logical": "adx_pagetemplate",
                "id": "adx_pagetemplateid",
                "name": "adx_name",
                "fields": ["adx_name", "adx_description"],
            },
            "mspp_pagetemplates": {
                "logical": "mspp_pagetemplate",
                "id": "mspp_pagetemplateid",
                "name": "mspp_name",
                "fields": ["mspp_name", "mspp_description"],
            },
        }

        results: list = []
        diagnostics: list = []

        for entity_set, cfg in search_map.items():
            primary_id = cfg["id"]
            name_field = cfg["name"]
            logical_name = cfg["logical"]

            for field in cfg["fields"]:
                diag: dict = {
                    "table": entity_set,
                    "field": field,
                    "status": None,
                    "method": None,
                    "matches": 0,
                    "detail": "",
                }

                try:
                    parent_cfg = cfg.get("parent_lookup")
                    select_fields = [primary_id, name_field]
                    if parent_cfg and parent_cfg["field"] not in select_fields:
                        select_fields.append(parent_cfg["field"])
                    if field not in select_fields:
                        select_fields.append(field)
                    select_clause = ",".join(select_fields)

                    if scan_mode == "comprehensive":
                        diag["method"] = "comprehensive_scan"
                        scan_headers = dict(self.headers)
                        scan_headers["Prefer"] = 'odata.include-annotations="*",odata.maxpagesize=250'

                        probe_url = (
                            f"{self.org_url}/api/data/v9.2/{entity_set}"
                            f"?$select={select_clause}&$top=1"
                        )
                        probe = requests.get(probe_url, headers=scan_headers)
                        if probe.status_code == 400:
                            table_probe = requests.get(
                                f"{self.org_url}/api/data/v9.2/{entity_set}"
                                f"?$select={primary_id},{name_field}&$top=1",
                                headers=scan_headers,
                            )
                            if table_probe.ok:
                                diag["status"] = "not_found"
                                diag["detail"] = (
                                    f"Table '{entity_set}' is accessible but field '{field}' "
                                    "does not exist in this environment"
                                )
                            elif table_probe.status_code == 404:
                                diag["status"] = "not_found"
                                diag["detail"] = "table does not exist in this environment"
                            else:
                                diag["status"] = "error"
                                diag["detail"] = f"Table probe failed: HTTP {table_probe.status_code}"
                            diagnostics.append(diag)
                            continue
                        elif probe.status_code == 404:
                            diag["status"] = "not_found"
                            diag["detail"] = "table does not exist in this environment"
                            diagnostics.append(diag)
                            continue
                        elif probe.status_code in (401, 403):
                            diag["status"] = "no_access"
                            diag["detail"] = f"HTTP {probe.status_code}"
                            diagnostics.append(diag)
                            continue
                        elif not probe.ok:
                            diag["status"] = "error"
                            diag["detail"] = f"HTTP {probe.status_code}"
                            diagnostics.append(diag)
                            continue

                        page_url: str | None = (
                            f"{self.org_url}/api/data/v9.2/{entity_set}?$select={select_clause}"
                        )
                        total_scanned = 0
                        found = 0

                        while page_url:
                            page_res = requests.get(page_url, headers=scan_headers)
                            if not page_res.ok:
                                diag["status"] = "error"
                                diag["detail"] = f"HTTP {page_res.status_code}"
                                break

                            page_data = page_res.json()
                            rows = page_data.get("value", [])
                            total_scanned += len(rows)

                            for item in rows:
                                raw_val = item.get(field) or ""
                                extracted = self._extract_content(raw_val)
                                candidate = f"{item.get(name_field, '')} {raw_val} {extracted}"
                                if self._matches_search(search_text, candidate, match_mode):
                                    found += 1
                                    result_item: dict = {
                                        "Table": entity_set,
                                        "Record ID": item.get(primary_id),
                                        "Record Name": item.get(name_field) or "Unnamed Record",
                                        "Field": field,
                                        "Content": extracted if extracted else str(raw_val),
                                    }
                                    if parent_cfg:
                                        ann_key = (
                                            f"{parent_cfg['field']}"
                                            "@OData.Community.Display.V1.FormattedValue"
                                        )
                                        result_item["Parent"] = (
                                            item.get(ann_key) or item.get(parent_cfg["field"]) or ""
                                        )
                                        result_item["ParentLabel"] = parent_cfg["label"]
                                    results.append(result_item)

                            page_url = page_data.get("@odata.nextLink")

                        if diag["status"] is None:
                            diag["status"] = "ok"
                            diag["matches"] = found
                            diag["detail"] = f"{total_scanned} records scanned"
                        diagnostics.append(diag)
                        continue

                    # --- Attempt 1: OData contains() ---
                    filter_query = (
                        f"?$filter=contains({field}, '{safe_search}')&$select={select_clause}"
                    )
                    res = requests.get(
                        f"{self.org_url}/api/data/v9.2/{entity_set}{filter_query}",
                        headers=self.headers,
                    )
                    diag["method"] = "odata"
                    diag["detail"] = f"HTTP {res.status_code}"

                    # --- Attempt 2: FetchXML for memo/ntext fields that reject contains() ---
                    if res.status_code == 400:
                        fetch_xml = (
                            f"<fetch top='50'><entity name='{logical_name}'>"
                            f"<attribute name='{primary_id}'/>"
                            f"<attribute name='{name_field}'/>"
                            f"<attribute name='{field}'/>"
                            f"<filter><condition attribute='{field}' operator='like' "
                            f"value='%{safe_search}%'/></filter>"
                            f"</entity></fetch>"
                        )
                        url_fetch = (
                            f"{self.org_url}/api/data/v9.2/{entity_set}"
                            f"?fetchXml={quote(fetch_xml)}"
                        )
                        res = requests.get(url_fetch, headers=self.headers)
                        diag["method"] = "fetchxml"
                        diag["detail"] = f"HTTP {res.status_code}"

                    # --- Attempt 3: Paginated local scan ---
                    if res.status_code == 400:
                        diag["method"] = "local_scan"
                        scan_headers2 = dict(self.headers)
                        scan_headers2["Prefer"] = 'odata.include-annotations="*",odata.maxpagesize=250'

                        probe_url = (
                            f"{self.org_url}/api/data/v9.2/{entity_set}"
                            f"?$select={select_clause}&$top=1"
                        )
                        probe = requests.get(probe_url, headers=self.headers)
                        if probe.status_code == 400:
                            table_probe = requests.get(
                                f"{self.org_url}/api/data/v9.2/{entity_set}"
                                f"?$select={primary_id},{name_field}&$top=1",
                                headers=self.headers,
                            )
                            if table_probe.ok:
                                diag["status"] = "not_found"
                                diag["detail"] = (
                                    f"Table '{entity_set}' is accessible but field '{field}' "
                                    "does not exist in this environment"
                                )
                            else:
                                diag["status"] = "error"
                                diag["detail"] = (
                                    f"Table probe failed: HTTP {table_probe.status_code}"
                                )
                            diagnostics.append(diag)
                            continue

                        scan_page_url: str | None = (
                            f"{self.org_url}/api/data/v9.2/{entity_set}?$select={select_clause}"
                        )
                        found = 0
                        total_scanned = 0
                        scan_ok = True
                        while scan_page_url:
                            page_res = requests.get(scan_page_url, headers=scan_headers2)
                            if not page_res.ok:
                                diag["status"] = "error"
                                diag["detail"] = f"HTTP {page_res.status_code}"
                                scan_ok = False
                                break
                            page_data = page_res.json()
                            for item in page_data.get("value", []):
                                total_scanned += 1
                                raw_val = item.get(field) or ""
                                searchable = str(raw_val)
                                try:
                                    parsed = (
                                        _json.loads(raw_val)
                                        if isinstance(raw_val, str) and raw_val.startswith("[")
                                        else None
                                    )
                                    if parsed and isinstance(parsed, list):
                                        searchable = " ".join(
                                            str(entry.get("Value", "")) for entry in parsed
                                        )
                                except Exception:
                                    pass
                                if self._matches_search(search_text, searchable, match_mode):
                                    found += 1
                                    results.append({
                                        "Table": entity_set,
                                        "Record ID": item.get(primary_id),
                                        "Record Name": item.get(name_field) or "Unnamed Record",
                                        "Field": field,
                                        "Content": self._extract_content(item.get(field)),
                                    })
                            scan_page_url = page_data.get("@odata.nextLink")
                        if scan_ok:
                            diag["status"] = "ok"
                            diag["matches"] = found
                            diag["detail"] = f"{total_scanned} records scanned"
                        diagnostics.append(diag)
                        continue

                    if res.ok:
                        server_matches = res.json().get("value", [])

                        # JSON-heavy fields: local scan fallback when server returns 0 matches
                        json_heavy_fields = {
                            ("powerpagecomponents", "content"),
                            ("adx_webformsteps", "adx_instructions"),
                        }
                        needs_local_fallback = (
                            len(server_matches) == 0
                            and (entity_set, field) in json_heavy_fields
                        )

                        if needs_local_fallback:
                            diag["method"] = "odata+local_scan"
                            scan_headers3 = dict(self.headers)
                            scan_headers3["Prefer"] = (
                                'odata.include-annotations="*",odata.maxpagesize=250'
                            )
                            lf_url: str | None = (
                                f"{self.org_url}/api/data/v9.2/{entity_set}"
                                f"?$select={select_clause}"
                            )
                            found = 0
                            total_scanned = 0
                            while lf_url:
                                page_res = requests.get(lf_url, headers=scan_headers3)
                                if not page_res.ok:
                                    diag["status"] = "error"
                                    diag["detail"] = f"Local scan failed: HTTP {page_res.status_code}"
                                    diagnostics.append(diag)
                                    break
                                page_data = page_res.json()
                                for item in page_data.get("value", []):
                                    total_scanned += 1
                                    raw_val = item.get(field) or ""
                                    extracted = self._extract_content(raw_val)
                                    if self._matches_search(
                                        search_text, f"{raw_val} {extracted}", match_mode
                                    ):
                                        found += 1
                                        results.append({
                                            "Table": entity_set,
                                            "Record ID": item.get(primary_id),
                                            "Record Name": item.get(name_field) or "Unnamed Record",
                                            "Field": field,
                                            "Content": extracted,
                                        })
                                lf_url = page_data.get("@odata.nextLink")
                            else:
                                diag["status"] = "ok"
                                diag["matches"] = found
                                diag["detail"] = (
                                    f"HTTP {res.status_code} (0 server matches; "
                                    f"local scan {total_scanned} records, {found} matches)"
                                )
                                diagnostics.append(diag)
                            continue

                        diag["detail"] += f" ({len(server_matches)} matches)"
                        for match in server_matches:
                            result_entry: dict = {
                                "Table": entity_set,
                                "Record ID": match.get(primary_id),
                                "Record Name": match.get(name_field) or "Unnamed Record",
                                "Field": field,
                                "Content": self._extract_content(match.get(field)),
                            }
                            if parent_cfg:
                                ann_key = (
                                    f"{parent_cfg['field']}"
                                    "@OData.Community.Display.V1.FormattedValue"
                                )
                                result_entry["Parent"] = (
                                    match.get(ann_key) or match.get(parent_cfg["field"]) or ""
                                )
                                result_entry["ParentLabel"] = parent_cfg["label"]
                            results.append(result_entry)
                        diag["status"] = "ok"
                        diag["matches"] = len(server_matches)
                        diagnostics.append(diag)
                        continue

                    if res.status_code == 404:
                        diag["status"] = "not_found"
                        diag["detail"] += " — table/field does not exist in this environment"
                        diagnostics.append(diag)
                        continue

                    if res.status_code in (401, 403):
                        diag["status"] = "no_access"
                        try:
                            detail_body = res.json().get("error", {}).get("message", res.text[:200])
                        except Exception:
                            detail_body = res.text[:200]
                        diag["detail"] += f" — {detail_body}"
                        diagnostics.append(diag)
                        continue

                    if not res.ok:
                        diag["status"] = "error"
                        try:
                            detail_body = res.json().get("error", {}).get("message", res.text[:300])
                        except Exception:
                            detail_body = res.text[:300]
                        diag["detail"] += f" — {detail_body}"
                        diagnostics.append(diag)
                        continue

                except Exception as exc:
                    diag["status"] = "error"
                    diag["detail"] = str(exc)
                    logging.error("Exception searching %s.%s: %s", entity_set, field, exc)

                diagnostics.append(diag)

        # Also scan CRM system form XML
        try:
            crm_results = self.search_crm_forms(search_text, match_mode)
            results.extend(crm_results)
        except Exception as exc:
            logging.warning("CRM form search failed (non-fatal): %s", exc)

        return results, diagnostics

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_portal_content(
        self, table: str, record_id: str, field: str, new_content: str
    ) -> bool:
        """
        PATCH a portal metadata record's field with new HTML/content.
        For powerpagecomponents.content, wraps back into {"source": "<HTML>"}.
        """
        url = f"{self.org_url}/api/data/v9.2/{table}({record_id})"
        if table == "powerpagecomponents" and field == "content":
            payload = {field: _json.dumps({"source": new_content}, ensure_ascii=False)}
        else:
            payload = {field: new_content}
        res = requests.patch(url, headers=self.headers, json=payload)
        res.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # OData helper
    # ------------------------------------------------------------------

    def _odata_get(self, path: str, params=None) -> dict | None:
        """GET {org_url}/api/data/v9.2/{path}. Returns parsed JSON or None."""
        from urllib.parse import urlencode
        url = f"{self.org_url}/api/data/v9.2/{path}"
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        res = requests.get(url, headers=self.headers)
        if res.status_code in (400, 404, 401, 403):
            if res.status_code == 400:
                try:
                    msg = res.json().get("error", {}).get("message", res.text[:200])
                except Exception:
                    msg = res.text[:200]
                logging.warning("OData 400 for %r: %s", path, msg)
            return None
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # Liquid reference parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_liquid_references(source: str) -> dict:
        """
        Scan a Liquid/HTML source and extract references to entity lists,
        entity forms, web forms, included sub-templates, content snippets,
        and inline FetchXML queries.
        """
        if not source:
            return {}

        refs: dict = {
            "entity_lists": [],
            "entity_forms": [],
            "web_forms": [],
            "includes": [],
            "snippets": [],
        }

        for m in re.finditer(
            r"""\{%-?\s*entitylist[^%]*?(?:name|id)\s*:\s*(?:["']([^"']+)["']|([^"'\s%}]+))""",
            source, re.IGNORECASE,
        ):
            v = (m.group(1) or m.group(2) or "").strip()
            if v and v not in refs["entity_lists"]:
                refs["entity_lists"].append(v)

        for m in re.finditer(
            r"""\{%-?\s*entityform[^%]*?(?:name|id)\s*:\s*(?:["']([^"']+)["']|([^"'\s%}]+))""",
            source, re.IGNORECASE,
        ):
            v = (m.group(1) or m.group(2) or "").strip()
            if v and v not in refs["entity_forms"]:
                refs["entity_forms"].append(v)

        for m in re.finditer(
            r"""\{%-?\s*webform[^%]*?(?:name|id)\s*:\s*(?:["']([^"']+)["']|([^"'\s%}]+))""",
            source, re.IGNORECASE,
        ):
            v = (m.group(1) or m.group(2) or "").strip()
            if v and v not in refs["web_forms"]:
                refs["web_forms"].append(v)

        for m in re.finditer(
            r"""\{%-?\s*include\s*['"]entity_list['"]\s+key\s*:\s*["']([^"']+)["']""",
            source, re.IGNORECASE,
        ):
            v = m.group(1).strip()
            if v and v not in refs["entity_lists"]:
                refs["entity_lists"].append(v)

        for m in re.finditer(
            r"""\{%-?\s*include\s*['"]snippet['"]\s+snippet_name\s*:\s*['"]([^'"]+)['"]""",
            source, re.IGNORECASE,
        ):
            v = m.group(1).strip()
            if v and v not in refs["snippets"]:
                refs["snippets"].append(v)

        for m in re.finditer(
            r"""\{%-?\s*(?:include|extends)\s*["']([^"']+)["']""", source, re.IGNORECASE
        ):
            v = m.group(1).strip()
            if v.lower() in ("snippet", "entity_list"):
                continue
            if v and v not in refs["includes"]:
                refs["includes"].append(v)

        for m in re.finditer(r"""snippets\s*\[\s*["']([^"']+)["']""", source, re.IGNORECASE):
            v = m.group(1).strip()
            if v and v not in refs["snippets"]:
                refs["snippets"].append(v)

        for m in re.finditer(
            r"""editable\s+snippets\s+["']([^"']+)["']""", source, re.IGNORECASE
        ):
            v = m.group(1).strip()
            if v and v not in refs["snippets"]:
                refs["snippets"].append(v)

        # Inline FetchXML data queries
        refs["fetch_queries"] = []
        seen_fq: set = set()
        for m in re.finditer(
            r"""\{%-?\s*fetchxml\s+(\w+)\s*-?%\}(.*?)\{%-?\s*endfetchxml\s*-?%\}""",
            source, re.IGNORECASE | re.DOTALL,
        ):
            var_name = m.group(1)
            xml_body = m.group(2)
            entity_names = re.findall(
                r'<entity\s+name=["\']([^"\']+)["\']', xml_body, re.IGNORECASE
            )
            primary = entity_names[0] if entity_names else None
            if primary:
                key = (primary, var_name)
                if key not in seen_fq:
                    seen_fq.add(key)
                    linked = list(dict.fromkeys(
                        re.findall(r'<link-entity\s+name=["\']([^"\']+)["\']', xml_body, re.IGNORECASE)
                    ))
                    attrs = list(dict.fromkeys(
                        re.findall(r'<attribute\s+name=["\']([^"\']+)["\']', xml_body, re.IGNORECASE)
                    ))
                    order_by = []
                    for om in re.finditer(
                        r'<order\s+attribute=["\']([^"\']+)["\'](?:[^>]*?descending=["\']([^"\']+)["\'])?',
                        xml_body, re.IGNORECASE,
                    ):
                        direction = "desc" if (om.group(2) or "").lower() == "true" else "asc"
                        order_by.append(f"{om.group(1)} {direction}")
                    filter_attrs = list(dict.fromkeys(
                        re.findall(
                            r'<condition\s+attribute=["\']([^"\']+)["\']', xml_body, re.IGNORECASE
                        )
                    ))
                    # Usage snippets (how var is referenced in surrounding Liquid)
                    usage_snippets: list = []
                    seen_usages: set = set()
                    for pattern in [
                        r'\{%-?\s*for\s+\w+\s+in\s+' + re.escape(var_name) + r'[^\s%}]*',
                        r'\{\{-?\s*' + re.escape(var_name) + r'\.results\.[^\s}|]+',
                        r'\{%-?\s*assign\s+\w+\s*=\s*' + re.escape(var_name) + r'[^\s%}]*',
                        r'\{%-?\s*if\s+' + re.escape(var_name) + r'[^\s%}]*',
                    ]:
                        for um in re.finditer(pattern, source, re.IGNORECASE):
                            snip = um.group(0)[:80].strip()
                            if snip not in seen_usages:
                                seen_usages.add(snip)
                                usage_snippets.append(snip)
                    refs["fetch_queries"].append({
                        "var_name": var_name,
                        "entity": primary,
                        "linked_entities": linked,
                        "attributes": attrs,
                        "order_by": order_by,
                        "filter_attrs": filter_attrs,
                        "usage_snippets": usage_snippets[:8],
                        "xml_body": xml_body.strip(),
                    })

        return refs

    # ------------------------------------------------------------------
    # Liquid reference resolver
    # ------------------------------------------------------------------

    def _resolve_liquid_refs(self, refs: dict, depth: int, visited: set, schema: str) -> dict:
        resolved: dict = {}

        if refs.get("entity_lists"):
            resolved["entity_lists"] = []
            table = "mspp_entitylists" if schema == "mspp" else "adx_entitylists"
            id_field = "mspp_entitylistid" if schema == "mspp" else "adx_entitylistid"
            name_field = "mspp_name" if schema == "mspp" else "adx_name"
            for ref in refs["entity_lists"]:
                rec = self._fetch_component_by_name_or_id(table, id_field, name_field, ref)
                if rec:
                    key = (table, rec.get(id_field))
                    if key not in visited:
                        visited.add(key)
                        node = self._build_entity_list_node(
                            rec, id_field, name_field, depth, visited, schema
                        )
                        resolved["entity_lists"].append(node)

        if refs.get("entity_forms"):
            resolved["entity_forms"] = []
            table = "mspp_entityforms" if schema == "mspp" else "adx_entityforms"
            id_field = "mspp_entityformid" if schema == "mspp" else "adx_entityformid"
            name_field = "mspp_name" if schema == "mspp" else "adx_name"
            for ref in refs["entity_forms"]:
                rec = self._fetch_component_by_name_or_id(table, id_field, name_field, ref)
                if rec:
                    key = (table, rec.get(id_field))
                    if key not in visited:
                        visited.add(key)
                        node = self._build_entity_form_node(
                            rec, id_field, name_field, depth, visited, schema
                        )
                        resolved["entity_forms"].append(node)

        if refs.get("web_forms"):
            resolved["web_forms"] = []
            table = "mspp_webforms" if schema == "mspp" else "adx_webforms"
            id_field = "mspp_webformid" if schema == "mspp" else "adx_webformid"
            name_field = "mspp_name" if schema == "mspp" else "adx_name"
            for ref in refs["web_forms"]:
                rec = self._fetch_component_by_name_or_id(table, id_field, name_field, ref)
                if rec:
                    key = (table, rec.get(id_field))
                    if key not in visited:
                        visited.add(key)
                        node = self._build_web_form_node(
                            rec, id_field, name_field, depth, visited, schema
                        )
                        resolved["web_forms"].append(node)

        if refs.get("includes"):
            resolved["includes"] = []
            table = "mspp_webtemplates" if schema == "mspp" else "adx_webtemplates"
            id_field = "mspp_webtemplateid" if schema == "mspp" else "adx_webtemplateid"
            name_field = "mspp_name" if schema == "mspp" else "adx_name"
            src_field = "mspp_source" if schema == "mspp" else "adx_source"
            for ref in refs["includes"]:
                rec = self._fetch_component_by_name_or_id(
                    table, id_field, name_field, ref, extra_select=[src_field]
                )
                if rec:
                    key = (table, rec.get(id_field))
                    if key not in visited:
                        visited.add(key)
                        sub_source = rec.get(src_field, "")
                        sub_node: dict = {
                            "_type": "web_template",
                            "_table": table,
                            "_id": rec.get(id_field),
                            "name": rec.get(name_field, ""),
                            "source_preview": sub_source[:300] if sub_source else "",
                        }
                        if depth > 0:
                            sub_refs = self._parse_liquid_references(sub_source)
                            sub_node["liquid_refs"] = self._resolve_liquid_refs(
                                sub_refs, depth - 1, visited, schema
                            )
                        resolved["includes"].append(sub_node)

        if refs.get("snippets"):
            resolved["snippets"] = []
            table = "mspp_contentsnippets" if schema == "mspp" else "adx_contentsnippets"
            id_field = "mspp_contentsnippetid" if schema == "mspp" else "adx_contentsnippetid"
            name_field = "mspp_name" if schema == "mspp" else "adx_name"
            val_field = "mspp_value" if schema == "mspp" else "adx_value"
            for ref in refs["snippets"]:
                rec = self._fetch_component_by_name_or_id(
                    table, id_field, name_field, ref, extra_select=[val_field]
                )
                if rec:
                    raw_val = rec.get(val_field, "")
                    extracted = self._extract_content(raw_val)
                    hrefs = re.findall(r'href=["\'](.*?)["\']', extracted or "") if extracted else []
                    snip_node: dict = {
                        "_type": "content_snippet",
                        "_table": table,
                        "_id": rec.get(id_field),
                        "name": rec.get(name_field, ""),
                        "content_preview": (extracted or "")[:300],
                        "hrefs": hrefs,
                    }
                    if depth > 0:
                        snip_node["full_content"] = extracted or ""
                    resolved["snippets"].append(snip_node)

        if refs.get("fetch_queries"):
            resolved["fetch_queries"] = [
                {
                    "_type": "fetchxml_query",
                    "var_name": fq["var_name"],
                    "entity": fq["entity"],
                    "linked_entities": fq.get("linked_entities", []),
                    "attributes": fq.get("attributes", []),
                    "order_by": fq.get("order_by", []),
                    "filter_attrs": fq.get("filter_attrs", []),
                    "usage_snippets": fq.get("usage_snippets", []),
                    "xml_body": fq.get("xml_body", ""),
                }
                for fq in refs["fetch_queries"]
            ]

        return resolved

    # ------------------------------------------------------------------
    # Component record fetchers
    # ------------------------------------------------------------------

    def _fetch_component_by_name_or_id(
        self, table: str, id_field: str, name_field: str, ref: str, extra_select=None
    ) -> dict | None:
        guid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        select_fields = [id_field, name_field] + (extra_select or [])
        select = ",".join(select_fields)

        if guid_re.match(ref.strip()):
            data = self._odata_get(f"{table}({ref.strip()})?$select={select}")
            if data and id_field in data:
                return data
        else:
            safe = ref.replace("'", "''")
            data = self._odata_get(
                f"{table}?$filter={name_field} eq '{safe}'&$select={select}&$top=1"
            )
            if data:
                rows = data.get("value", [])
                if rows:
                    return rows[0]
        return None

    def _build_entity_list_node(
        self, rec: dict, id_field: str, name_field: str, depth: int, visited: set, schema: str
    ) -> dict:
        pfx = "mspp_" if schema == "mspp" else "adx_"
        node: dict = {
            "_type": "entity_list",
            "_table": f"{pfx}entitylists",
            "_id": rec.get(id_field),
            "name": rec.get(name_field, ""),
        }
        if depth >= 1:
            action_map = {
                "view":     (f"{pfx}viewenabled",    f"{pfx}viewactionlabel"),
                "create":   (f"{pfx}createenabled",  f"{pfx}createactionlabel"),
                "edit":     (f"{pfx}editenabled",    f"{pfx}editactionlabel"),
                "delete":   (f"{pfx}deleteenabled",  f"{pfx}deleteactionlabel"),
                "download": (f"{pfx}downloadenabled", None),
            }
            fetch_fields = [id_field, name_field]
            for _, (en_f, lbl_f) in action_map.items():
                fetch_fields.append(en_f)
                if lbl_f:
                    fetch_fields.append(lbl_f)
            full = self._odata_get(
                f"{pfx}entitylists({rec.get(id_field)})?$select={','.join(fetch_fields)}"
            )
            if full:
                rec = full

            buttons: dict = {}
            for action, (en_f, lbl_f) in action_map.items():
                if rec.get(en_f, False):
                    label = rec.get(lbl_f, action.capitalize()) if lbl_f else action.capitalize()
                    btn: dict = {"enabled": True, "label": label or action.capitalize()}
                    if depth >= 2:
                        btn["details"] = self._get_list_button_details(rec, action, schema)
                    buttons[action] = btn
            if buttons:
                node["action_buttons"] = buttons

            if depth >= 2:
                # Columns (views)
                cols_data = self._odata_get(
                    f"{pfx}entitylistoptions?$filter=_{id_field[:-2]}id_value eq '{rec.get(id_field)}'"
                    f"&$select={pfx}entitylistoptionid,{pfx}name&$top=50"
                )
                if cols_data:
                    node["columns"] = [
                        {"name": r.get(f"{pfx}name", "")}
                        for r in cols_data.get("value", [])
                    ]

            if depth >= 3:
                options = self._fetch_entity_list_options(rec.get(id_field), pfx, id_field)
                if options:
                    node["options"] = options

        return node

    def _fetch_entity_list_options(self, list_id: str, pfx: str, id_field: str) -> list:
        if not list_id:
            return []
        opt_table = f"{pfx}entitylistoptions"
        fk_field = f"_{id_field}_value"
        select_fields = [
            f"{pfx}entitylistoptionid", f"{pfx}name", f"{pfx}targettype",
            f"{pfx}buttonlabel", f"{pfx}fetchxml", f"{pfx}url", f"{pfx}enabled",
            f"_{pfx}entityformid_value", f"_{pfx}webpageid_value",
        ]
        data = self._odata_get(
            f"{opt_table}?$filter={fk_field} eq '{list_id}'"
            f"&$select={','.join(select_fields)}&$top=50"
        )
        if not data:
            return []
        _target_map = {
            0: "URL", 1: "Webpage", 2: "Basic Form",
            100000000: "Basic Form", 100000001: "Webpage", 100000002: "URL",
        }
        options = []
        for o in data.get("value", []):
            target_type_raw = o.get(f"{pfx}targettype")
            target_type = (
                o.get(f"{pfx}targettype@OData.Community.Display.V1.FormattedValue")
                or _target_map.get(target_type_raw, str(target_type_raw) if target_type_raw is not None else "")
            )
            options.append({
                "name": o.get(f"{pfx}name", ""),
                "target_type": target_type,
                "entity_form": o.get(f"_{pfx}entityformid_value@OData.Community.Display.V1.FormattedValue") or "",
                "webpage": o.get(f"_{pfx}webpageid_value@OData.Community.Display.V1.FormattedValue") or "",
                "url": o.get(f"{pfx}url") or "",
                "button_label": o.get(f"{pfx}buttonlabel") or "",
                "fetchxml": o.get(f"{pfx}fetchxml") or "",
                "enabled": o.get(f"{pfx}enabled", True),
            })
        return options

    def _get_list_button_details(self, rec: dict, action: str, schema: str) -> dict:
        pfx = "mspp_" if schema == "mspp" else "adx_"
        details: dict = {}
        url_field = f"{pfx}{action}actionurl" if action != "view" else f"{pfx}viewurl"
        for f in [url_field, f"{pfx}{action}buttoncssclass", f"{pfx}{action}buttontooltip",
                  f"{pfx}{action}actiontargetentitylogicalname"]:
            v = rec.get(f)
            if v:
                details[f.replace(pfx, "")] = v
        return details

    def _build_entity_form_node(
        self, rec: dict, id_field: str, name_field: str, depth: int, visited: set, schema: str
    ) -> dict:
        pfx = "mspp_" if schema == "mspp" else "adx_"
        node: dict = {
            "_type": "entity_form",
            "_table": f"{pfx}entityforms",
            "_id": rec.get(id_field),
            "name": rec.get(name_field, ""),
        }
        if depth >= 1:
            base_fields = [f"{pfx}entityname", f"{pfx}formname", f"{pfx}mode", f"{pfx}tab_name"]
            full = self._odata_get(
                f"{pfx}entityforms({rec.get(id_field)})?$select={','.join([id_field, name_field] + base_fields)}"
            )
            if full is None:
                base_fields_no_tab = [f"{pfx}entityname", f"{pfx}formname", f"{pfx}mode"]
                full = self._odata_get(
                    f"{pfx}entityforms({rec.get(id_field)})?$select={','.join([id_field, name_field] + base_fields_no_tab)}"
                )
                if full:
                    tab_data = self._odata_get(
                        f"{pfx}entityforms({rec.get(id_field)})?$select={pfx}tab_name"
                    )
                    if tab_data:
                        full[f"{pfx}tab_name"] = tab_data.get(f"{pfx}tab_name", "")
            if full:
                node["entity"] = full.get(f"{pfx}entityname", "")
                node["crm_form"] = full.get(f"{pfx}formname", "")
                mode_map = {100000000: "Insert", 100000001: "Edit", 100000002: "ReadOnly"}
                node["mode"] = mode_map.get(
                    full.get(f"{pfx}mode"), str(full.get(f"{pfx}mode", ""))
                )
                portal_tab = full.get(f"{pfx}tab_name", "")
                if portal_tab:
                    node["portal_tab_name"] = portal_tab

            if node.get("entity") and node.get("crm_form"):
                crm_struct = self.get_crm_form_structure(
                    node["entity"], node["crm_form"],
                    tab_filter=node.get("portal_tab_name") or None,
                )
                if crm_struct:
                    node["crm_form_structure"] = crm_struct

            if depth >= 2:
                meta_table = f"{pfx}entityformmetadatas"
                meta_id = f"{pfx}entityformmetadataid"
                metas = self._odata_get(
                    f"{meta_table}?$filter=_{id_field[:-2]}id_value eq '{rec.get(id_field)}'"
                    f"&$select={meta_id},{pfx}attributelogicalname,{pfx}label,{pfx}description"
                    f"&$top=100"
                )
                if metas:
                    node["field_metadata"] = []
                    for m in metas.get("value", []):
                        fm: dict = {
                            "attribute": m.get(f"{pfx}attributelogicalname", ""),
                            "label": m.get(f"{pfx}label", ""),
                        }
                        if depth >= 3:
                            fm["description"] = (m.get(f"{pfx}description") or "")[:300]
                        node["field_metadata"].append(fm)
        return node

    def _get_webform_steps_ordered(self, wf_id: str, pfx: str) -> list:
        _type_map = {
            100000000: "Condition",
            100000001: "Load Form",
            100000002: "Load Tab",
            100000003: "Redirect",
            100000004: "Load Form (Complete)",
        }
        step_id_field = f"{pfx}webformstepid"
        select = ",".join([
            step_id_field, f"{pfx}name", f"{pfx}type",
            f"{pfx}targetentitylogicalname", f"{pfx}formname", f"{pfx}tabname",
            f"_{pfx}nextstep_value",
        ])
        steps_data = self._odata_get(
            f"{pfx}webformsteps?$filter=_{pfx}webform_value eq '{wf_id}' and statecode eq 0"
            f"&$select={select}&$top=50"
        )
        if steps_data is None:
            steps_data = self._odata_get(
                f"{pfx}webformsteps?$filter=_{pfx}webform_value eq '{wf_id}' and statecode eq 0"
                f"&$select={step_id_field},{pfx}name,{pfx}type,{pfx}targetentitylogicalname"
                f"&$top=50"
            )
        if not steps_data:
            return []

        all_steps = {s.get(step_id_field): s for s in steps_data.get("value", [])}
        if not all_steps:
            return []

        wf_rec = self._odata_get(
            f"{pfx}webforms({wf_id})?$select={pfx}webformid,_{pfx}startstep_value"
        )
        start_id = wf_rec.get(f"_{pfx}startstep_value") if wf_rec else None
        if not start_id or start_id not in all_steps:
            start_id = next(iter(all_steps))

        _form_types = {100000001, 100000002, 100000004}

        def _build_step(s: dict, name_suffix: str = "") -> dict:
            type_raw = s.get(f"{pfx}type")
            entity = s.get(f"{pfx}targetentitylogicalname") or ""
            form_name = s.get(f"{pfx}formname") or ""
            tab_name = s.get(f"{pfx}tabname") or ""
            step: dict = {
                "name": s.get(f"{pfx}name", "") + name_suffix,
                "type": _type_map.get(type_raw, str(type_raw) if type_raw is not None else ""),
                "form": form_name,
                "tab": tab_name,
                "table": entity,
            }
            if type_raw in _form_types and entity and form_name:
                crm_struct = self.get_crm_form_structure(
                    entity, form_name, tab_filter=tab_name or None
                )
                if crm_struct:
                    step["crm_form_structure"] = crm_struct
            return step

        ordered: list = []
        seen: set = set()
        current_id = start_id
        while current_id and current_id in all_steps and current_id not in seen:
            seen.add(current_id)
            ordered.append(_build_step(all_steps[current_id]))
            current_id = all_steps[current_id].get(f"_{pfx}nextstep_value")

        for sid, s in all_steps.items():
            if sid not in seen:
                ordered.append(_build_step(s, name_suffix=" *(branch)*"))
        return ordered

    def _build_web_form_node(
        self, rec: dict, id_field: str, name_field: str, depth: int, visited: set, schema: str
    ) -> dict:
        pfx = "mspp_" if schema == "mspp" else "adx_"
        node: dict = {
            "_type": "web_form",
            "_table": f"{pfx}webforms",
            "_id": rec.get(id_field),
            "name": rec.get(name_field, ""),
        }
        if depth >= 1:
            steps = self._get_webform_steps_ordered(rec.get(id_field), pfx)
            if steps:
                node["steps"] = steps
        return node

    # ------------------------------------------------------------------
    # CRM form structure
    # ------------------------------------------------------------------

    def get_crm_form_structure(
        self, entity_name: str, form_name: str, tab_filter: str | None = None
    ) -> dict | None:
        """
        Fetch and parse the model-driven form XML for (entity_name, form_name).
        Returns a structured dict or None if not found.
        """
        safe_entity = entity_name.replace("'", "''")
        safe_form = form_name.replace("'", "''")

        form_row = None
        data = self._odata_get(
            f"systemforms?$filter=objecttypecode eq '{safe_entity}' and name eq '{safe_form}'"
            f"&$select=formid,name,objecttypecode,formxml,type&$top=5"
        )
        if data and data.get("value"):
            form_row = data["value"][0]
        else:
            data2 = self._odata_get(
                f"systemforms?$filter=objecttypecode eq '{safe_entity}' and type eq 2"
                f"&$select=formid,name,objecttypecode,formxml,type&$top=100"
            )
            if data2 and data2.get("value"):
                rows = data2["value"]
                form_row = next(
                    (r for r in rows if r.get("name", "").lower() == form_name.lower()), None
                )
                if not form_row:
                    form_row = next(
                        (r for r in rows if form_name.lower() in r.get("name", "").lower()), None
                    )

        if not form_row:
            return None

        solution_id = self.get_solution_id_for_form(form_row.get("formid", ""))

        result: dict = {
            "_type": "crm_form_structure",
            "form_id": form_row.get("formid", ""),
            "form_name": form_row.get("name", ""),
            "entity": form_row.get("objecttypecode", ""),
            "form_type": form_row.get("type"),
            "solution_id": solution_id,
            "tabs": [],
        }

        formxml_str = form_row.get("formxml", "")
        if not formxml_str:
            return result

        try:
            root = ET.fromstring(formxml_str)
        except ET.ParseError as exc:
            logging.warning("Could not parse formxml for %s/%s: %s", entity_name, form_name, exc)
            return result

        SUBGRID_CLASSIDS = {
            "e7a81278-8635-4d9e-8d4d-59480b391c5b",
            "9fdf5f91-88b1-47f4-ad53-c11efc01a01d",
        }

        def _get_label(el: ET.Element, lang: str = "1033") -> str:
            for lbl in el.findall("labels/label"):
                if lbl.get("languagecode") == lang:
                    return lbl.get("description", "")
            first = el.find("labels/label")
            return first.get("description", "") if first is not None else ""

        for tab_el in root.findall(".//tabs/tab"):
            tab_label = _get_label(tab_el) or tab_el.get("name", tab_el.get("id", ""))
            tab_node: dict = {
                "tab_id": tab_el.get("id", ""),
                "tab_name": tab_el.get("name", ""),
                "label": tab_label,
                "expanded": str(tab_el.get("expanded", "")).lower() == "true",
                "sections": [],
                "subgrids": [],
            }

            for section_el in tab_el.findall(".//section"):
                sec_label = _get_label(section_el) or section_el.get("name", "")
                section_node: dict = {
                    "section_id": section_el.get("id", ""),
                    "section_name": section_el.get("name", ""),
                    "label": sec_label,
                    "subgrids": [],
                    "fields": [],
                }

                for cell_el in section_el.findall(".//cell"):
                    ctrl = cell_el.find("control")
                    if ctrl is None:
                        continue
                    classid = ctrl.get("classid", "").lower().strip("{}")
                    ctrl_id = ctrl.get("id", "")

                    if classid in SUBGRID_CLASSIDS:
                        params = ctrl.find("parameters")
                        title = ""
                        target_entity = ""
                        view_id = ""
                        relationship = ""
                        records_per_page = ""
                        if params is not None:
                            for child in params:
                                tag = child.tag
                                txt = (child.text or "").strip()
                                if tag in ("Title", "Label") and not title:
                                    title = txt
                                elif tag == "TargetEntityType":
                                    target_entity = txt
                                elif tag == "ViewId":
                                    view_id = txt.strip("{}")
                                elif tag == "RelationshipName":
                                    relationship = txt
                                elif tag == "RecordsPerPage":
                                    records_per_page = txt
                        sg: dict = {
                            "control_id": ctrl_id,
                            "title": title or ctrl_id,
                            "target_entity": target_entity,
                            "view_id": view_id,
                            "relationship": relationship,
                            "records_per_page": records_per_page,
                        }
                        section_node["subgrids"].append(sg)
                        tab_node["subgrids"].append(sg)
                    else:
                        field_name = ctrl.get("datafieldname", "")
                        if field_name:
                            section_node["fields"].append(field_name)

                tab_node["sections"].append(section_node)

            if tab_filter:
                if (
                    tab_el.get("name", "").lower() != tab_filter.lower()
                    and tab_el.get("id", "").lower() != tab_filter.lower()
                ):
                    continue

            result["tabs"].append(tab_node)

        return result

    # ------------------------------------------------------------------
    # CRM form search
    # ------------------------------------------------------------------

    def search_crm_forms(self, search_text: str, match_mode: str = "exact") -> list:
        """Search CRM form XML for the given text, tracing matches back to portal forms."""
        results: list = []
        needle_lower = search_text.lower()

        portal_form_refs: list = []
        for pfx, ef_table, id_f, name_f, entity_f, form_f, tab_f in [
            ("mspp", "mspp_entityforms", "mspp_entityformid", "mspp_name",
             "mspp_entityname", "mspp_formname", "mspp_tab_name"),
            ("adx",  "adx_entityforms",  "adx_entityformid",  "adx_name",
             "adx_entityname",  "adx_formname",  "adx_tab_name"),
        ]:
            data = self._odata_get(
                f"{ef_table}?$select={id_f},{name_f},{entity_f},{form_f},{tab_f}&$top=500"
            )
            has_tab_field = data is not None
            if data is None:
                data = self._odata_get(
                    f"{ef_table}?$select={id_f},{name_f},{entity_f},{form_f}&$top=500"
                )
            if not data:
                continue
            rows = data.get("value", [])
            tab_by_id: dict = {}
            if not has_tab_field and rows and len(rows) <= 200:
                for row in rows:
                    rid = row.get(id_f, "")
                    if rid:
                        td = self._odata_get(f"{ef_table}({rid})?$select={tab_f}")
                        if td and td.get(tab_f):
                            tab_by_id[rid] = td.get(tab_f, "")
            for row in rows:
                entity = row.get(entity_f, "")
                formname = row.get(form_f, "")
                if entity and formname:
                    rid = row.get(id_f, "")
                    portal_form_refs.append({
                        "schema": pfx,
                        "ef_id": rid,
                        "ef_name": row.get(name_f, ""),
                        "entity": entity,
                        "formname": formname,
                        "tab_name": row.get(tab_f, "") or tab_by_id.get(rid, ""),
                    })

        if not portal_form_refs:
            return results

        seen_pairs: dict = {}
        for ref in portal_form_refs:
            key = (ref["entity"].lower(), ref["formname"].lower())
            if key not in seen_pairs:
                seen_pairs[key] = None

        for (ent_lower, form_lower) in list(seen_pairs.keys()):
            sample = next(
                r for r in portal_form_refs
                if r["entity"].lower() == ent_lower and r["formname"].lower() == form_lower
            )
            row = None
            data = self._odata_get(
                f"systemforms?$filter=objecttypecode eq '{sample['entity'].replace(chr(39), chr(39)*2)}'"
                f" and name eq '{sample['formname'].replace(chr(39), chr(39)*2)}'"
                f"&$select=formid,name,objecttypecode,formxml,type&$top=5"
            )
            if data and data.get("value"):
                row = data["value"][0]
            else:
                data2 = self._odata_get(
                    f"systemforms?$filter=objecttypecode eq '{sample['entity'].replace(chr(39), chr(39)*2)}'"
                    f" and type eq 2&$select=formid,name,objecttypecode,formxml,type&$top=100"
                )
                if data2 and data2.get("value"):
                    row = next(
                        (r for r in data2["value"]
                         if r.get("name", "").lower() == form_lower),
                        None,
                    )
            seen_pairs[(ent_lower, form_lower)] = row

        def _get_lbl(el: ET.Element, lang: str = "1033") -> str:
            for lbl in el.findall("labels/label"):
                if lbl.get("languagecode") == lang:
                    return lbl.get("description", "")
            first = el.find("labels/label")
            return first.get("description", "") if first is not None else ""

        for (ent_lower, form_lower), form_row in seen_pairs.items():
            if not form_row:
                continue
            formxml_str = form_row.get("formxml", "")
            if not formxml_str or needle_lower not in formxml_str.lower():
                continue

            form_name = form_row.get("name", "")
            entity_name = form_row.get("objecttypecode", "")
            form_id = form_row.get("formid", "")

            contexts: list = []
            try:
                root = ET.fromstring(formxml_str)
                for tab_el in root.findall(".//tabs/tab"):
                    tab_label = _get_lbl(tab_el) or tab_el.get("name", "")
                    if needle_lower in tab_label.lower():
                        contexts.append(f"Tab: '{tab_label}'")
                        continue
                    for section_el in tab_el.findall(".//section"):
                        sec_label = _get_lbl(section_el) or section_el.get("name", "")
                        if needle_lower in sec_label.lower():
                            contexts.append(f"Tab: '{tab_label}' > Section: '{sec_label}'")
                            continue
                        for cell_el in section_el.findall(".//cell"):
                            ctrl = cell_el.find("control")
                            if ctrl is None:
                                continue
                            params = ctrl.find("parameters")
                            if params is not None:
                                for child in params:
                                    if child.tag in ("Title", "Label"):
                                        txt = (child.text or "").strip()
                                        if needle_lower in txt.lower():
                                            contexts.append(
                                                f"Tab: '{tab_label}' > "
                                                f"Section: '{sec_label}' > "
                                                f"Sub-Grid: '{txt}'"
                                            )
            except ET.ParseError:
                contexts.append("(form XML could not be parsed)")

            if not contexts:
                continue

            unique_ctx = list(dict.fromkeys(contexts))
            using_forms = [
                r for r in portal_form_refs
                if r["entity"].lower() == ent_lower and r["formname"].lower() == form_lower
            ]
            content_parts = [" | ".join(unique_ctx)]
            if using_forms:
                content_parts.append(
                    "Portal Basic Form(s): " + ", ".join(r["ef_name"] for r in using_forms)
                )
            results.append({
                "Table": "systemforms",
                "Record ID": form_id,
                "Record Name": f"{form_name} ({entity_name})",
                "Field": "formxml",
                "Content": "  —  ".join(content_parts),
                "crm_form_context": {
                    "form_name": form_name,
                    "entity": entity_name,
                    "contexts": unique_ctx,
                    "portal_basic_forms": using_forms,
                },
            })

        return results

    # ------------------------------------------------------------------
    # System pages
    # ------------------------------------------------------------------

    SYSTEM_PAGES = {
        "signin":             {"label": "Sign In",             "snippet_prefixes": ["Account/SignIn","Account/Signin","Login"],  "setting_prefixes": ["Authentication"]},
        "register":           {"label": "Register",            "snippet_prefixes": ["Account/Register"],                        "setting_prefixes": ["Authentication/Registration"]},
        "redeeminvitation":   {"label": "Redeem Invitation",   "snippet_prefixes": ["Account/Redeem","Account/RedeemInvitation"],"setting_prefixes": []},
        "forgotpassword":     {"label": "Forgot Password",     "snippet_prefixes": ["Account/ForgotPassword","Account/PasswordReset"],"setting_prefixes": []},
        "passwordreset":      {"label": "Password Reset",      "snippet_prefixes": ["Account/PasswordReset","Account/ResetPassword"],"setting_prefixes": []},
        "profile":            {"label": "Profile / My Account","snippet_prefixes": ["Account/Manage","Account/ChangePassword","Account/ChangeEmail","Account/SetPassword"],"setting_prefixes": ["Authentication/Registration"]},
        "emailconfirmation":  {"label": "Email Confirmation",  "snippet_prefixes": ["Account/ConfirmEmail","Account/ConfirmEmailRequest"],"setting_prefixes": []},
        "login":              {"label": "Sign In (Login)",     "snippet_prefixes": ["Account/SignIn","Account/Signin","Login"],  "setting_prefixes": ["Authentication"]},
    }

    @classmethod
    def _match_system_page(cls, partial_url: str) -> dict | None:
        return cls.SYSTEM_PAGES.get(partial_url.lower().strip("/"))

    def get_system_page_structure(self, partial_url: str) -> dict:
        cfg = self._match_system_page(partial_url)
        if not cfg:
            return {"error": f"'{partial_url}' is not a recognised Power Pages system page."}
        result: dict = {
            "_type": "system_page",
            "name": cfg["label"],
            "partial_url": partial_url,
            "content_snippets": [],
            "site_settings": [],
        }
        for table, id_f, name_f, val_f in [
            ("adx_contentsnippets",  "adx_contentsnippetid",  "adx_name",  "adx_value"),
            ("mspp_contentsnippets", "mspp_contentsnippetid", "mspp_name", "mspp_value"),
        ]:
            for prefix in cfg["snippet_prefixes"]:
                safe_prefix = prefix.replace("'", "''")
                data = self._odata_get(
                    f"{table}?$filter=startswith({name_f},'{safe_prefix}')"
                    f"&$select={id_f},{name_f},{val_f}&$top=100"
                )
                if not data:
                    continue
                for row in data.get("value", []):
                    raw_val = row.get(val_f, "")
                    extracted = self._extract_content(raw_val)
                    hrefs = re.findall(r'href=["\'](.*?)["\']', extracted or "") if extracted else []
                    already = any(
                        s["name"] == row.get(name_f) and s["schema"] == table
                        for s in result["content_snippets"]
                    )
                    if not already:
                        result["content_snippets"].append({
                            "_id": row.get(id_f), "name": row.get(name_f, ""),
                            "value": extracted or raw_val or "",
                            "hrefs": hrefs, "schema": table,
                        })
        result["content_snippets"].sort(key=lambda x: x["name"])

        for table, id_f, name_f, val_f in [
            ("adx_sitesettings",  "adx_sitesettingid",  "adx_name",  "adx_value"),
            ("mspp_sitesettings", "mspp_sitesettingid", "mspp_name", "mspp_value"),
        ]:
            for prefix in cfg["setting_prefixes"]:
                safe_prefix = prefix.replace("'", "''")
                data = self._odata_get(
                    f"{table}?$filter=startswith({name_f},'{safe_prefix}')"
                    f"&$select={id_f},{name_f},{val_f}&$top=100"
                )
                if not data:
                    continue
                for row in data.get("value", []):
                    already = any(
                        s["name"] == row.get(name_f) and s["schema"] == table
                        for s in result["site_settings"]
                    )
                    if not already:
                        result["site_settings"].append({
                            "_id": row.get(id_f), "name": row.get(name_f, ""),
                            "value": row.get(val_f, "") or "", "schema": table,
                        })
        result["site_settings"].sort(key=lambda x: x["name"])
        return result

    # ------------------------------------------------------------------
    # Page listing & lookup
    # ------------------------------------------------------------------

    def list_all_webpages(self) -> list:
        pages: list = []
        for schema, table, id_f, name_f, url_f in [
            ("mspp", "mspp_webpages", "mspp_webpageid", "mspp_name", "mspp_partialurl"),
            ("adx",  "adx_webpages",  "adx_webpageid",  "adx_name",  "adx_partialurl"),
        ]:
            seen_ids: set = set()
            path: str | None = (
                f"{table}?$select={id_f},{name_f},{url_f}&$top=500"
            )
            while path:
                data = self._odata_get(path)
                if not data:
                    break
                for row in data.get("value", []):
                    rid = row.get(id_f)
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        pages.append({
                            "name": row.get(name_f, ""),
                            "partial_url": row.get(url_f, ""),
                            "id": rid,
                            "schema": schema,
                        })
                next_link = data.get("@odata.nextLink", "")
                if next_link:
                    m = re.search(r"/api/data/v9\.2/(.+)", next_link)
                    path = m.group(1) if m else None
                else:
                    path = None
        return pages

    def get_webpage_by_partial_url(self, partial_url: str) -> tuple:
        safe = partial_url.strip("/").replace("'", "''")

        select_mspp = (
            "mspp_webpageid,mspp_name,mspp_partialurl,mspp_title,"
            "_mspp_pagetemplateid_value,"
            "_mspp_parentpageid_value,_mspp_websiteid_value"
        )
        data = self._odata_get(
            f"mspp_webpages?$filter=mspp_partialurl eq '{safe}'&$select={select_mspp}&$top=1"
        )
        if data and data.get("value"):
            return data["value"][0], "mspp"

        select_adx = (
            "adx_webpageid,adx_name,adx_partialurl,adx_title,"
            "_adx_pagetemplateid_value,"
            "_adx_parentpageid_value,_adx_websiteid_value"
        )
        data = self._odata_get(
            f"adx_webpages?$filter=adx_partialurl eq '{safe}'&$select={select_adx}&$top=5"
        )
        if data and data.get("value"):
            rows = data["value"]
            row = next(
                (r for r in rows if r.get("_adx_pagetemplateid_value")), rows[0]
            )
            return row, "adx"

        return None, None

    # ------------------------------------------------------------------
    # Page structure explorer (main entry)
    # ------------------------------------------------------------------

    def get_page_structure(self, partial_url: str, max_depth: int = 3) -> dict:
        visited: set = set()

        if self._match_system_page(partial_url):
            return self.get_system_page_structure(partial_url)

        page, schema = self.get_webpage_by_partial_url(partial_url)
        if not page:
            needle = partial_url.lower()
            all_pages = self.list_all_webpages()
            suggestions = [
                p for p in all_pages
                if needle in (p["partial_url"] or "").lower()
                or needle in (p["name"] or "").lower()
            ]
            return {
                "error": f"No webpage found with Partial URL '{partial_url}'",
                "suggestions": suggestions,
                "all_pages": all_pages,
            }

        pfx = f"{schema}_"
        page_id = page.get(f"{pfx}webpageid")
        visited.add((f"{pfx}webpages", page_id))

        for _link_field in [
            f"_{pfx}webform_value",
            f"_{pfx}entityform_value",
            f"_{pfx}entitylist_value",
        ]:
            _res = self._odata_get(f"{pfx}webpages({page_id})?$select={_link_field}")
            if _res and _res.get(_link_field) is not None:
                page = {**page, **{_link_field: _res[_link_field]}}

        structure: dict = {
            "_type": "webpage",
            "_table": f"{pfx}webpages",
            "_id": page_id,
            "name": page.get(f"{pfx}name", ""),
            "partial_url": page.get(f"{pfx}partialurl", ""),
            "title": page.get(f"{pfx}title", ""),
            "website_id": page.get(f"_{pfx}websiteid_value"),
        }

        if max_depth == 0:
            return structure

        pt_id = page.get(f"_{pfx}pagetemplateid_value")
        pt_type_map = {1: "Web Template", 2: "Fixed Layout"}
        wt_id = None
        if pt_id:
            pt_table = f"{pfx}pagetemplates"
            pt_id_field = f"{pfx}pagetemplateid"
            pt_data = self._odata_get(
                f"{pt_table}({pt_id})?$select={pt_id_field},{pfx}name,{pfx}type,_{pfx}webtemplateid_value"
            )
            if pt_data is None:
                pt_data = self._odata_get(f"{pt_table}({pt_id})?$select={pt_id_field},{pfx}name")
            if pt_data:
                raw_type = pt_data.get(f"{pfx}type")
                structure["page_template"] = {
                    "_type": "page_template",
                    "_table": pt_table,
                    "_id": pt_id,
                    "name": pt_data.get(f"{pfx}name", ""),
                    "type": pt_type_map.get(raw_type, str(raw_type) if raw_type is not None else ""),
                    "web_template_id": pt_data.get(f"_{pfx}webtemplateid_value"),
                }
                wt_id = pt_data.get(f"_{pfx}webtemplateid_value")
            else:
                structure["page_template"] = {"_id": pt_id, "name": "(could not fetch)"}

        wt_table = f"{pfx}webtemplates"
        wt_id_field = f"{pfx}webtemplateid"
        src_field = f"{pfx}source"
        if wt_id:
            visited.add((wt_table, wt_id))
            wt_data = self._odata_get(
                f"{wt_table}({wt_id})?$select={wt_id_field},{pfx}name,{src_field}"
            )
            if wt_data:
                source = wt_data.get(src_field, "")
                structure["web_template"] = {
                    "_type": "web_template",
                    "_table": wt_table,
                    "_id": wt_id,
                    "name": wt_data.get(f"{pfx}name", ""),
                    "source_preview": source[:300] if source else "",
                }
                if max_depth >= 1:
                    refs = self._parse_liquid_references(source)
                    structure["web_template"]["liquid_refs"] = self._resolve_liquid_refs(
                        refs, max_depth - 1, visited, schema
                    )
            else:
                structure["web_template"] = {"_id": wt_id, "name": "(could not fetch)"}

        wf_id = page.get(f"_{pfx}webform_value")
        if wf_id:
            wf_table = f"{pfx}webforms"
            wf_id_field = f"{pfx}webformid"
            wf_data = self._odata_get(f"{wf_table}({wf_id})?$select={wf_id_field},{pfx}name")
            wf_node: dict = {
                "_type": "web_form",
                "_table": wf_table,
                "_id": wf_id,
                "name": wf_data.get(f"{pfx}name", "") if wf_data else "(could not fetch)",
            }
            if wf_data:
                steps = self._get_webform_steps_ordered(wf_id, pfx)
                if steps:
                    wf_node["steps"] = steps
            structure["multistep_form"] = wf_node

        ef_id = page.get(f"_{pfx}entityform_value")
        if ef_id:
            ef_table = f"{pfx}entityforms"
            ef_id_field = f"{pfx}entityformid"
            ef_data = self._odata_get(
                f"{ef_table}({ef_id})?$select={ef_id_field},{pfx}name"
                f",{pfx}entityname,{pfx}formname,{pfx}mode"
            )
            if ef_data:
                structure["basic_form"] = {
                    "_type": "entity_form",
                    "_table": ef_table,
                    "_id": ef_id,
                    "name": ef_data.get(f"{pfx}name", ""),
                    "entity": ef_data.get(f"{pfx}entityname", ""),
                    "crm_form": ef_data.get(f"{pfx}formname", ""),
                    "mode": ef_data.get(f"{pfx}mode"),
                }

        el_id = page.get(f"_{pfx}entitylist_value")
        if el_id:
            el_table = f"{pfx}entitylists"
            el_id_field = f"{pfx}entitylistid"
            el_data = self._odata_get(
                f"{el_table}({el_id})?$select={el_id_field},{pfx}name,{pfx}entityname"
            )
            if el_data:
                structure["entity_list"] = {
                    "_type": "entity_list",
                    "_table": el_table,
                    "_id": el_id,
                    "name": el_data.get(f"{pfx}name", ""),
                    "entity": el_data.get(f"{pfx}entityname", ""),
                }

        if max_depth >= 1:
            children_data = self._odata_get(
                f"{pfx}webpages?$filter=_{pfx}parentpageid_value eq '{page_id}'"
                f"&$select={pfx}webpageid,{pfx}name,{pfx}partialurl&$top=20"
            )
            if children_data:
                child_list: list = []
                for ch in children_data.get("value", []):
                    ch_id = ch.get(f"{pfx}webpageid")
                    child_node: dict = {
                        "_type": "child_page",
                        "_table": f"{pfx}webpages",
                        "_id": ch_id,
                        "name": ch.get(f"{pfx}name", ""),
                        "partial_url": ch.get(f"{pfx}partialurl", ""),
                    }
                    if max_depth >= 2 and ch_id and (f"{pfx}webpages", ch_id) not in visited:
                        visited.add((f"{pfx}webpages", ch_id))
                        sub = self.get_page_structure(
                            ch.get(f"{pfx}partialurl", ""), max_depth=max_depth - 2
                        )
                        child_node["structure"] = sub
                    child_list.append(child_node)
                structure["child_pages"] = child_list

        return structure
