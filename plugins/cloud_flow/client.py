from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _session_with_token(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    return s


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[int, str] = {
    1: "Running",
    2: "Running",
    3: "Running",
    4: "Succeeded",
    5: "Failed",
    6: "Cancelled",
}

_STATUS_ICON: dict[str, str] = {
    "Succeeded": "✅",
    "Failed": "❌",
    "Cancelled": "⚠️",
    "Running": "🔄",
}


def _map_status(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _STATUS_MAP.get(code, str(code))


# ---------------------------------------------------------------------------
# Dataverse client
# ---------------------------------------------------------------------------


class DataverseClient:
    """Dataverse REST client for Cloud Flow execution history."""

    def __init__(self, org_url: str, token: str) -> None:
        self.org_url = org_url.rstrip("/")
        self._session = _session_with_token(token)
        self._session.headers.update(
            {
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
                "Prefer": 'odata.include-annotations="*"',
            }
        )

    # ------------------------------------------------------------------
    # Flow discovery
    # ------------------------------------------------------------------

    def get_modern_flows(self) -> list[dict]:
        """Return all solution-aware Cloud Flows (workflow category 5)."""
        params = (
            "?$filter=category eq 5"
            "&$select=workflowid,name,statecode,modifiedon,description"
            "&$orderby=name asc"
        )
        next_url: str | None = f"{self.org_url}/api/data/v9.2/workflows{params}"
        all_flows: list[dict] = []
        try:
            while next_url:
                res = self._session.get(next_url, timeout=30)
                res.raise_for_status()
                data = res.json()
                all_flows.extend(data.get("value", []))
                next_url = data.get("@odata.nextLink")
            return all_flows
        except Exception as exc:
            logging.error("get_modern_flows failed: %s", exc)
            raise

    def get_instance_id(self) -> str | None:
        """Return the Dataverse organization ID (matches PA environment name)."""
        try:
            res = self._session.get(
                f"{self.org_url}/api/data/v9.2/organizations?$select=organizationid",
                timeout=30,
            )
            res.raise_for_status()
            val = res.json().get("value", [])
            return val[0]["organizationid"] if val else None
        except Exception as exc:
            logging.warning("get_instance_id failed: %s", exc)
            return None

    def get_solutions(self) -> list[dict]:
        """Return all visible solutions (for display/grouping)."""
        params = (
            "?$select=friendlyname,uniquename,solutionid"
            "&$filter=isvisible eq true"
            "&$orderby=friendlyname asc"
        )
        url = f"{self.org_url}/api/data/v9.2/solutions{params}"
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.error("get_solutions failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Run history — per flow
    # ------------------------------------------------------------------

    def get_flow_runs(self, workflow_id: str, top: int = 50) -> list[dict]:
        """
        Fetch the latest runs for a single flow.

        Tries the ``flowruns`` navigation property first (supported in some
        environments), then falls back to querying the ``flowsessions`` table.
        """
        url = (
            f"{self.org_url}/api/data/v9.2/workflows({workflow_id})/flowruns"
            f"?$select=flowrunid,starttime,endtime,status,durationinmilliseconds,triggername"
            f"&$orderby=starttime desc&$top={top}"
        )
        try:
            res = self._session.get(url, timeout=30)
            if res.status_code == 404:
                logging.info("flowruns navigation not available — falling back to flowsessions")
                return self._get_flow_sessions(workflow_id, top)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.error("get_flow_runs(%s) failed: %s", workflow_id, exc)
            raise

    def _get_flow_sessions(self, workflow_id: str, top: int = 50) -> list[dict]:
        """Fallback: query ``flowsessions`` table filtered by workflow id."""
        params = (
            f"?$filter=_regardingobjectid_value eq '{workflow_id}'"
            f"&$select=flowsessionid,startedon,completedon,statuscode"
            f"&$orderby=startedon desc&$top={top}"
        )
        url = f"{self.org_url}/api/data/v9.2/flowsessions{params}"
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            rows = res.json().get("value", [])
            return [
                {
                    "flowrunid": r.get("flowsessionid"),
                    "starttime": r.get("startedon"),
                    "endtime": r.get("completedon"),
                    "status": _map_status(r.get("statuscode")),
                    "durationinmilliseconds": None,
                    "triggername": None,
                }
                for r in rows
            ]
        except Exception as exc:
            logging.error("_get_flow_sessions(%s) fallback failed: %s", workflow_id, exc)
            raise

    # ------------------------------------------------------------------
    # Run history — date range across all flows
    # ------------------------------------------------------------------

    def get_sessions_by_date(
        self, start_dt: str, end_dt: str, top: int = 500
    ) -> list[dict]:
        """
        Fetch flow sessions between *start_dt* and *end_dt* (ISO strings).
        Returns a flat list where each row includes ``workflowid``.
        """
        params = (
            f"?$filter=startedon ge '{start_dt}' and startedon le '{end_dt}'"
            f"&$select=flowsessionid,startedon,completedon,statuscode,_regardingobjectid_value"
            f"&$orderby=startedon desc&$top={top}"
        )
        url = f"{self.org_url}/api/data/v9.2/flowsessions{params}"
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            rows = res.json().get("value", [])
            return [
                {
                    "flowrunid": r.get("flowsessionid"),
                    "starttime": r.get("startedon"),
                    "endtime": r.get("completedon"),
                    "status": _map_status(r.get("statuscode")),
                    "workflowid": r.get("_regardingobjectid_value"),
                    "durationinmilliseconds": None,
                }
                for r in rows
            ]
        except Exception as exc:
            logging.error("get_sessions_by_date failed: %s", exc)
            raise

    def get_failed_flow_runs(self, start_dt: str, end_dt: str) -> list[dict]:
        """
        High-speed search using the modern ``flowruns`` elastic table (GA Nov 2024).
        Returns [] silently if the table is unavailable in this environment.
        """
        params = (
            f"?$filter=status ne 'Succeeded' and starttime ge '{start_dt}' and starttime le '{end_dt}'"
            "&$select=flowrunid,starttime,endtime,status,flowname,flowid,errormessage,errorcode,duration,triggertype"
            "&$orderby=starttime desc"
        )
        url = f"{self.org_url}/api/data/v9.2/flowruns{params}"
        try:
            res = self._session.get(url, timeout=30)
            if res.status_code == 404:
                logging.info("flowruns elastic table not available in this environment")
                return []
            res.raise_for_status()
            rows = res.json().get("value", [])
            return [
                {
                    "flowrunid": r.get("flowrunid"),
                    "starttime": r.get("starttime"),
                    "endtime": r.get("endtime"),
                    "status": r.get("status"),
                    "workflowid": r.get("flowid"),
                    "Flow Name": r.get("flowname"),
                    "durationinmilliseconds": None,
                    "Error Message": r.get("errormessage"),
                }
                for r in rows
            ]
        except Exception as exc:
            logging.error("get_failed_flow_runs failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Power Automate API client (optional — requires flow token)
# ---------------------------------------------------------------------------

_PA_BASE = "https://api.flow.microsoft.com"
_PA_SCOPE = "https://service.flow.microsoft.com//.default"


class PowerAutomateClient:
    """Light wrapper around the Power Automate management REST API."""

    def __init__(self, token: str) -> None:
        self._session = _session_with_token(token)

    def get_environments(self) -> list[dict]:
        url = f"{_PA_BASE}/providers/Microsoft.ProcessSimple/environments?api-version=2016-11-01"
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.error("PA get_environments failed: %s", exc)
            return []

    def get_flow_runs(
        self,
        environment_id: str,
        flow_id: str,
        top: int = 50,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        base = (
            f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
            f"/environments/{environment_id}/flows/{flow_id}/runs?api-version=2016-11-01"
        )

        filters: list[str] = []
        if status:
            filters.append(f"status eq '{status}'")
        if start_date and end_date:
            # Server-side date filter: ask the PA API to return only runs inside
            # the requested window. This avoids walking backwards through every
            # recent run when searching deep history for high-frequency flows.
            filters.append(f"startTime ge {start_date} and startTime le {end_date}")

        if filters:
            base += "&$filter=" + " and ".join(filters).replace(" ", "%20")

        if not start_date or not end_date:
            url = f"{base}&$top={top}"
            try:
                res = self._session.get(url, timeout=30)
                res.raise_for_status()
                return res.json().get("value", [])
            except Exception:
                return []

        import pandas as pd

        accumulated: list[dict] = []
        next_link: str | None = f"{base}&$top=100"
        target_start = pd.to_datetime(start_date)
        page_count = 0

        # Allow up to 100 pages so dense histories (e.g. every 10 minutes for
        # multiple weeks) can be retrieved within a narrow date window.
        while next_link and page_count < 100:
            try:
                res = self._session.get(next_link, timeout=30)
                res.raise_for_status()
                data = res.json()
                page_runs = data.get("value", [])
                if not page_runs:
                    break
                accumulated.extend(page_runs)
                last_time_str = page_runs[-1].get("properties", {}).get("startTime")
                if last_time_str and pd.to_datetime(last_time_str) < target_start:
                    break
                next_link = data.get("nextLink")
                page_count += 1
            except Exception:
                break

        return accumulated

    def get_flow_run_details(
        self, environment_id: str, flow_id: str, run_id: str
    ) -> dict:
        """Return the full PA run metadata for a single run."""
        url = (
            f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
            f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}"
            "?api-version=2016-11-01"
        )
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            return res.json()
        except Exception as exc:
            logging.error("get_flow_run_details failed (%s): %s", run_id, exc)
            return {}

    def get_flow_run_actions(
        self, environment_id: str, flow_id: str, run_id: str
    ) -> list[dict]:
        """Return the list of action records for a single run."""
        url = (
            f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
            f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}/actions"
            "?api-version=2016-11-01"
        )
        try:
            res = self._session.get(url, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            logging.error("get_flow_run_actions failed (%s): %s", run_id, exc)
            return []

    def get_content_from_link(self, url: str) -> any:
        """Follow a SAS/content link without auth headers (pre-signed URL)."""
        import requests as _requests

        try:
            res = _requests.get(url, timeout=30)
            res.raise_for_status()
            try:
                return res.json()
            except Exception:
                return res.text
        except Exception as exc:
            logging.warning("get_content_from_link failed: %s", exc)
            return None

    def get_action_repetitions(
        self,
        environment_id: str,
        flow_id: str,
        run_id: str,
        action_name: str,
        max_items: int = 500,
    ) -> list[dict]:
        """Fetch loop iterations (scopeRepetitions / repetitions) for a loop action."""
        import urllib.parse

        name_candidates = [
            urllib.parse.quote(action_name, safe=""),
            urllib.parse.quote(action_name.replace(" ", "_"), safe=""),
            urllib.parse.quote(action_name.replace("_", " "), safe=""),
            action_name,
        ]
        unique_names = list(dict.fromkeys(name_candidates))

        for act_name in unique_names:
            urls = [
                (
                    f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
                    f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}"
                    f"/actions/{act_name}/scopeRepetitions?api-version=2016-11-01&$top=100"
                ),
                (
                    f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
                    f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}"
                    f"/actions/{act_name}/repetitions?api-version=2016-11-01&$top=100"
                ),
            ]
            for base_url in urls:
                try:
                    next_link: str | None = base_url
                    accumulated: list[dict] = []
                    while next_link and len(accumulated) < max_items:
                        res = self._session.get(next_link, timeout=30)
                        if res.status_code in (400, 404):
                            break
                        res.raise_for_status()
                        data = res.json()
                        page_items = data.get("value", [])
                        if not page_items:
                            break
                        accumulated.extend(page_items)
                        next_link = data.get("nextLink") or data.get("@odata.nextLink")
                    if accumulated:
                        return accumulated
                except Exception as exc:
                    logging.debug("get_action_repetitions attempt on %s failed: %s", base_url, exc)
        return []

    def get_scope_repetition_actions(
        self,
        environment_id: str,
        flow_id: str,
        run_id: str,
        action_name: str,
        repetition_name: str,
    ) -> list[dict]:
        """Fetch child action execution records inside a specific loop iteration."""
        import urllib.parse

        name_candidates = [
            urllib.parse.quote(action_name, safe=""),
            urllib.parse.quote(action_name.replace(" ", "_"), safe=""),
            urllib.parse.quote(action_name.replace("_", " "), safe=""),
            action_name,
        ]
        unique_names = list(dict.fromkeys(name_candidates))

        for act_name in unique_names:
            urls = [
                (
                    f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
                    f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}"
                    f"/actions/{act_name}/scopeRepetitions/{repetition_name}/actions?api-version=2016-11-01"
                ),
                (
                    f"{_PA_BASE}/providers/Microsoft.ProcessSimple"
                    f"/environments/{environment_id}/flows/{flow_id}/runs/{run_id}"
                    f"/actions/{act_name}/repetitions/{repetition_name}/actions?api-version=2016-11-01"
                ),
            ]
            for url in urls:
                try:
                    res = self._session.get(url, timeout=30)
                    if res.status_code in (400, 404):
                        continue
                    res.raise_for_status()
                    return res.json().get("value", [])
                except Exception:
                    continue
        return []

    def get_flow_run_full_dump(
        self,
        environment_id: str,
        flow_id: str,
        run_id: str,
        include_loop_repetitions: bool = True,
        max_repetitions: int = 500,
        progress_callback=None,
    ) -> dict:
        """
        Assemble a comprehensive execution dump for a single flow run.

        Includes run metadata, trigger inputs/outputs, all action inputs/outputs
        (dereferencing pre-signed SAS blob URLs), and loop repetitions for Foreach actions.
        """
        dump: dict = {
            "environment_id": environment_id,
            "flow_id": flow_id,
            "run_id": run_id,
            "run_details": {},
            "trigger": {},
            "actions": [],
            "loops": {},
        }

        # 1. Flow run details
        if progress_callback:
            progress_callback(0.1, "Fetching run details…")
        run_details = self.get_flow_run_details(environment_id, flow_id, run_id)
        dump["run_details"] = run_details

        props = run_details.get("properties", {})
        trigger = props.get("trigger", {})
        resolved_trigger = dict(trigger)

        # Resolve trigger payload links if present
        t_in_link = trigger.get("inputsLink", {}).get("uri")
        if t_in_link:
            resolved_trigger["inputs_resolved"] = self.get_content_from_link(t_in_link)
        t_out_link = trigger.get("outputsLink", {}).get("uri")
        if t_out_link:
            resolved_trigger["outputs_resolved"] = self.get_content_from_link(t_out_link)
        dump["trigger"] = resolved_trigger

        # 2. Flow actions
        if progress_callback:
            progress_callback(0.2, "Fetching action execution records…")
        actions = self.get_flow_run_actions(environment_id, flow_id, run_id)

        resolved_actions: list[dict] = []
        loop_actions: list[dict] = []

        total_actions = len(actions) or 1
        for idx, action in enumerate(actions):
            a_copy = dict(action)
            a_props = a_copy.get("properties", {})

            # Dereference inputsLink / outputsLink
            in_link = a_props.get("inputsLink", {}).get("uri")
            if in_link:
                a_copy["inputs_resolved"] = self.get_content_from_link(in_link)
            out_link = a_props.get("outputsLink", {}).get("uri")
            if out_link:
                a_copy["outputs_resolved"] = self.get_content_from_link(out_link)

            a_type = str(a_copy.get("type", "")).lower()
            a_name = a_copy.get("name", "")
            props_type = str(a_props.get("type", "")).lower()
            props_action_type = str(a_props.get("actionType", "")).lower()
            norm_name = a_name.lower().replace(" ", "_")

            # Check if this action is a loop (Foreach, Until, Scope, Apply_to_each, etc.)
            is_loop = (
                any(t in a_type for t in ("foreach", "until"))
                or any(t in props_type for t in ("foreach", "until", "scope"))
                or any(t in props_action_type for t in ("foreach", "until", "scope"))
                or any(k in a_props for k in ("repetitionCount", "iterationCount", "repetitionIndexes"))
                or any(w in norm_name for w in ("apply_to_each", "foreach", "until", "loop"))
                or "iterationcount" in str(a_props).lower()
                or "repetitioncount" in str(a_props).lower()
            )
            if is_loop:
                loop_actions.append(a_copy)

            resolved_actions.append(a_copy)

            if progress_callback and not loop_actions:
                progress_callback(0.2 + (0.4 * (idx + 1) / total_actions), f"Processing action: {a_name}")

        dump["actions"] = resolved_actions

        # 3. Loop repetitions
        if include_loop_repetitions and loop_actions:
            loops_dict: dict[str, list[dict]] = {}
            for l_idx, l_action in enumerate(loop_actions):
                l_name = l_action.get("name", "")
                if progress_callback:
                    progress_callback(
                        0.6 + (0.35 * l_idx / len(loop_actions)),
                        f"Fetching loop iterations for '{l_name}'…",
                    )
                repetitions = self.get_action_repetitions(
                    environment_id, flow_id, run_id, l_name, max_items=max_repetitions
                )
                resolved_reps: list[dict] = []
                total_reps = len(repetitions)
                for r_idx, rep in enumerate(repetitions):
                    rep_copy = dict(rep)
                    rep_name = rep.get("name", str(r_idx))
                    r_props = rep.get("properties", {})

                    rin_link = r_props.get("inputsLink", {}).get("uri")
                    if rin_link:
                        rep_copy["inputs_resolved"] = self.get_content_from_link(rin_link)
                    rout_link = r_props.get("outputsLink", {}).get("uri")
                    if rout_link:
                        rep_copy["outputs_resolved"] = self.get_content_from_link(rout_link)

                    # Fetch actions executed inside this repetition (e.g. Get Choices for Application)
                    child_actions = self.get_scope_repetition_actions(
                        environment_id, flow_id, run_id, l_name, rep_name
                    )
                    resolved_child_actions: list[dict] = []
                    for ca in child_actions:
                        ca_copy = dict(ca)
                        ca_props = ca_copy.get("properties", {})
                        ca_in = ca_props.get("inputsLink", {}).get("uri")
                        if ca_in:
                            ca_copy["inputs_resolved"] = self.get_content_from_link(ca_in)
                        ca_out = ca_props.get("outputsLink", {}).get("uri")
                        if ca_out:
                            ca_copy["outputs_resolved"] = self.get_content_from_link(ca_out)
                        resolved_child_actions.append(ca_copy)

                    rep_copy["child_actions"] = resolved_child_actions
                    resolved_reps.append(rep_copy)

                    if progress_callback and total_reps > 0 and (r_idx % 20 == 0 or r_idx == total_reps - 1):
                        progress_callback(
                            0.6 + (0.35 * (r_idx + 1) / total_reps),
                            f"Fetching iteration {r_idx + 1}/{total_reps} for '{l_name}'…",
                        )

                loops_dict[l_name] = resolved_reps
            dump["loops"] = loops_dict

        if progress_callback:
            progress_callback(1.0, "Done!")

        return dump
