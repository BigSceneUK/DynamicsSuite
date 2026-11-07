from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
import requests
from mcp_server.auth import DynamicsAuthProvider

logger = logging.getLogger("mcp_dependency_tools")


class DependencyTools:
    """Cloud Flow triggers and automation dependency inspection tools."""

    def __init__(self, auth: DynamicsAuthProvider) -> None:
        self.auth = auth

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
        env = self.auth.get_environment(environment)
        url = f"{env.url}/api/data/v9.2/{endpoint}" if not endpoint.startswith("http") else endpoint
        res = requests.get(url, headers=self._headers(environment, account=account), params=params, timeout=45)
        res.raise_for_status()
        return res.json()

    def list_entity_triggers(
        self,
        entity_logical_name: str,
        message: Optional[str] = None,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List all automated Cloud Flows that trigger upon Create, Update, or Delete on an entity."""
        target_entity = entity_logical_name.strip().lower()

        # Query all active modern cloud flows (category 5)
        query = (
            "workflows?$filter=category eq 5 and type eq 1 and statecode eq 1"
            "&$select=workflowid,name,description,statecode,clientdata"
            "&$top=100"
        )
        res = self._get(query, environment=environment, account=account)
        flows = res.get("value", [])

        matching_triggers: List[Dict[str, Any]] = []

        for f in flows:
            cdata_str = f.get("clientdata")
            if not cdata_str:
                continue
            try:
                cdata = json.loads(cdata_str)
                props = cdata.get("properties") or cdata
                triggers = props.get("definition", {}).get("triggers") or {}

                for t_name, t_body in triggers.items():
                    inputs = t_body.get("inputs") or {}
                    params = inputs.get("parameters") or {}

                    trigger_entity = None
                    for key in ["subscriptionRequest/entityname", "entityName", "table", "entitySetName"]:
                        val = params.get(key)
                        if val and isinstance(val, str):
                            trigger_entity = val.strip().lower()
                            break

                    if trigger_entity == target_entity:
                        msg_type = params.get("subscriptionRequest/message") or "Unknown"
                        # Filter attribute checks
                        filter_attributes = params.get("subscriptionRequest/filteringattributes")
                        filter_expression = params.get("subscriptionRequest/filterexpression")

                        matching_triggers.append({
                            "workflow_id": f.get("workflowid"),
                            "flow_name": f.get("name"),
                            "trigger_name": t_name,
                            "trigger_type": t_body.get("type"),
                            "event_message": msg_type,
                            "filtering_attributes": filter_attributes,
                            "filter_expression": filter_expression,
                        })
            except Exception:
                continue

        if message:
            m_clean = message.lower().strip()
            matching_triggers = [t for t in matching_triggers if m_clean in str(t.get("event_message")).lower()]

        return {
            "entity": target_entity,
            "total_matching_flows": len(matching_triggers),
            "triggers": matching_triggers,
        }

    def get_failed_flow_runs(
        self,
        environment: Optional[str] = None,
        time_window_hours: int = 24,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query recent failed flow session records from Dataverse flowsession / asyncoperation tables."""
        failed_jobs: List[Dict[str, Any]] = []

        # 1. Check flowsession table if available
        try:
            url = (
                "flowsessions?$filter=statuscode eq 4"
                "&$select=flowsessionid,name,createdon,completedon,errorcode,errormessage"
                "&$orderby=createdon desc&$top=30"
            )
            res = self._get(url, environment=environment, account=account)
            for item in res.get("value", []):
                failed_jobs.append({
                    "session_id": item.get("flowsessionid"),
                    "name": item.get("name"),
                    "created_on": item.get("createdon"),
                    "completed_on": item.get("completedon"),
                    "error_code": item.get("errorcode"),
                    "error_message": item.get("errormessage"),
                })
        except Exception:
            # 2. Fallback to failed system jobs (asyncoperation)
            try:
                url_async = (
                    "asyncoperations?$filter=statecode eq 3 and statuscode eq 31"
                    "&$select=asyncoperationid,name,operationtype,createdon,completedon,friendlymessage,message"
                    "&$orderby=completedon desc&$top=30"
                )
                res_a = self._get(url_async, environment=environment, account=account)
                for a in res_a.get("value", []):
                    failed_jobs.append({
                        "job_id": a.get("asyncoperationid"),
                        "name": a.get("name"),
                        "created_on": a.get("createdon"),
                        "completed_on": a.get("completedon"),
                        "error_message": a.get("friendlymessage") or a.get("message"),
                    })
            except Exception as exc:
                logger.warning("Failed querying system jobs fallback: %s", exc)

        return {
            "environment": self.auth.get_environment(environment).name,
            "time_window_hours": time_window_hours,
            "total_failures": len(failed_jobs),
            "failed_runs": failed_jobs,
        }

