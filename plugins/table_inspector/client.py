import urllib.parse
import requests
import pandas as pd
import logging
import os
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional

# ── Live scan log ────────────────────────────────────────────────────────────
# Written to logs/ at workspace root (or custom DYNAMICS_SCAN_LOG_PATH)
# to avoid leaking runtime data into plugin distribution archives.
_DEFAULT_LOG_DIR = os.path.join(os.getcwd(), "logs")
_LOG_PATH = os.environ.get(
    "DYNAMICS_SCAN_LOG_PATH",
    os.path.join(_DEFAULT_LOG_DIR, "table_inspector_scan.log"),
)
_log_lock = threading.Lock()

def _ensure_log_dir() -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    except OSError:
        pass

def _scan_log(msg: str) -> None:
    """Append a timestamped line to the live scan log and flush immediately."""
    _ensure_log_dir()
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n"
    with _log_lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    logging.debug(msg)

def _scan_log_header(title: str) -> None:
    """Write a separator + title block to mark the start of a new scan."""
    _ensure_log_dir()
    sep = "=" * 70
    with _log_lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n{sep}\n{datetime.now().isoformat()} — {title}\n{sep}\n")

class TableInspectorClient:
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
            # Fallback to first available localized label if UserLocalizedLabel is missing
            localized_labels = display_name.get("LocalizedLabels")
            if localized_labels and isinstance(localized_labels, list) and len(localized_labels) > 0:
                return localized_labels[0].get("Label", default)
            return default
            
        return user_label.get("Label", default)

    def get_entities(self) -> List[Dict[str, Any]]:
        """Fetch all entities with their logical and display names."""
        url = f"{self.api_url}/EntityDefinitions?$select=LogicalName,DisplayName,EntitySetName"
        res = requests.get(url, headers=self.headers, timeout=60)
        res.raise_for_status()
        entities = res.json().get("value", [])
        
        processed = []
        for e in entities:
            display_name = self._get_label(e, e.get("LogicalName", "Unknown"))
            processed.append({
                "LogicalName": e["LogicalName"],
                "DisplayName": display_name,
                "EntitySetName": e["EntitySetName"]
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
        return processed

    def get_forms_containing_attributes(self, logical_name: str) -> Dict[str, List[str]]:
        """
        Identify which attributes are present on which forms.
        Returns a mapping of attribute logical name -> list of form names.
        """
        # We query systemforms for the entity (objecttypecode)
        url = f"{self.api_url}/systemforms?$filter=objecttypecode eq '{logical_name}' and (type eq 2 or type eq 7)&$select=name,formxml"
        try:
            res = requests.get(url, headers=self.headers, timeout=60)
            res.raise_for_status()
            forms = res.json().get("value", [])
            
            attr_to_forms = {}
            for f in forms:
                form_name = f["name"]
                xml_content = f.get("formxml", "")
                if not xml_content:
                    continue
                # Simple check for attribute logical names in form XML
                import re
                controls = re.findall(r'datafieldname="([^"]+)"', xml_content)
                for ctrl in set(controls):
                    if ctrl not in attr_to_forms:
                        attr_to_forms[ctrl] = []
                    attr_to_forms[ctrl].append(form_name)
            
            return attr_to_forms
        except Exception as e:
            logging.error(f"Error fetching forms for {logical_name}: {e}")
            return {}

    def _fetchxml_page(self, entity_set_name: str, fetch_xml: str, diag: list) -> list:
        """Execute a single FetchXML page. Appends diagnostic lines to `diag`.
        Retries up to 3 times on HTTP 429 (throttle) with exponential back-off.
        Returns list of records, or None on hard error."""
        import time
        encoded = urllib.parse.quote(fetch_xml)
        url = f"{self.api_url}/{entity_set_name}?fetchXml={encoded}"
        diag.append(f"→ GET {url[:200]}")
        _scan_log(f"FETCH  entity={entity_set_name}")
        _scan_log(f"  xml  = {fetch_xml[:300]}")
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                res = requests.get(url, headers=self.headers, timeout=60)
                if res.status_code == 200:
                    data = res.json()
                    records = data.get("value", [])
                    if attempt == 0:
                        diag.append(f"  ← HTTP 200, records: {len(records)}")
                    _scan_log(f"  OK     {len(records)} records returned")
                    return records
                elif res.status_code == 429:
                    retry_after = int(res.headers.get("Retry-After", 2 ** (attempt + 1)))
                    msg = f"  429 throttled, retry in {retry_after}s (attempt {attempt + 1}/{max_retries})"
                    diag.append(f"  ← HTTP 429 (throttled), retry in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                    _scan_log(msg)
                    if attempt < max_retries:
                        time.sleep(retry_after)
                        continue
                    diag.append("  Max retries reached on 429 — skipping page.")
                    _scan_log("  ERROR  max retries on 429 — page skipped")
                    return None
                else:
                    msg = f"  ERROR  HTTP {res.status_code}: {res.text[:300]}"
                    diag.append(f"  ← HTTP {res.status_code}, ERROR: {res.text[:400]}")
                    _scan_log(msg)
                    return None
            except Exception as e:
                diag.append(f"  EXCEPTION (attempt {attempt + 1}): {e}")
                _scan_log(f"  EXCEPTION (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None
        return None

    def _fetchxml_page_with_cookie(
        self,
        entity_set_name: str,
        fetch_xml: str,
        diag: list,
    ) -> tuple:
        """Execute a FetchXML page and return both the records and the next paging cookie.

        Paging cookies let Dataverse serve each page in O(page_size) work rather than
        forcing a full-table seek to reach page N.  Without cookies, pages 50+ on a
        ~400k-row table consistently time out because the server must re-scan from
        record 1 every time.

        Returns:
            (records, next_cookie) where
                records    – list of record dicts (or None on hard error)
                next_cookie – XML-attribute-encoded cookie for the next <fetch> element,
                              or None when this was the last page / an error occurred.
        """
        import time

        encoded = urllib.parse.quote(fetch_xml)
        url = f"{self.api_url}/{entity_set_name}?fetchXml={encoded}"
        _scan_log(f"FETCH  entity={entity_set_name}")
        _scan_log(f"  xml  = {fetch_xml[:300]}")

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                res = requests.get(url, headers=self.headers, timeout=60)
                if res.status_code == 200:
                    data = res.json()
                    records = data.get("value", [])
                    _scan_log(f"  OK     {len(records)} records returned")

                    # Extract the paging cookie for the next request.
                    #
                    # Dataverse returns the cookie as a URL-encoded XML fragment in
                    # @Microsoft.Dynamics.CRM.fetchxmlpagingcookie.  The correct usage
                    # is to embed this value VERBATIM (still URL-encoded) as the
                    # paging-cookie attribute in the next <fetch> element, WITHOUT
                    # any URL-decoding or XML-escaping on our side.
                    #
                    # Why: urllib.parse.quote() encodes the whole FetchXML string
                    # before it is sent as a GET parameter, which further encodes the
                    # % signs in our verbatim cookie (e.g. %3C → %253C).  Dataverse
                    # then URL-decodes the fetchXml query parameter once to recover
                    # the FetchXML (with %3C still in the paging-cookie attribute),
                    # then URL-decodes the paging-cookie attribute value a second time
                    # to get the actual <cookie ...> XML.  This double-decode only
                    # works if we don't pre-decode the value ourselves.
                    #
                    # URL-encoded strings (%XX sequences) contain no XML special
                    # characters, so no XML-escaping is needed for this value.
                    raw = data.get("@Microsoft.Dynamics.CRM.fetchxmlpagingcookie", "")
                    _scan_log(f"  COOKIE_RAW   = {raw[:300]}")
                    if raw:
                        next_cookie = raw  # embed verbatim — do NOT decode
                        _scan_log(f"  COOKIE_NEXT  = {next_cookie[:300]}")
                    else:
                        _scan_log("  COOKIE_RAW   = (empty — last page)")
                        next_cookie = None

                    return records, next_cookie

                elif res.status_code == 429:
                    retry_after = int(res.headers.get("Retry-After", 2 ** (attempt + 1)))
                    _scan_log(f"  429  retry in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                    diag.append(f"  ← HTTP 429 throttled, retry in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries:
                        time.sleep(retry_after)
                        continue
                    return None, None
                else:
                    _scan_log(f"  ERROR  HTTP {res.status_code}: {res.text[:300]}")
                    diag.append(f"  ← HTTP {res.status_code}: {res.text[:400]}")
                    return None, None

            except Exception as e:
                _scan_log(f"  EXCEPTION (attempt {attempt + 1}): {e}")
                diag.append(f"  EXCEPTION (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None, None

        return None, None

    def get_table_total_count(self, logical_name: str, entity_set_name: str) -> tuple:
        """Get exact total record count via FetchXML aggregate (single request, no cap).

        Dataverse's OData $count is hard-capped at 5,000 and cannot be used reliably.
        FetchXML aggregate=count returns the true count regardless of table size.
        Falls back to serial FetchXML pagination if aggregate fails.
        Returns (total_count, diag_lines).
        """
        diag = [f"[TotalCount] entity={logical_name}, set={entity_set_name}"]
        primary_key = f"{logical_name}id"

        # Primary: FetchXML aggregate count — single request, no 5,000 cap
        _scan_log(f"COUNT  entity={logical_name} — trying FetchXML aggregate count")
        agg_xml = (
            f'<fetch aggregate="true" no-lock="true">'
            f'<entity name="{logical_name}">'
            f'<attribute name="{primary_key}" alias="record_count" aggregate="count"/>'
            f'</entity>'
            f'</fetch>'
        )
        agg_diag: List[str] = []
        agg_records = self._fetchxml_page(entity_set_name, agg_xml, agg_diag)
        diag.extend(agg_diag)
        if agg_records and len(agg_records) > 0:
            total = agg_records[0].get("record_count")
            if total is None:
                # Dataverse may annotate the alias differently
                total = next(
                    (v for k, v in agg_records[0].items() if "record_count" in k),
                    None,
                )
            if total is not None:
                diag.append(f"  aggregate count = {total}")
                _scan_log(f"COUNT  result = {total} (via FetchXML aggregate)")
                logging.info("\n".join(diag))
                return int(total), diag
            diag.append(f"  aggregate returned unexpected record: {agg_records[0]}")
            _scan_log(f"COUNT  aggregate parse failed — record={agg_records[0]}")
        else:
            diag.append("  aggregate count returned no records, falling back.")
            _scan_log("COUNT  aggregate returned no records — falling back to pagination")

        # Fallback: serial FetchXML pagination (slow but correct)
        _scan_log("COUNT  falling back to serial FetchXML pagination")
        total = 0
        page = 1
        while True:
            xml = (
                f'<fetch page="{page}" no-lock="true">'
                f'<entity name="{logical_name}">'
                f'<attribute name="{primary_key}"/>'
                f'</entity>'
                f'</fetch>'
            )
            records = self._fetchxml_page(entity_set_name, xml, diag)
            if records is None:
                diag.append(f"  Aborting count at page {page} due to API error.")
                break
            if len(records) == 0:
                break
            total += len(records)
            page += 1
        diag.append(f"  Total count result (pagination fallback): {total}")
        _scan_log(f"COUNT  result = {total} (via pagination fallback)")
        logging.info("\n".join(diag))
        return total, diag

    def get_usage_statistics_accurate(
        self,
        logical_name: str,
        entity_set_name: str,
        attributes: List[str],
        progress_callback=None,
    ) -> tuple:
        """Calculate exact usage statistics (% non-null) for every attribute.

        Strategy — parallel chunks, serial cookie-based pages per chunk:

          Without paging cookies, Dataverse must scan from record 1 every time you
          request page N, making pages 50+ progressively slower and eventually causing
          read timeouts on large tables (observed: consistent 60 s timeouts on page 57+
          of a 386k-row table).

          Each attribute chunk (50 attrs max) gets one worker that paginates through
          the whole table sequentially, threading the paging cookie from each response
          into the next request.  This keeps every individual request O(page_size) on
          the server side rather than O(full_table_scan).

          Up to 10 chunks run concurrently; pages within each chunk are strictly serial
          so the cookie chain is never broken.

        Returns a tuple of (usage_dict, actual_records_scanned, diag_lines).
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not attributes:
            return {}, 0, ["No attributes provided."]

        primary_key = f"{logical_name}id"
        CHUNK_SIZE = 50
        attr_chunks = [attributes[i:i + CHUNK_SIZE] for i in range(0, len(attributes), CHUNK_SIZE)]

        _scan_log_header(
            f"FULL SCAN  entity={logical_name}  attrs={len(attributes)}  chunks={len(attr_chunks)}"
        )

        diag: List[str] = [
            f"[UsageScan] entity={logical_name}, set={entity_set_name}",
            f"  total attributes: {len(attributes)}, chunks: {len(attr_chunks)}",
        ]
        diag_lock = threading.Lock()
        progress_lock = threading.Lock()

        # Get total record count upfront for the progress bar denominator.
        total_count, count_diag = self.get_table_total_count(logical_name, entity_set_name)
        with diag_lock:
            diag.extend(count_diag)
        diag.append(f"  Total records (aggregate): {total_count}")
        _scan_log(f"PLAN   total_records={total_count}  chunks={len(attr_chunks)}")

        non_null_counts: Dict[str, int] = {a: 0 for a in attributes}
        counts_lock = threading.Lock()
        # chunk 0's actual page count becomes the authoritative records-seen tally
        chunk0_records_seen = [0]

        def _scan_chunk(chunk_idx: int, chunk: List[str]):
            """Paginate through the entire table for one attribute chunk.

            Uses keyset pagination (WHERE primarykey > last_seen_id ORDER BY primarykey):
            - Every page is an index seek → O(page_size) on the server regardless of depth.
            - No paging cookies needed, so no encoding issues with Dataverse custom plugins.
            - Works correctly at any depth (page 1 or page 1000).
            """
            local_diag: List[str] = []
            local_counts: Dict[str, int] = {a: 0 for a in chunk}
            records_seen = 0
            last_id: Optional[str] = None  # GUID of the last record seen on previous page

            attr_set = list(dict.fromkeys([primary_key] + chunk))
            attr_xml = "".join(f'<attribute name="{a}"/>' for a in attr_set)
            order_xml = f'<order attribute="{primary_key}" descending="false"/>'

            page = 0
            while True:
                page += 1
                filter_xml = (
                    f'<filter>'
                    f'<condition attribute="{primary_key}" operator="gt" value="{last_id}"/>'
                    f'</filter>'
                    if last_id else ""
                )
                xml = (
                    f'<fetch count="5000" no-lock="true">'
                    f'<entity name="{logical_name}">'
                    f"{attr_xml}"
                    f"{order_xml}"
                    f"{filter_xml}"
                    f"</entity>"
                    f"</fetch>"
                )
                _scan_log(
                    f"PAGE   chunk={chunk_idx + 1}/{len(attr_chunks)} page={page}"
                    f"  after={str(last_id)[:18] if last_id else 'start'}"
                )
                records = self._fetchxml_page(entity_set_name, xml, local_diag)

                if not records:
                    _scan_log(f"PAGE   chunk={chunk_idx + 1} page={page} → no records / error — done")
                    break

                for rec in records:
                    for attr in chunk:
                        val = rec.get(attr)
                        if val is None:
                            val = rec.get(f"_{attr}_value")
                        if val is not None:
                            local_counts[attr] += 1

                last_id = records[-1].get(primary_key)
                records_seen += len(records)

                # Use chunk 0 as the single source-of-truth for progress reporting.
                if chunk_idx == 0:
                    with progress_lock:
                        chunk0_records_seen[0] = records_seen
                        denom = max(total_count, 1)
                        _scan_log(
                            f"PROGRESS  {records_seen:,} / {denom:,} "
                            f"({100 * records_seen / denom:.1f}%)"
                        )
                        if progress_callback:
                            try:
                                progress_callback(records_seen, denom)
                            except Exception:
                                pass  # never let a callback error kill a worker

                if len(records) < 5000:
                    # Fewer records than the page size → this was the last page.
                    _scan_log(f"PAGE   chunk={chunk_idx + 1} page={page} → last page ({len(records)} records)")
                    break

            with diag_lock:
                diag.extend(local_diag)
            return chunk_idx, local_counts, records_seen

        # Run all chunk workers concurrently (pages within each are serial).
        max_workers = min(10, len(attr_chunks))
        actual_records = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_scan_chunk, i, ch)
                for i, ch in enumerate(attr_chunks)
            ]
            for future in as_completed(futures):
                chunk_idx, local_counts, records_seen = future.result()
                with counts_lock:
                    for attr, cnt in local_counts.items():
                        non_null_counts[attr] += cnt
                if chunk_idx == 0:
                    actual_records = records_seen

        # Prefer the actual records scanned (chunk 0) for the usage denominator;
        # fall back to the aggregate count when chunk 0 somehow produced nothing.
        final_total = actual_records if actual_records > 0 else total_count

        diag.append(f"  Actual records scanned (chunk 0): {actual_records}")
        diag.append(
            f"  Sample non_null_counts (first 5): "
            f"{ {k: non_null_counts[k] for k in list(non_null_counts)[:5]} }"
        )
        _scan_log(f"FULL SCAN COMPLETE  total_records={final_total}")
        logging.info("\n".join(diag))

        if final_total == 0:
            return {a: 0.0 for a in attributes}, 0, diag

        usage = {a: non_null_counts[a] / final_total for a in attributes}
        return usage, final_total, diag
