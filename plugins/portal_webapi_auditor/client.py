from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode, quote

import requests

logger = logging.getLogger(__name__)

# Mapping from common entity set names to entity logical names
COMMON_ENTITY_SET_MAP: dict[str, str] = {
    "accounts": "account",
    "contacts": "contact",
    "leads": "lead",
    "incidents": "incident",
    "tasks": "task",
    "emails": "email",
    "phonecalls": "phonecall",
    "appointments": "appointment",
    "systemusers": "systemuser",
    "teams": "team",
    "businessunits": "businessunit",
    "annotations": "annotation",
    "invoices": "invoice",
    "orders": "salesorder",
    "quotes": "quote",
    "opportunities": "opportunity",
    "products": "product",
    "competitors": "competitor",
    "campaigns": "campaign",
    "lists": "list",
}

_PORTAL_MGMT_LOCATIONS: dict[str, str] = {
    "adx_sitesettings": "Portal Management → Site Settings",
    "mspp_sitesettings": "Power Pages Management → Site Settings",
    "adx_webtemplates": "Portal Management → Web Templates",
    "mspp_webtemplates": "Power Pages Management → Web Templates",
    "adx_webpages": "Portal Management → Web Pages",
    "mspp_webpages": "Power Pages Management → Web Pages",
    "adx_entityforms": "Portal Management → Basic Forms",
    "mspp_entityforms": "Power Pages Management → Basic Forms",
    "adx_webformsteps": "Portal Management → Multistep Form Steps",
    "mspp_webformsteps": "Power Pages Management → Multistep Form Steps",
    "adx_entitylists": "Portal Management → Lists",
    "mspp_entitylists": "Power Pages Management → Lists",
    "adx_contentsnippets": "Portal Management → Content Snippets",
    "mspp_contentsnippets": "Power Pages Management → Content Snippets",
    "adx_webfiles": "Portal Management → Web Files",
    "mspp_webfiles": "Power Pages Management → Web Files",
}

_TABLE_ENTITY_NAMES: dict[str, str] = {
    "adx_sitesettings": "adx_sitesetting",
    "mspp_sitesettings": "mspp_sitesetting",
    "adx_webtemplates": "adx_webtemplate",
    "mspp_webtemplates": "mspp_webtemplate",
    "adx_webpages": "adx_webpage",
    "mspp_webpages": "mspp_webpage",
    "adx_entityforms": "adx_entityform",
    "mspp_entityforms": "mspp_entityform",
    "adx_webformsteps": "adx_webformstep",
    "mspp_webformsteps": "mspp_webformstep",
    "adx_entitylists": "adx_entitylist",
    "mspp_entitylists": "mspp_entitylist",
    "adx_contentsnippets": "adx_contentsnippet",
    "mspp_contentsnippets": "mspp_contentsnippet",
    "adx_webfiles": "adx_webfile",
    "mspp_webfiles": "mspp_webfile",
}


class PortalWebApiAuditorClient:
    """
    Dataverse client and analysis engine for auditing Power Pages Web API usage,
    detecting deprecated '*' field configurations, scanning portal code,
    and applying site setting remediations.
    """

    _DISCOVERED_WRAPPERS: set[str] = set()

    def __init__(self, org_url: str, token: str) -> None:
        self.org_url = org_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }
        self.last_errors: list[str] = []
        self._entity_set_cache: dict[str, str] = dict(COMMON_ENTITY_SET_MAP)
        self._table_attributes_cache: dict[str, list[str]] = {}
        self._form_fields_cache: dict[str, list[str]] = {}
        self._form_fields_by_name_cache: dict[tuple[str, str], list[str]] = {}
        self._portal_mgmt_app_id: Optional[str] = None
        self._website_schema_cache: dict[str, str] = {}
        self._website_name_cache: dict[str, str] = {}
        self.discovered_wrapper_funcs: set[str] = set()

    def clear_all_caches(self) -> None:
        """Clear all internal in-memory caches to guarantee 100% fresh data on re-scans."""
        self._table_attributes_cache.clear()
        self._form_fields_cache.clear()
        self._form_fields_by_name_cache.clear()
        self._entity_set_cache = dict(COMMON_ENTITY_SET_MAP)
        self._website_schema_cache.clear()
        self._website_name_cache.clear()
        self.discovered_wrapper_funcs.clear()
        PortalWebApiAuditorClient._DISCOVERED_WRAPPERS.clear()
        if hasattr(self, "_all_form_fields_map"):
            self._all_form_fields_map.clear()
        self.last_errors.clear()

    # ------------------------------------------------------------------
    # Low-level OData HTTP helpers
    # ------------------------------------------------------------------

    def _odata_get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        url = f"{self.org_url}/api/data/v9.2/{path}"
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        try:
            res = requests.get(url, headers=self.headers, timeout=60)
            if res.status_code in (400, 404, 401, 403):
                err_msg = ""
                try:
                    err_msg = res.json().get("error", {}).get("message", res.text[:200])
                except Exception:
                    err_msg = res.text[:200]
                self.last_errors.append(f"HTTP {res.status_code} for {path}: {err_msg}")
                return None
            res.raise_for_status()
            return res.json()
        except Exception as exc:
            self.last_errors.append(f"OData GET error ({path}): {exc}")
            return None

    def _fetch_all_pages(self, initial_query: str, max_pages: int = 15) -> list[dict]:
        """Fetch all pages following @odata.nextLink up to max_pages."""
        records: list[dict] = []
        next_url: Optional[str] = f"{self.org_url}/api/data/v9.2/{initial_query}"

        page = 0
        while next_url and page < max_pages:
            try:
                res = requests.get(next_url, headers=self.headers, timeout=60)
                if not res.ok:
                    try:
                        err_msg = res.json().get("error", {}).get("message", res.text[:200])
                    except Exception:
                        err_msg = res.text[:200]
                    self.last_errors.append(f"Page fetch failed ({next_url}): {err_msg}")
                    break
                data = res.json()
                records.extend(data.get("value", []))
                next_url = data.get("@odata.nextLink")
                page += 1
            except Exception as exc:
                self.last_errors.append(f"Pagination error: {exc}")
                break
        return records

    # ------------------------------------------------------------------
    # Portal App Metadata & Deep Links
    # ------------------------------------------------------------------

    def get_portal_app_meta(self, prefer_power_pages: bool = False) -> dict:
        if prefer_power_pages and getattr(self, "_power_pages_mgmt_app_id", None):
            return {"portal_mgmt_app_id": self._power_pages_mgmt_app_id}
        if not prefer_power_pages and self._portal_mgmt_app_id is not None:
            return {"portal_mgmt_app_id": self._portal_mgmt_app_id}

        ppm_id = getattr(self, "_power_pages_mgmt_app_id", "")
        pm_id = self._portal_mgmt_app_id or ""

        if not ppm_id or not pm_id:
            data = self._odata_get(
                "appmodules?$filter=contains(name,'Power Pages') or contains(name,'Portal Management')"
                "&$select=appmoduleid,name,uniquename&$top=10"
            )
            if data and data.get("value"):
                apps = data["value"]
                for a in apps:
                    name_lower = a.get("name", "").strip().lower()
                    if "power pages" in name_lower and not ppm_id:
                        ppm_id = a.get("appmoduleid", "")
                    elif "portal management" in name_lower and not pm_id:
                        pm_id = a.get("appmoduleid", "")

                if not ppm_id and apps:
                    ppm_id = apps[0].get("appmoduleid", "")
                if not pm_id and apps:
                    pm_id = apps[0].get("appmoduleid", "")

            self._power_pages_mgmt_app_id = ppm_id
            self._portal_mgmt_app_id = pm_id

        selected_id = ppm_id if prefer_power_pages and ppm_id else (pm_id or ppm_id)
        return {"portal_mgmt_app_id": selected_id}

    def get_record_link(self, table_name: str, record_id: str) -> str:
        if not record_id or not self.org_url:
            return ""
        is_mspp = "mspp" in table_name
        meta = self.get_portal_app_meta(prefer_power_pages=is_mspp)
        app_id = meta.get("portal_mgmt_app_id", "")
        entity_name = _TABLE_ENTITY_NAMES.get(table_name, table_name)
        if app_id:
            return f"{self.org_url}/main.aspx?appid={app_id}&pagetype=entityrecord&etn={entity_name}&id={record_id}"
        return f"{self.org_url}/main.aspx?pagetype=entityrecord&etn={entity_name}&id={record_id}"

    def get_location_breadcrumb(self, table_name: str) -> str:
        default_prefix = "Power Pages Management" if "mspp" in table_name else "Portal Management"
        return _PORTAL_MGMT_LOCATIONS.get(table_name, f"{default_prefix} → {table_name}")

    # ------------------------------------------------------------------
    # Websites
    # ------------------------------------------------------------------

    def get_websites(self) -> list[dict]:
        """Fetch all portal websites across adx and mspp schemas."""
        websites: list[dict] = []
        seen_ids: set[str] = set()

        for schema in ["adx", "mspp"]:
            table = f"{schema}_websites"
            id_col = f"{schema}_websiteid"
            name_col = f"{schema}_name"
            domain_col = f"{schema}_primarydomainname"
            rows = self._odata_get(f"{table}?$select={id_col},{name_col},{domain_col}&$orderby={name_col} asc")
            if rows and rows.get("value"):
                for r in rows["value"]:
                    wid = r.get(id_col)
                    wname = r.get(name_col) or "Unnamed Portal Website"
                    if wid:
                        self._website_schema_cache[wid] = schema
                        self._website_name_cache[wid] = wname
                    if wid and wid not in seen_ids:
                        seen_ids.add(wid)
                        websites.append({
                            "id": wid,
                            "name": wname,
                            "domain": r.get(domain_col) or "",
                            "schema": schema,
                        })
        return websites

    def get_website_schema(self, website_id: Optional[str]) -> str:
        """Return 'mspp' (Enhanced Data Model) or 'adx' (Standard Data Model) for a website_id."""
        if not website_id:
            return "adx"
        if website_id in self._website_schema_cache:
            return self._website_schema_cache[website_id]
        # Query Dataverse directly if not preloaded in cache
        chk_mspp = self._odata_get(f"mspp_websites({website_id})?$select=mspp_websiteid,mspp_name")
        if chk_mspp and chk_mspp.get("mspp_websiteid"):
            self._website_schema_cache[website_id] = "mspp"
            self._website_name_cache[website_id] = chk_mspp.get("mspp_name") or ""
            return "mspp"
        chk_adx = self._odata_get(f"adx_websites({website_id})?$select=adx_websiteid,adx_name")
        if chk_adx and chk_adx.get("adx_websiteid"):
            self._website_schema_cache[website_id] = "adx"
            self._website_name_cache[website_id] = chk_adx.get("adx_name") or ""
            return "adx"
        self._website_schema_cache[website_id] = "adx"
        return "adx"

    # ------------------------------------------------------------------
    # Entity Set / Logical Name Resolution
    # ------------------------------------------------------------------

    def resolve_entity_name(self, candidate: str) -> str:
        """Resolve a candidate (plural set name or logical name) to logical name."""
        clean = candidate.strip().lower()
        if clean in self._entity_set_cache:
            return self._entity_set_cache[clean]

        # Check Dataverse directly if not in cache (handles custom EntitySetNames like custom_enquiries -> custom_enquiry)
        try:
            lookup = self._odata_get(f"EntityDefinitions?$select=LogicalName,EntitySetName&$filter=EntitySetName eq '{clean}' or LogicalName eq '{clean}'")
            if lookup and lookup.get("value"):
                for row in lookup["value"]:
                    l_name = (row.get("LogicalName") or "").lower()
                    s_name = (row.get("EntitySetName") or "").lower()
                    if l_name:
                        self._entity_set_cache[l_name] = l_name
                    if s_name and l_name:
                        self._entity_set_cache[s_name] = l_name
                if clean in self._entity_set_cache:
                    return self._entity_set_cache[clean]
        except Exception:
            pass

        # Fast heuristic: common plural endings
        # Rule 1: -ies → -y  (e.g. policies → policy)
        if clean.endswith("ies") and len(clean) > 3:
            singular = clean[:-3] + "y"
            self._entity_set_cache[clean] = singular
            return singular

        # Rule 2: words where plural genuinely adds 'es' to a consonant:
        # sibilant consonant clusters (-sses, -xes, -zes, -ches, -shes) and -oes:
        # e.g. addresses → address, statuses → status, boxes → box
        if re.search(r'(ss|sh|ch|x|z)es$', clean) and len(clean) > 4:
            singular = clean[:-2]  # strip 'es' back to consonant
            self._entity_set_cache[clean] = singular
            return singular

        # Rule 3: words ending in vowel + 'es' or consonant + 'e' + 's':
        # (e.g. languages → language, choices → choice, services → service, cases → case)
        # In English, words ending in 'e' simply take 's' to form plural.
        # Stripping only 's' correctly preserves the root 'e'.
        if clean.endswith("s") and not clean.endswith("ss") and len(clean) > 1:
            singular = clean[:-1]
            self._entity_set_cache[clean] = singular
            return singular

        self._entity_set_cache[clean] = clean
        return clean

    def preload_entity_definitions(self) -> None:
        """Query Dataverse metadata across pages to map all EntitySetNames to LogicalNames."""
        try:
            rows = self._fetch_all_pages("EntityDefinitions?$select=LogicalName,EntitySetName", max_pages=15)
            if rows:
                for row in rows:
                    l_name = (row.get("LogicalName") or "").lower()
                    s_name = (row.get("EntitySetName") or "").lower()
                    if l_name:
                        self._entity_set_cache[l_name] = l_name
                    if s_name and l_name:
                        self._entity_set_cache[s_name] = l_name
        except Exception as exc:
            logger.debug("Failed preloading entity definitions: %s", exc)

    @staticmethod
    def _is_guid_or_hex_fragment(s: str) -> bool:
        """Check if a string is a GUID or a hex fragment (e.g. d17febd2, e706e168, a233203e)."""
        if not s or not isinstance(s, str):
            return False
        clean = s.strip().lower()
        if len(clean) < 8:
            return False
        # 8 hex characters: e.g. d17febd2, e706e168, a233203e
        if len(clean) == 8 and bool(re.match(r"^[0-9a-f]{8}$", clean)):
            return True
        # 32 hex characters without hyphens
        if len(clean) == 32 and bool(re.match(r"^[0-9a-f]{32}$", clean)):
            return True
        # Full GUID or partial GUID with hyphens: e.g. d17febd2-8b9a-4c21-...
        if bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}", clean)):
            return True
        return False

    @staticmethod
    def filter_table_attributes(attrs: list[str]) -> list[str]:
        """
        Filter out internal CRM plumbing and virtual display names (*name, *yominame)
        from a list of attribute names, preserving real business and audit columns.
        Also discards GUID hex fragments and JavaScript variable names/DOM IDs.
        """
        internal_system_attrs = {
            # Versioning & Timezones
            "versionnumber", "timezoneruleversionnumber", "utcconversiontimezonecode",
            # Import & Migration tracking
            "importsequencenumber", "overriddencreatedon",
            # Exchange & Outlook integration
            "exchangeitemid", "exchangeweblink", "exchangerate",
            # Workflow & Async execution engine
            "postponeactivityprocessinguntil", "isworkflowcreated",
            "processid", "stageid", "traversedpath",
            # Delivery & Mail Router
            "deliverylastattemptedon", "deliveryprioritycode", "sendermailboxid",
            # Activity party pointers (polymorphic collections, cannot be queried as standard attributes)
            "activityadditionalparams", "bcc", "cc", "from", "to", "customers", "partners",
            "optionalattendees", "organizer", "requiredattendees", "resources",
            # MAPI & Billing flags
            "ismapiprivate", "isregularactivity", "isbilled",
            # Telephony & SLA tracking
            "leftvoicemail", "onholdtime", "lastonholdtime", "slainvokedid",
            # Series pointers & internal types
            "seriesid", "instancetypecode", "owneridtype", "regardingobjecttypecode",
            "sortdate",
            # Internal system lookups (portal users have no access / no reason to query these)
            "createdonbehalfby", "modifiedonbehalfby", "owningbusinessunit", "owningteam", "owninguser",
            "serviceid", "slaid", "transactioncurrencyid",
            # Internal lookup values
            "_createdonbehalfby_value", "_modifiedonbehalfby_value", "_owningbusinessunit_value",
            "_owningteam_value", "_owninguser_value", "_sendermailboxid_value", "_serviceid_value",
            "_slaid_value", "_slainvokedid_value", "_transactioncurrencyid_value",
            # Non-field noise tokens from HTTP payloads & XHR/error callbacks
            "bind", "odata", "message", "responsetext", "responsejson", "innererror", "pluginerror",
            # Non-field noise tokens from JavaScript variables, DOM element IDs, and helper functions
            "foundid", "responseval", "action", "labelid", "labelid2", "labelid3",
            "getquerystringparameter", "odataurl", "entityid",
            "entityformcontrol_entityformview_entityid", "entityformview_entityid",
        }

        all_set = set(f.lower() for f in attrs if f)
        filtered = []
        for f in attrs:
            fl = f.strip().lower()
            if not fl or fl == "*":
                continue
            if PortalWebApiAuditorClient._is_guid_or_hex_fragment(fl):
                continue
            if fl in internal_system_attrs or fl.startswith("versionnumber") or fl.endswith("yominame"):
                continue
            if fl.endswith("codename") and fl[:-4] in all_set:
                continue
            if fl.endswith("name") and fl != "name":
                base = fl[:-4]
                if base in all_set:
                    continue
                if fl.endswith("idname") and base + "id" in all_set:
                    continue
                if fl in ("createdbyname", "modifiedbyname", "createdonbehalfbyname", "modifiedonbehalfbyname",
                          "owningbusinessunitname", "communityname", "deliveryprioritycodename", "slaname"):
                    continue
            filtered.append(fl)
        return sorted(list(set(filtered)))

    def get_table_attributes(self, table_name: str) -> list[str]:
        """
        Fetch valid attribute logical names for a table from Dataverse metadata.
        Applies smart filtering to exclude internal system plumbing (Exchange sync,
        workflow engine state, activity parties) and virtual display names (*name, *yominame)
        so that generated Web API whitelists remain clean, performant, and secure.
        """
        clean_tbl = table_name.strip().lower()
        if clean_tbl in self._table_attributes_cache:
            return self._table_attributes_cache[clean_tbl]

        attrs: set[str] = {f"{clean_tbl}id", "createdon", "modifiedon", "statecode", "statuscode", "ownerid"}
        try:
            url = f"EntityDefinitions(LogicalName='{clean_tbl}')/Attributes?$select=LogicalName,AttributeType,AttributeOf,IsValidForRead"
            rows = self._fetch_all_pages(url, max_pages=10)
            if rows:
                for r in rows:
                    l_name = (r.get("LogicalName") or "").lower()
                    attr_type = (r.get("AttributeType") or "").lower()
                    attr_of = (r.get("AttributeOf") or "").lower()

                    if not l_name:
                        continue

                    # Skip virtual attributes linked to a parent attribute
                    if attr_of:
                        continue

                    # Skip virtual attributes marked by Dataverse
                    if attr_type == "virtual":
                        continue

                    attrs.add(l_name)

                    # For lookups, include _<name>_value
                    if ("lookup" in attr_type or "customer" in attr_type or "owner" in attr_type):
                        attrs.add(f"_{l_name}_value")
        except Exception as exc:
            logger.debug("Failed fetching attributes for %s: %s", clean_tbl, exc)

        # Apply smart filter to clean out system plumbing, internal sync, and virtual display names
        attr_list = self.filter_table_attributes(list(attrs))
        if f"{clean_tbl}id" not in attr_list:
            attr_list.insert(0, f"{clean_tbl}id")

        self._table_attributes_cache[clean_tbl] = attr_list
        return attr_list

    def validate_fields_exist(
        self, table_name: str, fields: list[str] | str
    ) -> dict[str, Any]:
        """
        Internal verification utility: Check whether the specified fields exist
        in Dataverse entity metadata for the given table.
        Returns:
            {
                "valid": list[str],          # Fields confirmed in Dataverse metadata
                "invalid": list[str],        # Fields NOT found in Dataverse metadata
                "all_table_attributes": list[str], # All known valid attributes on entity
                "is_all_valid": bool,
                "table_name": str,
            }
        """
        if isinstance(fields, str):
            f_list = [f.strip().lower() for f in fields.split(",") if f.strip()]
        else:
            f_list = [f.strip().lower() for f in fields if f and f.strip()]

        clean_tbl = self.resolve_entity_name(table_name).strip().lower()
        tbl_attrs = self.get_table_attributes(clean_tbl)
        if not tbl_attrs and clean_tbl != table_name.strip().lower():
            tbl_attrs = self.get_table_attributes(table_name.strip().lower())

        valid_set = set(a.lower() for a in tbl_attrs)
        valid_set.add(f"{clean_tbl}id")
        valid_set.add(f"{table_name.strip().lower()}id")
        # Standard system fields that exist on every entity
        valid_set.update({"createdon", "modifiedon", "statecode", "statuscode", "ownerid", "_ownerid_value"})

        valid_fields: list[str] = []
        invalid_fields: list[str] = []

        for f in f_list:
            if f in valid_set:
                valid_fields.append(f)
            # Also allow lookup navigation format: _<lookup>_value if <lookup> exists in valid_set
            elif f.startswith("_") and f.endswith("_value") and f[1:-6] in valid_set:
                valid_fields.append(f)
            # Or <lookup> if _<lookup>_value is in valid_set
            elif f"_{f}_value" in valid_set:
                valid_fields.append(f)
            else:
                invalid_fields.append(f)

        return {
            "valid": sorted(list(set(valid_fields))),
            "invalid": sorted(list(set(invalid_fields))),
            "all_table_attributes": sorted(list(valid_set)),
            "is_all_valid": len(invalid_fields) == 0,
            "table_name": clean_tbl,
        }

    def get_form_xml_fields(self, form_id: str) -> list[str]:
        """Extract all field logical names configured on a CRM systemform via its formxml."""
        if not form_id:
            return []
        if form_id in self._form_fields_cache:
            return self._form_fields_cache[form_id]
        try:
            res = self._odata_get(f"systemforms({form_id})?$select=formxml")
            if res and res.get("formxml"):
                fxml = res["formxml"]
                matches = re.findall(r"""datafieldname=["']([a-zA-Z0-9_]+)["']""", fxml, re.IGNORECASE)
                fields = sorted(list(set(m.lower() for m in matches if m and m.lower() != "*")))
                self._form_fields_cache[form_id] = fields
                return fields
        except Exception:
            pass
        return []

    def get_form_fields_by_name(self, entity_name: str, form_name: str) -> list[str]:
        """Extract all field logical names configured on a model-driven form via systemforms."""
        clean_ent = entity_name.strip().lower()
        clean_form = form_name.strip()
        if not clean_ent or not clean_form:
            return []
        cache_k = (clean_ent, clean_form.lower())
        if cache_k in self._form_fields_by_name_cache:
            return self._form_fields_by_name_cache[cache_k]
        try:
            query = f"systemforms?$filter=objecttypecode eq '{clean_ent}' and name eq '{quote(clean_form)}'&$select=formid,formxml&$top=1"
            res = self._odata_get(query)
            if res and res.get("value") and len(res["value"]) > 0:
                fxml = res["value"][0].get("formxml")
                if fxml:
                    matches = re.findall(r"""datafieldname=["']([a-zA-Z0-9_]+)["']""", fxml, re.IGNORECASE)
                    fields = sorted(list(set(m.lower() for m in matches if m and m.lower() != "*")))
                    self._form_fields_by_name_cache[cache_k] = fields
                    return fields
        except Exception as exc:
            logger.debug("Failed fetching form fields for %s / %s: %s", clean_ent, clean_form, exc)
        return []

    def load_all_form_fields(self) -> dict[str, set[str]]:
        """Fetch all Basic Forms and Multistep Steps across the portal once, mapping entity -> set of fields."""
        if hasattr(self, "_all_form_fields_map") and self._all_form_fields_map:
            return self._all_form_fields_map

        form_map: dict[str, set[str]] = {}
        try:
            for schema in ["adx", "mspp"]:
                # Basic Forms
                bfs = self._odata_get(f"{schema}_entityforms?$select={schema}_entityname,{schema}_formname")
                if bfs and bfs.get("value"):
                    for row in bfs["value"]:
                        ent = (row.get(f"{schema}_entityname") or "").strip().lower()
                        fname = row.get(f"{schema}_formname")
                        if ent and fname:
                            f_list = self.get_form_fields_by_name(ent, fname)
                            if f_list:
                                form_map.setdefault(ent, set()).update(f_list)

                # Multistep Steps
                steps = self._odata_get(f"{schema}_webformsteps?$select={schema}_targetentitylogicalname,{schema}_formname")
                if steps and steps.get("value"):
                    for row in steps["value"]:
                        ent = (row.get(f"{schema}_targetentitylogicalname") or "").strip().lower()
                        fname = row.get(f"{schema}_formname")
                        if ent and fname:
                            f_list = self.get_form_fields_by_name(ent, fname)
                            if f_list:
                                form_map.setdefault(ent, set()).update(f_list)
        except Exception as exc:
            logger.debug("Failed loading all form fields: %s", exc)

        self._all_form_fields_map = form_map
        return form_map

    def get_table_form_fields(self, table_name: str) -> list[str]:
        """Extract all field logical names rendered on Basic Forms and Multistep Form Steps for this table."""
        clean_ent = self.resolve_entity_name(table_name).strip().lower()
        if not clean_ent:
            return []
        all_map = self.load_all_form_fields()
        direct = all_map.get(clean_ent, set()) | all_map.get(table_name.strip().lower(), set())
        return sorted(list(direct))

    # ------------------------------------------------------------------
    # Site Settings Audit
    # ------------------------------------------------------------------

    def get_webapi_site_settings(self, website_id: Optional[str] = None) -> list[dict]:
        """
        Fetch all Web API related site settings (Webapi/<table-name>/enabled,
        Webapi/<table-name>/fields, Webapi/<table-name>/error-handling).
        Groups settings by table logical name.
        """
        self.preload_entity_definitions()
        settings_raw: list[dict] = []

        for schema in ["adx", "mspp"]:
            table = f"{schema}_sitesettings"
            id_col = f"{schema}_sitesettingid"
            name_col = f"{schema}_name"
            val_col = f"{schema}_value"
            site_col = f"_{schema}_websiteid_value"

            filter_clause = f"contains({name_col}, 'webapi') or contains({name_col}, 'Webapi')"
            if website_id:
                filter_clause = f"({filter_clause}) and {site_col} eq {website_id}"

            query = f"{table}?$filter={quote(filter_clause)}&$select={id_col},{name_col},{val_col},{site_col}"
            rows = self._fetch_all_pages(query, max_pages=10)
            for r in rows:
                r["_schema"] = schema
                r["_table"] = table
                r["_id_col"] = id_col
                r["_name_col"] = name_col
                r["_val_col"] = val_col
                r["_site_col"] = site_col
                settings_raw.append(r)

        # Parse and aggregate settings by (website_id, table_name)
        # Regex to extract: Webapi/<table_name>/<setting_key>
        pattern = re.compile(r"^webapi/([^/]+)/([^/]+)$", re.IGNORECASE)
        grouped: dict[tuple[str, str], dict] = {}

        for r in settings_raw:
            schema = r["_schema"]
            name_val = (r.get(r["_name_col"]) or "").strip()
            raw_val = (r.get(r["_val_col"]) or "").strip()
            rec_id = r.get(r["_id_col"]) or ""
            web_id = r.get(r["_site_col"]) or ""

            match = pattern.match(name_val)
            if not match:
                continue

            table_part = match.group(1).strip().lower()
            setting_key = match.group(2).strip().lower()
            logical_table = self.resolve_entity_name(table_part)

            group_key = (web_id, logical_table)
            if group_key not in grouped:
                grouped[group_key] = {
                    "website_id": web_id,
                    "table_name": logical_table,
                    "table_alias_raw": table_part,
                    "schema": schema,
                    "setting_table": r["_table"],
                    "enabled": False,
                    "enabled_setting_id": None,
                    "fields_raw": "",
                    "fields_setting_id": None,
                    "fields_list": [],
                    "has_wildcard": False,
                    "error_handling": "",
                    "error_handling_setting_id": None,
                    "duplicate_fields_settings": [],
                }

            item = grouped[group_key]
            if setting_key == "enabled":
                item["enabled"] = raw_val.lower() in ("true", "1", "yes")
                item["enabled_setting_id"] = rec_id
            elif setting_key == "fields":
                is_wc = "*" in raw_val
                if item["fields_setting_id"] and item["fields_setting_id"] != rec_id:
                    item["duplicate_fields_settings"].append({
                        "name": name_val,
                        "val": raw_val,
                        "id": rec_id,
                    })

                # If ANY setting record for this table has wildcard '*', preserve has_wildcard = True!
                if is_wc:
                    item["has_wildcard"] = True
                    item["fields_raw"] = raw_val
                    item["fields_setting_id"] = rec_id
                elif not item["has_wildcard"]:
                    item["fields_raw"] = raw_val
                    item["fields_setting_id"] = rec_id

                cols = [c.strip().lower() for c in raw_val.split(",") if c.strip()]
                existing_cols = set(item["fields_list"])
                for c in cols:
                    if c not in existing_cols:
                        item["fields_list"].append(c)
                        existing_cols.add(c)
            elif setting_key in ("error-handling", "errorhandling"):
                item["error_handling"] = raw_val
                item["error_handling_setting_id"] = rec_id

        return list(grouped.values())

    # ------------------------------------------------------------------
    # Code Scanner & Field Usage Extractor
    # ------------------------------------------------------------------

    def scan_portal_code_for_webapi(
        self,
        website_id: Optional[str] = None,
        known_tables: Optional[list[str]] = None,
        table_fields_map: Optional[dict[str, list[str]]] = None,
    ) -> list[dict]:
        """
        Deep-scan all portal metadata records where Web API calls or references can reside:
          - Web Templates (source)
          - Web Pages (custom JS, copy)
          - Basic Forms (custom JS, target table)
          - Multistep Form Steps (custom JS, target table)
          - Entity Lists (custom JS)
          - Content Snippets (value)
          - Form Metadata (inline scripts, PCF controls)
          - Web Files (.js attachments in annotations)

        Returns a list of extracted occurrences with component details,
        table name, fields extracted, line numbers, and code excerpts.
        """
        self.preload_entity_definitions()

        occurrences: list[dict] = []
        known_tbl_set = set(t.lower() for t in (known_tables or []))

        # Pre-fetch valid Dataverse attributes for all known Web API tables
        table_attrs_map: dict[str, list[str]] = {}
        if known_tbl_set:
            for tbl in known_tbl_set:
                table_attrs_map[tbl] = self.get_table_attributes(tbl)

        scan_targets = [
            # (schema, table, id_col, name_col, [content_cols], site_col, component_type)
            ("adx", "adx_webtemplates", "adx_webtemplateid", "adx_name", ["adx_source"], "_adx_websiteid_value", "Web Template"),
            ("mspp", "mspp_webtemplates", "mspp_webtemplateid", "mspp_name", ["mspp_source"], "_mspp_websiteid_value", "Web Template"),
            ("adx", "adx_webpages", "adx_webpageid", "adx_name", ["adx_customjavascript", "adx_copy"], "_adx_websiteid_value", "Web Page"),
            ("mspp", "mspp_webpages", "mspp_webpageid", "mspp_name", ["mspp_customjavascript", "mspp_copy"], "_mspp_websiteid_value", "Web Page"),
            ("adx", "adx_entityforms", "adx_entityformid", "adx_name", ["adx_registerstartupscript"], "_adx_websiteid_value", "Basic Form"),
            ("mspp", "mspp_entityforms", "mspp_entityformid", "mspp_name", ["mspp_registerstartupscript"], "_mspp_websiteid_value", "Basic Form"),
            ("adx", "adx_webformsteps", "adx_webformstepid", "adx_name", ["adx_registerstartupscript"], None, "Multistep Form Step"),
            ("mspp", "mspp_webformsteps", "mspp_webformstepid", "mspp_name", ["mspp_registerstartupscript"], None, "Multistep Form Step"),
            ("adx", "adx_entitylists", "adx_entitylistid", "adx_name", ["adx_registerstartupscript"], "_adx_websiteid_value", "Entity List"),
            ("mspp", "mspp_entitylists", "mspp_entitylistid", "mspp_name", ["mspp_registerstartupscript"], "_mspp_websiteid_value", "Entity List"),
            ("adx", "adx_contentsnippets", "adx_contentsnippetid", "adx_name", ["adx_value"], "_adx_websiteid_value", "Content Snippet"),
            ("mspp", "mspp_contentsnippets", "mspp_contentsnippetid", "mspp_name", ["mspp_value"], "_mspp_websiteid_value", "Content Snippet"),
            ("adx", "adx_entityformmetadatas", "adx_entityformmetadataid", "adx_attributelogicalname", ["adx_description", "adx_label"], None, "Form Metadata (Inline Script)"),
            ("mspp", "mspp_entityformmetadatas", "mspp_entityformmetadataid", "mspp_attributelogicalname", ["mspp_description", "mspp_label"], None, "Form Metadata (Inline Script)"),
            ("adx", "adx_webformstepmetadatas", "adx_webformstepmetadataid", "adx_attributelogicalname", ["adx_description", "adx_label"], None, "Step Metadata (Inline Script)"),
            ("mspp", "mspp_webformstepmetadatas", "mspp_webformstepmetadataid", "mspp_attributelogicalname", ["mspp_description", "mspp_label"], None, "Step Metadata (Inline Script)"),
        ]

        # 1. Scan standard text / code fields across templates, pages, forms, snippets, metadata
        logger.debug("Starting deep scan diagnostics for website_id: %s, known_tables: %s", website_id, sorted(list(known_tbl_set)))

        # Collect all code blocks and pre-index JS query helper functions across all templates/components
        all_code_blocks: list[dict] = []
        query_func_registry: dict[str, dict] = {}

        for schema, table, id_col, name_col, content_cols, site_col, comp_type in scan_targets:
            select_cols = [id_col, name_col] + content_cols
            if site_col:
                select_cols.append(site_col)

            filter_clauses = []
            if website_id and site_col:
                filter_clauses.append(f"({site_col} eq {website_id} or {site_col} eq null)")

            query = f"{table}?$select={','.join(select_cols)}"
            if filter_clauses:
                query += f"&$filter={quote(' and '.join(filter_clauses))}"

            rows = self._fetch_all_pages(query, max_pages=30)
            if not rows and any("registerstartupscript" in c for c in content_cols):
                # Fallback to customjavascript if registerstartupscript is not present in this schema
                fallback_cols = [c.replace("registerstartupscript", "customjavascript") for c in content_cols]
                f_query = f"{table}?$select={','.join([id_col, name_col] + fallback_cols)}"
                if site_col:
                    f_query += f",{site_col}"
                if filter_clauses:
                    f_query += f"&$filter={quote(' and '.join(filter_clauses))}"
                fallback_rows = self._fetch_all_pages(f_query, max_pages=30)
                if fallback_rows:
                    rows = fallback_rows
                    content_cols = fallback_cols

            with_code = sum(1 for r in rows if any(r.get(c) for c in content_cols))
            logger.debug("Target: %s (%s) -> fetched %d records (%d with code)", table, comp_type, len(rows), with_code)
            if len(rows) == 0 and self.last_errors:
                logger.debug("   [Notice] Last error: %s", self.last_errors[-1])

            for row in rows:
                rec_id = row.get(id_col) or ""
                rec_name = row.get(name_col) or "Unnamed Record"
                web_id = row.get(site_col) if site_col else ""

                for col in content_cols:
                    code_text = row.get(col)
                    if not code_text or not isinstance(code_text, str):
                        continue

                    # Pre-index helper functions in Pass 1
                    helper_map = self._extract_query_helper_functions(code_text)
                    if helper_map:
                        query_func_registry.update(helper_map)

                    all_code_blocks.append({
                        "rec_id": rec_id,
                        "rec_name": rec_name,
                        "web_id": web_id,
                        "table": table,
                        "col": col,
                        "comp_type": comp_type,
                        "code_text": code_text,
                    })

        # Pass 1B: Automatically discover custom Web API wrapper functions across all templates/components
        discovered_wrappers = self._discover_webapi_wrapper_functions(all_code_blocks)
        logger.debug("Pre-indexed %d query helper functions: %s", len(query_func_registry), list(query_func_registry.keys()))
        logger.debug("Discovered %d custom Web API wrappers: %s", len(discovered_wrappers), sorted(list(discovered_wrappers)))

        for block in all_code_blocks:
            rec_id = block["rec_id"]
            rec_name = block["rec_name"]
            web_id = block["web_id"]
            table = block["table"]
            col = block["col"]
            comp_type = block["comp_type"]
            code_text = block["code_text"]

            lower_code = code_text.lower()

            # A. Parse direct Web API / safeAjax / SDK / HTTP-client calls or OData queries
            wrapper_triggers = tuple(discovered_wrappers)
            _WEBAPI_TRIGGERS = (
                "/_api/", "webapi", "safeajax",
                "$select=", "fetch(",
                "$.ajax", "$.get(", "$.post(",
                "axios.", "xmlhttprequest",
                "portalajax", "shell.ajax",
                "fetchxml=", "?fetchxml",
            ) + wrapper_triggers
            parsed_tables_in_comp: set[str] = set()
            if any(t in code_text or t in lower_code for t in _WEBAPI_TRIGGERS):
                hits = self._parse_code_for_webapi_calls(
                    code_text,
                    query_helpers=query_func_registry,
                    discovered_wrappers=discovered_wrappers,
                )
                for hit in hits:
                    hit.update({
                        "component_type": comp_type,
                        "component_name": rec_name,
                        "component_id": rec_id,
                        "table_source": table,
                        "field_source": col,
                        "website_id": web_id or website_id or "",
                    })
                    occurrences.append(hit)
                    parsed_tables_in_comp.add(hit["table_name"])
                    logger.debug("  [WEBAPI HIT] %s in %s '%s': fields=%s", hit['table_name'], comp_type, rec_name, hit['fields'])


                    # B. Extract FetchXML queries (<entity name="..."> <link-entity name="...">)
                    fx_hits = self._extract_fetchxml_usage(code_text, allowed_tables=known_tbl_set)
                    for fx in fx_hits:
                        fx.update({
                            "component_type": comp_type,
                            "component_name": rec_name,
                            "component_id": rec_id,
                            "table_source": table,
                            "field_source": col,
                            "website_id": web_id or website_id or "",
                        })
                        occurrences.append(fx)
                        parsed_tables_in_comp.add(fx["table_name"])

                    # C. Extract Liquid entity lookups
                    lq_hits = self._extract_liquid_entity_usage(code_text, allowed_tables=known_tbl_set)
                    for lq in lq_hits:
                        lq.update({
                            "component_type": comp_type,
                            "component_name": rec_name,
                            "component_id": rec_id,
                            "table_source": table,
                            "field_source": col,
                            "website_id": web_id or website_id or "",
                        })
                        occurrences.append(lq)
                        parsed_tables_in_comp.add(lq["table_name"])

                    # C2. Extract Liquid Portal tag usages (entityview, entityform, recordview)
                    lt_hits = self._extract_liquid_tag_usage(code_text, allowed_tables=known_tbl_set)
                    for lt in lt_hits:
                        lt.update({
                            "component_type": comp_type,
                            "component_name": rec_name,
                            "component_id": rec_id,
                            "table_source": table,
                            "field_source": col,
                            "website_id": web_id or website_id or "",
                        })
                        occurrences.append(lt)
                        parsed_tables_in_comp.add(lt["table_name"])

                    # D. Fallback: Check for mentions of known Web API tables in client JS/HTML
                    # Strip FetchXML blocks from the code so XML attributes like uitype="contact" don't trigger false positives
                    code_for_fuzzy = re.sub(r'<fetch\b.*?</fetch>', '', code_text, flags=re.DOTALL | re.IGNORECASE)
                    code_for_fuzzy = re.sub(r'\{%\s*fetchxml\b.*?\{%\s*endfetchxml\s*%\}', '', code_for_fuzzy, flags=re.DOTALL | re.IGNORECASE)

                    # Exclude common English stop words and generic CRM system attributes from fuzzy matching
                    common_stop_words = {
                        "to", "from", "in", "on", "by", "is", "as", "at", "or", "an", "do", "no", "so",
                        "up", "me", "my", "we", "he", "if", "it", "all", "get", "set", "for", "not",
                        "out", "new", "top", "end", "run", "key", "url", "raw", "tag", "tab", "row",
                        "statecode", "statuscode", "createdon", "modifiedon", "ownerid", "versionnumber",
                    }

                    if known_tbl_set and code_for_fuzzy.strip():
                        lines = code_for_fuzzy.splitlines()
                        for tbl in known_tbl_set:
                            # Skip if this component already had an explicit OData/FetchXML query parsed
                            if tbl in parsed_tables_in_comp:
                                continue

                            tbl_pattern = re.compile(r'\b' + re.escape(tbl) + r'(?:s|ies)?\b', re.IGNORECASE)
                            if tbl_pattern.search(code_for_fuzzy):
                                tbl_valid_attrs = list(set(table_attrs_map.get(tbl, []) + (table_fields_map or {}).get(tbl, [])))

                                for line_idx, line in enumerate(lines, start=1):
                                    if tbl_pattern.search(line):
                                        start_l = max(0, line_idx - 15)
                                        end_l = min(len(lines), line_idx + 20)
                                        snip_block = "\n".join(lines[start_l:end_l]).lower()

                                        matched_fields = set()
                                        for attr in tbl_valid_attrs:
                                            if (
                                                attr not in common_stop_words
                                                and ("_" in attr or len(attr) >= 4)
                                                and re.search(r'\b' + re.escape(attr) + r'\b', snip_block)
                                            ):
                                                matched_fields.add(attr)

                                        # Fallback: scan full component for any configured fields
                                        if not matched_fields:
                                            for attr in tbl_valid_attrs:
                                                if attr not in common_stop_words and re.search(r'\b' + re.escape(attr) + r'\b', code_for_fuzzy.lower()):
                                                    matched_fields.add(attr)

                                        is_client_js = any(x in col.lower() for x in ("customjavascript", "registerstartupscript", "registercustomjavascript")) or "web file" in comp_type.lower() or "script" in comp_type.lower()
                                        ep_label = f"Client Script Reference to {tbl}" if is_client_js else f"Portal Template / Code Reference to {tbl}"
                                        comp_label = f"{comp_type} Script" if is_client_js and "script" not in comp_type.lower() else comp_type

                                        occurrences.append({
                                            "raw_endpoint": ep_label,
                                            "table_name": tbl,
                                            "table_raw": tbl,
                                            "fields": sorted(list(f for f in matched_fields if f and f != "*")),
                                            "line_number": line_idx,
                                            "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                                            "component_type": comp_label,
                                            "component_name": rec_name,
                                            "component_id": rec_id,
                                            "table_source": table,
                                            "field_source": col,
                                            "website_id": web_id or website_id or "",
                                        })
                                        break  # Record one occurrence per component per table

        # 2. Scan Web Files (.js / .jsx / .ts attachments in annotation table)
        for schema in ["adx", "mspp"]:
            tbl_webfiles = f"{schema}_webfiles"
            id_col = f"{schema}_webfileid"
            name_col = f"{schema}_name"
            site_col = f"_{schema}_websiteid_value"
            url_col = f"{schema}_partialurl"

            q = f"{tbl_webfiles}?$select={id_col},{name_col},{site_col},{url_col}"
            if website_id:
                q += f"&$filter={site_col} eq {website_id} or {site_col} eq null"

            wf_rows = self._fetch_all_pages(q, max_pages=20)
            logger.debug("Target: %s -> fetched %d web files", tbl_webfiles, len(wf_rows))

            for wf in wf_rows:
                w_name = (wf.get(name_col) or "").lower()
                w_url = (wf.get(url_col) or "").lower()
                wf_id = wf.get(id_col)
                if not wf_id:
                    continue

                # Inspect if name or URL indicates script, OR fetch annotation to verify
                is_script_hint = any(
                    x in w_name or x in w_url
                    for x in (".js", ".jsx", ".ts", "script", "bundle", "main", "app")
                )

                note_q = (
                    f"annotations?$filter=_objectid_value eq {wf_id}"
                    f"&$select=annotationid,filename,documentbody,mimetype&$top=1"
                )
                notes = self._odata_get(note_q)
                if not notes or not notes.get("value"):
                    continue
                note_rec = notes["value"][0]
                fn = (note_rec.get("filename") or "").lower()
                mime = (note_rec.get("mimetype") or "").lower()

                # Verify that it is indeed a JavaScript/TypeScript asset
                if not (is_script_hint or fn.endswith(".js") or "javascript" in mime or "typescript" in mime):
                    continue

                doc_body = note_rec.get("documentbody")
                if not doc_body:
                    continue


                try:
                    decoded_js = base64.b64decode(doc_body).decode("utf-8", errors="ignore")
                except Exception:
                    continue

                hits = self._parse_code_for_webapi_calls(decoded_js)
                for hit in hits:
                    hit.update({
                        "component_type": "Web File (.js attachment)",
                        "component_name": wf.get(name_col) or note_rec.get("filename") or "Web File JS",
                        "component_id": wf_id,
                        "table_source": tbl_webfiles,
                        "field_source": "documentbody (annotation)",
                        "website_id": wf.get(site_col) or website_id or "",
                    })
                    occurrences.append(hit)

                parsed_wf_tables = set(h["table_name"] for h in hits)

                # Also match known Web API table attributes in Web File JS
                if known_tbl_set:
                    lower_js = decoded_js.lower()
                    lines = decoded_js.splitlines()
                    for tbl in known_tbl_set:
                        if tbl in parsed_wf_tables:
                            continue
                        tbl_pattern = re.compile(r'\b' + re.escape(tbl) + r'(?:s|ies)?\b', re.IGNORECASE)
                        if tbl_pattern.search(decoded_js):
                            tbl_valid_attrs = list(set(table_attrs_map.get(tbl, []) + (table_fields_map or {}).get(tbl, [])))
                            for line_idx, line in enumerate(lines, start=1):
                                if tbl_pattern.search(line):
                                    start_l = max(0, line_idx - 15)
                                    end_l = min(len(lines), line_idx + 20)
                                    snip_block = "\n".join(lines[start_l:end_l]).lower()

                                    matched_fields = set()
                                    for attr in tbl_valid_attrs:
                                        if (
                                            attr not in common_stop_words
                                            and ("_" in attr or len(attr) >= 4)
                                            and re.search(r'\b' + re.escape(attr) + r'\b', snip_block)
                                        ):
                                            matched_fields.add(attr)

                                    # Fallback: scan whole file
                                    if not matched_fields:
                                        for attr in tbl_valid_attrs:
                                            if attr not in common_stop_words and re.search(r'\b' + re.escape(attr) + r'\b', decoded_js.lower()):
                                                matched_fields.add(attr)

                                    logger.debug("  [WEB FILE HIT] %s in '%s' (fn='%s'): fields=%s", tbl, w_name, fn, sorted(list(matched_fields)))
                                    occurrences.append({
                                        "raw_endpoint": f"Web File JS Reference to {tbl}",
                                        "table_name": tbl,
                                        "table_raw": tbl,
                                        "fields": sorted(list(f for f in matched_fields if f and f != "*")),
                                        "line_number": line_idx,
                                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                                        "component_type": "Web File (.js attachment)",
                                        "component_name": wf.get(name_col) or note_rec.get("filename") or "Web File JS",
                                        "component_id": wf_id,
                                        "table_source": tbl_webfiles,
                                        "field_source": "documentbody (annotation)",
                                        "website_id": wf.get(site_col) or website_id or "",
                                    })
                                    break

        # 3. Detect PCF Code Components configured in Form Metadata
        for schema in ["adx", "mspp"]:
            meta_tbl = f"{schema}_entityformmetadatas"
            id_col = f"{schema}_entityformmetadataid"
            attr_col = f"{schema}_attributelogicalname"
            style_col = f"{schema}_controlstyle"
            form_lookup = f"_{schema}_entityformid_value"

            # 756150001 is Code Component (PCF) in Power Pages metadata
            pcf_meta = self._fetch_all_pages(
                f"{meta_tbl}?$filter={style_col} eq 756150001"
                f"&$select={id_col},{attr_col},{style_col},{form_lookup}",
                max_pages=10,
            )
            if pcf_meta:
                form_ids = list(set(r.get(form_lookup) for r in pcf_meta if r.get(form_lookup)))
                form_table_map: dict[str, tuple[str, str]] = {}
                for fid in form_ids:
                    form_data = self._odata_get(f"{schema}_entityforms({fid})?$select={schema}_entityname,{schema}_name")
                    if form_data:
                        form_table_map[fid] = (form_data.get(f"{schema}_entityname") or "", form_data.get(f"{schema}_name") or "Basic Form")

                for pm in pcf_meta:
                    fid = pm.get(form_lookup)
                    target_entity, form_name = form_table_map.get(fid, ("", "Basic Form"))
                    col_name = pm.get(attr_col)
                    if target_entity:
                        logical_tbl = self.resolve_entity_name(target_entity)
                        occurrences.append({
                            "raw_endpoint": f"PCF Code Component on {form_name}",
                            "table_name": logical_tbl,
                            "table_raw": target_entity,
                            "fields": [col_name.lower()] if col_name else [],
                            "line_number": 1,
                            "context_snippet": (
                                f"// PCF Code Component enabled on Basic Form: {form_name}\n"
                                f"// Target Column: {col_name}\n"
                                f"// Control Style: Code Component (PCF) via Power Pages Web API"
                            ),
                            "component_type": "PCF Code Component (Form)",
                            "component_name": form_name,
                            "component_id": fid or pm.get(id_col),
                            "table_source": meta_tbl,
                            "field_source": style_col,
                            "website_id": website_id or "",
                        })

        # 4. (Removed: Basic Form occurrence generation was server-side, governed by
        #    Table Permissions — NOT by Webapi/<table>/fields.  Basic Form *custom JS*
        #    is already scanned in Step 1 via adx_registercustomjavascript.)

        # 5. Implicit framework usage: Entity List OData + Portal Lookup Controls
        if known_tbl_set:
            implicit = self.scan_implicit_framework_usage(
                known_tables=list(known_tbl_set),
                website_id=website_id,
                table_fields_map=table_fields_map,
            )
            for tbl_occs in implicit.values():
                occurrences.extend(tbl_occs)

        logger.debug("Total occurrences found: %d", len(occurrences))

        return occurrences

    # ------------------------------------------------------------------
    # Implicit Framework Usage Scanner
    # ------------------------------------------------------------------

    # Component types that represent Power Pages framework calls (not custom code)
    FRAMEWORK_COMPONENT_TYPES: set[str] = {
        "Entity List (OData Feed)",
        "Entity List (Portal)",
        "Portal Lookup Control",
        "Dynamic UI Catalog",
    }

    @staticmethod
    def detect_catalog_and_validation_role(table_name: str, fields: list[str]) -> Optional[dict]:
        """
        Heuristically determine if a table is configured for Web API as a:
          1. Dynamic Validation Rule Catalog (e.g. contains validationregex, regexerrormessage)
          2. Cascading / Dependent Child Lookup (e.g. contains parent FK lookup _..._value)
          3. Portal-Filtered Option Catalog (e.g. contains showonportal, showinportal)
          4. Portal Navigation / Flow Step Helper (e.g. editguid, createguid, stepid)
          5. Geographic / Address Lookup (e.g. postcode, acorncategory, statename)

        These tables are often queried dynamically by generic client helpers, PCF controls,
        or portal form validation scripts. Disabling Web API on these breaks portal UI.
        """
        if not fields:
            return None

        clean_fields = [f.strip().lower() for f in fields if f and f != "*"]
        if not clean_fields:
            return None

        matched_roles: list[dict] = []
        lower_tbl = table_name.lower()

        # 1. Validation Schemes (Regex / Validation rules)
        regex_attrs = [f for f in clean_fields if any(k in f for k in (
            "validationregex", "regexvalidationmessage", "regexerrormessage",
            "validationmessage", "subtestsmin", "subtestsmax", "scoreinterval",
            "applyportalvalidation", "maxscore", "postcoderegex"
        ))]
        if regex_attrs:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "validation_scheme",
                "role_title": "Dynamic Validation Rule Catalog",
                "badge": "🧩 Dynamic Validation Scheme",
                "description": (
                    f"Table delivers client-side validation rules ({', '.join(regex_attrs)}) "
                    f"to dynamically evaluate form inputs in real time before submission."
                ),
                "key_attributes": regex_attrs,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal forms depend on its validation rules "
                    f"to validate user entries on the fly."
                ),
            })

        # 2. Cascading / Dependent Child Lookups
        # Fields like _parent_value, _country_value, _lookup_value
        system_lookup_skips = {
            "_createdby_value", "_createdonbehalfby_value",
            "_modifiedby_value", "_modifiedonbehalfby_value",
            "_ownerid_value", "_owningbusinessunit_value",
            "_owninguser_value", "_owningteam_value",
            "_regardingobjectid_value", "_transactioncurrencyid_value",
            "_stageid_value", "_processid_value",
        }
        lookup_attrs = [
            f for f in clean_fields
            if f.startswith("_") and f.endswith("_value") and f not in system_lookup_skips
        ]
        if lookup_attrs and len(clean_fields) <= 10:
            parents = []
            for lk in lookup_attrs:
                core = lk[1:-6]
                parents.append(core)
            parent_str = ", ".join(parents)
            matched_roles.append({
                "is_catalog": True,
                "role_type": "cascading_lookup",
                "role_title": "Cascading Dependent Lookup",
                "badge": "🔗 Cascading Child Lookup",
                "description": (
                    f"Table functions as a child lookup catalog filtered dynamically by parent entity "
                    f"({parent_str}) using /_api/{table_name}?$filter={lookup_attrs[0]} eq <id>."
                ),
                "key_attributes": lookup_attrs,
                "parent_tables": parents,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Cascading dropdowns (filtered by {parent_str}) "
                    f"require this endpoint to populate child options."
                ),
            })

        # 3. Portal Filtered Option Catalogs / Reason Codes
        portal_filter_attrs = [f for f in clean_fields if any(k in f for k in (
            "showonportal", "showinportal", "visibleinportal", "displayonportal", "portaldisplay",
            "portalvisible", "portalvisibility", "portalactive"
        ))]
        if portal_filter_attrs:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "portal_picklist",
                "role_title": "Portal-Filtered Option Catalog",
                "badge": "📋 Filtered Option Catalog",
                "description": (
                    f"Table acts as a dynamic picklist / reason code catalog with portal visibility flags "
                    f"({', '.join(portal_filter_attrs)}) to populate conditional choice dropdowns."
                ),
                "key_attributes": portal_filter_attrs,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal dropdowns query this table to display "
                    f"authorized options/reasons to users."
                ),
            })

        # 4. Portal Step / Navigation Configuration
        step_attrs = [f for f in clean_fields if any(k in f for k in (
            "editguid", "createguid", "stepid", "portalshowchoicelookup", "portalstep", "formstep"
        ))]
        if step_attrs:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "portal_navigation",
                "role_title": "Portal Navigation / Step Helper",
                "badge": "🧭 Step Navigation Helper",
                "description": (
                    f"Table provides dynamic form step routing and GUID mappings ({', '.join(step_attrs)}) "
                    f"for portal multi-step application flows."
                ),
                "key_attributes": step_attrs,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal navigation scripts reference these step GUIDs."
                ),
            })

        # 5. Geographic / Address Verification Catalog
        geo_attrs = [f for f in clean_fields if any(k in f for k in (
            "statename", "postcoderegex", "postcodemandatory", "staterequired", "countryname", "zipcode", "postalcode"
        )) or any(k in lower_tbl for k in ("country", "state", "postcode", "address", "zip"))]
        if geo_attrs and ("country" in lower_tbl or "state" in lower_tbl or "postcode" in lower_tbl or "address" in lower_tbl):
            matched_roles.append({
                "is_catalog": True,
                "role_type": "geo_catalog",
                "role_title": "Geographic / Address Catalog",
                "badge": "🌍 Address & Geo Catalog",
                "description": (
                    f"Table provides real-time address validation, state/province cascading, or postcode mapping "
                    f"({', '.join(geo_attrs)}) in portal forms."
                ),
                "key_attributes": geo_attrs,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Address and postcode lookup controls depend on it."
                ),
            })

        # 6. Application / Portal Settings & Configuration Store (Key-Value Parameters)
        # e.g. app_settings, app_config with fields like value, settingvalue, config_value
        is_setting_tbl = any(k in lower_tbl for k in ("setting", "config", "parameter", "preference", "constant", "featureflag"))
        setting_val_attrs = [f for f in clean_fields if any(k in f for k in ("value", "val", "setting", "config", "param", "key", "enabled")) and f != "statecode"]
        if is_setting_tbl or (setting_val_attrs and any("setting" in f or "config" in f for f in clean_fields)):
            matched_roles.append({
                "is_catalog": True,
                "role_type": "system_settings",
                "role_title": "Application Settings & Feature Flags Store",
                "badge": "⚙️ Configuration & Settings Store",
                "description": (
                    f"Table functions as a centralized key-value settings or feature flags repository "
                    f"queried by portal scripts to read runtime operational parameters ({', '.join(setting_val_attrs) if setting_val_attrs else 'values'})."
                ),
                "key_attributes": setting_val_attrs or clean_fields,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal scripts query this table to read "
                    f"feature toggles, validation thresholds, or runtime configuration."
                ),
            })

        # 7. Entity Policy & Feature Toggles (e.g. dynamic business toggles, eligibility flags)
        toggle_attrs = [f for f in clean_fields if any(k in f for k in (
            "allow", "allowed", "enable", "enabled",
            "eligible", "eligibility", "optin", "optout", "permitted", "featureenabled", "isactive"
        )) and f not in ("statecode", "statuscode")]
        if toggle_attrs:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "policy_feature_toggle",
                "role_title": "Entity Policy & Feature Toggle Catalog",
                "badge": "🚩 Policy & Feature Toggles",
                "description": (
                    f"Table delivers entity-level feature flags and workflow routing toggles ({', '.join(toggle_attrs)}) "
                    f"to dynamically switch portal forms, permissions, or process flows."
                ),
                "key_attributes": toggle_attrs,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal client scripts query these flags to determine "
                    f"workflow routing and feature availability."
                ),
            })

        # 8. Calendar, Session & Policy Quota Catalog (e.g. calendaryear, academicyear)
        calendar_attrs = [f for f in clean_fields if any(k in f for k in (
            "startdate", "enddate", "numericyear", "academicyear", "sessionyear",
            "maxnumber", "quota", "allowance", "semester", "term", "fiscalyear"
        ))]
        is_calendar_tbl = any(k in lower_tbl for k in ("year", "term", "session", "semester", "calendar"))
        if is_calendar_tbl or calendar_attrs:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "calendar_quota",
                "role_title": "Calendar Session & Policy Quotas",
                "badge": "📅 Session & Quotas",
                "description": (
                    f"Table defines calendar date boundaries and policy allowances "
                    f"({', '.join(calendar_attrs) if calendar_attrs else 'session parameters'}) for portal forms."
                ),
                "key_attributes": calendar_attrs or clean_fields,
                "safety_advice": (
                    f"Do NOT disable Web API for '{table_name}'. Portal scripts query this table to validate "
                    f"dates and enforce policy quotas."
                ),
            })

        # 9. Fallback if no specific role matched: Universal Reference & Lookup Catalog
        # Only true small lookup tables (<= 10 columns) are reference catalogs. Large entities (> 10 columns) are business entities.
        if not matched_roles and len(clean_fields) <= 10:
            matched_roles.append({
                "is_catalog": True,
                "role_type": "reference_catalog",
                "role_title": "Reference & Lookup Catalog",
                "badge": "📚 Reference & Lookup Catalog",
                "description": (
                    f"Table is configured with an explicit whitelist ({len(clean_fields)} attributes). "
                    f"In Power Pages, reference catalogs and lookup tables are queried dynamically by generic client helpers, "
                    f"portal framework lookups, or PCF components rather than static hardcoded scripts."
                ),
                "key_attributes": clean_fields,
                "safety_advice": (
                    f"Preserve this explicit whitelist. Web API site settings for reference catalogs ensure portal lookups and "
                    f"dynamic queries function without exposing unauthorized columns."
                ),
            })

        if not matched_roles:
            return None

        # Return single role if only 1 matched
        if len(matched_roles) == 1:
            matched_roles[0]["all_roles"] = matched_roles
            return matched_roles[0]

        # Multi-Role Composite Catalog
        all_key_attrs: list[str] = []
        for r in matched_roles:
            for a in r.get("key_attributes", []):
                if a not in all_key_attrs:
                    all_key_attrs.append(a)

        badges = [r["badge"] for r in matched_roles]
        composite_badge = " · ".join(badges[:2]) + (f" (+{len(badges)-2} more)" if len(badges) > 2 else "")
        titles = [r["role_title"] for r in matched_roles]
        composite_title = f"Multi-Role Catalog ({', '.join(titles)})"
        role_summaries = "; ".join(f"[{r['badge']}]: {r['description']}" for r in matched_roles)
        composite_desc = f"Table serves {len(matched_roles)} concurrent architectural roles: {role_summaries}"
        composite_advice = f"Do NOT disable Web API for '{table_name}'. It serves {len(matched_roles)} essential portal roles ({', '.join(titles)})."

        return {
            "is_catalog": True,
            "is_multi_role": True,
            "role_type": "multi_role",
            "role_title": composite_title,
            "badge": composite_badge,
            "description": composite_desc,
            "key_attributes": all_key_attrs,
            "all_roles": matched_roles,
            "safety_advice": composite_advice,
        }

    @staticmethod
    def _is_webapi_governed(occ: dict) -> bool:
        """
        Return True ONLY if this occurrence represents a call actually restricted
        by the Webapi/<table>/fields site setting.

        The setting governs client-side /_api/ HTTP calls ONLY:
          ✓ Direct fetch / XHR / $.ajax / axios calls to /_api/<table>
          ✓ SDK wrappers: webAPI.retrieveRecord, Xrm.WebApi.*, PortalAPI.*
          ✓ PCF Code Components using context.webAPI
          ✓ Entity List OData Feed  (/_api/entitylists/{id}/entityrecords)
          ✓ Portal Lookup Control auto-calls (framework issues /_api/<table>?$select=...)
          ✓ Dynamic UI Catalogs (cascading lookups, validation schemes)

        NOT governed (server-side, Table Permissions apply instead):
          ✗ FetchXML in Liquid templates  ({% fetchxml %} / <entity name="...">)
          ✗ Liquid entityview / entityform / recordview tags
          ✗ entities['...'] Liquid lookups
          ✗ Standard Basic Forms / Multistep Forms / Entity Lists (no OData)
          ✗ Fuzzy text-mention matches ("Portal Template / Code Reference")
        """
        raw_ep = (occ.get("raw_endpoint") or "").lower()
        comp_type = (occ.get("component_type") or "")

        # Framework signals that genuinely use /_api/ under the hood
        # Note: "Entity List (Portal)" WITHOUT OData is server-side — excluded
        if comp_type in ("Entity List (OData Feed)", "Portal Lookup Control", "Dynamic UI Catalog"):
            return True

        # PCF Code Components call context.webAPI
        if "pcf" in comp_type.lower():
            return True

        # Direct /_api/ endpoint call (fetch, XHR, $.ajax, axios, safeAjax)
        if "/_api/" in raw_ep:
            return True

        # Custom discovered wrapper call or marked wrapper call
        if occ.get("is_wrapper_call"):
            return True

        # SDK wrapper calls (webAPI.retrieveRecord, Xrm.WebApi, PortalAPI, safeAjax, and discovered wrappers)
        standard_wrappers = {".webapi", "xrm.webapi", "portalapi", "safeajax"}
        all_wrappers = standard_wrappers | PortalWebApiAuditorClient._DISCOVERED_WRAPPERS
        if any(t in raw_ep for t in all_wrappers):
            return True

        # OData Query pattern produced by the api_pattern regex scanner
        if raw_ep.startswith("odata query:"):
            return True

        # fetch/axios/$.ajax/XHR scanner pattern
        if "http client call" in raw_ep:
            return True

        # Client-side JavaScript references: code in registercustomjavascript, customjavascript,
        # Web Files, or any component originating from client script
        field_src = (occ.get("field_source") or "").lower()
        if (
            "customjavascript" in field_src
            or "registerstartupscript" in field_src
            or "registercustomjavascript" in field_src
            or "web file" in comp_type.lower()
            or "script" in comp_type.lower()
            or "client script" in raw_ep
        ):
            return True

        return False

    def scan_implicit_framework_usage(
        self,
        known_tables: list[str],
        website_id: Optional[str] = None,
        table_fields_map: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, list[dict]]:
        """
        Detect implicit Power Pages framework Web API calls for known tables:

        Signal 1 – Entity List OData Feed
          Entity Lists can expose their data as an OData feed via /_api/entitylists({id})/entityrecords.
          When OData is enabled the browser calls that endpoint directly for the table's rows.
          Even when OData is disabled, listing a table in an Entity List causes the portal framework
          to call /_api/<table> for column-filter lookups and sorting.

        Signal 2 – Portal Lookup Control (auto-generated by Power Pages framework)
          When a Basic Form on table B has a lookup column pointing to table A, the portal
          framework auto-issues /_api/<table_A>?$select=<fields>&$filter=statecode eq 0
          in the browser to populate the lookup dropdown.  The developer never writes this JS;
          Power Pages injects it automatically at runtime.
          Without Webapi/<table_A>/fields configured, the lookup dropdown fails to populate.
        """
        result: dict[str, list[dict]] = {t: [] for t in known_tables}
        known_set = set(known_tables)

        # --- Signal 1: Entity List OData Feed ---
        for schema in ["adx", "mspp"]:
            el_tbl = f"{schema}_entitylists"
            id_col = f"{schema}_entitylistid"
            name_col = f"{schema}_name"
            ent_col = f"{schema}_entityname"
            odata_col = f"{schema}_odata_enabled"
            site_col = f"_{schema}_websiteid_value"

            query = f"{el_tbl}?$select={id_col},{name_col},{ent_col},{odata_col},{site_col}"
            if website_id:
                query += f"&$filter={site_col} eq {website_id}"

            rows = self._fetch_all_pages(query, max_pages=10)
            for row in rows:
                ent_name = (row.get(ent_col) or "").strip().lower()
                if ent_name not in known_set:
                    continue

                odata_on = bool(row.get(odata_col))
                list_name = row.get(name_col) or "Entity List"
                list_id = row.get(id_col) or ""
                web_id = row.get(site_col) or website_id or ""
                comp_type = "Entity List (OData Feed)" if odata_on else "Entity List (Portal)"
                endpoint = (
                    f"/_api/entitylists('{list_id}')/entityrecords"
                    if odata_on
                    else f"/_api/{ent_name}s (implicit – portal list view)"
                )

                el_fields = [f for f in (table_fields_map or {}).get(ent_name, []) if f and f != "*"] or ["statecode", f"{ent_name}id", "name"]
                result[ent_name].append({
                    "raw_endpoint": endpoint,
                    "table_name": ent_name,
                    "table_raw": ent_name,
                    "fields": sorted(list(el_fields)),
                    "line_number": 1,
                    "context_snippet": (
                        f"// Entity List: {list_name}\n"
                        f"// OData Feed: {'Enabled — browser calls /_api/entitylists({list_id})/entityrecords' if odata_on else 'Disabled — portal list still issues column/filter lookups via Web API'}\n"
                        f"// Web API site settings REQUIRED for column filtering and OData access to work."
                    ),
                    "component_type": comp_type,
                    "component_name": list_name,
                    "component_id": list_id,
                    "table_source": el_tbl,
                    "field_source": odata_col,
                    "website_id": web_id,
                })

        # --- Signal 2: Portal Forms & Multistep Steps with Custom Lookup Web API Coding or PCF Controls ---
        emitted_keys: set[tuple[str, str]] = set()

        for schema in ["adx", "mspp"]:
            form_targets = [
                (
                    f"{schema}_entityforms",
                    f"{schema}_entityformid",
                    f"{schema}_name",
                    f"{schema}_entityname",
                    f"_{schema}_websiteid_value",
                    f"{schema}_formname",
                    f"{schema}_registerstartupscript",
                    f"{schema}_entityformmetadatas",
                    f"_{schema}_entityformid_value",
                    "Basic Form",
                ),
                (
                    f"{schema}_webformsteps",
                    f"{schema}_webformstepid",
                    f"{schema}_name",
                    f"{schema}_targetentitylogicalname",
                    None,
                    f"{schema}_formname",
                    f"{schema}_registerstartupscript",
                    f"{schema}_webformstepmetadatas",
                    f"_{schema}_webformstepid_value",
                    "Multistep Form Step",
                ),
            ]

            for ef_tbl, id_col, name_col, ent_col, site_col, formname_col, js_col, meta_tbl, meta_fk_col, comp_kind in form_targets:
                base_cols = [id_col, name_col, ent_col, formname_col]
                if site_col:
                    base_cols.append(site_col)
                cols_to_sel = base_cols + ([js_col] if js_col else [])
                q = f"{ef_tbl}?$select={','.join(cols_to_sel)}"
                if website_id and site_col:
                    q += f"&$filter={quote(f'({site_col} eq {website_id} or {site_col} eq null)')}"

                portal_forms = self._fetch_all_pages(q, max_pages=15)
                if not portal_forms and js_col:
                    # Fallback without js_col so Form XML layout fields are NEVER dropped if script column name differs
                    fallback_q = f"{ef_tbl}?$select={','.join(base_cols)}"
                    if website_id and site_col:
                        fallback_q += f"&$filter={quote(f'({site_col} eq {website_id} or {site_col} eq null)')}"
                    portal_forms = self._fetch_all_pages(fallback_q, max_pages=15)
                for form in portal_forms:
                    fid = form.get(id_col)
                    fname = form.get(name_col) or comp_kind
                    fent = (form.get(ent_col) or "").strip().lower()
                    mda_form = form.get(formname_col) or ""
                    custom_js = form.get(js_col) or ""

                    # 0. Extract model-driven Form XML fields for this Basic Form / Multistep Step
                    if fent and mda_form:
                        form_fields = self.get_form_fields_by_name(fent, mda_form)
                        if form_fields:
                            dedup_form_k = (fent, f"formxml_{fid or fname}")
                            if dedup_form_k not in emitted_keys:
                                emitted_keys.add(dedup_form_k)
                                result.setdefault(fent, []).append({
                                    "raw_endpoint": f"Model-Driven Form: '{mda_form}' (Table: {fent})",
                                    "table_name": fent,
                                    "table_raw": fent,
                                    "fields": form_fields,
                                    "line_number": 1,
                                    "context_snippet": (
                                        f"// {comp_kind}: {fname} (Target Table: {fent})\n"
                                        f"// Model-Driven Form Layout: '{mda_form}'\n"
                                        f"// Form XML Fields ({len(form_fields)} fields rendered on form layout):\n"
                                        f"// {', '.join(form_fields)}"
                                    ),
                                    "component_type": f"{comp_kind} (Form XML)",
                                    "component_name": fname,
                                    "component_id": fid or "",
                                    "table_source": ef_tbl,
                                    "field_source": formname_col,
                                    "website_id": form.get(site_col) if site_col else (website_id or ""),
                                    "is_form_xml": True,
                                })

                    # 1. Check Form / Step Metadata for PCF Code Components or Custom Controls
                    if fid:
                        meta_q = (
                            f"{meta_tbl}?$filter={meta_fk_col} eq {fid}"
                            f"&$select={schema}_{'entityformmetadataid' if 'entity' in meta_tbl else 'webformstepmetadataid'},{schema}_attributelogicalname,{schema}_controlstyle"
                        )
                        meta_rows = self._odata_get(meta_q)
                        if meta_rows and meta_rows.get("value"):
                            for mr in meta_rows["value"]:
                                style = mr.get(f"{schema}_controlstyle")
                                attr = (mr.get(f"{schema}_attributelogicalname") or "").lower()
                                # 756150000 = Code Component (PCF)
                                if style in (756150000, "756150000") or "pcf" in str(style).lower():
                                    for tbl in known_set:
                                        if tbl in attr or attr.startswith(f"_{tbl}"):
                                            dedup_k = (tbl, fid)
                                            if dedup_k not in emitted_keys:
                                                emitted_keys.add(dedup_k)
                                                # Determine fields required on target table by the lookup/PCF control
                                                target_attrs = self.get_table_attributes(tbl)
                                                pk = next((a for a in target_attrs if a.endswith("id") and not a.startswith("_")), f"{tbl}id")
                                                pname = next((a for a in target_attrs if a in (f"{tbl}name", f"{tbl}_name", "name", "title")), "name")
                                                base_needed = {pk, pname, "statecode"}
                                                cfg_fields = set(f for f in (table_fields_map or {}).get(tbl, []) if f and f != "*")
                                                needed_fields = sorted(list(base_needed | cfg_fields))

                                                result[tbl].append({
                                                    "raw_endpoint": f"PCF Code Component on {comp_kind}: {fname} (Field: {attr})",
                                                    "table_name": tbl,
                                                    "table_raw": tbl,
                                                    "fields": needed_fields,
                                                    "line_number": 1,
                                                    "context_snippet": (
                                                        f"// {comp_kind}: {fname} (Model-driven form: '{mda_form}', table: {fent})\n"
                                                        f"// Control Style: Code Component (PCF) enabled on attribute '{attr}'\n"
                                                        f"// PCF controls consume client-side Web API (context.webAPI) requiring Webapi/{tbl}/fields: {', '.join(needed_fields)}"
                                                    ),
                                                    "component_type": "Portal Lookup Control",
                                                    "component_name": fname,
                                                    "component_id": fid,
                                                    "table_source": ef_tbl,
                                                    "field_source": attr,
                                                    "website_id": form.get(site_col) if site_col else (website_id or ""),
                                                })

                    # 2. Check custom JS on portal form/step for lookup checking / typeahead / Web API calls
                    if custom_js and known_set:
                        lower_js = custom_js.lower()
                        for tbl in known_set:
                            # Match table name or plural form in JS (e.g. custom_entity or custom_entities)
                            tbl_pat = re.compile(r'\b' + re.escape(tbl) + r'(?:s|ies)?\b', re.IGNORECASE)
                            if tbl_pat.search(lower_js):
                                dedup_k = (tbl, fid or fname)
                                if dedup_k not in emitted_keys:
                                    emitted_keys.add(dedup_k)
                                    # Extract any valid fields of tbl mentioned in the script
                                    tbl_valid_attrs = self.get_table_attributes(tbl)
                                    fields_hit = set()
                                    for a in tbl_valid_attrs:
                                        if a and ("_" in a or len(a) >= 4) and re.search(r'\b' + re.escape(a) + r'\b', lower_js):
                                            fields_hit.add(a)

                                    result[tbl].append({
                                        "raw_endpoint": f"{comp_kind} Custom Script Reference: {fname}",
                                        "table_name": tbl,
                                        "table_raw": tbl,
                                        "fields": sorted(list(fields_hit)),
                                        "line_number": 1,
                                        "context_snippet": (
                                            f"// {comp_kind}: {fname} (Model-driven form: '{mda_form}', target table: {fent})\n"
                                            f"// Custom JavaScript on this portal step interacts with {tbl}\n"
                                            f"// Detected fields: {', '.join(sorted(list(fields_hit))) if fields_hit else 'None'}"
                                        ),
                                        "component_type": f"{comp_kind} Script",
                                        "component_name": fname,
                                        "component_id": fid,
                                        "table_source": ef_tbl,
                                        "field_source": js_col,
                                        "website_id": form.get(site_col) if site_col else (website_id or ""),
                                    })

        # --- Signal 3: Dynamic Validation Schemes & Cascading UI Catalogs ---
        for tbl in known_tables:
            # If no framework or script occurrences have been detected for tbl yet
            if not result.get(tbl):
                cfg_flds = (table_fields_map or {}).get(tbl, [])
                cat_role = self.detect_catalog_and_validation_role(tbl, cfg_flds)
                if cat_role:
                    key_attrs = cat_role.get("key_attributes", [])
                    clean_cfg_flds = [f for f in cfg_flds if f and f != "*"]
                    result[tbl].append({
                        "raw_endpoint": f"Dynamic UI Catalog ({cat_role['role_title']})",
                        "table_name": tbl,
                        "table_raw": tbl,
                        "fields": sorted(list(set(clean_cfg_flds))),
                        "line_number": 1,
                        "context_snippet": (
                            f"// Table: {tbl}\n"
                            f"// Architectural Role: {cat_role['role_title']} ({cat_role['badge']})\n"
                            f"// Purpose: {cat_role['description']}\n"
                            f"// Key Diagnostic Attributes: {', '.join(key_attrs) if key_attrs else 'N/A'}\n"
                            f"// Guidance: {cat_role['safety_advice']}"
                        ),
                        "component_type": "Dynamic UI Catalog",
                        "component_name": f"{cat_role['badge']} ({tbl})",
                        "component_id": f"catalog_{tbl}",
                        "table_source": "Site Setting Whitelist",
                        "field_source": f"Webapi/{tbl}/fields",
                        "website_id": website_id or "",
                    })

        return result


    @staticmethod
    def _parse_form_control_attributes(formxml: str) -> set[str]:
        """
        Parse a Dynamics 365 / Power Platform model-driven form's XML and return
        the set of attribute logical names that are actually rendered as controls.

        The formxml contains <control datafieldname="<attribute_logical_name>"/> elements
        for every field placed on the form layout.  We collect all datafieldname values
        so that the lookup cross-reference check can confirm whether a lookup attribute
        is truly visible on the form (and therefore triggers a Web API call) versus
        merely having a Dataverse relationship that is never displayed.

        Returns an empty set if the formxml is empty, invalid, or unparseable —
        the caller should treat an empty set as 'unknown / emit conservatively'.
        """
        attrs: set[str] = set()
        if not formxml:
            return attrs
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(formxml)
            for control in root.iter("control"):
                datafieldname = (control.get("datafieldname") or "").strip().lower()
                if datafieldname:
                    attrs.add(datafieldname)
        except Exception:
            pass
        return attrs

    def _extract_fetchxml_usage(self, code: str, allowed_tables: Optional[set[str]] = None) -> list[dict]:

        """
        Extract FetchXML entity queries and their <attribute name="..." />, <condition attribute="..." /> tags.
        Accurately parses both root <entity> and linked <link-entity> nodes, assigning attributes
        strictly to the entity that owns them.
        """
        results: list[dict] = []
        lines = code.splitlines()

        # Parse each <entity> and <link-entity> individually
        pattern = re.compile(
            r'<(entity|link-entity)\s+[^>]*?name=["\']([a-zA-Z0-9_]+)["\']([^>]*)>(.*?)(?=<(?:entity|link-entity)|\Z)',
            re.DOTALL | re.IGNORECASE,
        )

        for match in pattern.finditer(code):
            node_type = match.group(1).lower()
            raw_ent = match.group(2).strip().lower()
            content = match.group(4)
            logical_tbl = self.resolve_entity_name(raw_ent)
            if allowed_tables and logical_tbl not in allowed_tables:
                continue

            attrs = set(re.findall(r'<attribute\s+name=["\']([a-zA-Z0-9_]+)["\']', content, re.IGNORECASE))
            conds = set(re.findall(r'<condition\s+attribute=["\']([a-zA-Z0-9_]+)["\']', content, re.IGNORECASE))
            orders = set(re.findall(r'<order\s+attribute=["\']([a-zA-Z0-9_]+)["\']', content, re.IGNORECASE))
            has_all_attributes = bool(re.search(r'<all-attributes\s*/?>', content, re.IGNORECASE))
            all_fields = sorted(list(f.lower() for f in (attrs | conds | orders) if f and f != "*"))

            start_pos = match.start()
            line_num = code[:start_pos].count("\n") + 1

            endpoint_label = f"FetchXML {'Linked ' if node_type == 'link-entity' else ''}Entity: {raw_ent}"
            if has_all_attributes:
                endpoint_label += " (<all-attributes /> SELECT *)"

            results.append({
                "raw_endpoint": endpoint_label,
                "table_name": logical_tbl,
                "table_raw": raw_ent,
                "fields": all_fields,
                "is_unprojected": False,
                "is_fetchxml": True,           # ← signals reconciler to route to server_xml tier
                "has_all_attributes": has_all_attributes,
                "unprojected_reason": "",
                "line_number": line_num,
                "context_snippet": "\n".join(lines[max(0, line_num - 2):min(len(lines), line_num + 5)]),
            })
        return results

    def _extract_liquid_entity_usage(self, code: str, allowed_tables: Optional[set[str]] = None) -> list[dict]:
        """
        Extract Liquid entity usages like entities['custom_agreement']
        or entities.custom_agreement.
        """
        results: list[dict] = []
        liquid_pattern = re.compile(
            r"""entities\[["']([a-zA-Z0-9_]+)["']\](?:\.([a-zA-Z0-9_]+)|\[["']([a-zA-Z0-9_]+)["']\])?""",
            re.IGNORECASE,
        )
        lines = code.splitlines()

        for match in liquid_pattern.finditer(code):
            raw_ent = match.group(1).strip().lower()
            field_name = (match.group(2) or match.group(3) or "").strip().lower()
            logical_tbl = self.resolve_entity_name(raw_ent)
            if allowed_tables and logical_tbl not in allowed_tables:
                continue

            start_pos = match.start()
            line_num = code[:start_pos].count("\n") + 1

            results.append({
                "raw_endpoint": f"Liquid Entity: entities['{raw_ent}']",
                "table_name": logical_tbl,
                "table_raw": raw_ent,
                "fields": [field_name] if field_name else [],
                "is_liquid": True,             # ← signals reconciler to route to server_xml tier
                "line_number": line_num,
                "context_snippet": "\n".join(lines[max(0, line_num - 2):min(len(lines), line_num + 3)]),
            })
        return results

    def _extract_liquid_tag_usage(self, code: str, allowed_tables: Optional[set[str]] = None) -> list[dict]:
        """
        Extract Liquid Power Pages portal tag usages that reference a table/entity by logical name.
        Covers:
          - {% entityview logicalname:'table_name' ... %}
          - {% entityform logicalname:'table_name' ... %}
          - {% recordview logicalname:'table_name' ... %}
          - {% assign result = entityviewresult | where: 'table_name' ... %}
          - [entityview:table_name] / [entityform:table_name] shorthand

        Note: These tags use server-side Table Permissions, NOT the client-side Web API.
        They are recorded as informational code references (not whitelist-violation triggers).
        """
        results: list[dict] = []
        lines = code.splitlines()

        # Pattern: {% entityview logicalname:'incident' ... %} or {% entityform logicalname:"incident" %}
        tag_pattern = re.compile(
            r"""\{%-?\s*(entityview|entityform|recordview|entitylist)\s[^%]*?logicalname\s*:\s*["']([a-zA-Z0-9_]+)["']""",
            re.IGNORECASE,
        )

        # Pattern: {% assign x = entities['incident'] %} or entities.incident
        assign_pattern = re.compile(
            r"""\{%-?\s*assign\s+\w+\s*=\s*entities(?:\[["']([a-zA-Z0-9_]+)["']\]|\.([a-zA-Z0-9_]+))""",
            re.IGNORECASE,
        )

        # Pattern: {% fetchxml alias %} already handled; also catch endfetchxml variable usage
        # fetchxml tag results accessed via variable: {{ result.results.entities }}
        # The entity name itself is inside the XML block handled by _extract_fetchxml_usage

        for pattern in (tag_pattern, assign_pattern):
            for match in pattern.finditer(code):
                groups = match.groups()
                # For tag_pattern: groups = (tag_type, table_name)
                # For assign_pattern: groups = (table_name_bracket, table_name_dot)
                if pattern is tag_pattern:
                    tag_type = groups[0].strip().lower()
                    raw_ent = (groups[1] or "").strip().lower()
                else:
                    tag_type = "entities_assign"
                    raw_ent = (groups[0] or groups[1] or "").strip().lower()

                if not raw_ent:
                    continue

                logical_tbl = self.resolve_entity_name(raw_ent)
                if allowed_tables and logical_tbl not in allowed_tables:
                    continue

                start_pos = match.start()
                line_num = code[:start_pos].count("\n") + 1

                results.append({
                    "raw_endpoint": f"Liquid Tag ({tag_type}): {raw_ent}",
                    "table_name": logical_tbl,
                    "table_raw": raw_ent,
                    "fields": [],
                    "is_liquid": True,          # ← signals reconciler to route to server_xml tier
                    "line_number": line_num,
                    "context_snippet": "\n".join(lines[max(0, line_num - 2):min(len(lines), line_num + 3)]),
                })

        return results

    def _analyze_odata_query(self, query_str: str) -> dict:
        """
        Analyze an OData query string for $select, $filter, $orderby, $expand and detect
        unprojected queries (missing root $select or using wildcard $select=*).
        Power Pages Web API requires explicit whitelisted columns in $select once '*' is deprecated.
        Note: A $select inside $expand(...) only projects the expanded related entity and DOES NOT
        protect the root entity from an unprojected HTTP 403 error.
        """
        fields: set[str] = set()
        select_fields: list[str] = []
        filter_fields: list[str] = []
        orderby_fields: list[str] = []
        has_root_select = False
        is_wildcard_select = False
        expand_names: list[str] = []

        if not query_str:
            return {
                "fields": fields,
                "select_fields": select_fields,
                "has_select": False,
                "has_root_select": False,
                "is_wildcard_select": False,
                "is_unprojected": True,
                "unprojected_reason": "Missing $select query option (implicit SELECT *)",
                "filter_fields": filter_fields,
                "orderby_fields": orderby_fields,
                "expands": expand_names,
            }

        reserved_keywords = {
            "eq", "ne", "gt", "ge", "lt", "le", "and", "or", "not", "null",
            "true", "false", "asc", "desc", "contains", "startswith", "endswith",
            "in", "year", "month", "day", "hour", "minute", "second",
            "select", "filter", "orderby", "top", "skip", "expand", "apply",
        }

        # Mask out content inside parentheses to isolate root-level OData options
        # This prevents $select inside $expand(...) from being mistaken for a root $select
        masked = []
        depth = 0
        for char in query_str:
            if char == "(":
                depth += 1
                masked.append(" ")
            elif char == ")":
                depth = max(0, depth - 1)
                masked.append(" ")
            elif depth > 0:
                masked.append(" ")
            else:
                masked.append(char)
        root_query = "".join(masked)

        # Detect $expand navigation properties
        for m in re.finditer(r"\$expand=([a-zA-Z0-9_]+)", query_str, re.IGNORECASE):
            expand_names.append(m.group(1))

        # 1. Root $select=col1,col2,...
        root_select_match = re.search(r"(?:[?&]|\b)\$select=([a-zA-Z0-9_,\s*]+?)(?:[&\"'`\}\]\r\n]|$)", root_query, re.IGNORECASE)
        if root_select_match:
            has_root_select = True
            raw_cols = root_select_match.group(1).split(",")
            for f in raw_cols:
                f_clean = f.strip().lower()
                if f_clean == "*":
                    is_wildcard_select = True
                elif f_clean and f_clean not in reserved_keywords and not f_clean.isdigit() and not self._is_guid_or_hex_fragment(f_clean):
                    fields.add(f_clean)
                    select_fields.append(f_clean)

        # 2. Check if $select appears only inside $expand(...)
        any_select_match = re.search(r"\$select=([a-zA-Z0-9_,\s*]+?)(?:[&\"'`\)\}\]\r\n]|$)", query_str, re.IGNORECASE)
        has_any_select = bool(any_select_match)

        # 3. $orderby=col1 asc, col2 desc,... at root
        orderby_match = re.search(r"(?:[?&]|\b)\$orderby=([a-zA-Z0-9_,\s]+?)(?:[&\"'`\}\]\r\n]|$)", root_query, re.IGNORECASE)
        if orderby_match:
            for item in orderby_match.group(1).split(","):
                parts = item.strip().split()
                if parts:
                    f_clean = parts[0].strip().lower()
                    if f_clean and f_clean != "*" and f_clean not in reserved_keywords and not f_clean.isdigit() and not self._is_guid_or_hex_fragment(f_clean):
                        fields.add(f_clean)
                        orderby_fields.append(f_clean)

        # 4. $filter comparison operators: col eq ..., col gt ..., etc.
        # Prefer matching in root_query first to avoid attributing related entity attributes to root entity
        filter_matches = re.findall(r"""\b([a-zA-Z_][a-zA-Z0-9_]{1,50})\s+(?:eq|ne|gt|ge|lt|le)\b""", root_query, re.IGNORECASE)
        if not filter_matches:
            filter_matches = re.findall(r"""\b([a-zA-Z_][a-zA-Z0-9_]{1,50})\s+(?:eq|ne|gt|ge|lt|le)\b""", query_str, re.IGNORECASE)
        for fm in filter_matches:
            clean_fm = fm.strip().lower()
            if clean_fm and clean_fm not in reserved_keywords and not clean_fm.isdigit() and not self._is_guid_or_hex_fragment(clean_fm):
                fields.add(clean_fm)
                filter_fields.append(clean_fm)

        # 5. $filter functions: contains(col, '...'), startswith(col, '...'), endswith(col, '...')
        fn_matches = re.findall(r"""\b(?:contains|startswith|endswith)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]{1,50})\s*,""", root_query, re.IGNORECASE)
        if not fn_matches:
            fn_matches = re.findall(r"""\b(?:contains|startswith|endswith)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]{1,50})\s*,""", query_str, re.IGNORECASE)
        for fnm in fn_matches:
            clean_fnm = fnm.strip().lower()
            if clean_fnm and clean_fnm not in reserved_keywords and not clean_fnm.isdigit() and not self._is_guid_or_hex_fragment(clean_fnm):
                fields.add(clean_fnm)
                filter_fields.append(clean_fnm)

        is_unprojected = (not has_root_select) or is_wildcard_select or (len(select_fields) == 0)

        unproj_reason = ""
        if is_unprojected:
            if is_wildcard_select:
                unproj_reason = "Explicit $select=* wildcard projection"
            elif not has_root_select and has_any_select and expand_names:
                unproj_reason = f"Missing root $select query option (implicit SELECT * on root table; $select inside $expand({expand_names[0]}) only projects the related entity)"
            elif not has_root_select:
                unproj_reason = "Missing $select query option (implicit SELECT *)"
            else:
                unproj_reason = "Empty $select query option"

        return {
            "fields": fields,
            "select_fields": sorted(list(set(select_fields))),
            "has_select": has_root_select,
            "has_root_select": has_root_select,
            "is_wildcard_select": is_wildcard_select,
            "is_unprojected": is_unprojected,
            "unprojected_reason": unproj_reason,
            "filter_fields": sorted(list(set(filter_fields))),
            "orderby_fields": sorted(list(set(orderby_fields))),
            "expands": sorted(list(set(expand_names))),
        }

    def _extract_odata_fields(self, query_str: str) -> set[str]:
        """
        Extract field/column logical names from OData query strings ($select, $filter, $orderby).
        Power Pages Web API requires all columns used in $select, $filter, and $orderby to be whitelisted.
        """
        return self._analyze_odata_query(query_str)["fields"]

    def _extract_function_body(self, code: str, open_brace_pos: int) -> str:
        """
        Extract the code block between matching '{' and '}' starting at open_brace_pos.
        Skips strings and comments so nested braces and callbacks are handled accurately.
        """
        n = len(code)
        if open_brace_pos >= n or code[open_brace_pos] != "{":
            return ""

        depth = 1
        idx = open_brace_pos + 1
        in_string = None  # '"', "'", or '`'
        escape = False
        in_line_comment = False
        in_block_comment = False

        while idx < n and depth > 0:
            ch = code[idx]
            next_ch = code[idx + 1] if idx + 1 < n else ""

            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if ch == "*" and next_ch == "/":
                    in_block_comment = False
                    idx += 1
            elif in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_string:
                    in_string = None
            else:
                if ch == "/" and next_ch == "/":
                    in_line_comment = True
                    idx += 1
                elif ch == "/" and next_ch == "*":
                    in_block_comment = True
                    idx += 1
                elif ch in ('"', "'", "`"):
                    in_string = ch
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return code[open_brace_pos + 1:idx]
            idx += 1

        return code[open_brace_pos + 1:min(open_brace_pos + 3000, n)]

    def _discover_webapi_wrapper_functions(self, all_code_blocks: list[dict]) -> set[str]:
        """
        Pass 1: Scan all JS code across templates, pages, forms, and snippets to automatically
        discover custom Web API wrapper functions (e.g. functions constructing calls to '/_api/').

        Discovers:
          1. Direct Wrappers: Functions containing '/_api/' or safeAjax that accept parameters.
          2. Transitive Wrappers: Functions that delegate their parameters to an already discovered wrapper.
        """
        fn_defs: dict[str, tuple[str, bool]] = {}  # lower_fn_name -> (body, has_params)

        fn_pattern = re.compile(
            r"""(?:function\s+([a-zA-Z0-9_$]+)\s*\(([^)]*)\)|(?:(?:var|let|const|window\.)\s*)?([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:function\s*\(([^)]*)\)|\(([^)]*)\)\s*=>)|([a-zA-Z0-9_$]+)\s*:\s*(?:async\s*)?(?:function\s*\(([^)]*)\)|\(([^)]*)\)\s*=>))\s*\{""",
            re.IGNORECASE,
        )

        reserved_words = {
            "if", "for", "while", "switch", "catch", "function", "return", "try", "else", "do",
            "get", "set", "constructor", "then"
        }

        for block in all_code_blocks:
            code_text = block.get("code_text") or ""
            if not code_text or not isinstance(code_text, str):
                continue

            for match in fn_pattern.finditer(code_text):
                fn_name = (match.group(1) or match.group(3) or match.group(6) or "").strip()
                params = (match.group(2) or match.group(4) or match.group(5) or match.group(7) or match.group(8) or "").strip()
                if not fn_name or fn_name.lower() in reserved_words:
                    continue

                open_brace = match.end() - 1
                body = self._extract_function_body(code_text, open_brace)
                if body:
                    has_params = bool(params or "arguments" in body.lower())
                    fn_defs[fn_name.lower()] = (body, has_params)

        discovered: set[str] = set()

        # Step 1: Direct wrappers (must be parameterized or forward arguments)
        for fn_name, (body, has_params) in fn_defs.items():
            if not has_params:
                continue
            lower_body = body.lower()
            if "/_api/" in lower_body or "safeajax" in lower_body:
                discovered.add(fn_name)

        # Step 2: Transitive propagation (functions calling an already discovered wrapper)
        changed = True
        iterations = 0
        while changed and iterations < 10:
            changed = False
            iterations += 1
            for fn_name, (body, has_params) in fn_defs.items():
                if fn_name not in discovered and has_params:
                    for known in list(discovered):
                        if re.search(r'\b' + re.escape(known) + r'\s*\(', body, re.IGNORECASE):
                            discovered.add(fn_name)
                            changed = True
                            break

        PortalWebApiAuditorClient._DISCOVERED_WRAPPERS.update(discovered)
        self.discovered_wrapper_funcs.update(discovered)
        return discovered

    def _extract_query_helper_functions(self, code: str) -> dict[str, dict]:
        """
        Scan JavaScript code for query-builder helper functions, such as:
          function getAgentContactsByAgentOrgQuery(accountID) {
              var query = `contacts?fetchXml=<fetch ...>...</fetch>`;
              return query;
          }
        or functions returning OData URLs. Returns map of lower_func_name -> {table_name, table_raw, fields, is_fetchxml, raw_endpoint, linked_entities}.
        """
        registry: dict[str, dict] = {}

        # Match function declarations: function funcName(...) { ... }
        # or assignment: var/let/const funcName = (...) => { ... }
        fn_pattern = re.compile(
            r"""(?:function\s+([a-zA-Z0-9_]+)\s*\([^)]*\)|(?:\b(?:var|let|const)\s+)?([a-zA-Z0-9_]+)\s*=\s*(?:function|\([^)]*\)\s*=>))\s*\{?""",
            re.IGNORECASE,
        )

        for match in fn_pattern.finditer(code):
            fn_name = (match.group(1) or match.group(2) or "").strip().lower()
            if not fn_name or fn_name in ("if", "for", "while", "switch", "catch", "then"):
                continue
            start_pos = match.end()
            rest = code[start_pos:]
            next_fn = re.search(r'\n\s*function\b', rest)
            end_pos = next_fn.start() if next_fn else min(len(rest), 3000)
            body = rest[:end_pos]

            # Check for client-side FetchXML query: e.g. `contacts?fetchXml=<fetch ...>`
            fx_m = re.search(
                r"""(?:[/]_api[/]|[`'"])?([a-zA-Z0-9_]{3,60})\?fetchXml=\s*[`'"]?(<fetch\b.*?<\/fetch>)""",
                body,
                re.IGNORECASE | re.DOTALL,
            )
            if fx_m:
                raw_tbl = fx_m.group(1).strip()
                xml_body = fx_m.group(2)
                logical_tbl = self.resolve_entity_name(raw_tbl)

                attrs = set(re.findall(r'<attribute\s+name=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
                conds = set(re.findall(r'<condition\s+attribute=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
                orders = set(re.findall(r'<order\s+attribute=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
                all_fields = sorted(list(f.lower() for f in (attrs | conds | orders) if f and f != "*"))

                linked_entities = []
                for lm in re.finditer(r'<link-entity\s+[^>]*?name=["\']([a-zA-Z0-9_]+)["\']([^>]*)>(.*?)(?=<(?:entity|link-entity)|\Z)', xml_body, re.DOTALL | re.IGNORECASE):
                    l_ent = lm.group(1).strip().lower()
                    l_content = lm.group(3)
                    l_attrs = set(re.findall(r'<attribute\s+name=["\']([a-zA-Z0-9_]+)["\']', l_content, re.IGNORECASE))
                    if l_attrs:
                        linked_entities.append({
                            "table_name": self.resolve_entity_name(l_ent),
                            "table_raw": l_ent,
                            "fields": sorted(list(f.lower() for f in l_attrs if f and f != "*")),
                        })

                registry[fn_name] = {
                    "fn_name": fn_name,
                    "table_name": logical_tbl,
                    "table_raw": raw_tbl,
                    "fields": all_fields,
                    "linked_entities": linked_entities,
                    "is_fetchxml": True,
                    "raw_endpoint": f"/_api/{raw_tbl}?fetchXml=...",
                }
                continue

            # Check for OData queries: e.g. `contacts?$select=...` or `accounts?$filter=...`
            odata_m = re.search(r"""(?:[/]_api[/]|[`'"])([a-zA-Z0-9_]{3,60})\?([^"'`\r\n;]+)""", body, re.IGNORECASE)
            if odata_m:
                raw_tbl = odata_m.group(1).strip()
                q_part = odata_m.group(2).strip()
                logical_tbl = self.resolve_entity_name(raw_tbl)
                f_found = self._extract_odata_fields(q_part)

                registry[fn_name] = {
                    "fn_name": fn_name,
                    "table_name": logical_tbl,
                    "table_raw": raw_tbl,
                    "fields": sorted(list(f_found)),
                    "linked_entities": [],
                    "is_fetchxml": False,
                    "raw_endpoint": f"/_api/{raw_tbl}?{q_part}",
                }

        return registry

    def _parse_code_for_webapi_calls(self, code: str, query_helpers: Optional[dict] = None, discovered_wrappers: Optional[set[str]] = None) -> list[dict]:
        """
        Parse JavaScript/HTML snippet for calls to /_api/<table_collection>
        and extract requested columns ($select), filtered/sorted columns ($filter, $orderby),
        or modified columns (JSON payload keys).
        Supports direct /_api/, SDK wrappers (webapi.retrieveRecord, context.webAPI),
        client-side FetchXML queries, resolves query helper functions,
        and flags dynamic Liquid endpoints and unprojected queries (missing $select or $select=*).
        """
        results: list[dict] = []
        lines = code.splitlines()

        # 1. Direct /_api/<table_name> endpoints or OData queries with $select / $filter / $orderby:
        # e.g.: "/_api/contacts", `custom_agreements?$select=_parent_value,...`
        # Query part must allow commas (in $select), parentheses (in $expand), and spaces (in $filter)
        api_pattern = re.compile(
            r"""(?:[/]_api[/]([a-zA-Z0-9_]+)(?:\([^\)]*\))?([^"'`\r\n\};]*)|[`'"]([a-zA-Z0-9_]{3,60})(?:\([^\)]*\))?(\?(?:[^"'`\r\n\};]*\$select=[^"'`\r\n\};]*|[^"'`\r\n\};]*\$filter=[^"'`\r\n\};]*|[^"'`\r\n\};]*\$orderby=[^"'`\r\n\};]*)))""",
            re.IGNORECASE,
        )

        # 1b. Direct client-side Web API FetchXML query (e.g. `contacts?fetchXml=<fetch ...>`, `/_api/contacts?fetchXml=...`)
        client_fetchxml_pattern = re.compile(
            r"""(?:[/]_api[/]|[`'"])?([a-zA-Z0-9_]{3,60})\?fetchXml=\s*[`'"]?(<fetch\b.*?<\/fetch>)""",
            re.IGNORECASE | re.DOTALL,
        )
        for match in client_fetchxml_pattern.finditer(code):
            raw_ent = match.group(1).strip()
            xml_body = match.group(2)
            logical_tbl = self.resolve_entity_name(raw_ent)

            attrs = set(re.findall(r'<attribute\s+name=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
            conds = set(re.findall(r'<condition\s+attribute=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
            orders = set(re.findall(r'<order\s+attribute=["\']([a-zA-Z0-9_]+)["\']', xml_body, re.IGNORECASE))
            all_fields = sorted(list(f.lower() for f in (attrs | conds | orders) if f and f != "*"))

            line_num = code[:match.start()].count("\n") + 1
            results.append({
                "raw_endpoint": f"/_api/{raw_ent}?fetchXml=...",
                "table_name": logical_tbl,
                "table_raw": raw_ent,
                "fields": all_fields,
                "is_unprojected": False,
                "unprojected_reason": "",
                "line_number": line_num,
                "context_snippet": "\n".join(lines[max(0, line_num - 2):min(len(lines), line_num + 3)]),
            })

            # Also check linked entities: <link-entity name="account" ...>
            for lm in re.finditer(r'<link-entity\s+[^>]*?name=["\']([a-zA-Z0-9_]+)["\']([^>]*)>(.*?)(?=<(?:entity|link-entity)|\Z)', xml_body, re.DOTALL | re.IGNORECASE):
                l_ent = lm.group(1).strip().lower()
                l_content = lm.group(3)
                l_attrs = set(re.findall(r'<attribute\s+name=["\']([a-zA-Z0-9_]+)["\']', l_content, re.IGNORECASE))
                if l_attrs:
                    results.append({
                        "raw_endpoint": f"/_api/{raw_ent}?fetchXml=... (linked: {l_ent})",
                        "table_name": self.resolve_entity_name(l_ent),
                        "table_raw": l_ent,
                        "fields": sorted(list(f.lower() for f in l_attrs if f and f != "*")),
                        "is_unprojected": False,
                        "unprojected_reason": "",
                        "line_number": line_num,
                        "context_snippet": "\n".join(lines[max(0, line_num - 2):min(len(lines), line_num + 3)]),
                    })

        # Gap 2 fix: also catch fetch/axios/$.ajax/XHR patterns that use /_api/
        # These patterns may spread the URL and options across multiple lines
        http_client_pattern = re.compile(
            r"""(?:fetch|axios\.(?:get|post|put|patch|delete)|\$\.(?:ajax|get|post)|new\s+XMLHttpRequest)\s*[.(]\s*["'`]([^"'`\r\n]*/_api/([a-zA-Z0-9_]+)[^"'`\r\n]*)["'`]""",
            re.IGNORECASE,
        )

        for line_idx, line in enumerate(lines, start=1):
            for match in api_pattern.finditer(line):
                table_raw = (match.group(1) or match.group(3) or "").strip()
                # Reject function names mistakenly captured by api_pattern
                if table_raw.lower().endswith("query") or (table_raw.lower().startswith("get") and len(table_raw) > 10):
                    continue
                query_part = match.group(2) or match.group(4) or ""
                logical_table = self.resolve_entity_name(table_raw)

                # Gap 2 fix: widen context window to 30 lines up + 15 lines down
                start_l = max(0, line_idx - 30)
                end_l = min(len(lines), line_idx + 15)
                context_block = "\n".join(lines[start_l:end_l])

                # Extract local string variables in surrounding scope to resolve concatenation like "$select=" + odata
                local_vars = dict(re.findall(r"""(?:var|let|const)\s+([a-zA-Z0-9_]+)\s*=\s*["'\x27`]?([^"'\x27`\r\n;]+)["'\x27`]?""", context_block))
                resolved_line = line
                if local_vars:
                    for vname, vval in local_vars.items():
                        resolved_line = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, resolved_line)
                        resolved_line = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, resolved_line)

                fields_found: set[str] = set()
                fields_found.update(self._extract_odata_fields(query_part))
                fields_found.update(self._extract_odata_fields(resolved_line))
                if not fields_found:
                    stmt_lines = []
                    for l in lines[line_idx - 1 : min(len(lines), line_idx + 8)]:
                        stmt_lines.append(l)
                        if len(stmt_lines) > 1 and any(k in l for k in (");", "fetch(", "webAPI.", "$.ajax(", "/_api/")):
                            break
                        if len(stmt_lines) > 1 and re.search(r"""(?:contentType|success|error|headers|data)\s*:""", l, re.IGNORECASE):
                            break
                    stmt_text = "\n".join(stmt_lines)
                    if local_vars:
                        for vname, vval in local_vars.items():
                            stmt_text = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, stmt_text)
                            stmt_text = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, stmt_text)
                    fields_found.update(self._extract_odata_fields(stmt_text))

                payload_fields = self._extract_payload_fields(context_block)
                fields_found.update(payload_fields)
                fields_found.update(self._extract_response_fields(context_block))

                endpoint_name = match.group(0)
                if not endpoint_name.startswith("/_api/"):
                    endpoint_name = f"OData Query: {table_raw}"

                query_analysis = self._analyze_odata_query(query_part or resolved_line)
                # Fallback to statement lookahead if $select was split across lines or concatenated with '+'
                if query_analysis["is_unprojected"]:
                    stmt_lines = []
                    for l in lines[line_idx - 1 : min(len(lines), line_idx + 8)]:
                        stmt_lines.append(l)
                        if len(stmt_lines) > 1 and any(k in l for k in (");", "fetch(", "webAPI.", "$.ajax(", "/_api/")):
                            break
                        if len(stmt_lines) > 1 and re.search(r"""(?:contentType|success|error|headers|data)\s*:""", l, re.IGNORECASE):
                            break
                    stmt_text = "\n".join(stmt_lines)
                    if local_vars:
                        for vname, vval in local_vars.items():
                            stmt_text = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, stmt_text)
                            stmt_text = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, stmt_text)
                    stmt_analysis = self._analyze_odata_query(stmt_text)
                    if stmt_analysis["has_select"] and not stmt_analysis["is_wildcard_select"]:
                        query_analysis = stmt_analysis
                        fields_found.update(stmt_analysis["fields"])

                is_write_method = bool(re.search(r"""(?:type|method)\s*:\s*["']\s*(?:POST|PUT|PATCH|DELETE)\b""", context_block, re.IGNORECASE))
                is_unproj = query_analysis["is_unprojected"] and not is_write_method
                unproj_reason = query_analysis.get("unprojected_reason", "") if is_unproj else ""

                results.append({
                    "raw_endpoint": endpoint_name,
                    "table_name": logical_table,
                    "table_raw": table_raw,
                    "fields": sorted(list(f for f in fields_found if f and f != "*")),
                    "is_unprojected": is_unproj,
                    "unprojected_reason": unproj_reason,
                    "line_number": line_idx,
                    "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                })

            # Gap 2 fix: scan for fetch/axios/$.ajax/XHR calls to /_api/
            for match in http_client_pattern.finditer(line):
                full_url = match.group(1).strip()
                table_raw = match.group(2).strip()
                logical_table = self.resolve_entity_name(table_raw)

                start_l = max(0, line_idx - 30)
                end_l = min(len(lines), line_idx + 15)
                context_block = "\n".join(lines[start_l:end_l])

                local_vars = dict(re.findall(r"""(?:var|let|const)\s+([a-zA-Z0-9_]+)\s*=\s*["'\x27`]?([^"'\x27`\r\n;]+)["'\x27`]?""", context_block))
                resolved_url = full_url
                if local_vars:
                    for vname, vval in local_vars.items():
                        resolved_url = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, resolved_url)
                        resolved_url = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, resolved_url)

                fields_found: set[str] = set()
                fields_found.update(self._extract_odata_fields(resolved_url))
                if not fields_found:
                    # Look ahead a few lines for concatenated query string for this specific call
                    stmt_lines = []
                    for l in lines[line_idx - 1 : min(len(lines), line_idx + 8)]:
                        stmt_lines.append(l)
                        if len(stmt_lines) > 1 and any(k in l for k in (");", "fetch(", "webAPI.", "$.ajax(", "/_api/")):
                            break
                        if len(stmt_lines) > 1 and re.search(r"""(?:contentType|success|error|headers|data)\s*:""", l, re.IGNORECASE):
                            break
                    stmt_text = "\n".join(stmt_lines)
                    if local_vars:
                        for vname, vval in local_vars.items():
                            stmt_text = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, stmt_text)
                            stmt_text = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, stmt_text)
                    fields_found.update(self._extract_odata_fields(stmt_text))
                payload_fields = self._extract_payload_fields(context_block)
                fields_found.update(payload_fields)
                fields_found.update(self._extract_response_fields(context_block))

                query_analysis = self._analyze_odata_query(resolved_url)
                if query_analysis["is_unprojected"]:
                    stmt_lines = []
                    for l in lines[line_idx - 1 : min(len(lines), line_idx + 8)]:
                        stmt_lines.append(l)
                        if len(stmt_lines) > 1 and any(k in l for k in (");", "fetch(", "webAPI.", "$.ajax(", "/_api/")):
                            break
                        if len(stmt_lines) > 1 and re.search(r"""(?:contentType|success|error|headers|data)\s*:""", l, re.IGNORECASE):
                            break
                    stmt_text = "\n".join(stmt_lines)
                    if local_vars:
                        for vname, vval in local_vars.items():
                            stmt_text = re.sub(r"""["'\x27`]\s*\+\s*""" + re.escape(vname) + r"""\b""", vval, stmt_text)
                            stmt_text = re.sub(r"""\$\{\s*""" + re.escape(vname) + r"""\s*\}""", vval, stmt_text)
                    stmt_analysis = self._analyze_odata_query(stmt_text)
                    if stmt_analysis["has_select"] and not stmt_analysis["is_wildcard_select"]:
                        query_analysis = stmt_analysis
                        fields_found.update(stmt_analysis["fields"])

                is_write_method = bool(
                    re.search(r"""(?:type|method)\s*:\s*["']\s*(?:POST|PUT|PATCH|DELETE)\b""", context_block, re.IGNORECASE)
                    or re.search(r"""(?:axios\.(?:post|put|patch|delete)|\$\.post)\b""", line, re.IGNORECASE)
                )
                is_unproj = query_analysis["is_unprojected"] and not is_write_method
                unproj_reason = query_analysis.get("unprojected_reason", "") if is_unproj else ""

                results.append({
                    "raw_endpoint": f"/_api/{table_raw} (HTTP client call)",
                    "table_name": logical_table,
                    "table_raw": table_raw,
                    "fields": sorted(list(f for f in fields_found if f and f != "*")),
                    "is_unprojected": is_unproj,
                    "unprojected_reason": unproj_reason,
                    "line_number": line_idx,
                    "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                })

        # 2. SDK / PCF / Helper wrapper pattern:
        # e.g.: webAPI.retrieveRecord("contact", ...), Xrm.WebApi.createRecord("lead", ...),
        #       webAPI.retrieveMultipleRecords("contact", "?$select=...&$orderby=...")
        wrapper_pattern = re.compile(
            r"""(?:\.webAPI|webapi|Xrm\.WebApi|\bPortalAPI)\.(?:retrieveRecord|createRecord|updateRecord|deleteRecord|retrieveMultipleRecords)\s*\(\s*["']([a-zA-Z0-9_]+)["'](?:\s*,\s*(?:["']([^"'\r\n]+)["']|([^,\)\r\n]+)))?""",
            re.IGNORECASE,
        )
        for line_idx, line in enumerate(lines, start=1):
            for match in wrapper_pattern.finditer(line):
                table_raw = match.group(1).strip()
                wrapper_query = (match.group(2) or match.group(3) or "").strip()
                logical_table = self.resolve_entity_name(table_raw)

                start_l = max(0, line_idx - 15)
                end_l = min(len(lines), line_idx + 10)
                context_block = "\n".join(lines[start_l:end_l])

                fields_found: set[str] = set()
                if wrapper_query:
                    fields_found.update(self._extract_odata_fields(wrapper_query))
                fields_found.update(self._extract_odata_fields(line))
                if not fields_found:
                    fields_found.update(self._extract_odata_fields(context_block))

                payload_fields = self._extract_payload_fields(context_block)
                fields_found.update(payload_fields)
                fields_found.update(self._extract_response_fields(context_block))

                is_retrieve = "retrieve" in match.group(0).lower()
                query_analysis = self._analyze_odata_query(wrapper_query)
                is_unproj = False
                unproj_reason = ""
                if is_retrieve:
                    if not wrapper_query or wrapper_query.lower() in ("null", "undefined", '""', "''"):
                        is_unproj = True
                        unproj_reason = "Web API retrieve wrapper called without query options / $select"
                    elif query_analysis["is_unprojected"]:
                        is_unproj = True
                        unproj_reason = (
                            "Web API retrieve wrapper called with $select=*"
                            if query_analysis["is_wildcard_select"]
                            else "Web API retrieve wrapper called without $select"
                        )

                results.append({
                    "raw_endpoint": match.group(0) + "(...)",
                    "table_name": logical_table,
                    "table_raw": table_raw,
                    "fields": sorted(list(f for f in fields_found if f and f != "*")),
                    "is_unprojected": is_unproj,
                    "unprojected_reason": unproj_reason,
                    "line_number": line_idx,
                    "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                })

        # 2b. Portal custom Web API helper functions (dynamically discovered in Pass 1):
        active_wrappers = set(discovered_wrappers or self.discovered_wrapper_funcs or PortalWebApiAuditorClient._DISCOVERED_WRAPPERS)
        if not active_wrappers:
            # Fallback for standalone/isolated snippet parsing
            active_wrappers = self._discover_webapi_wrapper_functions([{"code_text": code}])

        if active_wrappers:
            wrapper_alts = "|".join(re.escape(w) for w in sorted(active_wrappers, key=len, reverse=True))
            custom_wrapper_pattern = re.compile(
                rf"""\b({wrapper_alts})\s*\(\s*(?:[`'"]([a-zA-Z0-9_]+)(?:\([^\)]*\))?([^"'`\r\n\);]*)[`'"]|([a-zA-Z0-9_]+))""",
                re.IGNORECASE,
            )
            for line_idx, line in enumerate(lines, start=1):
                for match in custom_wrapper_pattern.finditer(line):
                    # Skip function definitions (e.g. async function makeWebApiCall(query) { ... })
                    prefix = line[:match.start()].strip()
                    if prefix.endswith("function") or "function " in prefix:
                        continue

                    fn_name = match.group(1)
                    table_raw = (match.group(2) or "").strip()
                    query_part = (match.group(3) or "").strip()
                    var_arg = (match.group(4) or "").strip()

                    start_l = max(0, line_idx - 30)
                    end_l = min(len(lines), line_idx + 15)
                    context_block = "\n".join(lines[start_l:end_l])

                    local_vars = dict(re.findall(r"""(?:var|let|const)\s+([a-zA-Z0-9_]+)\s*=\s*["'\x27`]?([^"'\x27`\r\n;]+)["'\x27`]?""", context_block))

                    if var_arg and not table_raw:
                        val = local_vars.get(var_arg, "").strip()

                        # 1. Check if val is a function call: e.g. getAgentContactsByAgentOrgQuery(UserOrgnisationID)
                        m_fn = re.match(r"""^([a-zA-Z0-9_]+)\s*\(.*\)""", val)
                        if m_fn:
                            called_fn = m_fn.group(1).strip().lower()
                            resolved = (query_helpers or {}).get(called_fn)
                            if not resolved:
                                local_helpers = self._extract_query_helper_functions(code)
                                resolved = local_helpers.get(called_fn)

                            if resolved:
                                table_raw = resolved["table_raw"]
                                logical_table = resolved["table_name"]
                                fields_found = set(resolved["fields"])
                                results.append({
                                    "raw_endpoint": f"{fn_name}({called_fn} -> {resolved['raw_endpoint']})",
                                    "table_name": logical_table,
                                    "table_raw": table_raw,
                                    "fields": sorted(list(f for f in fields_found if f and f != "*")),
                                    "is_unprojected": False,
                                    "unprojected_reason": "",
                                    "line_number": line_idx,
                                    "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                                    "is_wrapper_call": True,
                                })
                                for link_e in resolved.get("linked_entities", []):
                                    results.append({
                                        "raw_endpoint": f"{fn_name}({called_fn} -> linked {link_e['table_raw']})",
                                        "table_name": link_e["table_name"],
                                        "table_raw": link_e["table_raw"],
                                        "fields": link_e["fields"],
                                        "is_unprojected": False,
                                        "unprojected_reason": "",
                                        "line_number": line_idx,
                                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                                        "is_wrapper_call": True,
                                    })
                                continue
                            else:
                                # It's an unresolvable function call, NOT a Dataverse table name!
                                # Do NOT treat function name (e.g. getAgentContactsByAgentOrgQuery) as a table.
                                continue

                        m_val = re.match(r"""^([a-zA-Z0-9_]+)(?:\([^\)]*\))?(.*)$""", val)
                        if m_val:
                            cand = m_val.group(1).strip()
                            if not cand.lower().endswith("query") and not cand.lower().startswith("get"):
                                table_raw = cand
                                query_part = m_val.group(2).strip()

                    if not table_raw:
                        continue

                    # Reject function names mistakenly captured as table names
                    if table_raw.lower().endswith("query") or (table_raw.lower().startswith("get") and len(table_raw) > 10):
                        continue

                    logical_table = self.resolve_entity_name(table_raw)
                    fields_found: set[str] = set()
                    fields_found.update(self._extract_odata_fields(query_part))
                    fields_found.update(self._extract_odata_fields(line))
                    if not fields_found:
                        fields_found.update(self._extract_odata_fields(context_block))
                    payload_fields = self._extract_payload_fields(context_block)
                    fields_found.update(payload_fields)
                    fields_found.update(self._extract_response_fields(context_block))

                    query_analysis = self._analyze_odata_query(query_part or line)
                    is_unproj = query_analysis["is_unprojected"]
                    unproj_reason = (
                        f"{fn_name}() called without $select query option (implicit SELECT *)"
                        if is_unproj
                        else ""
                    )

                    results.append({
                        "raw_endpoint": f"{fn_name}({table_raw}{query_part})",
                        "table_name": logical_table,
                        "table_raw": table_raw,
                        "fields": sorted(list(f for f in fields_found if f and f != "*")),
                        "is_unprojected": is_unproj,
                        "unprojected_reason": unproj_reason,
                        "line_number": line_idx,
                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                        "is_wrapper_call": True,
                    })

        # 3. Dynamic Liquid / JS template literal endpoints:
        dynamic_liquid_pattern = re.compile(r"""[/]_api[/]\{\{\s*([^}]+)\s*\}\}""", re.IGNORECASE)
        dynamic_js_pattern = re.compile(r"""[/]_api[/]\$\{([^}]+)\}""", re.IGNORECASE)

        for line_idx, line in enumerate(lines, start=1):
            for dm in dynamic_liquid_pattern.finditer(line):
                expr = dm.group(1).strip()
                results.append({
                    "raw_endpoint": dm.group(0),
                    "table_name": f"(Dynamic Liquid: {expr})",
                    "table_raw": expr,
                    "fields": [],
                    "line_number": line_idx,
                    "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                })
            for dj in dynamic_js_pattern.finditer(line):
                expr = dj.group(1).strip()
                start_l = max(0, line_idx - 30)
                end_l = min(len(lines), line_idx + 15)
                context_block = "\n".join(lines[start_l:end_l])

                resolved_table = None
                var_def = re.search(
                    r'(?:var|let|const)\s+' + re.escape(expr) + r'\s*=\s*[\'"`]?([a-zA-Z0-9_]{3,60})',
                    context_block
                )
                if var_def:
                    resolved_table = var_def.group(1)

                if resolved_table:
                    logical_table = self.resolve_entity_name(resolved_table)
                    fields_found: set[str] = set()
                    fields_found.update(self._extract_odata_fields(context_block))
                    fields_found.update(self._extract_payload_fields(context_block))
                    fields_found.update(self._extract_response_fields(context_block))
                    results.append({
                        "raw_endpoint": dj.group(0) + f" (resolved: {resolved_table})",
                        "table_name": logical_table,
                        "table_raw": resolved_table,
                        "fields": sorted(list(f for f in fields_found if f and f != "*")),
                        "line_number": line_idx,
                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                    })
                else:
                    results.append({
                        "raw_endpoint": dj.group(0),
                        "table_name": f"(Dynamic JS: {expr})",
                        "table_raw": expr,
                        "fields": [],
                        "line_number": line_idx,
                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                    })

        # 4. Concatenated string URLs: "/_api/" + tableVar or "/_api/" + "tableName"
        concat_pattern = re.compile(
            r"""[/]_api[/]["']?\s*\+\s*(?:["']([a-zA-Z0-9_]+)["']|([a-zA-Z0-9_]+))""",
            re.IGNORECASE
        )
        for line_idx, line in enumerate(lines, start=1):
            for mc in concat_pattern.finditer(line):
                literal_tbl = mc.group(1)
                var_tbl = mc.group(2)
                start_l = max(0, line_idx - 30)
                end_l = min(len(lines), line_idx + 15)
                context_block = "\n".join(lines[start_l:end_l])

                table_raw = literal_tbl
                if not table_raw and var_tbl:
                    var_def = re.search(
                        r'(?:var|let|const)\s+' + re.escape(var_tbl) + r'\s*=\s*[\'"`]?([a-zA-Z0-9_]{3,60})',
                        context_block
                    )
                    if var_def:
                        table_raw = var_def.group(1)
                    else:
                        table_raw = var_tbl

                if table_raw:
                    logical_table = self.resolve_entity_name(table_raw)
                    fields_found = set()
                    fields_found.update(self._extract_odata_fields(context_block))
                    fields_found.update(self._extract_payload_fields(context_block))
                    fields_found.update(self._extract_response_fields(context_block))
                    results.append({
                        "raw_endpoint": f"/_api/ + {table_raw}",
                        "table_name": logical_table,
                        "table_raw": table_raw,
                        "fields": sorted(list(f for f in fields_found if f and f != "*")),
                        "line_number": line_idx,
                        "context_snippet": "\n".join(lines[max(0, line_idx - 2):min(len(lines), line_idx + 3)]),
                    })


        return results

    def _extract_payload_fields(self, code_block: str) -> set[str]:
        """
        Extract property keys from JSON payloads passed to Web API AJAX calls
        e.g. data: JSON.stringify({ "emailaddress1": email, firstname: "John", "new_score": 100 })
        or: var record = { new_name: "test" }; ... data: JSON.stringify(record)
        """
        found = set()
        skip_words = {
            "type", "url", "data", "contenttype", "datatype", "beforesend",
            "success", "error", "complete", "async", "headers", "method",
            "get", "post", "patch", "put", "delete", "true", "false", "null",
            "function", "return", "var", "let", "const", "id", "this", "undefined",
            "processdata", "cache", "timeout", "crossdomain", "jsonp",
            "bind", "odata", "responsetext", "responsejson", "innererror", "pluginerror",
            "message", "status", "statuscode", "statustext", "xhr", "entityid",
            "foundid", "responseval", "action", "labelid", "labelid2", "labelid3",
            "getquerystringparameter", "odataurl",
            "entityformcontrol_entityformview_entityid", "entityformview_entityid",
        }

        # 1. Look for direct object literal in JSON.stringify(...)
        # Handle nested template literals `${...}` or Liquid tags `{{...}}` using balanced parentheses
        idx = code_block.find("JSON.stringify")
        if idx != -1:
            start_paren = code_block.find("(", idx)
            if start_paren != -1:
                depth = 0
                end_paren = -1
                for i in range(start_paren, len(code_block)):
                    if code_block[i] == "(":
                        depth += 1
                    elif code_block[i] == ")":
                        depth -= 1
                        if depth == 0:
                            end_paren = i
                            break
                stringify_content = code_block[start_paren + 1:end_paren] if end_paren != -1 else code_block[start_paren + 1:]
                key_matches = re.findall(r"""["']?(?:@[a-zA-Z0-9_.]+|([a-zA-Z0-9_]{2,50})(?:@[a-zA-Z0-9_.]+)?)["']?\s*:""", stringify_content)
                for k in key_matches:
                    k_clean = k.strip().lower()
                    if k_clean and k_clean not in skip_words and not k_clean.isdigit() and not self._is_guid_or_hex_fragment(k_clean):
                        found.add(k_clean)

        # 2. Look for variable passed to JSON.stringify(recordVar)
        json_var_match = re.search(r"JSON\.stringify\s*\(\s*([a-zA-Z0-9_]+)\s*\)", code_block)
        if json_var_match:
            var_name = json_var_match.group(1)
            # Find definition: var <var_name> = { ... }
            # Use balanced braces to support Liquid tags {{...}} and template literals ${...} inside object properties
            var_def_m = re.search(r"(?:var|let|const)?\s*" + re.escape(var_name) + r"\s*=\s*\{", code_block)
            if var_def_m:
                start_b = var_def_m.end() - 1  # at '{'
                depth = 0
                end_b = -1
                for i in range(start_b, len(code_block)):
                    if code_block[i] == "{":
                        depth += 1
                    elif code_block[i] == "}":
                        depth -= 1
                        if depth == 0:
                            end_b = i
                            break
                var_content = code_block[start_b + 1:end_b] if end_b != -1 else code_block[start_b + 1:]
                key_matches = re.findall(r"""["']?(?:@[a-zA-Z0-9_.]+|([a-zA-Z0-9_]{2,50})(?:@[a-zA-Z0-9_.]+)?)["']?\s*:""", var_content)
                for k in key_matches:
                    k_clean = k.strip().lower()
                    if k_clean and k_clean not in skip_words and not k_clean.isdigit() and not self._is_guid_or_hex_fragment(k_clean):
                        found.add(k_clean)

            # Also look for property assignments: recordVar.prop = ... or recordVar['prop'] = ...
            prop_pattern = re.compile(
                re.escape(var_name) + r"""(?:\.([a-zA-Z0-9_]{2,50})|\[["'](?:@[a-zA-Z0-9_.]+|([a-zA-Z0-9_]{2,50})(?:@[a-zA-Z0-9_.]+)?)["']\])\s*=""",
                re.IGNORECASE
            )
            for m in prop_pattern.finditer(code_block):
                p = (m.group(1) or m.group(2) or "").strip().lower()
                if p and p not in skip_words and not p.isdigit() and not self._is_guid_or_hex_fragment(p):
                    found.add(p)

        return found

    def _extract_response_fields(self, code_block: str) -> set[str]:
        """
        Extract property accesses on response/data objects returned from Web API calls
        e.g. var maxChoices = data.maxchoices;
             var bookingDays = res['bookingdays'];
             const { name, isactive } = data;
        """
        found = set()
        skip_words = {
            "length", "tostring", "value", "status", "statuscode", "message", "error",
            "headers", "statustext", "readystate", "then", "catch", "foreach", "map",
            "filter", "find", "push", "slice", "split", "indexof", "includes",
            "substring", "replace", "trim", "tolowercase", "touppercase", "constructor",
            "prototype", "valueof", "hasownproperty", "data", "res", "result", "response",
            "item", "record", "entity", "obj", "type", "url", "id", "guid", "key",
            "responsetext", "responsejson", "innererror", "pluginerror", "json", "text",
            "blob", "arraybuffer", "formdata", "clone", "ok", "redirected", "bind",
            "foundid", "responseval", "action", "labelid", "labelid2", "labelid3",
            "getquerystringparameter", "odataurl", "entityid",
            "entityformcontrol_entityformview_entityid", "entityformview_entityid",
        }

        # 1. Dot access: data.property, res.property, result.property, etc.
        for m in re.finditer(r"""\b(?:data|res|result|response|item|record|entity|obj|d)\.([a-zA-Z0-9_]{2,50})\b""", code_block, re.IGNORECASE):
            prop = m.group(1).strip().lower()
            if prop not in skip_words and not prop.isdigit() and not self._is_guid_or_hex_fragment(prop):
                found.add(prop)

        # 2. Bracket string access: data['property'], res["property"]
        for m in re.finditer(r"""\b(?:data|res|result|response|item|record|entity|obj|d)\[["']([a-zA-Z0-9_]{2,50})["']\]""", code_block, re.IGNORECASE):
            prop = m.group(1).strip().lower()
            if prop not in skip_words and not prop.isdigit() and not self._is_guid_or_hex_fragment(prop):
                found.add(prop)

        # 3. Object destructuring: const { field1, field2 } = data;
        for m in re.finditer(r"""(?:const|let|var)\s*\{\s*([a-zA-Z0-9_,\s]+)\s*\}\s*=\s*(?:data|res|result|response)""", code_block, re.IGNORECASE):
            for f in m.group(1).split(","):
                clean_f = f.strip().lower()
                if clean_f and clean_f not in skip_words and not clean_f.isdigit() and not self._is_guid_or_hex_fragment(clean_f):
                    found.add(clean_f)

        # 4. Iteration callbacks: .forEach(r => r.prop) or .map(item => item.prop)
        for m in re.finditer(r"""\b(?:r|row|item|rec|val|entry)\.([a-zA-Z0-9_]{2,50})\b""", code_block, re.IGNORECASE):
            prop = m.group(1).strip().lower()
            if prop not in skip_words and not prop.isdigit() and not self._is_guid_or_hex_fragment(prop):
                found.add(prop)

        # 5. jQuery & DOM form field bindings: $('#fieldname').val(...) or $("#fieldname").text(...)
        for m in re.finditer(r"""\$\(\s*["']#([a-zA-Z0-9_]{2,50})["']\s*\)\.(?:val|text|html)\(""", code_block, re.IGNORECASE):
            prop = m.group(1).strip().lower()
            if prop not in skip_words and not prop.isdigit() and not self._is_guid_or_hex_fragment(prop):
                found.add(prop)

        return found

    # ------------------------------------------------------------------
    # Reconciliation Engine
    # ------------------------------------------------------------------

    def reconcile_webapi_usage(
        self,
        site_settings: list[dict],
        code_occurrences: list[dict],
        suggestion_mode: str = "forms",
        include_server_and_forms: Optional[bool] = None,
        target_website_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Reconcile configured site settings against fields detected in code.
        Produces a comprehensive list of tables with compliance status,
        violations, missing fields, and recommended fixes.
        Supports 3 distinct recommendation tiers:
          1. 'client': Only client-side Web API calls (/_api/...), safeAjax, PCF, and Entity List views.
          2. 'forms': Client calls + columns rendered on Basic Form / Multistep Form XML layouts.
          3. 'server': All sources, including server-side FetchXML & Liquid templates.
        """
        # Determine default setting table from target website schema
        target_schema = None
        if target_website_id:
            target_schema = self.get_website_schema(target_website_id)
        elif site_settings:
            tables = [st.get("setting_table") for st in site_settings if st.get("setting_table")]
            if tables:
                target_schema = "mspp" if "mspp" in max(set(tables), key=tables.count) else "adx"
        default_setting_table = f"{target_schema}_sitesettings" if target_schema else "adx_sitesettings"

        # Map detected fields by table_name — governed occurrences ONLY
        # (Only /_api/ calls, SDK wrappers, PCF, Entity List OData, Lookup Controls
        #  are restricted by Webapi/<table>/fields.  FetchXML, Liquid, Basic Forms
        #  are server-side and governed by Table Permissions, not this setting.)
        # Map site settings by table_name
        settings_by_table: dict[str, dict] = {s["table_name"]: s for s in site_settings}

        if include_server_and_forms is not None:
            if not include_server_and_forms:
                suggestion_mode = "client"
            elif suggestion_mode not in ("client", "forms", "server"):
                suggestion_mode = "forms"

        def _match_table_key(t: str) -> str:
            if t in settings_by_table:
                return t
            if t.endswith("s") and t[:-1] in settings_by_table:
                return t[:-1]
            if not t.endswith("s") and (t + "s") in settings_by_table:
                return t + "s"
            for sk, sv in settings_by_table.items():
                if sv.get("table_alias_raw") == t:
                    return sk
            return t

        detected_by_table: dict[str, set[str]] = {}
        occurrences_by_table: dict[str, list[dict]] = {}

        for occ in code_occurrences:
            raw_tbl = occ["table_name"]
            matched_tbl = _match_table_key(raw_tbl)
            occurrences_by_table.setdefault(matched_tbl, []).append(occ)
            if self._is_webapi_governed(occ):
                detected_by_table.setdefault(matched_tbl, set()).update(occ.get("fields") or [])

        # Strict scope: only tables configured in Web API site settings,
        # plus tables where actual client-side /_api/ / SDK / PCF / framework calls were found.
        webapi_called_tables = set(
            _match_table_key(occ["table_name"]) for occ in code_occurrences
            if self._is_webapi_governed(occ)
        )
        all_tables = sorted(list(set(list(settings_by_table.keys()) + list(webapi_called_tables))))

        report: list[dict] = []

        for tbl in all_tables:
            s = settings_by_table.get(tbl)
            occ_list = occurrences_by_table.get(tbl, [])

            # Split into governed (affect compliance) vs informational (context only)
            governed_occs = [o for o in occ_list if self._is_webapi_governed(o)]
            framework_occs = [o for o in governed_occs if o.get("component_type") in self.FRAMEWORK_COMPONENT_TYPES]
            custom_code_occs = [o for o in governed_occs if o.get("component_type") not in self.FRAMEWORK_COMPONENT_TYPES]

            # Resolve table attributes from Dataverse metadata only if there are fields to validate
            logical_tbl = self.resolve_entity_name(tbl)
            raw_disc = detected_by_table.get(tbl, set())
            filtered_disc = self.filter_table_attributes(list(raw_disc))

            valid_attr_set = set()
            if filtered_disc:
                tbl_attrs = self.get_table_attributes(logical_tbl)
                if not tbl_attrs and logical_tbl != tbl:
                    tbl_attrs = self.get_table_attributes(tbl)
                if tbl_attrs:
                    valid_attr_set = set(a.lower() for a in tbl_attrs)
                    valid_attr_set.add(f"{tbl}id".lower())
                    filtered_disc = [f for f in filtered_disc if f in valid_attr_set]

            discovered_fields = sorted(list(f for f in filtered_disc if f and f != "*"))
            governed_count = len(governed_occs)
            custom_count = len(custom_code_occs)

            total_occurrences = governed_count  # report only Web-API-governed references

            configured_fields = s.get("fields_list", []) if s else []
            has_wildcard = s.get("has_wildcard", False) if s else False
            is_enabled = s.get("enabled", False) if s else False
            fields_raw = s.get("fields_raw", "") if s else ""
            setting_id = s.get("fields_setting_id") if s else None
            setting_table = s.get("setting_table", default_setting_table) if s else default_setting_table
            website_id = s.get("website_id", "") if s else (target_website_id or "")

            # Check missing fields — only from fields detected in governed calls
            missing_from_whitelist = []
            if not has_wildcard and configured_fields:
                missing_from_whitelist = [f for f in discovered_fields if f not in configured_fields]
            elif not has_wildcard and not configured_fields and discovered_fields:
                missing_from_whitelist = list(discovered_fields)

            # Detect unprojected / wildcard calls for this table — governed client-side calls ONLY
            unprojected_occs = [o for o in occ_list if o.get("is_unprojected") and self._is_webapi_governed(o)]
            unprojected_calls_count = len(unprojected_occs)
            plural_ent = self._entity_set_cache.get(tbl, f"{tbl}s")

            # Architectural Role Inference: Framework Control / Catalog
            catalog_role = self.detect_catalog_and_validation_role(tbl, configured_fields)
            is_framework_table = len(framework_occs) > 0 or (catalog_role is not None)

            # Determine compliance status
            if not is_enabled:
                if governed_count > 0:
                    status = "🚨 DISABLED (SCRIPT CALLS FAILING)"
                    status_level = "critical"
                    status_desc = (
                        f"Web API is DISABLED for this table in site settings, but {governed_count} client-side Web API call(s) exist in portal code! "
                        f"All calls to /_api/{tbl} currently fail with HTTP 404/403 at runtime. "
                        f"You must set Webapi/{tbl}/enabled to 'true' and configure the fields whitelist."
                    )
                else:
                    status = "Disabled"
                    status_level = "neutral"
                    status_desc = "Web API is disabled for this table in site settings."
            elif has_wildcard:
                status_level = "critical"
                if unprojected_calls_count > 0:
                    status = "🚨 DEPRECATION VIOLATION (* & UNPROJECTED)"
                    status_desc = (
                        f"Site setting uses wildcard '*' (deprecated by Microsoft on Sept 14, 2026). "
                        f"Also detected {unprojected_calls_count} client-side query call(s) omitting $select. "
                        "Any Web API call referencing columns that are not explicitly whitelisted will fail once '*' is disabled. "
                        "Both site setting update AND JavaScript query rewrites are required."
                    )
                else:
                    status = "🚨 DEPRECATION VIOLATION (*)"
                    status_desc = (
                        "Table exposes ALL columns via wildcard '*'. Microsoft will block wildcard access in upcoming releases. "
                        "Replace '*' with an explicit comma-separated column whitelist immediately."
                    )
            elif not configured_fields and discovered_fields:
                status = "No Whitelist Configured"
                status_level = "warning"
                status_desc = f"Web API is enabled but no fields are whitelisted. {governed_count} governed Web API call(s) will fail with HTTP 403."
            elif missing_from_whitelist:
                status_level = "warning"
                if unprojected_calls_count > 0:
                    status = "⚠️ INCOMPLETE WHITELIST & UNPROJECTED"
                    status_desc = (
                        f"{len(missing_from_whitelist)} field(s) missing from whitelist, "
                        f"and {unprojected_calls_count} call(s) omit $select. Code update required."
                    )
                else:
                    status = "Missing Fields"
                    status_desc = f"{len(missing_from_whitelist)} field(s) accessed by governed Web API calls are not in the whitelist: {', '.join(missing_from_whitelist)}."
            elif unprojected_calls_count > 0:
                status = "⚠️ UNPROJECTED CODE CALLS"
                status_level = "warning"
                status_desc = (
                    f"Explicit whitelist configured, but {unprojected_calls_count} client-side call(s) omit $select (implicit SELECT *). "
                    "Any Web API call referencing columns that are not explicitly whitelisted will fail with HTTP 403 once wildcard '*' is disabled. "
                    "Client JavaScript MUST be updated with explicit $select projections."
                )
            elif not configured_fields and not discovered_fields:
                if catalog_role:
                    status = catalog_role["role_title"]
                    status_level = "framework"
                    status_desc = f"Table acts as a {catalog_role['role_title']}. Web API enabled for dynamic lookups/validation, but no whitelist is configured."
                else:
                    status = "Empty Whitelist (No Code Found)"
                    status_level = "info"
                    status_desc = "Web API enabled with empty fields setting, and no governed client-side calls were detected in code."
            else:
                status = "Compliant"
                status_level = "success"
                if is_framework_table:
                    framework_summary = "Portal Framework / Dynamic Catalog"
                    status_desc = f"Explicit whitelist in place; table correctly configured for Portal Framework ({framework_summary})."
                else:
                    status_desc = f"Explicit whitelist in place matching {governed_count} governed Web API call(s)."

            # Advisory: enabled but zero governed calls found
            unused_warning = None
            if is_enabled:
                if catalog_role:
                    # Provide architectural insight and warn NOT to disable Web API
                    unused_warning = catalog_role["safety_advice"]
                elif governed_count == 0:
                    unused_warning = (
                        f"No client-side Web API calls (/_api/...) detected for '{tbl}'. "
                        "If this table is only consumed by standard Basic Forms, Multistep Forms, or Entity Lists "
                        "(which execute server-side via Table Permissions), it does not require Web API site settings. "
                        f"Consider disabling Web API (`Webapi/{tbl}/enabled = false`) to reduce attack surface."
                    )

            # Recommended fields computation:
            # Separate fields by source tier:
            valid_existing = set(f.lower() for f in configured_fields if f and f != "*")
            valid_discovered = set(f.lower() for f in discovered_fields if f and f != "*")

            form_xml_fields = set()
            server_xml_fields = set()
            client_occ_fields = set()

            for occ in occurrences_by_table.get(tbl, []):
                f_list = [f.lower() for f in occ.get("fields", []) if f and f != "*"]
                c_type = occ.get("component_type", "")
                if occ.get("is_form_xml") or "Form XML" in c_type or c_type in ("Basic Form", "Multistep Form", "Multistep Form Step"):
                    form_xml_fields.update(f_list)
                elif "FetchXML" in c_type or "Liquid" in c_type or occ.get("is_fetchxml") or occ.get("is_liquid"):
                    server_xml_fields.update(f_list)
                else:
                    client_occ_fields.update(f_list)

            # Direct query safety net for Form XML fields (only if deep scan ran and
            # did not already index form fields for this table)
            if not form_xml_fields and code_occurrences:
                direct_form_fields = self.get_table_form_fields(tbl)
                if direct_form_fields:
                    form_xml_fields.update(f.lower() for f in direct_form_fields if f and f != "*")

            # Key attributes from architectural catalog role
            catalog_fields = set()
            if catalog_role:
                for fld in catalog_role.get("key_attributes", []):
                    if fld and fld != "*":
                        catalog_fields.add(fld.lower())

            # Tier 1: Client & Lists
            #   - Client-side JS /_api/ calls, safeAjax, PCF controls
            #   - Entity List OData feed columns
            #   - Portal Lookup Control auto-calls
            #   - Dynamic UI Catalog framework calls
            #   These are the ONLY calls actually governed by Webapi/<table>/fields.
            #   Standard Basic Forms / Multistep Forms render server-side via Table Permissions.
            strict_fields = set(valid_discovered | catalog_fields)

            # Tier 2: Include Forms
            #   - Adds fields from Basic Form / Multistep Form XML layouts.
            #   - Rationale: Portal Lookup Controls embedded in forms DO call /_api/, and
            #     custom form JS scripts typically access the same field set shown on the form.
            #   - Note: the form SUBMISSION itself is server-side (not /_api/-governed),
            #     but including form layout fields is a safe, conservative whitelist.
            form_fields = set(strict_fields | form_xml_fields)

            # Tier 3: All Sources (Include Server FetchXML / Liquid)
            #   - Adds fields referenced in server-side {% fetchxml %} blocks and Liquid
            #     entity / tag lookups.  These are NOT directly Web API governed (Table
            #     Permissions apply server-side), but are included so the whitelist can
            #     serve as a comprehensive reference — and because any client-side code that
            #     mirrors the same queries would also need those fields.
            #   - If <all-attributes /> is present in any FetchXML block for this table,
            #     ALL table attributes are added (SELECT * scenario).
            server_fields = set(form_fields | server_xml_fields)

            # Expand server_fields when any FetchXML occurrence uses <all-attributes />
            all_attributes_fetchxml = any(
                occ.get("has_all_attributes") and (occ.get("is_fetchxml") or "FetchXML" in occ.get("component_type", ""))
                for occ in occurrences_by_table.get(tbl, [])
            )
            if all_attributes_fetchxml:
                logical_tbl = self.resolve_entity_name(tbl)
                tbl_attrs = self.get_table_attributes(logical_tbl)
                if not tbl_attrs and logical_tbl != tbl:
                    tbl_attrs = self.get_table_attributes(tbl)
                if tbl_attrs:
                    for attr in tbl_attrs:
                        if attr and attr != "*":
                            server_fields.add(attr.lower())

            # If unprojected /_api/ calls exist (no $select / wildcard *), add all table fields as safety net
            all_table_fields_added = False
            if unprojected_calls_count > 0:
                logical_tbl = self.resolve_entity_name(tbl)
                tbl_attrs = self.get_table_attributes(logical_tbl)
                if not tbl_attrs and logical_tbl != tbl:
                    tbl_attrs = self.get_table_attributes(tbl)
                if tbl_attrs:
                    for attr in tbl_attrs:
                        if attr and attr != "*":
                            strict_fields.add(attr.lower())
                            form_fields.add(attr.lower())
                            server_fields.add(attr.lower())
                    all_table_fields_added = True

            # Clean fields through filter_table_attributes to remove virtual fields and non-field noise tokens
            filtered_strict = set(self.filter_table_attributes(list(strict_fields)))
            filtered_form = set(self.filter_table_attributes(list(form_fields)))
            filtered_server = set(self.filter_table_attributes(list(server_fields)))

            # Metadata Validation Guard: Discard any token that is not a real Dataverse attribute
            if valid_attr_set:
                filtered_strict = set(f for f in filtered_strict if f in valid_attr_set)
                filtered_form = set(f for f in filtered_form if f in valid_attr_set)
                filtered_server = set(f for f in filtered_server if f in valid_attr_set)

            if filtered_strict:
                filtered_strict.add(f"{tbl}id".lower())
                strict_str = ",".join(sorted(filtered_strict))
            else:
                strict_str = f"{tbl}id".lower()

            if filtered_form:
                filtered_form.add(f"{tbl}id".lower())
                form_str = ",".join(sorted(filtered_form))
            else:
                form_str = f"{tbl}id".lower()

            if filtered_server:
                filtered_server.add(f"{tbl}id".lower())
                server_str = ",".join(sorted(filtered_server))
            else:
                server_str = f"{tbl}id".lower()

            # Active recommendation based on user preference
            if suggestion_mode == "client":
                recommended_str = strict_str
                active_fields = filtered_strict
            elif suggestion_mode == "server":
                recommended_str = server_str
                active_fields = filtered_server
            else:  # "forms" (default)
                recommended_str = form_str
                active_fields = filtered_form

            for occ in occ_list:
                if occ.get("is_unprojected") and self._is_webapi_governed(occ):
                    occ["suggested_fields"] = sorted(list(active_fields))
                    sug_cols = ",".join(sorted(active_fields))
                    raw_ep = occ.get("raw_endpoint", "")
                    if "/_api/" in raw_ep:
                        url_part = raw_ep.split("/_api/", 1)[1].split()[0].rstrip("`'\"")
                        base_and_query = url_part.split("?", 1)
                        path_part = base_and_query[0]
                        existing_q = base_and_query[1] if len(base_and_query) > 1 else ""
                        remaining_params = [
                            p for p in existing_q.split("&")
                            if p and not p.lower().startswith("$select=")
                        ]
                        all_params = [f"$select={sug_cols}"] + remaining_params
                        occ["suggested_rewrite"] = f"/_api/{path_part}?{'&'.join(all_params)}"
                    else:
                        occ["suggested_rewrite"] = f"/_api/{plural_ent}?$select={sug_cols}"

            if unprojected_calls_count > 0:
                if "all table" not in status_desc.lower():
                    status_desc += f" (All {len(active_fields)} table attributes included in recommended whitelist to save)."

            report.append({
                "table_name": tbl,
                "status": status,
                "status_level": status_level,
                "status_desc": status_desc,
                "is_enabled": is_enabled,
                "has_wildcard": has_wildcard,
                "configured_fields_raw": fields_raw,
                "configured_fields": configured_fields,
                "discovered_fields": discovered_fields,
                "missing_from_whitelist": missing_from_whitelist,
                "recommended_fields": recommended_str,
                "recommended_fields_strict": strict_str,
                "recommended_fields_forms": form_str,
                "recommended_fields_server": server_str,
                "recommended_fields_comprehensive": form_str,
                "suggestion_mode": suggestion_mode,
                "total_code_references": total_occurrences,
                "unprojected_calls_count": unprojected_calls_count,
                "has_unprojected_calls": unprojected_calls_count > 0,
                "unprojected_occurrences": unprojected_occs,
                "requires_code_update": unprojected_calls_count > 0,
                "all_fields_included_for_unprojected": all_table_fields_added,
                "unused_warning": unused_warning,
                "catalog_role": catalog_role,
                "setting_id": setting_id,
                "setting_table": setting_table,
                "website_id": website_id,
                "occurrences": occurrences_by_table.get(tbl, []),
            })

        # Sort: critical first, then warning, then framework/catalog, then info, then compliant
        level_weight = {"critical": 0, "warning": 1, "framework": 2, "info": 3, "neutral": 4, "success": 5}
        report.sort(key=lambda r: (level_weight.get(r["status_level"], 9), r["table_name"]))
        return report

    # ------------------------------------------------------------------
    # Remediation / Mutation Operations
    # ------------------------------------------------------------------

    def update_site_setting_fields(
        self, setting_table: str, setting_id: str, new_fields_value: str
    ) -> bool:
        """
        PATCH the adx_sitesettings or mspp_sitesettings record with the new
        explicit comma-separated fields whitelist.
        """
        url = f"{self.org_url}/api/data/v9.2/{setting_table}({setting_id})"
        schema = "mspp" if "mspp" in setting_table else "adx"
        val_col = f"{schema}_value"
        payload = {val_col: new_fields_value.strip()}

        headers = dict(self.headers)
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["If-Match"] = "*"

        res = requests.patch(url, headers=headers, json=payload, timeout=30)
        if not res.ok:
            error_msg = f"HTTP {res.status_code} {res.reason}"
            try:
                body = res.json()
                if "error" in body and "message" in body["error"]:
                    error_msg = body["error"]["message"]
            except Exception:
                if res.text:
                    error_msg = res.text[:300]
            logger.error("Failed updating site setting %s(%s): %s", setting_table, setting_id, error_msg)
            raise Exception(error_msg)
        return True

    def create_or_enable_webapi_site_settings(
        self, setting_table: str, website_id: str, table_name: str, fields_value: str
    ) -> Tuple[bool, str]:
        """
        Create Webapi/<table_name>/enabled and Webapi/<table_name>/fields site settings
        if they do not already exist.
        """
        schema = "mspp" if "mspp" in setting_table else "adx"
        name_col = f"{schema}_name"
        val_col = f"{schema}_value"
        site_nav = f"{schema}_websiteid@odata.bind"

        base_url = f"{self.org_url}/api/data/v9.2/{setting_table}"
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json; charset=utf-8"

        # 1. Create or set enabled = true
        enabled_name = f"Webapi/{table_name}/enabled"
        enabled_payload = {
            name_col: enabled_name,
            val_col: "true",
            site_nav: f"/{schema}_websites({website_id})",
        }
        res1 = requests.post(base_url, headers=headers, json=enabled_payload, timeout=30)
        if not res1.ok and res1.status_code != 412:
            try:
                err_text = res1.json().get("error", {}).get("message", res1.text[:200])
            except Exception:
                err_text = res1.text[:200]
            return False, f"Failed creating enabled setting: {err_text}"

        # 2. Create fields setting
        fields_name = f"Webapi/{table_name}/fields"
        fields_payload = {
            name_col: fields_name,
            val_col: fields_value.strip(),
            site_nav: f"/{schema}_websites({website_id})",
        }
        res2 = requests.post(base_url, headers=headers, json=fields_payload, timeout=30)
        if not res2.ok:
            try:
                err_text = res2.json().get("error", {}).get("message", res2.text[:200])
            except Exception:
                err_text = res2.text[:200]
            return False, f"Failed creating fields setting: {err_text}"

        return True, "Successfully created and configured Web API site settings."

    # ------------------------------------------------------------------
    # Deep Table 360-Degree Context Inspector
    # ------------------------------------------------------------------

    def get_table_deep_context(
        self,
        table_name: str,
        website_id: Optional[str] = None,
        existing_code_occurrences: Optional[list[dict]] = None,
    ) -> dict:
        """
        Fetch full 360-degree context for a single table:
          - Site settings for this table (Webapi/<table_name>/*) strictly scoped to the active portal
          - Cross-portal site settings (if configured on other portals in the same environment)
          - Basic Forms targeting this table
          - Multistep Form Steps targeting this table
          - Entity Lists targeting this table
          - Form Metadata records for this table
          - Code occurrences from scanned components
          - Table Permissions
        """
        t_clean = table_name.strip().lower()
        target_schema = self.get_website_schema(website_id) if website_id else None
        context: dict[str, Any] = {
            "table_name": t_clean,
            "website_id": website_id or "",
            "target_schema": target_schema or "adx",
            "site_settings": [],
            "cross_portal_site_settings": [],
            "basic_forms": [],
            "multistep_steps": [],
            "entity_lists": [],
            "form_metadata": [],
            "code_occurrences": [],
            "table_permissions": [],
        }

        # 1. Site settings — scoped to selected website & schema, with cross-portal detection
        for schema in ["adx", "mspp"]:
            query = (
                f"{schema}_sitesettings?$filter=contains({schema}_name, '{t_clean}')"
                f"&$select={schema}_sitesettingid,{schema}_name,{schema}_value,_{schema}_websiteid_value"
            )
            ss = self._odata_get(query)
            if ss and ss.get("value"):
                for row in ss["value"]:
                    row_site_id = row.get(f"_{schema}_websiteid_value") or ""
                    row_site_name = self._website_name_cache.get(row_site_id) or (
                        "Enhanced Portal (mspp)" if schema == "mspp" else "Standard Portal (adx)"
                    )
                    item = {
                        "name": row.get(f"{schema}_name"),
                        "value": row.get(f"{schema}_value"),
                        "id": row.get(f"{schema}_sitesettingid"),
                        "table": f"{schema}_sitesettings",
                        "website_id": row_site_id,
                        "portal_name": row_site_name,
                    }
                    if website_id:
                        if row_site_id == website_id and schema == target_schema:
                            context["site_settings"].append(item)
                        else:
                            context["cross_portal_site_settings"].append(item)
                    else:
                        context["site_settings"].append(item)

        fields_val = ""
        for s in context["site_settings"]:
            if (s.get("name") or "").lower().endswith("/fields"):
                fields_val = s.get("value") or ""
                break
        f_list = [f.strip() for f in fields_val.split(",") if f.strip()]
        context["catalog_role"] = self.detect_catalog_and_validation_role(t_clean, f_list)

        # 2. Basic Forms — query target schema and filter by website if specified
        schemas_to_query = [target_schema] if target_schema else ["adx", "mspp"]
        for schema in schemas_to_query:
            filter_str = f"{schema}_entityname eq '{t_clean}'"
            if website_id:
                filter_str += f" and (_{schema}_websiteid_value eq {website_id} or _{schema}_websiteid_value eq null)"
            bfs = self._odata_get(
                f"{schema}_entityforms?$filter={quote(filter_str)}"
                f"&$select={schema}_entityformid,{schema}_name,{schema}_mode,{schema}_formname"
            )
            if bfs and bfs.get("value"):
                for row in bfs["value"]:
                    mda_fname = row.get(f"{schema}_formname")
                    mda_fields = self.get_form_fields_by_name(t_clean, mda_fname) if mda_fname else []
                    context["basic_forms"].append({
                        "id": row.get(f"{schema}_entityformid"),
                        "name": row.get(f"{schema}_name"),
                        "mode": row.get(f"{schema}_mode"),
                        "form_name": mda_fname,
                        "form_fields": mda_fields,
                        "table": f"{schema}_entityforms",
                    })

        # 3. Multistep Steps
        for schema in schemas_to_query:
            steps = self._odata_get(
                f"{schema}_webformsteps?$filter={schema}_targetentitylogicalname eq '{t_clean}'"
                f"&$select={schema}_webformstepid,{schema}_name,{schema}_type,{schema}_formname"
            )
            if steps and steps.get("value"):
                for row in steps["value"]:
                    mda_fname = row.get(f"{schema}_formname")
                    mda_fields = self.get_form_fields_by_name(t_clean, mda_fname) if mda_fname else []
                    context["multistep_steps"].append({
                        "id": row.get(f"{schema}_webformstepid"),
                        "name": row.get(f"{schema}_name"),
                        "type": row.get(f"{schema}_type"),
                        "form_name": mda_fname,
                        "form_fields": mda_fields,
                        "table": f"{schema}_webformsteps",
                    })

        # 4. Entity Lists
        for schema in schemas_to_query:
            filter_str = f"{schema}_entityname eq '{t_clean}'"
            if website_id:
                filter_str += f" and (_{schema}_websiteid_value eq {website_id} or _{schema}_websiteid_value eq null)"
            lists = self._odata_get(
                f"{schema}_entitylists?$filter={quote(filter_str)}"
                f"&$select={schema}_entitylistid,{schema}_name"
            )
            if lists and lists.get("value"):
                for row in lists["value"]:
                    context["entity_lists"].append({
                        "id": row.get(f"{schema}_entitylistid"),
                        "name": row.get(f"{schema}_name"),
                        "table": f"{schema}_entitylists",
                    })

        # 5. Form Metadata (for discovered basic forms)
        form_ids = [f["id"] for f in context["basic_forms"] if f.get("id")]
        for fid in form_ids:
            for schema in schemas_to_query:
                fmd = self._odata_get(
                    f"{schema}_entityformmetadatas?$filter=_{schema}_entityformid_value eq {fid}"
                    f"&$select={schema}_entityformmetadataid,{schema}_attributelogicalname,{schema}_controlstyle,{schema}_type,{schema}_label,{schema}_description"
                )
                if fmd and fmd.get("value"):
                    for row in fmd["value"]:
                        context["form_metadata"].append({
                            "attribute": row.get(f"{schema}_attributelogicalname") or "(Form Control)",
                            "control_style": row.get(f"{schema}_controlstyle"),
                            "type": row.get(f"{schema}_type"),
                            "label": row.get(f"{schema}_label") or "",
                            "description": row.get(f"{schema}_description") or "",
                            "id": row.get(f"{schema}_entityformmetadataid"),
                            "table": f"{schema}_entityformmetadatas",
                        })

        # 6. Table Permissions
        for schema in schemas_to_query:
            filter_str = f"({schema}_entityname eq '{t_clean}' or {schema}_entitylogicalname eq '{t_clean}')"
            if website_id:
                filter_str += f" and (_{schema}_websiteid_value eq {website_id} or _{schema}_websiteid_value eq null)"
            perms = self._odata_get(
                f"{schema}_entitypermissions?$filter={quote(filter_str)}"
                f"&$select={schema}_entitypermissionid,{schema}_entityname,{schema}_scope,{schema}_read,{schema}_create,{schema}_write"
            )
            if perms and perms.get("value"):
                for row in perms["value"]:
                    context["table_permissions"].append({
                        "name": row.get(f"{schema}_entityname") or "Table Permission",
                        "id": row.get(f"{schema}_entitypermissionid"),
                        "read": row.get(f"{schema}_read"),
                        "write": row.get(f"{schema}_write"),
                        "create": row.get(f"{schema}_create"),
                        "table": f"{schema}_entitypermissions",
                    })

        # 7. Use existing scanned code occurrences (fast & zero network timeout risk)
        if existing_code_occurrences:
            for occ in existing_code_occurrences:
                if occ.get("table_name") == t_clean or occ.get("table_raw") == t_clean:
                    context["code_occurrences"].append(occ)

        return context
