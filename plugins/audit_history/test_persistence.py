"""Unit tests for Dataverse Audit History persistence and session restoration."""

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from plugins.audit_history.correlator import (
    AuditCorrelationItem,
    CorrelatedFlow,
    FlowRunExecution,
)
from plugins.audit_history import persistence


class TestAuditHistoryPersistence(unittest.TestCase):
    """Test auto-save cache and named history storage in persistence.py."""

    def setUp(self):
        self.test_cache = Path(".ah_results_cache.json")
        self.test_cache.unlink(missing_ok=True)
        self.created_history_files = []

    def tearDown(self):
        self.test_cache.unlink(missing_ok=True)
        for fn in self.created_history_files:
            persistence.delete_history(fn)

    def _sample_session_data(self) -> dict:
        flow = CorrelatedFlow(
            workflow_id="wf-guid-1",
            name="Notify Manager Flow",
            category=5,
            category_label="Modern Cloud Flow",
            state_code=1,
            primary_entity="incident",
            modifies_attributes={"statuscode", "description"},
            triggers_on_entity=True,
        )
        run = FlowRunExecution(
            run_id="run-001",
            workflow_id="wf-guid-1",
            flow_name="Notify Manager Flow",
            started_on=datetime(2026, 10, 12, 14, 0, 0, tzinfo=timezone.utc),
            completed_on=datetime(2026, 10, 12, 14, 0, 5, tzinfo=timezone.utc),
            status="Succeeded",
            duration_str="5s",
        )
        item = AuditCorrelationItem(
            audit_id="aud-1",
            timestamp=datetime(2026, 10, 12, 14, 0, 2, tzinfo=timezone.utc),
            changed_date_str="12/10/2026 14:00",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="Status Reason",
            changed_field_logical="statuscode",
            old_value="In Progress",
            new_value="Resolved",
            transaction_id="tx-999",
            matched_flow=flow,
            matched_run=run,
            candidate_flows=[flow],
            confidence="Exact Run Match",
            confidence_badge="🟢",
        )
        return {
            "entity_logical_name": "incident",
            "entity_display_name": "Case",
            "record_id": "case-guid-1234",
            "record_label": "Case CAS-1001 (case-guid-1234)",
            "filter_config": [{"field": "ticketnumber", "value": "CAS-1001"}],
            "search_results": [{"incidentid": "case-guid-1234", "ticketnumber": "CAS-1001"}],
            "metadata": {"statuscode": "Status Reason", "ticketnumber": "Case Number"},
            "audit_data": [
                {
                    "Changed Date": "12/10/2026 14:00",
                    "Changed By": "# PowerAutomate",
                    "Event": "Update",
                    "Changed Field": "Status Reason",
                    "Old Value": "In Progress",
                    "New Value": "Resolved",
                }
            ],
            "raw_audit_details": [{"AuditRecord": {"auditid": "aud-1"}}],
            "correlation_results": [item],
            "logic_app_diagnostics": [],
            "raw_workflows": [{"workflowid": "wf-guid-1", "name": "Notify Manager Flow"}],
            "env_id": "env-prod-01",
        }

    def test_save_and_load_cache(self):
        data = self._sample_session_data()
        self.assertTrue(persistence.save_results(data))
        self.assertTrue(self.test_cache.exists())

        loaded = persistence.load_results()
        self.assertEqual(loaded["entity_logical_name"], "incident")
        self.assertEqual(loaded["record_id"], "case-guid-1234")
        self.assertEqual(len(loaded["audit_data"]), 1)
        self.assertEqual(len(loaded["correlation_results"]), 1)

        corr_item = loaded["correlation_results"][0]
        self.assertIsInstance(corr_item, AuditCorrelationItem)
        self.assertEqual(corr_item.changed_field, "Status Reason")
        self.assertEqual(corr_item.confidence, "Exact Run Match")
        self.assertIsInstance(corr_item.matched_flow, CorrelatedFlow)
        self.assertEqual(corr_item.matched_flow.name, "Notify Manager Flow")
        self.assertIn("statuscode", corr_item.matched_flow.modifies_attributes)
        self.assertIsInstance(corr_item.matched_run, FlowRunExecution)
        self.assertEqual(corr_item.matched_run.run_id, "run-001")

        persistence.clear_results()
        self.assertFalse(self.test_cache.exists())
        self.assertEqual(persistence.load_results(), {})

    def test_save_and_load_history(self):
        data = self._sample_session_data()
        fn = persistence.save_history(data, name="Investigating CAS-1001", remark="Customer escalation")
        self.assertIsNotNone(fn)
        self.created_history_files.append(fn)

        hist_list = persistence.list_history()
        match = next((h for h in hist_list if h["filename"] == fn), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Investigating CAS-1001")
        self.assertEqual(match["remark"], "Customer escalation")
        self.assertEqual(match["table"], "Case")
        self.assertEqual(match["audit_count"], 1)
        self.assertEqual(match["correlation_count"], 1)

        remark, loaded = persistence.load_history(fn)
        self.assertEqual(remark, "Customer escalation")
        self.assertEqual(loaded["entity_logical_name"], "incident")
        self.assertEqual(len(loaded["correlation_results"]), 1)
        self.assertIsInstance(loaded["correlation_results"][0], AuditCorrelationItem)

        # Test remark update
        self.assertTrue(persistence.update_history_remark(fn, "New remark updated"))
        new_remark, _ = persistence.load_history(fn)
        self.assertEqual(new_remark, "New remark updated")

        # Test deletion
        self.assertTrue(persistence.delete_history(fn))
        self.created_history_files.remove(fn)
        self.assertNotIn(fn, [h["filename"] for h in persistence.list_history()])

    def test_clientdata_cache(self):
        cache_file = Path(".ah_clientdata_cache.json")
        cache_file.unlink(missing_ok=True)
        try:
            self.assertEqual(persistence.load_clientdata_cache(), {})
            test_cache = {"wf-1": '{"properties": {"definition": {}}}'}
            persistence.save_clientdata_cache(test_cache)
            self.assertTrue(cache_file.exists())
            loaded = persistence.load_clientdata_cache()
            self.assertEqual(loaded, test_cache)
        finally:
            cache_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
