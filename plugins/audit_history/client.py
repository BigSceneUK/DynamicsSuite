from __future__ import annotations

import logging
import re
from typing import Optional

import requests


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
        self._user_cache: dict = {}
        self._workflow_cache: dict = {}
        self._attr_types: dict[str, dict[str, str]] = {}
        self.last_error: str | None = None

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
            res = requests.get(url, headers=self.headers, timeout=30)
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
            res = requests.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            mapping: dict[str, str] = {}
            types: dict[str, str] = {}
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
                types[logical] = attr.get("AttributeType") or "String"
            self._attr_types[entity_logical_name] = types
            return mapping
        except Exception as exc:
            logging.error("Failed to fetch attribute metadata: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Record search
    # ------------------------------------------------------------------

    def search_records(
        self,
        entity_set_name: str,
        filters: dict | None = None,
        top: int = 20,
        entity_logical_name: str | None = None,
    ) -> list[dict]:
        """Search records using contains for string fields, and eq for GUIDs/numbers."""
        self.last_error = None
        filter_parts = []
        select_fields = []

        attr_types = self._attr_types.get(entity_logical_name, {}) if entity_logical_name else {}
        uuid_pattern = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )

        if filters:
            for field, val in filters.items():
                if val is None or val == "":
                    continue

                field_type = attr_types.get(field, "")
                clean_val = str(val).strip("{} ")
                is_guid_val = bool(uuid_pattern.match(clean_val))

                is_guid_field = (
                    field_type == "Uniqueidentifier"
                    or (not field_type and (field.lower().endswith("id") or (field.startswith("_") and field.endswith("_value"))))
                )

                if is_guid_field:
                    if is_guid_val:
                        filter_parts.append(f"{field} eq {clean_val}")
                    else:
                        filter_parts.append(f"{field} eq '{clean_val}'")
                elif field_type in ("Integer", "BigInt", "Decimal", "Double", "Boolean") or isinstance(val, (int, float, bool)):
                    filter_parts.append(f"{field} eq {str(val).lower()}")
                else:
                    safe_val = str(val).replace("'", "''")
                    filter_parts.append(f"contains({field}, '{safe_val}')")

                select_fields.append(field)

        query = f"?$top={top}"
        if filter_parts:
            query += f"&$filter={' and '.join(filter_parts)}"
        if select_fields:
            query += f"&$select={','.join(list(set(select_fields)))}"

        url = f"{self.org_url}/api/data/v9.2/{entity_set_name}{query}"
        try:
            res = requests.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            return res.json().get("value", [])
        except Exception as exc:
            err_msg = str(exc)
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    err_json = exc.response.json()
                    err_msg = err_json.get("error", {}).get("message") or err_msg
                except Exception:
                    err_msg = exc.response.text or err_msg
            self.last_error = err_msg
            logging.error("Search failed for %s: %s", entity_set_name, err_msg)
            return []

    # ------------------------------------------------------------------
    # Audit history
    # ------------------------------------------------------------------

    def get_audit_history(self, entity_set_name: str, record_id: str) -> list[dict]:
        """
        Retrieve record-level audit history via RetrieveRecordChangeHistory.
        Returns a list of AuditDetail objects.
        """
        target = f"{self.org_url}/api/data/v9.2/{entity_set_name}({record_id})"
        url = (
            f"{self.org_url}/api/data/v9.2/RetrieveRecordChangeHistory(Target=@Target)"
            f"?@Target={{\"@odata.id\":\"{target}\"}}"
        )
        try:
            res = requests.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            data = res.json()
            details = data.get("AuditDetailCollection", {}).get("AuditDetails", [])
            if not details and "value" in data:
                details = data["value"]
            return details
        except Exception as exc:
            logging.error("Failed to fetch audit history: %s", exc)
            raise
