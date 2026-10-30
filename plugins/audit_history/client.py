from __future__ import annotations

import logging
from typing import Any, Optional
import uuid
import urllib.parse

import concurrent.futures
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _is_guid(val: Any) -> bool:
    """Check if a value represents a valid UUID/GUID."""
    if not isinstance(val, str):
        return False
    cleaned = val.strip("{}() '\"")
    try:
        uuid.UUID(cleaned)
        return True
    except (ValueError, AttributeError):
        return False


class DataverseClient:
    """Thin Dataverse REST client for the Audit History plugin."""

    def __init__(self, org_url: str, token: str):
        self.org_url = org_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            ),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._user_cache: dict = {}
        self._workflow_cache: dict = {}

    # ------------------------------------------------------------------
    # Entity / attribute discovery
    # ------------------------------------------------------------------

    def get_entities(self) -> list[dict]:
        """Return all audit-enabled or custom entities, sorted by display name."""
        query = (
            "?$select=LogicalName,DisplayName,EntitySetName"
            "&$filter=IsAuditEnabled/Value eq true or IsCustomEntity eq true"
        )
        url = f"{self.org_url}/api/data/v9.2/EntityDefinitions{query}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            entities = res.json().get("value", [])
            results = []
            for e in entities:
                if not e:
                    continue
                logical_name = e.get("LogicalName")
                display_name = logical_name
                display_obj = e.get("DisplayName")
                if isinstance(display_obj, dict):
                    loc = display_obj.get("UserLocalizedLabel")
                    if isinstance(loc, dict):
                        display_name = loc.get("Label") or logical_name
                    results.append(
                        {
                            "logical_name": logical_name,
                            "display_name": display_name,
                            "entity_set_name": e.get("EntitySetName"),
                        }
                    )
            return sorted(results, key=lambda x: x["display_name"])
        except Exception as exc:
            logging.error("Failed to fetch entities: %s", exc)
            return []

    def get_attribute_metadata(self, entity_logical_name: str) -> dict[str, str]:
        """Return a mapping of logicalName -> displayName for an entity's attributes."""
        url = (
            f"{self.org_url}/api/data/v9.2/EntityDefinitions"
            f"(LogicalName='{entity_logical_name}')/Attributes"
            "?$select=LogicalName,DisplayName,AttributeType"
        )
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            mapping: dict[str, str] = {}
            for attr in res.json().get("value", []):
                if not attr:
                    continue
                logical = attr.get("LogicalName")
                if not logical:
                    continue
                display = logical
                display_obj = attr.get("DisplayName")
                if isinstance(display_obj, dict):
                    loc = display_obj.get("UserLocalizedLabel")
                    if isinstance(loc, dict):
                        display = loc.get("Label") or logical
                mapping[logical] = display
            return mapping
        except Exception as exc:
            logging.error("Failed to fetch attribute metadata: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Record search
    # ------------------------------------------------------------------

    def search_records(
        self, entity_set_name: str, filters: dict | None = None, top: int = 20
    ) -> list[dict]:
        """Search records using simple contains / eq filters."""
        filter_parts = []

        if filters:
            for field, val in filters.items():
                if val is not None and val != "":
                    if isinstance(val, (int, float, bool)):
                        filter_parts.append(f"{field} eq {str(val).lower()}")
                    elif _is_guid(str(val)):
                        clean_guid = str(val).strip("{}() '\"")
                        filter_parts.append(f"{field} eq {clean_guid}")
                    else:
                        escaped_val = str(val).replace("'", "''")
                        filter_parts.append(f"contains({field}, '{escaped_val}')")

        query = f"?$top={top}"
        if filter_parts:
            query += f"&$filter={' and '.join(filter_parts)}"

        url = f"{self.org_url}/api/data/v9.2/{entity_set_name}{query}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.error("Search failed for %s: %s", entity_set_name, exc)
            return []

    # ------------------------------------------------------------------
    # Audit history
    # ------------------------------------------------------------------

    def get_audit_history(self, entity_set_name: str, record_id: str) -> list[dict]:
        """
        Retrieve record-level audit history via RetrieveRecordChangeHistory.
        Returns a list of AuditDetail objects.
        """
        clean_id = record_id.strip("{}() '\"")
        target = f"{self.org_url}/api/data/v9.2/{entity_set_name}({clean_id})"
        url = (
            f"{self.org_url}/api/data/v9.2/RetrieveRecordChangeHistory(Target=@Target)"
            f"?@Target={{\"@odata.id\":\"{target}\"}}"
        )
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            data = res.json()
            details = data.get("AuditDetailCollection", {}).get("AuditDetails", [])
            if not details and "value" in data:
                details = data["value"]
            return details
        except Exception as exc:
            logging.error("Failed to fetch audit history: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Automation & Workflow Discovery
    # ------------------------------------------------------------------

    def get_instance_id(self) -> str | None:
        """Return the Dataverse organization ID (matches Power Automate environment ID)."""
        try:
            url = f"{self.org_url}/api/data/v9.2/organizations?$select=organizationid"
            res = self.session.get(url, headers=self.headers, timeout=20)
            res.raise_for_status()
            val = res.json().get("value", [])
            return val[0]["organizationid"] if val else None
        except Exception as exc:
            logging.warning("Could not fetch organization ID: %s", exc)
            return None

    def get_workflows_for_entity(
        self,
        entity_logical_name: str = "",
        include_inactive: bool = False,
        ignore_name_patterns: list[str] | None = None,
    ) -> list[dict]:
        """
        Fetch Cloud Flows (category 5) and Classic Workflows (category 0) in the environment.
        Uses lightweight metadata fields and odata.maxpagesize=5000 so all active workflows
        (even environments with 8,000+ flows) are discovered quickly without downloading gigabytes of clientdata.
        Optionally filters out workflows matching ignore_name_patterns (e.g. ['CXP_']).
        """
        select_cols = (
            "workflowid,name,category,mode,statecode,statuscode,modifiedon,"
            "primaryentity,uniquename,description,triggeronupdateattributelist,"
            "triggeroncreate,triggerondelete,ondemand,subprocess"
        )
        norm_ent = (entity_logical_name or "").strip().lower()
        if norm_ent:
            filter_expr = f"((category eq 0 and primaryentity eq '{norm_ent}') or category eq 5) and type eq 1"
        else:
            filter_expr = "(category eq 5 or category eq 0) and type eq 1"

        if not include_inactive:
            filter_expr += " and statecode eq 1"
        url = (
            f"{self.org_url}/api/data/v9.2/workflows"
            f"?$select={select_cols}&$filter={filter_expr}&$orderby=name asc"
        )
        headers = dict(self.headers)
        headers["Prefer"] = "odata.maxpagesize=5000"
        results: list[dict] = []
        next_url: str | None = url

        clean_patterns = [
            p.strip().lower() for p in (ignore_name_patterns or []) if p and p.strip()
        ]

        try:
            while next_url:
                res = self.session.get(next_url, headers=headers, timeout=60)
                res.raise_for_status()
                data = res.json()
                page_items = data.get("value", [])
                if clean_patterns:
                    page_items = [
                        w for w in page_items
                        if not any(pat in str(w.get("name") or "").lower() for pat in clean_patterns)
                    ]
                results.extend(page_items)
                next_url = data.get("@odata.nextLink")
            return results
        except Exception as exc:
            logging.warning("Failed to fetch workflows: %s", exc)
            return []

    def get_workflows_touching_entity(
        self,
        entity_logical_name: str,
        include_inactive: bool = False,
        ignore_name_patterns: list[str] | None = None,
    ) -> list[dict]:
        """
        Targeted discovery of Cloud Flows and Classic Workflows touching a specific table.
        Attempts server-side FetchXML filter on clientdata and primaryentity first.
        Falls back to primaryentity OData query if clientdata FetchXML is restricted.
        Optionally filters out workflows matching ignore_name_patterns (e.g. ['CXP_']).
        """
        if not entity_logical_name:
            return []
        norm_ent = entity_logical_name.strip().lower()
        clean_patterns = [
            p.strip().lower() for p in (ignore_name_patterns or []) if p and p.strip()
        ]

        # 1. Try FetchXML to find workflows whose clientdata or primaryentity mentions the table
        state_cond = "" if include_inactive else "<condition attribute='statecode' operator='eq' value='1' />"
        fetchxml = (
            f"<fetch>"
            f"<entity name='workflow'>"
            f"<attribute name='workflowid' />"
            f"<attribute name='name' />"
            f"<attribute name='category' />"
            f"<attribute name='statecode' />"
            f"<attribute name='statuscode' />"
            f"<attribute name='modifiedon' />"
            f"<attribute name='primaryentity' />"
            f"<attribute name='uniquename' />"
            f"<attribute name='description' />"
            f"<attribute name='clientdata' />"
            f"<attribute name='triggeronupdateattributelist' />"
            f"<attribute name='triggeroncreate' />"
            f"<attribute name='triggerondelete' />"
            f"<attribute name='ondemand' />"
            f"<attribute name='subprocess' />"
            f"<filter type='and'>"
            f"<condition attribute='category' operator='in'>"
            f"<value>5</value>"
            f"<value>0</value>"
            f"</condition>"
            f"{state_cond}"
            f"<condition attribute='type' operator='eq' value='1' />"
            f"<filter type='or'>"
            f"<condition attribute='primaryentity' operator='eq' value='{norm_ent}' />"
            f"<condition attribute='clientdata' operator='like' value='%{norm_ent}%' />"
            f"</filter>"
            f"</filter>"
            f"</entity>"
            f"</fetch>"
        )
        url = f"{self.org_url}/api/data/v9.2/workflows?fetchXml={urllib.parse.quote(fetchxml)}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=45)
            if res.status_code == 200:
                data = res.json()
                results = data.get("value", [])
                if results:
                    if clean_patterns:
                        results = [
                            w for w in results
                            if not any(pat in str(w.get("name") or "").lower() for pat in clean_patterns)
                        ]
                    return results
        except Exception as exc:
            logging.debug("FetchXML clientdata filter failed: %s, falling back to OData", exc)

        # 2. Fallback: Query by primaryentity via OData
        select_cols = (
            "workflowid,name,category,mode,statecode,statuscode,modifiedon,"
            "primaryentity,uniquename,description,clientdata,"
            "triggeronupdateattributelist,triggeroncreate,triggerondelete,ondemand,subprocess"
        )
        if include_inactive:
            filter_expr = f"(category eq 5 or category eq 0) and type eq 1 and primaryentity eq '{norm_ent}'"
        else:
            filter_expr = f"(category eq 5 or category eq 0) and statecode eq 1 and type eq 1 and primaryentity eq '{norm_ent}'"
        url = f"{self.org_url}/api/data/v9.2/workflows?$select={select_cols}&$filter={urllib.parse.quote(filter_expr)}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=45)
            if res.status_code == 200:
                results = res.json().get("value", [])
                if clean_patterns and results:
                    results = [
                        w for w in results
                        if not any(pat in str(w.get("name") or "").lower() for pat in clean_patterns)
                    ]
                return results
        except Exception as exc:
            logging.warning("Failed to fetch touching workflows via OData: %s", exc)
        return []

    def get_workflow_clientdata(self, workflow_id: str) -> str | None:
        """Fetch clientdata definition JSON for a single workflow on demand with caching."""
        clean_id = workflow_id.strip("{}() '\"")
        if not clean_id:
            return None
        if clean_id in self._workflow_cache:
            return self._workflow_cache[clean_id]
        url = f"{self.org_url}/api/data/v9.2/workflows({clean_id})?$select=clientdata"
        try:
            res = self.session.get(url, headers=self.headers, timeout=25)
            if res.status_code == 200:
                cd = res.json().get("clientdata")
                if cd:
                    self._workflow_cache[clean_id] = cd
                return cd
        except Exception as exc:
            logging.debug("Failed to fetch clientdata for workflow %s: %s", clean_id, exc)
        return None

    def get_workflows_clientdata_batch(
        self, workflow_ids: list[str], max_workers: int = 8
    ) -> dict[str, str]:
        """Fetch clientdata definition JSON for multiple workflows concurrently with internal caching."""
        clean_ids = [w.strip("{}() '\"") for w in workflow_ids if w and w.strip("{}() '\"")]
        unique_ids = list(dict.fromkeys(clean_ids))
        results: dict[str, str] = {}
        missing_ids = []

        for wid in unique_ids:
            if wid in self._workflow_cache:
                results[wid] = self._workflow_cache[wid]
            else:
                missing_ids.append(wid)

        if not missing_ids:
            return results

        def _fetch_one(wid: str) -> tuple[str, Optional[str]]:
            cdata = self.get_workflow_clientdata(wid)
            return wid, cdata

        workers = min(max_workers, len(missing_ids))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            fut_to_id = {executor.submit(_fetch_one, wid): wid for wid in missing_ids}
            for fut in concurrent.futures.as_completed(fut_to_id):
                wid, cdata = fut.result()
                if cdata:
                    self._workflow_cache[wid] = cdata
                    results[wid] = cdata

        return results

    def get_workflow(self, workflow_id: str) -> dict | None:
        """Fetch workflow record (metadata and clientdata) by workflow ID on demand."""
        clean_id = workflow_id.strip("{}() '\"")
        if not clean_id:
            return None
        select_cols = (
            "workflowid,name,category,mode,statecode,statuscode,modifiedon,"
            "primaryentity,uniquename,description,clientdata"
        )
        url = f"{self.org_url}/api/data/v9.2/workflows({clean_id})?$select={select_cols}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=25)
            if res.status_code == 200:
                data = res.json()
                if data and data.get("clientdata"):
                    self._workflow_cache[clean_id] = data["clientdata"]
                return data
        except Exception as exc:
            logging.debug("Failed to fetch workflow %s: %s", clean_id, exc)
        return None

    def get_flow_sessions(
        self, start_iso: str, end_iso: str, top: int = 500
    ) -> list[dict]:
        """Fetch flow sessions executed within the [start_iso, end_iso] window."""
        params = (
            f"?$filter=startedon ge '{start_iso}' and startedon le '{end_iso}'"
            f"&$select=flowsessionid,startedon,completedon,statuscode,_regardingobjectid_value"
            f"&$orderby=startedon desc&$top={top}"
        )
        url = f"{self.org_url}/api/data/v9.2/flowsessions{params}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.warning("Failed to fetch flowsessions: %s", exc)
            return []

    def get_flow_runs(
        self, start_iso: str, end_iso: str, top: int = 500
    ) -> list[dict]:
        """
        Fetch modern flowruns elastic table records.
        Returns empty list if the elastic table is not available in this environment.
        """
        params = (
            f"?$filter=starttime ge '{start_iso}' and starttime le '{end_iso}'"
            f"&$select=flowrunid,starttime,endtime,status,flowname,flowid,errormessage,duration"
            f"&$orderby=starttime desc&$top={top}"
        )
        url = f"{self.org_url}/api/data/v9.2/flowruns{params}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            if res.status_code == 404:
                return []
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.debug("flowruns table not available: %s", exc)
            return []

    def get_async_operations(
        self, record_id: str, start_iso: str | None = None, end_iso: str | None = None, top: int = 100
    ) -> list[dict]:
        """Fetch asyncoperation executions (Classic Background Workflows & System Jobs) for the record."""
        clean_id = record_id.strip("{}() '\"")
        filter_parts = [f"_regardingobjectid_value eq '{clean_id}'"]
        if start_iso:
            filter_parts.append(f"startedon ge '{start_iso}'")
        if end_iso:
            filter_parts.append(f"startedon le '{end_iso}'")

        combined = " and ".join(filter_parts)
        fields = (
            "asyncoperationid,name,startedon,completedon,statuscode,statecode,"
            "_regardingobjectid_value,_workflowactivationid_value,friendlymessage,message"
        )
        url = (
            f"{self.org_url}/api/data/v9.2/asyncoperations"
            f"?$select={fields}&$filter={combined}&$top={top}&$orderby=startedon desc"
        )
        try:
            res = self.session.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.warning("Failed to fetch asyncoperations: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Power Pages & Dynamics 365 Portals Discovery
    # ------------------------------------------------------------------

    def get_portal_webform_sessions(
        self, record_id: str, start_iso: str | None = None, end_iso: str | None = None, top: int = 100
    ) -> list[dict]:
        """Fetch Power Pages / Dynamics 365 Portal webform session records for the target record."""
        clean_id = record_id.strip("{}() '\"")
        # Try adx_webformsessions first, fall back to mspp_webformsessions if not found
        for entity_set, prefix in [("adx_webformsessions", "adx_"), ("mspp_webformsessions", "mspp_")]:
            filter_parts = [f"{prefix}primaryrecordid eq '{clean_id}'"]
            if start_iso:
                filter_parts.append(f"createdon ge '{start_iso}'")
            if end_iso:
                filter_parts.append(f"createdon le '{end_iso}'")
            combined = " and ".join(filter_parts)
            fields = (
                f"{prefix}webformsessionid,{prefix}primaryrecordid,createdon,modifiedon,statecode,statuscode,"
                f"_{prefix}webform_value,_{prefix}webformstep_value,_{prefix}contact_value,_{prefix}user_value"
            )
            url = f"{self.org_url}/api/data/v9.2/{entity_set}?$select={fields}&$filter={combined}&$top={top}&$orderby=createdon desc"
            try:
                res = self.session.get(url, headers=self.headers, timeout=30)
                if res.status_code == 200:
                    rows = res.json().get("value", [])
                    if rows:
                        return rows
                elif res.status_code == 404:
                    continue
            except Exception as exc:
                logging.debug("Failed querying %s: %s", entity_set, exc)
        return []

    def get_portal_forms_for_entity(self, entity_logical_name: str) -> list[dict]:
        """
        Fetch Basic Forms (Entity Forms) and Multistep Form Steps (Web Form Steps)
        targeting the given table, along with their metadata.
        """
        if not entity_logical_name:
            return []
        norm_ent = entity_logical_name.strip().lower()
        forms: list[dict] = []

        # 1. Basic Forms (adx_entityforms / mspp_entityforms)
        for entity_set, pfx in [("adx_entityforms", "adx_"), ("mspp_entityforms", "mspp_")]:
            url = (
                f"{self.org_url}/api/data/v9.2/{entity_set}?"
                f"$filter={pfx}entityname eq '{norm_ent}' and statecode eq 0"
                f"&$select={pfx}entityformid,{pfx}name,{pfx}entityname,{pfx}mode,{pfx}formname,"
                f"{pfx}associatecurrentportaluser,{pfx}setentityreference"
            )
            try:
                res = self.session.get(url, headers=self.headers, timeout=30)
                if res.status_code == 200:
                    items = res.json().get("value", [])
                    for it in items:
                        forms.append({
                            "form_id": it.get(f"{pfx}entityformid"),
                            "name": it.get(f"{pfx}name") or "Unnamed Basic Form",
                            "form_type": "Basic Form",
                            "entity_name": it.get(f"{pfx}entityname") or norm_ent,
                            "mode": it.get(f"{pfx}mode"),  # 100000000=Insert, 100000001=Edit, 100000002=ReadOnly
                            "form_name": it.get(f"{pfx}formname"),
                            "associate_contact": bool(it.get(f"{pfx}associatecurrentportaluser")),
                            "source_table": entity_set,
                        })
                    if items:
                        break
            except Exception as exc:
                logging.debug("Failed querying %s: %s", entity_set, exc)

        # 2. Multistep Form Steps (adx_webformsteps / mspp_webformsteps)
        for entity_set, pfx in [("adx_webformsteps", "adx_"), ("mspp_webformsteps", "mspp_")]:
            url = (
                f"{self.org_url}/api/data/v9.2/{entity_set}?"
                f"$filter={pfx}targetentitylogicalname eq '{norm_ent}' and statecode eq 0"
                f"&$select={pfx}webformstepid,{pfx}name,{pfx}targetentitylogicalname,{pfx}mode,{pfx}formname,_{pfx}webform_value"
            )
            try:
                res = self.session.get(url, headers=self.headers, timeout=30)
                if res.status_code == 200:
                    items = res.json().get("value", [])
                    for it in items:
                        forms.append({
                            "form_id": it.get(f"{pfx}webformstepid"),
                            "name": it.get(f"{pfx}name") or "Unnamed Web Form Step",
                            "form_type": "Multistep Form Step",
                            "webform_id": it.get(f"_{pfx}webform_value"),
                            "webform_name": it.get(f"_{pfx}webform_value@OData.Community.Display.V1.FormattedValue") or "",
                            "entity_name": it.get(f"{pfx}targetentitylogicalname") or norm_ent,
                            "mode": it.get(f"{pfx}mode"),
                            "form_name": it.get(f"{pfx}formname"),
                            "source_table": entity_set,
                        })
                    if items:
                        break
            except Exception as exc:
                logging.debug("Failed querying %s: %s", entity_set, exc)

        # 3. Form metadata (Set Value On Save & Prefill attributes)
        form_ids = [f["form_id"] for f in forms if f.get("form_id")]
        if form_ids:
            for entity_set, pfx, form_fk in [
                ("adx_entityformmetadatas", "adx_", f"_adx_entityform_value"),
                ("mspp_entityformmetadatas", "mspp_", f"_mspp_entityform_value"),
            ]:
                try:
                    meta_url = (
                        f"{self.org_url}/api/data/v9.2/{entity_set}?"
                        f"$select={pfx}attributelogicalname,{pfx}type,{pfx}value,{form_fk}"
                        f"&$top=500"
                    )
                    res = self.session.get(meta_url, headers=self.headers, timeout=30)
                    if res.status_code == 200:
                        meta_items = res.json().get("value", [])
                        meta_by_form: dict[str, set[str]] = {}
                        for m in meta_items:
                            fid = m.get(form_fk)
                            attr = m.get(f"{pfx}attributelogicalname")
                            if fid and attr:
                                meta_by_form.setdefault(str(fid).lower(), set()).add(str(attr).lower())
                        for f in forms:
                            fid = str(f.get("form_id") or "").lower()
                            f["metadata_attributes"] = meta_by_form.get(fid, set())
                        break
                except Exception as exc:
                    logging.debug("Failed querying %s: %s", entity_set, exc)

        return forms


