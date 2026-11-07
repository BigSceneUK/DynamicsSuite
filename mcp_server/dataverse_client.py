from __future__ import annotations

import logging
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional
import requests
from mcp_server.auth import DynamicsAuthProvider, EnvironmentConfig

logger = logging.getLogger("mcp_dynamics_client")


def is_guid(val: Any) -> bool:
    """Check if value is a valid UUID/GUID."""
    if not isinstance(val, str):
        return False
    cleaned = val.strip("{}() '\"")
    try:
        uuid.UUID(cleaned)
        return True
    except (ValueError, AttributeError):
        return False


def clean_guid(val: Any) -> str:
    """Strip braces, quotes, and whitespace from a GUID."""
    if not val:
        return ""
    return str(val).strip("{}() '\"").lower()


class DataverseCaseClient:
    """Client for interacting with Dynamics 365 Cases and Dataverse diagnostics across environments."""

    def __init__(self, auth_provider: Optional[DynamicsAuthProvider] = None) -> None:
        self.auth = auth_provider or DynamicsAuthProvider()

    def list_environments(self) -> List[Dict[str, Any]]:
        """List all configured environments."""
        return self.auth.list_environments()

    def get_active_session(self) -> Dict[str, Any]:
        """Get live active session information and logged-in environment."""
        return self.auth.get_active_session_info()

    def list_accounts(self) -> List[Dict[str, Any]]:
        """List all connected user accounts (standard users, C-accounts) in the shared session."""
        return self.auth.list_accounts()

    def _get_api_url(self, environment: Optional[str] = None) -> str:
        env = self.auth.get_environment(environment)
        return f"{env.url}/api/data/v9.2"

    def _headers(self, environment: Optional[str] = None, account: Optional[str] = None) -> Dict[str, str]:
        token = self.auth.get_token(environment, account=account)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }

    def _get(
        self,
        endpoint: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        api_url = self._get_api_url(environment)
        url = f"{api_url}/{endpoint}" if not endpoint.startswith("http") else endpoint
        res = requests.get(url, headers=self._headers(environment, account=account), params=params, timeout=35)
        res.raise_for_status()
        return res.json()

    def _post(
        self,
        endpoint: str,
        data: Dict[str, Any],
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        api_url = self._get_api_url(environment)
        url = f"{api_url}/{endpoint}"
        headers = self._headers(environment, account=account)
        headers["Content-Type"] = "application/json"
        res = requests.post(url, headers=headers, json=data, timeout=35)
        res.raise_for_status()
        if res.status_code == 204 or not res.text:
            return {"status": "success"}
        return res.json()

    # ------------------------------------------------------------------
    # Case Operations
    # ------------------------------------------------------------------

    def get_case(
        self,
        identifier: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve case details by ticket number (e.g. CAS-12345-XXXX) or incident GUID."""
        identifier = identifier.strip()
        select_fields = (
            "incidentid,ticketnumber,title,description,createdon,modifiedon,"
            "statecode,statuscode,prioritycode,severitycode,casetypecode,"
            "_customerid_value,_ownerid_value,_primarycontactid_value,"
            "resolveby,firstresponsebykpiid,resolvebyslastatus,firstresponseslastatus"
        )

        env_info = self.auth.get_environment(environment)

        if is_guid(identifier):
            guid = clean_guid(identifier)
            res = self._get(f"incidents({guid})?$select={select_fields}", environment=environment, account=account)
            record = self._format_case_record(res)
            record["environment"] = env_info.name
            return record
        else:
            escaped_ticket = identifier.replace("'", "''")
            query = f"incidents?$filter=ticketnumber eq '{escaped_ticket}'&$select={select_fields}&$top=1"
            res = self._get(query, environment=environment, account=account)
            items = res.get("value", [])
            if not items:
                escaped_query = identifier.replace("'", "''")
                fallback_query = f"incidents?$filter=contains(title, '{escaped_query}')&$select={select_fields}&$top=1"
                fb_res = self._get(fallback_query, environment=environment, account=account)
                fb_items = fb_res.get("value", [])
                if not fb_items:
                    return {
                        "error": f"No case found matching identifier '{identifier}' in environment '{env_info.name}'."
                    }
                record = self._format_case_record(fb_items[0])
                record["environment"] = env_info.name
                return record

            record = self._format_case_record(items[0])
            record["environment"] = env_info.name
            return record

    def search_cases(
        self,
        query: Optional[str] = None,
        status: Optional[str] = None,
        customer_name: Optional[str] = None,
        limit: int = 10,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search cases by keyword, status ('Active', 'Resolved', 'Cancelled'), or customer name."""
        filters: List[str] = []

        if query:
            clean_q = query.replace("'", "''")
            filters.append(f"(contains(title, '{clean_q}') or contains(ticketnumber, '{clean_q}'))")

        if status:
            st = status.lower().strip()
            if st == "active":
                filters.append("statecode eq 0")
            elif st == "resolved":
                filters.append("statecode eq 1")
            elif st == "cancelled":
                filters.append("statecode eq 2")

        select_fields = (
            "incidentid,ticketnumber,title,createdon,modifiedon,"
            "statecode,statuscode,prioritycode,_customerid_value,_ownerid_value"
        )
        url = f"incidents?$select={select_fields}&$top={min(limit, 50)}&$orderby=createdon desc"
        if filters:
            url += f"&$filter={' and '.join(filters)}"

        res = self._get(url, environment=environment, account=account)
        results = [self._format_case_record(item) for item in res.get("value", [])]

        if customer_name:
            c_lower = customer_name.lower().strip()
            results = [
                r for r in results
                if c_lower in (r.get("customer") or "").lower()
            ]

        env_name = self.auth.get_environment(environment).name
        for r in results:
            r["environment"] = env_name

        return results

    def get_case_timeline(
        self,
        case_identifier: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve a consolidated chronological timeline for a case (notes, emails, tasks, resolutions)."""
        case_info = self.get_case(case_identifier, environment=environment, account=account)
        if "error" in case_info:
            return case_info

        incident_id = case_info["incident_id"]
        timeline_items: List[Dict[str, Any]] = []

        # Annotations (Notes)
        try:
            notes_res = self._get(
                f"annotations?$filter=_objectid_value eq {incident_id}"
                "&$select=annotationid,subject,notetext,filename,filesize,createdon,_createdby_value"
                "&$orderby=createdon desc&$top=50",
                environment=environment,
                account=account,
            )
            for n in notes_res.get("value", []):
                created_by = n.get("_createdby_value@OData.Community.Display.V1.FormattedValue") or "System"
                timeline_items.append({
                    "type": "Note",
                    "id": n.get("annotationid"),
                    "title": n.get("subject") or "(No subject)",
                    "content": n.get("notetext") or "",
                    "has_attachment": bool(n.get("filename")),
                    "attachment_name": n.get("filename"),
                    "created_by": created_by,
                    "created_on": n.get("createdon"),
                })
        except Exception as exc:
            logger.warning("Failed to fetch notes: %s", exc)

        # Emails
        try:
            emails_res = self._get(
                f"emails?$filter=_regardingobjectid_value eq {incident_id}"
                "&$select=activityid,subject,description,sender,torecipients,createdon,statecode,statuscode"
                "&$orderby=createdon desc&$top=50",
                environment=environment,
                account=account,
            )
            for em in emails_res.get("value", []):
                status_fmt = em.get("statuscode@OData.Community.Display.V1.FormattedValue") or "Email"
                timeline_items.append({
                    "type": "Email",
                    "id": em.get("activityid"),
                    "title": em.get("subject") or "(No Subject)",
                    "content": em.get("description") or "",
                    "sender": em.get("sender") or "",
                    "status": status_fmt,
                    "created_on": em.get("createdon"),
                })
        except Exception as exc:
            logger.warning("Failed to fetch emails: %s", exc)

        # Tasks
        try:
            tasks_res = self._get(
                f"tasks?$filter=_regardingobjectid_value eq {incident_id}"
                "&$select=activityid,subject,description,scheduledend,createdon,statecode,statuscode"
                "&$orderby=createdon desc&$top=50",
                environment=environment,
                account=account,
            )
            for t in tasks_res.get("value", []):
                timeline_items.append({
                    "type": "Task",
                    "id": t.get("activityid"),
                    "title": t.get("subject") or "(Task)",
                    "content": t.get("description") or "",
                    "status": t.get("statuscode@OData.Community.Display.V1.FormattedValue") or "",
                    "due_date": t.get("scheduledend"),
                    "created_on": t.get("createdon"),
                })
        except Exception as exc:
            logger.warning("Failed to fetch tasks: %s", exc)

        # Incident Resolutions
        try:
            res_data = self._get(
                f"incidentresolutions?$filter=_incidentid_value eq {incident_id}"
                "&$select=activityid,subject,resolutiontypecode,description,totaltime,createdon"
                "&$orderby=createdon desc&$top=10",
                environment=environment,
                account=account,
            )
            for r in res_data.get("value", []):
                timeline_items.append({
                    "type": "Case Resolution",
                    "id": r.get("activityid"),
                    "title": r.get("subject") or "Case Resolved",
                    "content": r.get("description") or "",
                    "resolution_type": r.get("resolutiontypecode@OData.Community.Display.V1.FormattedValue") or "",
                    "created_on": r.get("createdon"),
                })
        except Exception as exc:
            logger.warning("Failed to fetch resolutions: %s", exc)

        timeline_items.sort(key=lambda x: x.get("created_on") or "", reverse=True)

        return {
            "ticket_number": case_info.get("ticket_number"),
            "case_title": case_info.get("title"),
            "incident_id": incident_id,
            "environment": case_info.get("environment"),
            "total_events": len(timeline_items),
            "timeline": timeline_items,
        }

    def add_case_note(
        self,
        case_identifier: str,
        title: str,
        note_text: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a note (annotation) to a Dynamics 365 case."""
        case_info = self.get_case(case_identifier, environment=environment, account=account)
        if "error" in case_info:
            return case_info

        incident_id = case_info["incident_id"]
        payload = {
            "subject": title,
            "notetext": note_text,
            "objectid_incident@odata.bind": f"/incidents({incident_id})",
        }
        self._post("annotations", payload, environment=environment, account=account)
        return {
            "status": "success",
            "message": f"Note added successfully to case {case_info.get('ticket_number')}.",
            "ticket_number": case_info.get("ticket_number"),
            "incident_id": incident_id,
            "environment": case_info.get("environment"),
        }

    # ------------------------------------------------------------------
    # Analytical & Diagnostic Operations
    # ------------------------------------------------------------------

    def query_table(
        self,
        table_name: str,
        filter_expr: Optional[str] = None,
        select_fields: Optional[str] = None,
        top: int = 10,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Execute an arbitrary OData query on any Dataverse table."""
        params: List[str] = [f"$top={min(top, 100)}"]
        if select_fields:
            params.append(f"$select={select_fields}")
        if filter_expr:
            params.append(f"$filter={filter_expr}")

        query = f"{table_name}?{'&'.join(params)}"
        res = self._get(query, environment=environment, account=account)
        items = res.get("value", [])

        formatted: List[Dict[str, Any]] = []
        for item in items:
            clean_item: Dict[str, Any] = {}
            for k, v in item.items():
                if k.startswith("@"):
                    continue
                fmt_key = f"{k}@OData.Community.Display.V1.FormattedValue"
                if fmt_key in item:
                    clean_item[k] = item[fmt_key]
                else:
                    clean_item[k] = v
            formatted.append(clean_item)
        return formatted

    def execute_fetchxml(
        self,
        entity_set_name: str,
        fetch_xml: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a raw FetchXML query against Dataverse."""
        encoded_xml = urllib.parse.quote(fetch_xml.strip())
        endpoint = f"{entity_set_name}?fetchXml={encoded_xml}"
        res = self._get(endpoint, environment=environment, account=account)
        items = res.get("value", [])

        formatted: List[Dict[str, Any]] = []
        for item in items:
            clean_item: Dict[str, Any] = {}
            for k, v in item.items():
                if k.startswith("@"):
                    continue
                fmt_key = f"{k}@OData.Community.Display.V1.FormattedValue"
                if fmt_key in item:
                    clean_item[k] = item[fmt_key]
                else:
                    clean_item[k] = v
            formatted.append(clean_item)
        return formatted

    def get_entity_metadata(
        self,
        entity_logical_name: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch metadata, attributes, and primary keys for an entity."""
        endpoint = (
            f"EntityDefinitions(LogicalName='{entity_logical_name}')"
            "?$select=LogicalName,DisplayName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute,IsCustomEntity"
            "&$expand=Attributes($select=LogicalName,DisplayName,AttributeType,RequiredLevel)"
        )
        res = self._get(endpoint, environment=environment, account=account)

        def _label(obj: Any) -> str:
            if isinstance(obj, dict):
                user_loc = obj.get("UserLocalizedLabel")
                if user_loc and isinstance(user_loc, dict):
                    return user_loc.get("Label") or ""
            return ""

        attrs = []
        for a in res.get("Attributes", []):
            attrs.append({
                "logical_name": a.get("LogicalName"),
                "display_name": _label(a.get("DisplayName")),
                "type": a.get("AttributeType"),
            })

        return {
            "logical_name": res.get("LogicalName"),
            "display_name": _label(res.get("DisplayName")),
            "entity_set_name": res.get("EntitySetName"),
            "primary_id": res.get("PrimaryIdAttribute"),
            "primary_name": res.get("PrimaryNameAttribute"),
            "is_custom": res.get("IsCustomEntity"),
            "total_attributes": len(attrs),
            "attributes": attrs,
        }

    def diagnose_record(
        self,
        entity_set_name: str,
        record_id: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Deep diagnostic analysis of a record: current values, audit history,
        related asynchronous jobs, and plugin trace logs.
        """
        guid = clean_guid(record_id)
        report: Dict[str, Any] = {
            "entity_set_name": entity_set_name,
            "record_id": guid,
            "environment": self.auth.get_environment(environment).name,
        }

        # 1. Fetch current record
        try:
            rec = self._get(f"{entity_set_name}({guid})", environment=environment, account=account)
            clean_rec = {}
            for k, v in rec.items():
                if not k.startswith("@"):
                    fmt = f"{k}@OData.Community.Display.V1.FormattedValue"
                    clean_rec[k] = rec[fmt] if fmt in rec else v
            report["record"] = clean_rec
        except Exception as exc:
            report["record_error"] = str(exc)

        # 2. Fetch field audit history
        try:
            audits_res = self._get(
                f"audits?$filter=_objectid_value eq {guid}&$orderby=createdon desc&$top=30",
                environment=environment,
                account=account,
            )
            audit_events = []
            for a in audits_res.get("value", []):
                audit_events.append({
                    "audit_id": a.get("auditid"),
                    "action": a.get("action@OData.Community.Display.V1.FormattedValue") or a.get("action"),
                    "operation": a.get("operation@OData.Community.Display.V1.FormattedValue") or a.get("operation"),
                    "user": a.get("_userid_value@OData.Community.Display.V1.FormattedValue") or a.get("_userid_value"),
                    "created_on": a.get("createdon"),
                })
            report["audit_history"] = audit_events
        except Exception as exc:
            report["audit_error"] = str(exc)

        # 3. Fetch related system jobs (Async Operations)
        try:
            async_res = self._get(
                f"asyncoperations?$filter=_regardingobjectid_value eq {guid}&$orderby=completedon desc&$top=20",
                environment=environment,
                account=account,
            )
            async_jobs = []
            for job in async_res.get("value", []):
                async_jobs.append({
                    "job_id": job.get("asyncoperationid"),
                    "name": job.get("name"),
                    "status": job.get("statuscode@OData.Community.Display.V1.FormattedValue") or job.get("statuscode"),
                    "operation_type": job.get("operationtype@OData.Community.Display.V1.FormattedValue"),
                    "message": job.get("message"),
                    "friendly_message": job.get("friendlymessage"),
                    "completed_on": job.get("completedon"),
                })
            report["system_jobs"] = async_jobs
        except Exception as exc:
            report["system_jobs_error"] = str(exc)

        # 4. Fetch correlated Plugin Trace Logs
        try:
            trace_res = self._get(
                f"plugintracelogs?$filter=_primaryentityid_value eq {guid}&$orderby=createdon desc&$top=20",
                environment=environment,
                account=account,
            )
            traces = []
            for t in trace_res.get("value", []):
                traces.append({
                    "trace_id": t.get("plugintracelogid"),
                    "type_name": t.get("typename"),
                    "message_name": t.get("messagename"),
                    "mode": t.get("mode@OData.Community.Display.V1.FormattedValue"),
                    "depth": t.get("depth"),
                    "message": t.get("messageblock"),
                    "exception_details": t.get("exceptiondetails"),
                    "created_on": t.get("createdon"),
                })
            report["plugin_traces"] = traces
        except Exception as exc:
            report["plugin_traces_error"] = str(exc)

        return report

    # ------------------------------------------------------------------
    # Formatting Helpers
    # ------------------------------------------------------------------

    def _format_case_record(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Format raw OData case JSON into a clean, human-readable dictionary."""
        def _get_val(field: str) -> Any:
            fmt_key = f"{field}@OData.Community.Display.V1.FormattedValue"
            if fmt_key in raw:
                return raw[fmt_key]
            return raw.get(field)

        return {
            "incident_id": raw.get("incidentid"),
            "ticket_number": raw.get("ticketnumber"),
            "title": raw.get("title"),
            "description": raw.get("description"),
            "state": _get_val("statecode") or "Unknown",
            "status_reason": _get_val("statuscode") or "Unknown",
            "priority": _get_val("prioritycode") or "Normal",
            "severity": _get_val("severitycode"),
            "case_type": _get_val("casetypecode"),
            "customer": _get_val("_customerid_value") or "None",
            "customer_id": raw.get("_customerid_value"),
            "owner": _get_val("_ownerid_value") or "Unassigned",
            "owner_id": raw.get("_ownerid_value"),
            "created_on": raw.get("createdon"),
            "modified_on": raw.get("modifiedon"),
            "resolve_by": raw.get("resolveby"),
            "first_response_sla": _get_val("firstresponseslastatus"),
            "resolve_by_sla": _get_val("resolvebyslastatus"),
        }

