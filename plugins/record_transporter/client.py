import logging
import urllib.parse
import requests
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

class RecordTransporterClient:
    def __init__(self, org_url: str, token: str):
        self.org_url = org_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }
        self.api_url = f"{self.org_url}/api/data/v9.2"

    def _get_label(self, metadata_item: Dict[str, Any], default: str) -> str:
        """Safely extract the display name label from a metadata object."""
        display_name = metadata_item.get("DisplayName")
        if not display_name or not isinstance(display_name, dict):
            return default
        
        user_label = display_name.get("UserLocalizedLabel")
        if not user_label or not isinstance(user_label, dict):
            localized_labels = display_name.get("LocalizedLabels")
            if localized_labels and isinstance(localized_labels, list) and len(localized_labels) > 0:
                return localized_labels[0].get("Label", default)
            return default
            
        return user_label.get("Label", default)

    def get_entities(self) -> List[Dict[str, Any]]:
        """Fetch all entities with their logical and display names."""
        url = f"{self.api_url}/EntityDefinitions?$select=LogicalName,DisplayName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute"
        res = requests.get(url, headers=self.headers, timeout=60)
        res.raise_for_status()
        entities = res.json().get("value", [])
        
        processed = []
        for e in entities:
            display_name = self._get_label(e, e.get("LogicalName", "Unknown"))
            processed.append({
                "LogicalName": e["LogicalName"],
                "DisplayName": display_name,
                "EntitySetName": e["EntitySetName"],
                "PrimaryIdAttribute": e["PrimaryIdAttribute"],
                "PrimaryNameAttribute": e.get("PrimaryNameAttribute") or ""
            })
        return sorted(processed, key=lambda x: x["DisplayName"])

    def get_attributes(self, logical_name: str) -> List[Dict[str, Any]]:
        """Fetch attributes for a specific entity."""
        url = (
            f"{self.api_url}/EntityDefinitions(LogicalName='{logical_name}')/Attributes"
            "?$select=LogicalName,DisplayName,AttributeType,IsManaged"
        )
        res = requests.get(url, headers=self.headers, timeout=60)
        res.raise_for_status()
        attributes = res.json().get("value", [])
        
        processed = []
        for a in attributes:
            display_name = self._get_label(a, a.get("LogicalName", "Unknown"))
            processed.append({
                "LogicalName": a["LogicalName"],
                "DisplayName": display_name,
                "AttributeType": a["AttributeType"],
                "IsManaged": a["IsManaged"]
            })
        return sorted(processed, key=lambda x: x["DisplayName"])

    def get_lookup_metadata(self, entity_logical_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Fetch lookup attributes metadata for an entity using ManyToOneRelationships.
        Returns a mapping of logical_name -> {
            'Targets': [...], 
            'NavigationProperties': {target_entity: navigation_property_name}
        }
        """
        url = (
            f"{self.api_url}/EntityDefinitions(LogicalName='{entity_logical_name}')/ManyToOneRelationships"
            "?$select=ReferencingAttribute,ReferencedEntity,ReferencingEntityNavigationPropertyName"
        )
        try:
            res = requests.get(url, headers=self.headers, timeout=30)
            res.raise_for_status()
            items = res.json().get("value", [])
            mapping = {}
            for item in items:
                ref_attr = item.get("ReferencingAttribute")
                ref_ent = item.get("ReferencedEntity")
                nav_prop = item.get("ReferencingEntityNavigationPropertyName")
                
                if ref_attr and ref_ent and nav_prop:
                    if ref_attr not in mapping:
                        mapping[ref_attr] = {
                            "Targets": [],
                            "NavigationProperties": {}
                        }
                    if ref_ent not in mapping[ref_attr]["Targets"]:
                        mapping[ref_attr]["Targets"].append(ref_ent)
                    mapping[ref_attr]["NavigationProperties"][ref_ent] = nav_prop
            return mapping
        except Exception as e:
            logging.error(f"Error fetching lookup metadata for {entity_logical_name}: {e}")
            return {}

    def get_environments(self, bap_token: str) -> list[dict]:
        """List accessible Power Platform environments using the BAP API."""
        url = "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments?api-version=2021-04-01"
        headers = {
            "Authorization": f"Bearer {bap_token}",
            "Content-Type": "application/json"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.ok:
                return resp.json().get("value", [])
            # Try admin scope fallback
            url_admin = "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/scopes/admin/environments?api-version=2021-04-01"
            resp_admin = requests.get(url_admin, headers=headers, timeout=20)
            if resp_admin.ok:
                return resp_admin.json().get("value", [])
            return []
        except Exception:
            return []

    def fetch_records(self, entity_logical_name: str, entity_set_name: str, select_attrs: List[str], fetch_xml: Optional[str] = None, top: int = 50) -> List[Dict[str, Any]]:
        """Retrieve records from the environment using FetchXML or a top-N select query."""
        if fetch_xml:
            encoded = urllib.parse.quote(fetch_xml)
            url = f"{self.api_url}/{entity_set_name}?fetchXml={encoded}"
        else:
            pk = f"{entity_logical_name}id"
            # Ensure primary key is selected
            attrs = list(set([pk] + select_attrs))
            select_str = ",".join(attrs)
            url = f"{self.api_url}/{entity_set_name}?$select={select_str}&$top={top}"
            
        res = requests.get(url, headers=self.headers, timeout=60)
        res.raise_for_status()
        return res.json().get("value", [])

    def check_record_exists(self, entity_set_name: str, record_id: str) -> bool:
        """Query if a record with given GUID exists in target environment."""
        url = f"{self.api_url}/{entity_set_name}({record_id})"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code == 200:
                return True
            return False
        except Exception:
            return False

    def check_records_exist_in_target(self, records_to_check: List[Tuple[str, str]]) -> Dict[Tuple[str, str], bool]:
        """
        Check if multiple records exist in the target environment concurrently.
        records_to_check: list of (entity_set_name, record_id)
        Returns mapping of (entity_set_name, record_id) -> exists (bool)
        """
        results = {}
        unique_targets = list(set(records_to_check))
        if not unique_targets:
            return results

        def _check(set_name, rec_id):
            exists = self.check_record_exists(set_name, rec_id)
            return (set_name, rec_id), exists

        max_workers = min(10, len(unique_targets))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_check, s, r) for s, r in unique_targets]
            for fut in as_completed(futures):
                key, exists = fut.result()
                results[key] = exists

        return results

    def fetch_record_display_name(self, entity_set_name: str, record_id: str, primary_name_attr: str) -> str:
        """Fetch primary name/subject attribute value for display purposes."""
        if not primary_name_attr:
            return record_id
        url = f"{self.api_url}/{entity_set_name}({record_id})?$select={primary_name_attr}"
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            if res.status_code == 200:
                return res.json().get(primary_name_attr) or record_id
            return record_id
        except Exception:
            return record_id

    def fetch_record_details(self, entity_set_name: str, record_id: str) -> Dict[str, Any]:
        """Fetch all attributes for a single record from source to transport it."""
        url = f"{self.api_url}/{entity_set_name}({record_id})"
        res = requests.get(url, headers=self.headers, timeout=30)
        res.raise_for_status()
        return res.json()

    def get_root_business_unit(self) -> Optional[str]:
        """Fetch the root business unit GUID of this environment."""
        url = f"{self.api_url}/businessunits?$filter=parentbusinessunitid eq null&$select=businessunitid"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                vals = res.json().get("value", [])
                if vals:
                    return vals[0].get("businessunitid")
            return None
        except Exception:
            return None

    def get_base_currency(self) -> Optional[str]:
        """Fetch the base/default transaction currency GUID of this environment."""
        url = f"{self.api_url}/organizations?$select=basecurrencyid"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                vals = res.json().get("value", [])
                if vals:
                    return vals[0].get("basecurrencyid")
            return None
        except Exception:
            return None

    def get_system_user_by_email(self, email: str) -> Optional[str]:
        """Fetch a systemuserid by user's email address."""
        url = f"{self.api_url}/systemusers?$filter=internalemailaddress eq '{email}' or domainname eq '{email}'&$select=systemuserid"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                vals = res.json().get("value", [])
                if vals:
                    return vals[0].get("systemuserid")
            return None
        except Exception:
            return None

    def get_system_user_email(self, user_id: str) -> Optional[str]:
        """Fetch system user email for a given GUID."""
        url = f"{self.api_url}/systemusers({user_id})?$select=internalemailaddress,domainname"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                return data.get("internalemailaddress") or data.get("domainname")
            return None
        except Exception:
            return None

    def get_current_user_id(self) -> Optional[str]:
        """Fetch the current logged-in user's systemuserid in this environment using WhoAmI."""
        url = f"{self.api_url}/WhoAmI"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                return res.json().get("UserId")
            return None
        except Exception:
            return None

    def get_organization_id(self) -> Optional[str]:
        """Fetch the organizationid of this environment."""
        url = f"{self.api_url}/organizations?$select=organizationid"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code == 200:
                vals = res.json().get("value", [])
                if vals:
                    return vals[0].get("organizationid")
            return None
        except Exception:
            return None

    def upsert_record(self, entity_set_name: str, record_id: str, payload: dict, create_new: bool = True, update_existing: bool = True) -> Tuple[bool, str]:
        """
        Execute PATCH to upsert the record in the target environment.
        Uses If-None-Match or If-Match headers depending on toggles.
        """
        url = f"{self.api_url}/{entity_set_name}({record_id})"
        headers = self.headers.copy()
        if create_new and not update_existing:
            headers["If-None-Match"] = "*"
        elif not create_new and update_existing:
            headers["If-Match"] = "*"
        elif not create_new and not update_existing:
            return False, "Neither Create nor Update options were selected."
            
        try:
            res = requests.patch(url, headers=headers, json=payload, timeout=30)
            if res.status_code in (200, 204):
                return True, "Success"
            elif res.status_code == 412:
                if create_new and not update_existing:
                    return False, "Record already exists in target (skipped)."
                else:
                    return False, "Record does not exist in target (skipped)."
            else:
                error_msg = res.text
                try:
                    body = res.json()
                    if "error" in body and "message" in body["error"]:
                        error_msg = body["error"]["message"]
                except Exception:
                    pass
                return False, f"HTTP {res.status_code}: {error_msg}"
        except Exception as e:
            return False, str(e)
