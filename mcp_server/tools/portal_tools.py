from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import requests
from mcp_server.auth import DynamicsAuthProvider

logger = logging.getLogger("mcp_portal_tools")


class PortalTools:
    """Power Pages and Dynamics 365 Portals security and hierarchy inspection tools."""

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

    def audit_portal_webapi(
        self,
        website_id: Optional[str] = None,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Audit active Power Pages Web API site settings and exposed table fields."""
        prefix = "adx"
        # Check if mspp or adx
        try:
            self._get("adx_websites?$top=1", environment=environment, account=account)
        except Exception:
            prefix = "mspp"

        # Query site settings for WebAPI
        filter_expr = f"startswith(name, 'WebAPI/')"
        if website_id:
            filter_expr += f" and _mspp_websiteid_value eq {website_id}" if prefix == "mspp" else f" and _adx_websiteid_value eq {website_id}"

        url = f"{prefix}_sitesettings?$filter={filter_expr}&$select={prefix}_sitesettingid,name,value"
        res = self._get(url, environment=environment, account=account)
        settings = res.get("value", [])

        webapi_enabled_tables: Dict[str, Dict[str, Any]] = {}
        for s in settings:
            s_name = s.get("name", "")
            s_val = s.get("value", "")
            parts = s_name.split("/")
            if len(parts) >= 2:
                table = parts[1].lower()
                if table not in webapi_enabled_tables:
                    webapi_enabled_tables[table] = {"enabled": False, "fields": None, "raw_settings": []}
                webapi_enabled_tables[table]["raw_settings"].append({"setting": s_name, "value": s_val})

                if len(parts) == 3:
                    sub = parts[2].lower()
                    if sub == "enabled":
                        webapi_enabled_tables[table]["enabled"] = s_val.lower() == "true"
                    elif sub == "fields":
                        webapi_enabled_tables[table]["fields"] = [f.strip() for f in s_val.split(",") if f.strip()]

        # Check for wildcard exposure risk
        warnings = []
        for tbl, data in webapi_enabled_tables.items():
            if data["enabled"]:
                if data["fields"] == ["*"] or (data["fields"] and "*" in data["fields"]):
                    warnings.append(f"CRITICAL: Table '{tbl}' has Web API enabled with wildcard '*' fields exposure!")
                elif not data["fields"]:
                    warnings.append(f"WARNING: Table '{tbl}' is enabled for Web API but has no fields configured.")

        return {
            "environment": self.auth.get_environment(environment).name,
            "schema_model": "Enhanced (mspp)" if prefix == "mspp" else "Standard (adx)",
            "total_webapi_tables": len(webapi_enabled_tables),
            "tables": webapi_enabled_tables,
            "security_warnings": warnings,
        }

    def get_portal_page_hierarchy(
        self,
        webpage_id_or_url: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect the component hierarchy (Page -> Template -> Forms/Lists) of a Power Pages web page."""
        target = webpage_id_or_url.strip()
        prefix = "adx"
        try:
            self._get("adx_websites?$top=1", environment=environment, account=account)
        except Exception:
            prefix = "mspp"

        # Find webpage
        if len(target) == 36 and "-" in target:
            page_query = f"{prefix}_webpages({target})"
            page_data = self._get(page_query, environment=environment, account=account)
        else:
            escaped = target.replace("'", "''")
            page_query = f"{prefix}_webpages?$filter={prefix}_partialurl eq '{escaped}' or name eq '{escaped}'&$top=1"
            res = self._get(page_query, environment=environment, account=account)
            items = res.get("value", [])
            if not items:
                return {"error": f"Webpage '{target}' not found."}
            page_data = items[0]

        page_id = page_data.get(f"{prefix}_webpageid")
        page_name = page_data.get("name")
        partial_url = page_data.get(f"{prefix}_partialurl")

        # Find page template
        template_id = page_data.get(f"_{prefix}_pagetemplateid_value")
        template_name = page_data.get(f"_{prefix}_pagetemplateid_value@OData.Community.Display.V1.FormattedValue")

        # Find child forms or entity lists associated
        child_forms = []
        try:
            forms_query = f"{prefix}_entityforms?$filter=_{prefix}_websiteid_value eq {page_data.get(f'_{prefix}_websiteid_value')} and statecode eq 0"
            f_res = self._get(forms_query, environment=environment, account=account)
            for f in f_res.get("value", []):
                child_forms.append({
                    "form_id": f.get(f"{prefix}_entityformid"),
                    "name": f.get("name"),
                    "entity_name": f.get(f"{prefix}_entityname"),
                    "mode": f.get(f"{prefix}_mode@OData.Community.Display.V1.FormattedValue") or f.get(f"{prefix}_mode"),
                })
        except Exception:
            pass

        return {
            "page_id": page_id,
            "name": page_name,
            "partial_url": partial_url,
            "template": {
                "id": template_id,
                "name": template_name,
            },
            "available_entity_forms": child_forms,
        }

