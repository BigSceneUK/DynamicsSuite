import logging
import urllib.parse
import uuid
import re
import json
from email.parser import BytesParser
from email.policy import HTTP
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
        """
        Fetch attributes for a specific entity from Dataverse metadata.
        Filters out virtual, shadow (AttributeOf), and unreadable attributes
        to guarantee compatibility with Dataverse API queries.
        """
        url = (
            f"{self.api_url}/EntityDefinitions(LogicalName='{logical_name}')/Attributes"
            "?$select=LogicalName,DisplayName,AttributeType,IsManaged,IsValidForRead,IsValidForCreate,IsValidForUpdate,AttributeOf"
        )
        try:
            res = requests.get(url, headers=self.headers, timeout=60)
            res.raise_for_status()
            attributes = res.json().get("value", [])
        except Exception:
            # Fallback for environments with strict metadata projections
            fallback_url = (
                f"{self.api_url}/EntityDefinitions(LogicalName='{logical_name}')/Attributes"
                "?$select=LogicalName,DisplayName,AttributeType,IsManaged"
            )
            res = requests.get(fallback_url, headers=self.headers, timeout=60)
            res.raise_for_status()
            attributes = res.json().get("value", [])

        processed = []
        for a in attributes:
            lname = a.get("LogicalName", "")
            # Filter out virtual / shadow attributes pointing to another attribute (e.g. createdbyname -> createdby)
            if a.get("AttributeOf") is not None:
                continue

            # Filter out unreadable attributes
            if a.get("IsValidForRead") is False:
                continue

            # Filter out Virtual attribute types (which cannot be queried directly in Dataverse)
            if a.get("AttributeType") == "Virtual":
                continue

            # Additional safety: filter out common virtual shadow suffixes if AttributeOf was omitted in metadata
            shadow_suffixes = ("byname", "idname", "unitname", "codename", "parentname", "yominame")
            if any(lname.endswith(sfx) for sfx in shadow_suffixes):
                continue

            display_name = self._get_label(a, lname or "Unknown")
            processed.append({
                "LogicalName": lname,
                "DisplayName": display_name,
                "AttributeType": a.get("AttributeType", "String"),
                "IsManaged": a.get("IsManaged", False),
                "IsValidForCreate": a.get("IsValidForCreate", True),
                "IsValidForUpdate": a.get("IsValidForUpdate", True),
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

    def execute_batch_fetchxml(self, entity_set_name: str, fetch_xml: str) -> List[Dict[str, Any]]:
        """
        Execute FetchXML query via Dataverse $batch endpoint using HTTP POST.
        Bypasses HTTP 414 (Request URL Too Long) limits for wide tables or complex FetchXML.
        """
        batch_id = f"batch_{uuid.uuid4().hex}"
        encoded_fetch = urllib.parse.quote(fetch_xml)

        parsed_url = urllib.parse.urlparse(self.api_url)
        base_path = parsed_url.path.rstrip("/")
        inner_path = f"{base_path}/{entity_set_name}?fetchXml={encoded_fetch}"

        # Build multipart/mixed payload with strict CRLF line endings
        nl = "\r\n"
        body_parts = [
            f"--{batch_id}",
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            "",
            f"GET {inner_path} HTTP/1.1",
            "Accept: application/json",
            "OData-MaxVersion: 4.0",
            "OData-Version: 4.0",
            "",
            f"--{batch_id}--",
            ""
        ]
        batch_body = nl.join(body_parts)

        batch_url = f"{self.api_url}/$batch"
        headers = dict(self.headers)
        headers["Content-Type"] = f"multipart/mixed; boundary={batch_id}"

        res = requests.post(batch_url, data=batch_body.encode("utf-8"), headers=headers, timeout=60)
        res.raise_for_status()

        # Parse multipart/mixed response
        content_type = res.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', content_type)
        boundary = m.group(1).strip() if m else None
        if not boundary:
            first_line = res.content.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
            if first_line.startswith("--"):
                boundary = first_line[2:].strip()

        status_line = ""
        res_data = None

        # 1. Try BytesParser
        try:
            mime_header = f"Content-Type: multipart/mixed; boundary={boundary}\r\n\r\n".encode("utf-8")
            msg = BytesParser(policy=HTTP).parsebytes(mime_header + res.content)
            for part in msg.iter_parts():
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                http_parts = re.split(b"\r?\n\r?\n", payload, maxsplit=1)
                status_line = http_parts[0].splitlines()[0].decode("utf-8", errors="ignore") if http_parts else ""
                body_str = http_parts[1].decode("utf-8", errors="ignore").strip() if len(http_parts) > 1 else ""
                if body_str:
                    try:
                        res_data = json.loads(body_str)
                    except Exception:
                        res_data = body_str
                break
        except Exception:
            pass

        # 2. String/regex fallback parser
        if res_data is None:
            raw_text = res.content.decode("utf-8", errors="ignore")
            parts = re.split(rf"--{re.escape(boundary or 'batch')}(?:--)?", raw_text)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                idx = part.find("HTTP/1.")
                if idx != -1:
                    http_msg = part[idx:]
                    sections = re.split(r"\r?\n\r?\n", http_msg, maxsplit=1)
                    status_line = sections[0].splitlines()[0]
                    body_str = sections[1].strip() if len(sections) > 1 else ""
                    if body_str:
                        try:
                            res_data = json.loads(body_str)
                        except Exception:
                            res_data = body_str
                    break

        if isinstance(res_data, dict):
            if "error" in res_data:
                err_msg = res_data["error"].get("message", str(res_data["error"]))
                raise RuntimeError(f"Dataverse batch error ({status_line}): {err_msg}")
            return res_data.get("value", [])

        if status_line and not (status_line.startswith("HTTP/1.1 200") or status_line.startswith("HTTP/1.0 200")):
            raise RuntimeError(f"Dataverse batch error: {status_line} - {res_data}")

        return []

    def fetch_records(
        self,
        entity_logical_name: str,
        entity_set_name: str,
        select_attrs: List[str],
        fetch_xml: Optional[str] = None,
        top: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve records from Dataverse using native Dataverse FetchXML API.
        Bypasses OData Web API projection errors and policy restrictions.
        Automatically uses $batch POST or <all-attributes /> for wide entities (like crimson_application)
        to prevent HTTP 414 (Request URL Too Long).
        """
        if not fetch_xml:
            pk = f"{entity_logical_name}id"
            # Filter select_attrs to only valid attributes, ensuring pk is included
            clean_attrs = []
            for attr in select_attrs:
                if not attr:
                    continue
                # Discard shadow/virtual suffix patterns if any slipped through
                shadow_suffixes = ("byname", "idname", "unitname", "codename", "parentname", "yominame")
                if any(attr.endswith(sfx) for sfx in shadow_suffixes):
                    continue
                clean_attrs.append(attr)

            if pk not in clean_attrs:
                clean_attrs.insert(0, pk)

            # If user selected an exceptionally large number of attributes (> 80),
            # use <all-attributes /> which natively retrieves all fields without URL/payload bloat.
            if len(clean_attrs) > 80:
                fetch_xml = (
                    f'<fetch top="{top}" no-lock="true">\n'
                    f'  <entity name="{entity_logical_name}">\n'
                    f'    <all-attributes />\n'
                    f'  </entity>\n'
                    f'</fetch>'
                )
            elif clean_attrs:
                attr_tags = "\n    ".join(f'<attribute name="{a}" />' for a in clean_attrs)
                fetch_xml = (
                    f'<fetch top="{top}" no-lock="true">\n'
                    f'  <entity name="{entity_logical_name}">\n'
                    f'    {attr_tags}\n'
                    f'  </entity>\n'
                    f'</fetch>'
                )
            else:
                fetch_xml = (
                    f'<fetch top="{top}" no-lock="true">\n'
                    f'  <entity name="{entity_logical_name}">\n'
                    f'    <all-attributes />\n'
                    f'  </entity>\n'
                    f'</fetch>'
                )

        encoded = urllib.parse.quote(fetch_xml)
        url = f"{self.api_url}/{entity_set_name}?fetchXml={encoded}"

        # If URL exceeds standard GET length limit (2000 chars), transparently execute via Dataverse $batch POST
        if len(url) > 2000:
            return self.execute_batch_fetchxml(entity_set_name, fetch_xml)

        try:
            res = requests.get(url, headers=self.headers, timeout=60)
            res.raise_for_status()
            return res.json().get("value", [])
        except requests.HTTPError as exc:
            # If HTTP 414 (Request URL Too Long), fallback to $batch POST
            if exc.response is not None and (
                exc.response.status_code == 414
                or "414" in str(exc.response.status_code)
                or "URL Too Long" in exc.response.text
            ):
                try:
                    return self.execute_batch_fetchxml(entity_set_name, fetch_xml)
                except Exception as batch_exc:
                    raise RuntimeError(f"Dataverse API error (after $batch fallback): {batch_exc}") from batch_exc

            error_detail = ""
            try:
                err_json = exc.response.json()
                if "error" in err_json and "message" in err_json["error"]:
                    error_detail = err_json["error"]["message"]
            except Exception:
                pass
            if not error_detail:
                error_detail = exc.response.text if exc.response is not None else str(exc)
            raise RuntimeError(f"Dataverse API error: {error_detail}") from exc

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
        try:
            res = requests.get(url, headers=self.headers, timeout=30)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        # Fallback to Dataverse FetchXML by ID
        pk_field = f"{entity_set_name[:-1] if entity_set_name.endswith('s') else entity_set_name}id"
        fetch_xml = (
            f'<fetch top="1" no-lock="true">\n'
            f'  <entity name="{entity_set_name}">\n'
            f'    <all-attributes />\n'
            f'    <filter>\n'
            f'      <condition attribute="{pk_field}" operator="eq" value="{record_id}" />\n'
            f'    </filter>\n'
            f'  </entity>\n'
            f'</fetch>'
        )
        try:
            res = requests.get(f"{self.api_url}/{entity_set_name}?fetchXml={urllib.parse.quote(fetch_xml)}", headers=self.headers, timeout=30)
            if res.status_code == 200:
                val = res.json().get("value", [])
                if val:
                    return val[0]
        except Exception:
            pass

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
