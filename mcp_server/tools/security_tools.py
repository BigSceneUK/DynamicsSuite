from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, List, Optional
import requests
from mcp_server.auth import DynamicsAuthProvider

logger = logging.getLogger("mcp_security_tools")


class SecurityTools:
    """Security role, privilege, and access inspection tools."""

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
        res = requests.get(url, headers=self._headers(environment, account=account), params=params, timeout=35)
        res.raise_for_status()
        return res.json()

    def _resolve_user(self, user_email_or_id: str, environment: Optional[str] = None, account: Optional[str] = None) -> Dict[str, Any]:
        target = user_email_or_id.strip()
        if len(target) == 36 and "-" in target:
            res = self._get(
                f"systemusers({target})?$select=systemuserid,fullname,internalemailaddress,domainname,isdisabled",
                environment=environment,
                account=account,
            )
            return res
        else:
            escaped = target.replace("'", "''")
            query = (
                f"systemusers?$filter=(internalemailaddress eq '{escaped}' or domainname eq '{escaped}' or fullname eq '{escaped}')"
                "&$select=systemuserid,fullname,internalemailaddress,domainname,isdisabled&$top=1"
            )
            res = self._get(query, environment=environment, account=account)
            items = res.get("value", [])
            if not items:
                raise ValueError(f"User '{user_email_or_id}' was not found in environment.")
            return items[0]

    def list_user_roles(
        self,
        user_email_or_id: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch all direct security roles and team-inherited roles for a user."""
        user = self._resolve_user(user_email_or_id, environment=environment, account=account)
        user_id = user["systemuserid"]

        direct_roles: List[Dict[str, Any]] = []
        team_roles: List[Dict[str, Any]] = []
        teams: List[Dict[str, Any]] = []

        # 1. Direct roles
        try:
            url_roles = f"systemusers({user_id})/systemuserroles_association?$select=roleid,name&$expand=businessunitid($select=name)"
            res_r = self._get(url_roles, environment=environment, account=account)
            for r in res_r.get("value", []):
                bu = r.get("businessunitid", {}).get("name", "Unknown") if isinstance(r.get("businessunitid"), dict) else "Unknown"
                direct_roles.append({"role_id": r.get("roleid"), "role_name": r.get("name"), "business_unit": bu})
        except Exception as exc:
            logger.warning("Could not fetch direct roles: %s", exc)

        # 2. Teams & Team-inherited roles
        try:
            url_teams = f"systemusers({user_id})/teammembership_association?$select=teamid,name"
            res_t = self._get(url_teams, environment=environment, account=account)
            for t in res_t.get("value", []):
                t_id = t.get("teamid")
                t_name = t.get("name")
                teams.append({"team_id": t_id, "team_name": t_name})

                url_tr = f"teams({t_id})/teamroles_association?$select=roleid,name&$expand=businessunitid($select=name)"
                try:
                    res_tr = self._get(url_tr, environment=environment, account=account)
                    for r in res_tr.get("value", []):
                        bu = r.get("businessunitid", {}).get("name", "Unknown") if isinstance(r.get("businessunitid"), dict) else "Unknown"
                        team_roles.append({
                            "role_id": r.get("roleid"),
                            "role_name": r.get("name"),
                            "team_name": t_name,
                            "business_unit": bu,
                        })
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Could not fetch team roles: %s", exc)

        all_role_names = sorted(list({r["role_name"] for r in direct_roles + team_roles if r.get("role_name")}))

        return {
            "user_id": user_id,
            "fullname": user.get("fullname"),
            "email": user.get("internalemailaddress") or user.get("domainname"),
            "is_disabled": user.get("isdisabled", False),
            "total_direct_roles": len(direct_roles),
            "direct_roles": direct_roles,
            "total_teams": len(teams),
            "teams": teams,
            "total_team_roles": len(team_roles),
            "team_roles": team_roles,
            "all_distinct_role_names": all_role_names,
        }

    def check_user_access(
        self,
        user_email_or_id: str,
        entity_logical_name: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check effective entity permissions and assigned roles for a user on a given table."""
        roles_info = self.list_user_roles(user_email_or_id, environment=environment, account=account)
        user_id = roles_info["user_id"]
        entity = entity_logical_name.strip().lower()

        # Check if System Admin
        all_roles_lower = {r.lower() for r in roles_info["all_distinct_role_names"]}
        is_admin = "system administrator" in all_roles_lower

        # Fetch user effective privileges via Dataverse Function
        effective_privileges: List[Dict[str, Any]] = []
        try:
            url_privs = f"systemusers({user_id})/Microsoft.Dynamics.CRM.RetrieveUserPrivileges"
            res = self._get(url_privs, environment=environment, account=account)
            role_privs = res.get("RolePrivileges", [])

            # Get privilege metadata for this entity
            priv_name_prefix = f"prv{entity}"
            query_priv_meta = f"privileges?$filter=startswith(name, 'prvCreate{entity}') or startswith(name, 'prvRead{entity}') or startswith(name, 'prvWrite{entity}') or startswith(name, 'prvDelete{entity}') or startswith(name, 'prvAppend{entity}') or startswith(name, 'prvAssign{entity}') or startswith(name, 'prvShare{entity}')"
            priv_meta_res = self._get(query_priv_meta, environment=environment, account=account)
            meta_map = {p["privilegeid"]: p["name"] for p in priv_meta_res.get("value", [])}

            depth_names = {0: "None", 1: "User (Basic)", 2: "Business Unit (Local)", 4: "Parent:Child Business Units (Deep)", 8: "Organization (Global)"}

            for rp in role_privs:
                pid = rp.get("PrivilegeId")
                if pid in meta_map:
                    depth = rp.get("PrivilegeDepthMask", 0)
                    effective_privileges.append({
                        "privilege_name": meta_map[pid],
                        "depth_level": depth_names.get(depth, f"Level {depth}"),
                    })
        except Exception as exc:
            logger.warning("Could not calculate exact privilege depths: %s", exc)

        return {
            "user": roles_info["fullname"],
            "email": roles_info["email"],
            "entity": entity,
            "is_system_administrator": is_admin,
            "assigned_roles": roles_info["all_distinct_role_names"],
            "has_system_administrator_bypass": is_admin,
            "effective_entity_privileges": effective_privileges if not is_admin else [
                {"privilege_name": f"prvAll{entity}", "depth_level": "Organization (Global) - System Admin"}
            ],
        }

