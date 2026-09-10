from __future__ import annotations

import logging
import time
from urllib.parse import quote

import requests


class GraphClient:
    """Microsoft Graph v1.0 client. Requires a valid bearer token."""

    BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(self, method: str, endpoint: str,
                 json_data: dict = None, params: dict = None,
                 extra_headers: dict = None) -> requests.Response:
        url = f"{self.BASE_URL}{endpoint}"
        headers = {**self.headers, **(extra_headers or {})}
        retries = 3
        for attempt in range(retries):
            resp = requests.request(
                method, url, headers=headers, json=json_data, params=params, timeout=30
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            if resp.ok:
                return resp
            if attempt == retries - 1:
                resp.raise_for_status()
        return None  # unreachable but keeps type checkers happy

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_user_by_email(self, email: str) -> dict | None:
        email = email.strip()
        query = f"userPrincipalName eq '{email}' or mail eq '{email}'"
        endpoint = (
            f"/users?$filter={quote(query)}"
            "&$select=id,displayName,userPrincipalName,mail,accountEnabled"
        )
        try:
            resp = self._request("GET", endpoint)
            data = resp.json()
            return data["value"][0] if data.get("value") else None
        except Exception as exc:
            logging.error("get_user_by_email(%s): %s", email, exc)
            return None

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def get_group_by_name(self, group_name: str) -> dict | None:
        query = f"displayName eq '{group_name}'"
        endpoint = f"/groups?$filter={quote(query)}&$select=id,displayName"
        try:
            resp = self._request("GET", endpoint)
            data = resp.json()
            return data["value"][0] if data.get("value") else None
        except Exception as exc:
            logging.error("get_group_by_name(%s): %s", group_name, exc)
            return None

    def is_member_of(self, user_id: str, group_id: str) -> bool:
        endpoint = f"/groups/{group_id}/members?$filter=id eq '{user_id}'"
        try:
            resp = self._request(
                "GET", endpoint, extra_headers={"ConsistencyLevel": "eventual"}
            )
            return len(resp.json().get("value", [])) > 0
        except Exception:
            return False

    def add_user_to_group(self, user_id: str, group_id: str) -> bool:
        endpoint = f"/groups/{group_id}/members/$ref"
        payload = {
            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
        }
        try:
            self._request("POST", endpoint, json_data=payload)
            return True
        except Exception as exc:
            if "already exist" in str(exc).lower():
                return True
            logging.error("add_user_to_group(%s, %s): %s", user_id, group_id, exc)
            raise

    # ------------------------------------------------------------------
    # Licenses
    # ------------------------------------------------------------------

    def get_subscribed_skus(self) -> list[dict]:
        try:
            resp = self._request("GET", "/subscribedSkus")
            return resp.json().get("value", [])
        except Exception as exc:
            logging.error("get_subscribed_skus: %s", exc)
            return []

    def get_user_member_of(self, user_principal_name: str) -> list[dict]:
        endpoint = f"/users/{quote(user_principal_name)}/memberOf?$select=id,displayName,groupTypes,securityEnabled,mail"
        try:
            resp = self._request("GET", endpoint)
            return resp.json().get("value", [])
        except Exception as exc:
            logging.error("get_user_member_of(%s): %s", user_principal_name, exc)
            return []

    def get_user_licenses(self, user_principal_name: str) -> list[dict]:
        endpoint = f"/users/{quote(user_principal_name)}/licenseDetails"
        try:
            resp = self._request("GET", endpoint)
            return resp.json().get("value", [])
        except Exception as exc:
            logging.error("get_user_licenses(%s): %s", user_principal_name, exc)
            return []

