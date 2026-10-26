import unittest
from datetime import datetime, timezone
import json

from plugins.audit_history.correlator import (
    AuditProcessCorrelator,
    AuditCorrelationItem,
    CorrelatedFlow,
    FlowRunExecution,
    OutboundTriggerItem,
    FlowOutboundSummary,
    extract_flow_definition,
    is_automation_user,
    group_correlations,
    group_outbound_triggers,
    group_outbound_by_flow,
    parse_condition_clauses,
    evaluate_condition_rules,
    render_flow_history_table_html,
    render_outbound_triggers_table_html,
)



class TestAuditCorrelator(unittest.TestCase):
    def test_automation_user_detection(self):
        self.assertTrue(is_automation_user("# PowerAutomate"))
        self.assertTrue(is_automation_user("# Microsoft CRM Portals"))
        self.assertTrue(is_automation_user("SYSTEM"))
        self.assertFalse(is_automation_user("Koon Hung Luk"))
        self.assertFalse(is_automation_user("Amanda Tilley"))

    def test_extract_flow_definition(self):
        mock_clientdata = {
            "properties": {
                "definition": {
                    "triggers": {
                        "When_a_row_is_modified": {
                            "type": "OpenApiConnectionWebhook",
                            "inputs": {
                                "parameters": {
                                    "entityName": "crimson_applications",
                                    "message": 3,
                                    "filterAttributes": "crimson_extractedtosap,crimson_studentid"
                                }
                            }
                        }
                    },
                    "actions": {
                        "Update_Student_ID": {
                            "type": "OpenApiConnection",
                            "inputs": {
                                "host": {"connectionName": "shared_commondataserviceforapps"},
                                "parameters": {
                                    "entityName": "crimson_applications",
                                    "recordId": "@triggerOutputs()?['body/crimson_applicationid']",
                                    "item/crimson_studentid": "140375569",
                                    "item/crimson_extractedtosap": "Contact Create Complete"
                                }
                            }
                        }
                    }
                }
            }
        }

        mock_wf = {
            "workflowid": "e8d69736-2244-46c4-8c88-123456789abc",
            "name": "SAP - Student ID Sync",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps(mock_clientdata),
        }

        parsed = extract_flow_definition(mock_wf, "crimson_application")
        self.assertEqual(parsed.name, "SAP - Student ID Sync")
        self.assertTrue(parsed.triggers_on_entity)
        self.assertIn("crimson_studentid", parsed.modifies_attributes)
        self.assertIn("crimson_extractedtosap", parsed.modifies_attributes)

    def test_correlate_audit_details(self):
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-09T14:11:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-1",
                },
                "OldValue": {"crimson_studentid": None},
                "NewValue": {"crimson_studentid": "140375569"},
            }
        ]

        mock_wf = {
            "workflowid": "flow-1",
            "name": "SAP - Student ID Sync",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Row": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "host": {"connectionName": "shared_commondataserviceforapps"},
                                    "parameters": {
                                        "entityName": "crimson_application",
                                        "item/crimson_studentid": "140375569"
                                    }
                                }
                            }
                        }
                    }
                }
            })
        }

        mock_runs = [
            {
                "flowrunid": "run-999",
                "flowid": "flow-1",
                "flowname": "SAP - Student ID Sync",
                "status": "Succeeded",
                "starttime": "2026-10-09T14:10:58Z",
                "endtime": "2026-10-09T14:11:02Z",
            }
        ]

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_studentid": "Student ID"},
            tolerance_seconds=30,
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.run_id, "run-999")
        self.assertIn("https://make.powerautomate.com/environments/env-123/flows/flow-1/runs/run-999", item.process_url)

    def test_group_correlations_multi_variable(self):
        from plugins.audit_history.correlator import group_correlations

        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        # Multi-variable update in single transaction
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-09T14:11:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-batch-101",
                },
                "OldValue": {
                    "crimson_studentid": None,
                    "crimson_extractedtosap": "Error Detected",
                },
                "NewValue": {
                    "crimson_studentid": "140375569",
                    "crimson_extractedtosap": "Contact Create Complete",
                },
            }
        ]

        mock_wf = {
            "workflowid": "flow-1",
            "name": "SAP - Student ID Sync",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Row": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_application",
                                        "item/crimson_studentid": "140375569",
                                        "item/crimson_extractedtosap": "Contact Create Complete"
                                    }
                                }
                            }
                        }
                    }
                }
            })
        }

        mock_runs = [
            {
                "flowrunid": "run-999",
                "flowid": "flow-1",
                "flowname": "SAP - Student ID Sync",
                "status": "Succeeded",
                "starttime": "2026-10-09T14:10:59Z",
                "endtime": "2026-10-09T14:11:01Z",
            }
        ]

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={
                "crimson_studentid": "Student ID",
                "crimson_extractedtosap": "Extracted to SAP",
            },
            tolerance_seconds=30,
        )

        # 2 fields in items
        self.assertEqual(len(items), 2)

        # Grouping consolidates into 1 execution group
        grouped = group_correlations(items)
        self.assertEqual(len(grouped), 1)
        group = grouped[0]
        self.assertEqual(group.field_count, 2)
        self.assertIn("Student ID", group.changes_summary)
        self.assertIn("Extracted to SAP", group.changes_summary)
        self.assertIn("\n", group.changes_summary)
        self.assertNotIn(" | ", group.changes_summary)
        self.assertEqual(group.flow_name, "⚡ SAP - Student ID Sync")
        self.assertIn("https://make.powerautomate.com/environments/env-123/flows/flow-1/runs/run-999", group.process_url)
        self.assertIn("https://make.powerautomate.com/environments/env-123/flows/flow-1/details", group.flow_history_url)

    def test_render_dynamics_audit_table_html(self):
        from plugins.audit_history.correlator import (
            GroupedAuditExecution,
            render_dynamics_audit_table_html,
        )

        sample_group = GroupedAuditExecution(
            group_id="tx_123",
            timestamp=datetime(2026, 10, 1, 15, 35),
            changed_date_str="01/10/2026 15:35",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            transaction_id="tx_123",
            flow_name="⚡ SAP - Student ID Sync",
            process_url="https://make.powerautomate.com/environments/env-1/flows/flow-1/runs/run-1",
            process_link_label="Open Run History ↗",
            flow_history_url="https://make.powerautomate.com/environments/env-1/flows/flow-1/details",
            flow_history_label="Flow History (28d) ↗",
            confidence="Exact Run Match",
            confidence_badge="🟢",
            fields_changed=[
                {"Field": "Application Date", "Old Value": "", "New Value": "01/10/2026"},
                {"Field": "Extracted to SAP", "Old Value": "Ready for Extract", "New Value": "Contact Create Complete"},
                {"Field": "Student ID", "Old Value": "", "New Value": "260001616"},
            ],
            field_count=3,
        )

        html = render_dynamics_audit_table_html([sample_group], show_flow_columns=True)
        # Check header
        self.assertIn("Audit History", html)
        self.assertIn("DELETE CHANGE HISTORY", html)
        self.assertIn("Changed Date", html)
        self.assertIn("Changed By", html)
        self.assertIn("Event", html)
        self.assertIn("Changed Field", html)
        self.assertIn("Old Value", html)
        self.assertIn("New Value", html)
        # Check rowspan of 3 for multi-variable
        self.assertIn('rowspan="3"', html)
        # Check values
        self.assertIn("Application Date", html)
        self.assertIn("Extracted to SAP", html)
        self.assertIn("260001616", html)
        # Check history links
        self.assertIn("https://make.powerautomate.com/environments/env-1/flows/flow-1/runs/run-1", html)
        self.assertIn("https://make.powerautomate.com/environments/env-1/flows/flow-1/details", html)
        self.assertIn("Open Run History ↗", html)

    def test_group_correlations_multi_trigger_separation(self):
        from plugins.audit_history.correlator import (
            AuditCorrelationItem,
            CorrelatedFlow,
            group_correlations,
        )

        flow_a = CorrelatedFlow(
            workflow_id="wf-abc",
            name="Choice: PGR with studentship",
            category=5,
            category_label="Modern Flow",
            state_code=1,
            primary_entity="application",
        )

        # Two audit items for the SAME flow but distinct audit records / triggers (15:19:17 and 15:20:50)
        item1 = AuditCorrelationItem(
            audit_id="aud-001",
            timestamp=datetime(2026, 10, 1, 15, 19, 17, tzinfo=timezone.utc),
            changed_date_str="01/10/2026 15:19:17",
            user_name="# Portals-NU User",
            is_automation=True,
            event="Update",
            changed_field="Notification Sync",
            changed_field_logical="notification_sync",
            old_value=None,
            new_value="321000000",
            transaction_id="tx-1",
            extra_info="",
            matched_flow=flow_a,
            confidence="Candidate Flow",
            confidence_badge="🔵",
        )

        item2 = AuditCorrelationItem(
            audit_id="aud-002",
            timestamp=datetime(2026, 10, 1, 15, 20, 50, tzinfo=timezone.utc),
            changed_date_str="01/10/2026 15:20:50",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="Notification Sync",
            changed_field_logical="notification_sync",
            old_value="321000000",
            new_value="321000002",
            transaction_id="tx-2",
            extra_info="",
            matched_flow=flow_a,
            confidence="Candidate Flow",
            confidence_badge="🔵",
        )

        grouped = group_correlations([item1, item2])
        # Must be separated into 2 distinct groups
        self.assertEqual(len(grouped), 2)
        # Check that both triggers are annotated with sequence
        flow_names = [g.flow_name for g in grouped]
        self.assertTrue(any("Trigger #1 of 2" in fn for fn in flow_names))
        self.assertTrue(any("Trigger #2 of 2" in fn for fn in flow_names))

    def test_default_environment_fallback(self):
        # When environment_id is not provided, maker URL falls back to "default" referencing Cloud Flow History
        correlator = AuditProcessCorrelator(environment_id=None, org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-09T14:11:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                },
                "OldValue": {"status": "Draft"},
                "NewValue": {"status": "Submitted"},
            }
        ]

        mock_wf = {
            "workflowid": "wf-100",
            "name": "Auto Submit",
            "category": 5,
            "statecode": 1,
            "primaryentity": "application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "host": {"connectionName": "shared_commondataserviceforapps"},
                                    "parameters": {
                                        "entityName": "application",
                                        "item/status": "Submitted"
                                    }
                                }
                            }
                        }
                    }
                }
            }),
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="application",
            attr_metadata={"status": "Status"},
        )

        self.assertEqual(len(items), 1)
        self.assertIn("https://make.powerautomate.com/environments/default/flows/wf-100/details", items[0].process_url)

    def test_cloud_flow_history_run_matching_and_links(self):
        from plugins.audit_history.correlator import group_correlations

        env_id = "33ca7ea6-166f-4eab-8359-a9e3866b78c6"
        correlator = AuditProcessCorrelator(environment_id=env_id, org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-01T15:19:17Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# Portals-NU UniAdmissions Dev",
                    "transactionid": "tx-1",
                },
                "OldValue": {"firstname": None},
                "NewValue": {"firstname": "Test N"},
            }
        ]

        # Structure matches .cf_results_cache.json / Cloud Flow History
        cf_runs = [
            {
                "Flow Name": "Application : Update Related Choices Name Value",
                "_env_id": env_id,
                "_flow_id": "286ca055-ee3b-ec11-8c62-000d3a86fcdc",
                "_pa_run": True,
                "durationinmilliseconds": None,
                "endtime": "2026-10-01T15:19:26.1654795Z",
                "flowrunid": "08584107389196236414984598570CU26",
                "starttime": "2026-10-01 15:19:25.863460200+00:00",
                "status": "Succeeded",
                "workflowid": "286ca055-ee3b-ec11-8c62-000d3a86fcdc",
            }
        ]

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=cf_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="application",
            attr_metadata={"firstname": "First Name"},
            tolerance_seconds=45,
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.flow_name, "Application : Update Related Choices Name Value")
        self.assertEqual(item.matched_run.status, "Succeeded")
        # Ensure the link contains the real environment GUID, NOT 'default'
        expected_url = f"https://make.powerautomate.com/environments/{env_id}/flows/286ca055-ee3b-ec11-8c62-000d3a86fcdc/runs/08584107389196236414984598570CU26"
        self.assertEqual(item.process_url, expected_url)

        grouped = group_correlations(items)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].flow_name, "⚡ Application : Update Related Choices Name Value")
        self.assertEqual(grouped[0].process_url, expected_url)
        self.assertEqual(grouped[0].run_status, "Succeeded")

    def test_run_consolidation_no_spurious_triggers(self):
        from plugins.audit_history.correlator import group_correlations, AuditCorrelationItem, FlowRunExecution

        run = FlowRunExecution(
            run_id="run-set-deadline-1",
            workflow_id="wf-deadline",
            flow_name="Application : Set Deadline Date",
            started_on=datetime(2026, 10, 1, 15, 35, 20, tzinfo=timezone.utc),
            completed_on=datetime(2026, 10, 1, 15, 35, 21, tzinfo=timezone.utc),
            status="Succeeded",
            source="asyncoperations",
            duration_str="1s",
        )

        # 6 mutations from the SAME flow run with different audit IDs
        items = []
        for i in range(1, 7):
            items.append(
                AuditCorrelationItem(
                    audit_id=f"aud-00{i}",
                    timestamp=datetime(2026, 10, 1, 15, 35, 20, tzinfo=timezone.utc),
                    changed_date_str="01/10/2026 15:35:20",
                    user_name="# PowerAutomate Dev Flow Account",
                    is_automation=True,
                    event="Update",
                    changed_field=f"field_{i}",
                    changed_field_logical=f"field_{i}",
                    old_value="",
                    new_value=f"val_{i}",
                    transaction_id="tx-99",
                    matched_run=run,
                    confidence="Exact Run Match",
                    confidence_badge="🟢",
                )
            )

        grouped = group_correlations(items)
        # All 6 mutations MUST consolidate into ONE single row!
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].field_count, 6)
        # Should NOT have any spurious '(Trigger #1 of 6)'
        self.assertEqual(grouped[0].flow_name, "⚡ Application : Set Deadline Date")

    def test_distinct_flow_runs_trigger_annotation(self):
        from plugins.audit_history.correlator import group_correlations, AuditCorrelationItem, FlowRunExecution

        # 2 distinct runs of the same flow
        run1 = FlowRunExecution(
            run_id="run-101",
            workflow_id="wf-same",
            flow_name="Change Degree Document Required",
            started_on=datetime(2026, 10, 1, 15, 19, 49, tzinfo=timezone.utc),
            completed_on=datetime(2026, 10, 1, 15, 19, 50, tzinfo=timezone.utc),
            status="Succeeded",
            source="flowruns",
        )
        run2 = FlowRunExecution(
            run_id="run-102",
            workflow_id="wf-same",
            flow_name="Change Degree Document Required",
            started_on=datetime(2026, 10, 1, 15, 19, 50, tzinfo=timezone.utc),
            completed_on=datetime(2026, 10, 1, 15, 19, 51, tzinfo=timezone.utc),
            status="Succeeded",
            source="flowruns",
        )

        item1 = AuditCorrelationItem(
            audit_id="aud-a",
            timestamp=datetime(2026, 10, 1, 15, 19, 49, tzinfo=timezone.utc),
            changed_date_str="01/10/2026 15:19:49",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="doc_a",
            changed_field_logical="doc_a",
            old_value="",
            new_value="yes",
            matched_run=run1,
        )
        item2 = AuditCorrelationItem(
            audit_id="aud-b",
            timestamp=datetime(2026, 10, 1, 15, 19, 50, tzinfo=timezone.utc),
            changed_date_str="01/10/2026 15:19:50",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="doc_b",
            changed_field_logical="doc_b",
            old_value="",
            new_value="yes",
            matched_run=run2,
        )

        grouped = group_correlations([item1, item2])
        self.assertEqual(len(grouped), 2)
        names = [g.flow_name for g in grouped]
        self.assertTrue(any("Trigger #1 of 2" in n for n in names))
        self.assertTrue(any("Trigger #2 of 2" in n for n in names))

    def test_render_flow_history_table_html_multiline_vars(self):
        item1 = AuditCorrelationItem(
            audit_id="aud-1",
            timestamp=datetime(2026, 9, 18, 10, 51, 4, tzinfo=timezone.utc),
            changed_date_str="18/09/2026 10:51:04",
            user_name="# Microsoft CRM Portals",
            is_automation=True,
            event="Update",
            changed_field="Document Checklist Trigger",
            changed_field_logical="crimson_trigger",
            old_value="2026-09-18T10:50:26Z",
            new_value="2026-09-18T10:51:16Z",
            transaction_id="tx-100",
        )
        item2 = AuditCorrelationItem(
            audit_id="aud-2",
            timestamp=datetime(2026, 9, 18, 10, 51, 4, tzinfo=timezone.utc),
            changed_date_str="18/09/2026 10:51:04",
            user_name="# Microsoft CRM Portals",
            is_automation=True,
            event="Update",
            changed_field="crimson_applicationdate@OData.Community.Display.V1.FormattedValue",
            changed_field_logical="crimson_applicationdate",
            old_value="18/09/2026",
            new_value="19/09/2026",
            transaction_id="tx-100",
        )

        grouped = group_correlations([item1, item2])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].field_count, 2)

        html_out = render_flow_history_table_html(grouped)
        # Check that both variables are rendered in distinct div tags (next lines)
        self.assertIn("Document Checklist Trigger", html_out)
        self.assertIn("crimson_applicationdate", html_out)
        self.assertNotIn("@OData.Community.Display.V1.FormattedValue", html_out)
        self.assertIn("➔", html_out)
        self.assertIn("2026-09-18T10:50:26Z", html_out)
        self.assertIn("2026-09-18T10:51:16Z", html_out)

    def test_chronological_ordering_across_month_boundary(self):
        # September 22, 2026 vs October 9, 2026
        # Alphabetically: "09/10/2026" < "22/09/2026"
        # Chronologically: 2026-09-22 < 2026-10-09
        item_sep = AuditCorrelationItem(
            audit_id="aud-sep",
            timestamp=datetime(2026, 9, 22, 8, 36, 1, tzinfo=timezone.utc),
            changed_date_str="22/09/2026 08:36:01",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="field_a",
            changed_field_logical="field_a",
            old_value="1",
            new_value="2",
        )
        item_oct = AuditCorrelationItem(
            audit_id="aud-oct",
            timestamp=datetime(2026, 10, 9, 14, 20, 0, tzinfo=timezone.utc),
            changed_date_str="09/10/2026 14:20:00",
            user_name="# PowerAutomate",
            is_automation=True,
            event="Update",
            changed_field="field_b",
            changed_field_logical="field_b",
            old_value="A",
            new_value="B",
        )

        grouped = group_correlations([item_sep, item_oct])
        self.assertEqual(len(grouped), 2)
        # Default sort is descending chronological (newest first -> Oct 9 before Sep 22)
        self.assertEqual(grouped[0].changed_date_str, "09/10/2026 14:20:00")
        self.assertEqual(grouped[1].changed_date_str, "22/09/2026 08:36:01")

    def test_reject_flow_run_belonging_to_different_record(self):
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")
        
        target_app = "20ee48f7-88ab-f111-aad1-000d3a0cdc82"
        related_choices = {"8581a894-89ab-f111-aad1-000d3a0cdc82", "5ddbf3c3-89ab-f111-aad1-000d3a0cdc82"}
        all_target_guids = {target_app} | related_choices

        unrelated_choice = "607106fa-5d69-f111-9b47-000d3a86e21c"

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-09-08T13:28:48Z",
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate Production Flow Account",
                    "action": 2,
                    "transactionid": "tx-1",
                },
                "OldValue": {},
                "NewValue": {"crimson_reprocesseddate": "2026-09-08T13:26:58Z"},
            }
        ]

        mock_runs = [
            {
                "flowrunid": "run-unrelated-123",
                "workflowid": "wf-cas-email",
                "flowname": "CAS: Send Email to review Draft CAS",
                "status": "Succeeded",
                "starttime": "2026-09-08T13:28:48Z",
                "endtime": "2026-09-08T13:29:03Z",
            }
        ]

        # Mock PA client where run trigger output contains unrelated_choice
        class MockPAClient:
            def get_flow_run_details(self, env_id, wf_id, run_id):
                return {
                    "properties": {
                        "trigger": {
                            "outputsLink": {"uri": "https://sas.mock/trigger_output"}
                        }
                    }
                }

            def get_content_from_link(self, uri):
                return {"body": {"crimson_choiceid": unrelated_choice}}

            def get_flow_run_actions(self, env_id, wf_id, run_id):
                return []

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_reprocesseddate": "Reprocessed Date"},
            tolerance_seconds=45,
            only_automations=True,
            target_guids=all_target_guids,
            pa_client=MockPAClient(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        # Must NOT match run-unrelated-123 because it was executed for unrelated choice!
        self.assertIsNone(item.matched_run)
        self.assertNotEqual(item.matched_run, "run-unrelated-123")
        self.assertEqual(item.confidence, "Uncorrelated Automation")

    def test_action_level_variable_correlation(self):
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")
        target_guid = "20ee48f7-88ab-f111-aad1-000d3a0cdc82"

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-100",
                    "createdon": "2026-10-09T15:00:00Z",
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "action": 2,
                    "transactionid": "tx-100",
                },
                "OldValue": {"crimson_decision": None},
                "NewValue": {"crimson_decision": "Offer"},
            }
        ]

        mock_runs = [
            {
                "flowrunid": "run-decision-1",
                "workflowid": "wf-decision-flow",
                "flowname": "Process Admissions Decision",
                "status": "Succeeded",
                "starttime": "2026-10-09T14:59:58Z",
                "endtime": "2026-10-09T15:00:04Z",
            }
        ]

        class MockPAClientWithAction:
            def get_flow_run_details(self, env_id, wf_id, run_id):
                return {"properties": {}}

            def get_flow_run_actions(self, env_id, wf_id, run_id):
                return [
                    {
                        "name": "Update_Decision_Record",
                        "properties": {
                            "inputsLink": {"uri": "https://sas.mock/action_input"}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                return {
                    "entityName": "crimson_applications",
                    "recordId": target_guid,
                    "item": {
                        "crimson_decision": "Offer",
                        "statuscode": 1
                    }
                }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_decision": "Admissions Decision"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            pa_client=MockPAClientWithAction(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.run_id, "run-decision-1")
        self.assertIn("Update_Decision_Record", item.attribution_summary)
        self.assertIn("Admissions Decision", item.attribution_summary)

    def test_five_minute_pre_update_window_matching(self):
        """Test that a flow starting 3 minutes before the audit timestamp matches under the 5-minute pre-update rule."""
        correlator = AuditProcessCorrelator(environment_id="env-1", org_url="https://org.crm.dynamics.com")
        target_guid = "20ee48f7-88ab-f111-aad1-000d3a0cdc82"

        # Audit event created at 14:15:00
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-longrun",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-long",
                },
                "OldValue": {"statuscode": 1},
                "NewValue": {"statuscode": 2},
            }
        ]

        # Flow run started at 14:12:00 (3 minutes before audit update) and completed at 14:15:10
        mock_runs = [
            {
                "flowrunid": "run-3min-prior",
                "flowid": "flow-long",
                "flowname": "Long Processing Flow",
                "starttime": "2026-10-09T14:12:00Z",
                "endtime": "2026-10-09T14:15:10Z",
                "status": "Succeeded",
            }
        ]

        mock_wf = {
            "workflowid": "flow-long",
            "name": "Long Processing Flow",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Row": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_applications",
                                        "item/statuscode": 2,
                                    }
                                }
                            }
                        }
                    }
                }
            }),
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"statuscode": "Status Reason"},
            tolerance_seconds=30,  # Even with 30s tolerance param, the 5-min pre-window captures it
            only_automations=True,
            target_guids={target_guid},
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.run_id, "run-3min-prior")

    def test_run_completed_before_update_is_excluded(self):
        """Test that a flow run that completed 2 minutes before the audit event is not falsely correlated."""
        correlator = AuditProcessCorrelator(environment_id="env-1", org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                },
                "OldValue": {"statuscode": 1},
                "NewValue": {"statuscode": 2},
            }
        ]

        # Flow run ended at 14:13:00 (2 minutes before audit event occurred)
        mock_runs = [
            {
                "flowrunid": "run-ended-early",
                "flowid": "flow-early",
                "flowname": "Early Flow",
                "starttime": "2026-10-09T14:12:00Z",
                "endtime": "2026-10-09T14:13:00Z",
                "status": "Succeeded",
            }
        ]

        mock_wf = {
            "workflowid": "flow-early",
            "name": "Early Flow",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Row": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_applications",
                                        "item/statuscode": 2,
                                    }
                                }
                            }
                        }
                    }
                }
            }),
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=mock_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"statuscode": "Status Reason"},
            tolerance_seconds=30,
            only_automations=True,
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        # Should NOT match the run that ended early!
        self.assertIsNone(item.matched_run)
        self.assertEqual(item.confidence, "Candidate Flow")

    def test_extract_logic_app_definition(self):
        """Test extraction of Logic App WDL definition with URI-encoded path and body parameters."""
        mock_la = {
            "workflowid": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Logic/workflows/DYN-DEV-ContactPhotoAvailable",
            "name": "DYN-DEV-ContactPhotoAvailable",
            "category": 6,
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "For_each_contact": {
                                "type": "Foreach",
                                "actions": {
                                    "Update_Contact_Photo_Flag": {
                                        "type": "Http",
                                        "inputs": {
                                            "method": "patch",
                                            "path": "/api/data/v9.1/@{encodeURIComponent('contacts')}(@{items('For_each_contact')?['contactid']})",
                                            "body": {
                                                "crimson_getcontactphoto": 214350000
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        parsed = extract_flow_definition(mock_la, "contact")
        self.assertEqual(parsed.category, 6)
        self.assertEqual(parsed.category_label, "Azure Logic App")
        self.assertIn("crimson_getcontactphoto", parsed.modifies_attributes)

    def test_correlate_logic_app_candidate(self):
        """Test fallback to Candidate Flow when Logic App modifies field but no run is in window."""
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-la-1",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-la-1",
                },
                "OldValue": {"crimson_getcontactphoto": None},
                "NewValue": {"crimson_getcontactphoto": 214350000},
            }
        ]

        mock_la = {
            "workflowid": "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Logic/workflows/DYN-DEV-ContactPhotoAvailable",
            "name": "DYN-DEV-ContactPhotoAvailable",
            "category": 6,
            "statecode": 1,
            "primaryentity": "",
            "portal_url": "https://portal.azure.com/#@/resource/workflows/DYN-DEV-ContactPhotoAvailable",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Photo": {
                                "type": "Http",
                                "inputs": {
                                    "path": "/api/data/v9.1/@{encodeURIComponent('contacts')}(...)",
                                    "body": {
                                        "crimson_getcontactphoto": 214350000
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_getcontactphoto": "Photo Flag"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={"20ee48f7-88ab-f111-aad1-000d3a0cdc82"},
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Candidate Flow")
        self.assertIn("Azure Logic App 'DYN-DEV-ContactPhotoAvailable'", item.attribution_summary)
        self.assertIn("https://portal.azure.com", item.process_url)

    def test_correlate_logic_app_exact_payload_match(self):
        """Test exact run match when Logic App run payload targets record GUID in 5-minute pre-update window."""
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        target_guid = "20ee48f7-88ab-f111-aad1-000d3a0cdc82"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-la-2",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-la-2",
                },
                "OldValue": {"crimson_getcontactphoto": None},
                "NewValue": {"crimson_getcontactphoto": 214350000},
            }
        ]

        la_wid = "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Logic/workflows/DYN-DEV-ContactPhotoAvailable"
        mock_la = {
            "workflowid": la_wid,
            "name": "DYN-DEV-ContactPhotoAvailable",
            "category": 6,
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Photo": {
                                "type": "Http",
                                "inputs": {
                                    "path": "/api/data/v9.1/@{encodeURIComponent('contacts')}",
                                    "body": {"crimson_getcontactphoto": 214350000}
                                }
                            }
                        }
                    }
                }
            }
        }

        # Logic App run executed 1 minute before audit event
        run_res_id = f"{la_wid}/runs/08584839201928"
        mock_logic_runs = [
            {
                "id": run_res_id,
                "name": "08584839201928",
                "workflow_id": la_wid,
                "flow_name": "DYN-DEV-ContactPhotoAvailable",
                "properties": {
                    "startTime": "2026-10-09T14:14:50Z",
                    "endTime": "2026-10-09T14:15:02Z",
                    "status": "Succeeded",
                }
            }
        ]

        class MockLogicAppClient:
            def get_run_details(self, rid):
                return {
                    "properties": {
                        "trigger": {
                            "outputsLink": {"uri": "https://sas.blob.core.windows.net/trigger-out"}
                        }
                    }
                }

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Update_Contact_Photo_Flag",
                        "properties": {
                            "inputsLink": {"uri": "https://sas.blob.core.windows.net/action-inputs"}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                if "action-inputs" in uri:
                    return {
                        "path": f"/api/data/v9.1/contacts({target_guid})",
                        "body": {"crimson_getcontactphoto": 214350000}
                    }
                return {"body": {}}

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_getcontactphoto": "Photo Flag"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=mock_logic_runs,
            logic_client=MockLogicAppClient(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.source, "logicapps")
        self.assertIn("Azure Logic App 'DYN-DEV-ContactPhotoAvailable'", item.attribution_summary)
        self.assertIn("Update_Contact_Photo_Flag", item.attribution_summary)
        self.assertIn("https://portal.azure.com", item.process_url)

    def test_correlate_logic_app_rejects_unrelated_guid(self):
        """Test that a Logic App run targeting a different record GUID is rejected."""
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        target_guid = "20ee48f7-88ab-f111-aad1-000d3a0cdc82"
        other_guid = "99999999-0000-0000-0000-000000000000"

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-la-3",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-la-3",
                },
                "OldValue": {"crimson_getcontactphoto": None},
                "NewValue": {"crimson_getcontactphoto": 214350000},
            }
        ]

        la_wid = "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Logic/workflows/DYN-DEV-ContactPhotoAvailable"
        mock_la = {
            "workflowid": la_wid,
            "name": "DYN-DEV-ContactPhotoAvailable",
            "category": 6,
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Photo": {
                                "type": "Http",
                                "inputs": {
                                    "path": "/api/data/v9.1/@{encodeURIComponent('contacts')}",
                                    "body": {"crimson_getcontactphoto": 214350000}
                                }
                            }
                        }
                    }
                }
            }
        }

        run_res_id = f"{la_wid}/runs/08584839201999"
        mock_logic_runs = [
            {
                "id": run_res_id,
                "name": "08584839201999",
                "workflow_id": la_wid,
                "flow_name": "DYN-DEV-ContactPhotoAvailable",
                "properties": {
                    "startTime": "2026-10-09T14:14:50Z",
                    "endTime": "2026-10-09T14:15:02Z",
                    "status": "Succeeded",
                }
            }
        ]

        class MockLogicAppClientOtherGuid:
            def get_run_details(self, rid):
                return {"properties": {"trigger": {}}}

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Update_Contact_Photo_Flag",
                        "properties": {
                            "inputsLink": {"uri": "https://sas.blob.core.windows.net/action-inputs"}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                # Targets other_guid, not target_guid!
                return {
                    "path": f"/api/data/v9.1/contacts({other_guid})",
                    "body": {"crimson_getcontactphoto": 214350000}
                }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_getcontactphoto": "Photo Flag"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=mock_logic_runs,
            logic_client=MockLogicAppClientOtherGuid(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        # Run belongs to a different record GUID, so it must NOT be matched!
        self.assertIsNone(item.matched_run)
        self.assertEqual(item.confidence, "Candidate Flow")

    def test_real_run_within_pre_window_takes_precedence_over_candidate_flow(self):
        """Verify that an executing run in the 5-min pre-window is matched rather than falling back to a candidate flow."""
        correlator = AuditProcessCorrelator()

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-prod-1",
                    "createdon": "2026-10-09T14:15:30Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-prod-1",
                },
                "OldValue": {"crimson_status": "Draft"},
                "NewValue": {"crimson_status": "Submitted"},
            }
        ]

        # Candidate flow 1: Static flow matching the field, but had NO runs
        candidate_static = {
            "workflowid": "flow-static-cand",
            "name": "Static Inactive Flow",
            "category": 5,
            "statecode": 1,
            "primaryentity": "crimson_application",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Status": {
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_applications",
                                        "item/crimson_status": "Submitted",
                                    }
                                }
                            }
                        }
                    }
                }
            }),
        }

        # Logic App that actually RAN at 14:15:05 (started 25s before audit, completed 15s before audit)
        la_prod = {
            "workflowid": "la-prod-real",
            "name": "DYN-PROD-LA-UcasApplication",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_App": {
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_applications",
                                        "item/crimson_status": "Submitted",
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }

        real_logic_run = [
            {
                "id": "run-la-prod-123",
                "workflow_id": "la-prod-real",
                "flow_name": "DYN-PROD-LA-UcasApplication",
                "properties": {
                    "startTime": "2026-10-09T14:15:05Z",
                    "endTime": "2026-10-09T14:15:15Z",
                    "status": "Succeeded",
                }
            }
        ]

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[candidate_static, la_prod],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_status": "Status"},
            tolerance_seconds=45,
            only_automations=True,
            target_guids={"12345678-abcd-1234-abcd-123456789abc"},
            logic_runs=real_logic_run,
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        # Must match the REAL Logic App run, NOT the static inactive flow!
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.flow_name, "DYN-PROD-LA-UcasApplication")
        self.assertIn(item.confidence, ("Exact Run Match", "Probable Session Match"))

    def test_unhyphenated_guid_verification(self):
        """Verify that GUID matching succeeds when payload contains unhyphenated format."""
        correlator = AuditProcessCorrelator()
        target_guid = "11223344-5566-7788-99aa-bbccddeeff00"
        unhyphenated = target_guid.replace("-", "")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-unhyphen",
                    "createdon": "2026-10-09T14:15:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-u-1",
                },
                "OldValue": {"statuscode": 1},
                "NewValue": {"statuscode": 2},
            }
        ]

        mock_la = {
            "workflowid": "la-unhyphen-wf",
            "name": "DYN-PROD-LA-ContactUpdated",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {},
        }

        mock_runs = [
            {
                "id": "run-u-1",
                "workflow_id": "la-unhyphen-wf",
                "flow_name": "DYN-PROD-LA-ContactUpdated",
                "properties": {
                    "startTime": "2026-10-09T14:14:55Z",
                    "endTime": "2026-10-09T14:15:02Z",
                    "status": "Succeeded",
                }
            }
        ]

        class MockLogicAppClientUnhyphen:
            def get_run_details(self, rid):
                return {"properties": {"trigger": {}}}

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Update_Record",
                        "properties": {
                            "inputsLink": {"uri": "https://sas.blob.core.windows.net/action-inputs"}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                # URI contains unhyphenated GUID!
                return {
                    "path": f"/api/data/v9.1/contacts({unhyphenated})",
                    "body": {"statuscode": 2}
                }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"statuscode": "Status Reason"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=mock_runs,
            logic_client=MockLogicAppClientUnhyphen(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertEqual(item.confidence_badge, "🟢")

    def test_env_tag_detection(self):
        from core.context import AppContext
        from plugins.audit_history.ui import _detect_current_env_tag

        prod_ctx = AppContext(org_url="https://uonprod.crm4.dynamics.com", environment_name="Newcastle Production")
        self.assertEqual(_detect_current_env_tag(prod_ctx), "PROD")

        dev_ctx = AppContext(org_url="https://uondev.crm4.dynamics.com", environment_name="Newcastle Development")
        self.assertEqual(_detect_current_env_tag(dev_ctx), "DEV")

        qa_ctx = AppContext(org_url="https://uonqa.crm4.dynamics.com", environment_name="Newcastle QA Testing")
        self.assertEqual(_detect_current_env_tag(qa_ctx), "QA")

    def test_resource_group_matching(self):
        from core.context import AppContext
        from plugins.audit_history.ui import _match_resource_group_for_env, _get_available_resource_groups

        available_rgs = [
            "DYN-DEV-RG",
            "DYN-PROD-RG-Integrations",
            "DYN-QA-RG-Integrations",
            "DYN-TMP-RG-Integrations"
        ]

        # 1. QA Environment
        qa_ctx = AppContext(org_url="https://uonqa.crm4.dynamics.com", environment_name="Newcastle QA")
        self.assertEqual(_match_resource_group_for_env(qa_ctx, available_rgs), "DYN-QA-RG-Integrations")

        # 2. Production Environment
        prod_ctx = AppContext(org_url="https://uonprod.crm4.dynamics.com", environment_name="Newcastle Production")
        self.assertEqual(_match_resource_group_for_env(prod_ctx, available_rgs), "DYN-PROD-RG-Integrations")

        # 3. Development Environment
        dev_ctx = AppContext(org_url="https://uondev.crm4.dynamics.com", environment_name="Newcastle Dev Sandbox")
        self.assertEqual(_match_resource_group_for_env(dev_ctx, available_rgs), "DYN-DEV-RG")

        # 4. Get available resource groups
        rgs, counts = _get_available_resource_groups(prod_ctx)
        self.assertIsInstance(rgs, list)
        self.assertIsInstance(counts, dict)
        if "DYN-QA-RG-Integrations" in rgs:
            self.assertGreaterEqual(counts["DYN-QA-RG-Integrations"], 1)

    def test_progress_callback_invoked(self):
        correlator = AuditProcessCorrelator()
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-p1",
                    "createdon": "2026-10-10T14:30:00Z",
                    "_userid_value": "# PowerAutomate",
                    "action": 2,
                },
                "OldValue": {"telephone1": "111", "emailaddress1": "a@b.com"},
                "NewValue": {"telephone1": "222", "emailaddress1": "c@d.com"},
            }
        ]

        progress_reports = []
        def _cb(cur, tot, msg):
            progress_reports.append((cur, tot, msg))

        correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"telephone1": "Telephone", "emailaddress1": "Email"},
            progress_callback=_cb,
        )

        self.assertEqual(len(progress_reports), 2)
        self.assertEqual(progress_reports[0][0], 1)
        self.assertEqual(progress_reports[0][1], 2)
        self.assertEqual(progress_reports[1][0], 2)
        self.assertEqual(progress_reports[1][1], 2)

    def test_temporal_pool_pruning(self):
        """Runs outside [earliest_audit - 5.5m, latest_audit + 2m] should be pruned from the pool."""
        correlator = AuditProcessCorrelator()
        audit_time = "2026-10-10T14:30:00Z"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-t1",
                    "createdon": audit_time,
                    "_userid_value": "# PowerAutomate",
                    "action": 2,
                },
                "OldValue": {"telephone1": "111"},
                "NewValue": {"telephone1": "222"},
            }
        ]

        flow_runs = [
            # 1. Way before window (30 minutes before) -> should be pruned
            {
                "flowrunid": "run-too-early",
                "starttime": "2026-10-10T14:00:00Z",
                "endtime": "2026-10-10T14:01:00Z",
                "status": "Succeeded",
                "workflowid": "wf-1",
            },
            # 2. Inside window (started 2 min before, finished 5s before update) -> should be retained and matched
            {
                "flowrunid": "run-valid",
                "starttime": "2026-10-10T14:28:00Z",
                "endtime": "2026-10-10T14:29:55Z",
                "status": "Succeeded",
                "workflowid": "wf-1",
            },
            # 3. Way after window (2 hours later) -> should be pruned
            {
                "flowrunid": "run-too-late",
                "starttime": "2026-10-10T16:30:00Z",
                "endtime": "2026-10-10T16:31:00Z",
                "status": "Succeeded",
                "workflowid": "wf-1",
            },
        ]

        mock_wf = {
            "workflowid": "wf-1",
            "name": "Update Contact Phone",
            "category": 5,
            "primaryentity": "contact",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_record": {
                                "type": "OpenApiConnection",
                                "inputs": {
                                    "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"},
                                    "parameters": {
                                        "entityName": "contacts",
                                        "recordId": "11223344-5566-7788-9900-aabbccddeeff",
                                        "item/telephone1": "222",
                                    }
                                }
                            }
                        }
                    }
                }
            })
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_wf],
            flow_runs=flow_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"telephone1": "Telephone"},
        )

        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0].matched_run)
        self.assertEqual(items[0].matched_run.run_id, "run-valid")

    def test_extract_payload_keys(self):
        from plugins.audit_history.correlator import _extract_payload_keys

        # Nested dict with body
        data1 = {"body": {"firstname": "John", "lastname": "Doe"}}
        self.assertIn("firstname", _extract_payload_keys(data1))
        self.assertIn("lastname", _extract_payload_keys(data1))

        # Stringified JSON
        data2 = {"body": json.dumps({"crimson_fee": "1500", "crimson_status": "Paid"})}
        keys2 = _extract_payload_keys(data2)
        self.assertIn("crimson_fee", keys2)
        self.assertIn("crimson_status", keys2)

        # item/ prefixed keys
        data3 = {"parameters": {"item/emailaddress1": "a@b.com", "item/mobilephone": "07123"}}
        keys3 = _extract_payload_keys(data3)
        self.assertIn("emailaddress1", keys3)
        self.assertIn("mobilephone", keys3)

    def test_dynamic_unspecified_action_matched_via_incoming_json(self):
        """When the flow definition has no specified fields, match via incoming JSON in run history."""
        correlator = AuditProcessCorrelator()
        target_guid = "99887766-5544-3322-1100-aabbccddeeff"

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-dyn-1",
                    "createdon": "2026-10-09T14:20:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-dyn-1",
                },
                "OldValue": {"crimson_dynamic_field": "Old"},
                "NewValue": {"crimson_dynamic_field": "New"},
            }
        ]

        # Static definition has NO specified fields (empty modifies_attributes)
        mock_la = {
            "workflowid": "la-dynamic-unspecified",
            "name": "DYN-PROD-LA-GenericUpdater",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "HTTP_Patch_Dataverse": {
                                "type": "Http",
                                "inputs": {
                                    "method": "PATCH",
                                    "uri": f"/api/data/v9.2/contacts({target_guid})",
                                    "body": "@triggerBody()",  # Dynamic body!
                                }
                            }
                        }
                    }
                }
            },
        }

        mock_runs = [
            {
                "id": "run-dyn-1",
                "workflow_id": "la-dynamic-unspecified",
                "flow_name": "DYN-PROD-LA-GenericUpdater",
                "properties": {
                    "startTime": "2026-10-09T14:19:50Z",
                    "endTime": "2026-10-09T14:19:58Z",
                    "status": "Succeeded",
                }
            }
        ]

        class MockLogicAppClientDynamic:
            def get_run_details(self, rid):
                # Trigger output contains incoming JSON
                return {
                    "properties": {
                        "trigger": {
                            "outputsLink": {"uri": "https://storage.blob.core.windows.net/trigger-outputs"}
                        }
                    }
                }

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "HTTP_Patch_Dataverse",
                        "properties": {
                            "inputsLink": {"uri": "https://storage.blob.core.windows.net/action-inputs"}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                if "trigger-outputs" in uri:
                    return {
                        "body": {
                            "id": target_guid,
                            "crimson_dynamic_field": "New",
                        }
                    }
                # Action inputs evaluated at runtime:
                return {
                    "uri": f"/api/data/v9.2/contacts({target_guid})",
                    "body": {
                        "crimson_dynamic_field": "New",
                    }
                }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_dynamic_field": "Dynamic Field"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=mock_runs,
            logic_client=MockLogicAppClientDynamic(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertEqual(item.confidence_badge, "🟢")
        self.assertIn("HTTP_Patch_Dataverse", item.attribution_summary)
        self.assertIn("Dynamic Field", item.attribution_summary)
        self.assertIn("incoming JSON payload", item.attribution_summary)

    def test_loop_does_not_break_on_unverified_run_and_finds_verified_run(self):
        """Verify that an inconclusive unverified run does not stop the search, and a later verified run is chosen."""
        correlator = AuditProcessCorrelator()
        target_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-order-1",
                    "createdon": "2026-10-09T14:30:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-order-1",
                },
                "OldValue": {"statuscode": 1},
                "NewValue": {"statuscode": 2},
            }
        ]

        # Flow 1: Inconclusive/unverified payload, but modifies attribute
        wf_unverified = {
            "workflowid": "wf-unverified-1",
            "name": "Unverified Flow 1",
            "category": 5,
            "statecode": 1,
            "primaryentity": "contact",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Status": {
                                "inputs": {"parameters": {"item/statuscode": 2}}
                            }
                        }
                    }
                }
            })
        }

        # Flow 2: Verified on payload targeting our target GUID!
        wf_verified = {
            "workflowid": "wf-verified-2",
            "name": "Verified Flow 2",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {},
        }

        run_unverified = {
            "flowrunid": "run-unverified",
            "flowid": "wf-unverified-1",
            "flowname": "Unverified Flow 1",
            "starttime": "2026-10-09T14:29:45Z",
            "endtime": "2026-10-09T14:29:55Z",
            "status": "Succeeded",
        }

        run_verified = {
            "id": "run-verified-id",
            "workflow_id": "wf-verified-2",
            "flow_name": "Verified Flow 2",
            "properties": {
                "startTime": "2026-10-09T14:29:40Z",
                "endTime": "2026-10-09T14:29:50Z",
                "status": "Succeeded",
            }
        }

        class MockLogicAppVerifiedClient:
            def get_run_details(self, rid):
                return {"properties": {"trigger": {}}}

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Update_Contact",
                        "properties": {"inputsLink": {"uri": "https://storage/inputs"}}
                    }
                ]

            def get_content_from_link(self, uri):
                # Targets our exact record GUID!
                return {
                    "path": f"/contacts({target_guid})",
                    "body": {"statuscode": 2}
                }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[wf_unverified, wf_verified],
            flow_runs=[run_unverified],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"statuscode": "Status Reason"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=[run_verified],
            logic_client=MockLogicAppVerifiedClient(),
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        # Must pick the VERIFIED run, NOT the unverified one!
        self.assertEqual(item.matched_run.flow_name, "Verified Flow 2")
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertEqual(item.confidence_badge, "🟢")

    def test_multiple_candidate_flows_all_listed(self):
        """When no active run executed, all candidate flows configuring the attribute are surfaced."""
        correlator = AuditProcessCorrelator()

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-cand-all",
                    "createdon": "2026-10-09T14:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-cand-all",
                },
                "OldValue": {"crimson_status": "Draft"},
                "NewValue": {"crimson_status": "Submitted"},
            }
        ]

        cand_1 = {
            "workflowid": "cand-flow-1",
            "name": "Flow Alpha",
            "category": 5,
            "statecode": 1,
            "primaryentity": "contact",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "actions": {"Up": {"inputs": {"parameters": {"entityName": "contacts", "item/crimson_status": "Submitted"}}}}
                    }
                }
            })
        }
        cand_2 = {
            "workflowid": "cand-la-2",
            "name": "DYN-PROD-LA-ContactBeta",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {"Up": {"inputs": {"parameters": {"entityName": "contacts", "item/crimson_status": "Submitted"}}}}
                    }
                }
            }
        }

        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[cand_1, cand_2],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_status": "Status"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={"some-guid"},
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Candidate Flow")
        # Both candidate flows must be mentioned!
        self.assertIn("Flow Alpha", item.attribution_summary)
        self.assertIn("DYN-PROD-LA-ContactBeta", item.attribution_summary)

    def test_flow_runs_filtered_to_touching_flows_and_window(self):
        """Test that runs belonging to non-touching flows or outside the audit window are excluded."""
        import pandas as pd
        touching_cf_ids = {"flow-touching-1"}
        touching_cf_names = {"app flow updater"}

        def _parse_run_dt(val):
            if not val:
                return None
            try:
                dt = pd.to_datetime(val)
                if getattr(dt, "tzinfo", None) is not None:
                    return dt.tz_convert(None)
                return dt
            except Exception:
                return None

        def _is_touching_cf_run(r: dict) -> bool:
            r_wid = (r.get("flowid") or r.get("workflowid") or "").strip("{}() '\"")
            if r_wid and r_wid in touching_cf_ids:
                return True
            r_name = str(r.get("flowname") or r.get("Flow Name") or "").lower().strip()
            if r_name and r_name in touching_cf_names:
                return True
            return False

        min_dt = pd.to_datetime("2026-10-10 12:00:00")
        max_dt = pd.to_datetime("2026-10-10 12:12:00")

        def _is_run_in_window(st_val) -> bool:
            p_dt = _parse_run_dt(st_val)
            if p_dt is None:
                return False
            return min_dt <= p_dt <= max_dt

        test_runs = [
            {"flowrunid": "r1", "workflowid": "flow-touching-1", "starttime": "2026-10-10T12:05:00Z"},
            {"flowrunid": "r2", "flowname": "App Flow Updater", "starttime": "2026-10-10T12:08:00Z"},
            {"flowrunid": "r3", "workflowid": "flow-touching-1", "starttime": "2026-10-10T11:00:00Z"},  # outside window
            {"flowrunid": "r4", "workflowid": "other-flow-99", "starttime": "2026-10-10T12:05:00Z"},    # not touching
        ]

        filtered = [r for r in test_runs if _is_touching_cf_run(r) and _is_run_in_window(r.get("starttime"))]
        self.assertEqual(len(filtered), 2)
        self.assertEqual({r["flowrunid"] for r in filtered}, {"r1", "r2"})

    def test_logic_app_inline_payload_and_step_31_verification(self):
        """Verify Logic App with inline inputs payload is verified in Step 3.1 and matched in Step 3.2."""
        target_guid = "11111111-2222-3333-4444-555555555555"
        mock_audit = [{
            "AuditRecord": {
                "auditid": "aud-la-1",
                "createdon": "2026-10-10T12:05:05Z",
                "_userid_value": "# PowerAutomate",
                "action": 2,
                "operation": 2,
            },
            "OldValue": {"crimson_status": "Draft"},
            "NewValue": {"crimson_status": "Submitted"},
        }]

        la_workflow = {
            "workflowid": "la-wf-100",
            "name": "DYN-PROD-LA-ApplicationUpdater",
            "category": 6,
            "category_label": "Azure Logic App",
            "statecode": 1,
            "primaryentity": "",
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Update_Row": {
                                "inputs": {
                                    "parameters": {
                                        "entityName": "crimson_applications",
                                        "item/crimson_status": "Submitted",
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        mock_la_runs = [{
            "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Logic/workflows/DYN-PROD-LA-ApplicationUpdater/runs/085848001",
            "name": "085848001",
            "workflow_id": "la-wf-100",
            "flow_name": "DYN-PROD-LA-ApplicationUpdater",
            "properties": {
                "startTime": "2026-10-10T12:05:00Z",
                "endTime": "2026-10-10T12:05:03Z",
                "status": "Succeeded",
            }
        }]

        class MockLogicClient:
            def get_run_details(self, rid):
                return {
                    "properties": {
                        "trigger": {
                            "name": "manual",
                            "outputs": {"body": {"app_id": target_guid}},
                        }
                    }
                }

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Update_Record",
                        "properties": {
                            "status": "Succeeded",
                            # INLINE payload (no inputsLink!)
                            "inputs": {
                                "host": {"connectionName": "shared_commondataserviceforapps"},
                                "parameters": {
                                    "entityName": "crimson_applications",
                                    "recordId": target_guid,
                                    "item/crimson_status": "Submitted",
                                }
                            },
                            "outputs": {"statusCode": 200}
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                return None

        correlator = AuditProcessCorrelator()
        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[la_workflow],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_status": "Status"},
            tolerance_seconds=30,
            only_automations=True,
            target_guids={target_guid},
            logic_runs=mock_la_runs,
            logic_client=MockLogicClient(),
        )

        # Step 3.1: verify run was flagged in verified_runs
        self.assertEqual(len(correlator.verified_runs), 1)
        vr = correlator.verified_runs[0]
        self.assertEqual(vr["flow_name"], "DYN-PROD-LA-ApplicationUpdater")
        self.assertEqual(vr["source"], "logicapps")
        self.assertIn("crimson_status", vr["updated_fields"])

        # Step 3.2: verify audit item matched
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertEqual(item.matched_run.flow_name, "DYN-PROD-LA-ApplicationUpdater")
    def test_parallel_candidate_verification_and_caching(self):
        target_guid = "99887766-5544-3322-1100-aabbccddeeff"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-par-1",
                    "createdon": "2026-10-01T12:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-par-1",
                },
                "OldValue": {"stage": "1"},
                "NewValue": {"stage": "2"},
            }
        ]

        class MockPaClient:
            def __init__(self):
                self.calls = 0

            def get_flow_run_details(self, env_id, wf_id, rid):
                self.calls += 1
                return {"properties": {"trigger": {"outputs": {"body": {"foo": "bar"}}}}}

            def get_content_from_link(self, link):
                return None

            def get_flow_run_actions(self, env_id, wf_id, rid):
                if rid == "run-match":
                    return [
                        {
                            "name": "Update_Target_Row",
                            "properties": {
                                "status": "Succeeded",
                                "inputs": {"item_id": target_guid, "stage": "2"},
                            },
                        }
                    ]
                return [{"name": "Compose", "properties": {"status": "Succeeded", "inputs": {}}}]

        mock_pa = MockPaClient()
        cf_runs = [
            {
                "flowrunid": f"run-{i}",
                "workflowid": "wf-1",
                "Flow Name": f"Flow {i}",
                "starttime": "2026-10-01T11:58:00Z",
                "endtime": "2026-10-01T12:01:00Z",
                "status": "Succeeded",
            }
            for i in range(12)
        ]
        # Make one of them the matching run
        cf_runs[3]["flowrunid"] = "run-match"

        correlator = AuditProcessCorrelator(environment_id="env-1")
        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=cf_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="application",
            attr_metadata={"stage": "Stage"},
            tolerance_seconds=120,
            only_automations=True,
            target_guids={target_guid},
            pa_client=mock_pa,
            max_workers=4,
        )

        # Verify parallel run completed and found match
        self.assertEqual(len(correlator.verified_runs), 1)
        self.assertEqual(correlator.verified_runs[0]["run_id"], "run-match")
        self.assertEqual(len(correlator.verified_runs_cache), 12)
        self.assertTrue(correlator.verified_runs_cache["run-match"])

        # Second invocation using cache should make ZERO new calls to get_flow_run_details
        initial_calls = mock_pa.calls
        items_second = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=cf_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="application",
            attr_metadata={"stage": "Stage"},
            tolerance_seconds=120,
            only_automations=True,
            target_guids={target_guid},
            pa_client=mock_pa,
            cached_verified_runs=correlator.verified_runs_cache,
            cached_verified_actions=correlator.verified_actions_cache,
            max_workers=4,
        )
        self.assertEqual(mock_pa.calls, initial_calls)
        self.assertEqual(len(items_second), 1)
        self.assertEqual(items_second[0].confidence, "Exact Run Match")

    def test_cloud_flow_actions_inspected_even_if_trigger_differs(self):
        target_guid = "11111111-2222-3333-4444-555555555555"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-1",
                    "createdon": "2026-10-01T12:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "# PowerAutomate",
                    "transactionid": "tx-1",
                },
                "OldValue": {"stage": "Draft"},
                "NewValue": {"stage": "Completed"},
            }
        ]

        class MockPaClientWithActions:
            def __init__(self):
                self.actions_called = False

            def get_flow_run_details(self, env_id, wf_id, rid):
                # Trigger outputs mention a different record GUID, but workflow triggers_on_entity
                return {
                    "properties": {
                        "trigger": {
                            "outputs": {
                                "body": {
                                    "applicationid": "99999999-9999-9999-9999-999999999999"
                                }
                            }
                        }
                    }
                }

            def get_content_from_link(self, link):
                return None

            def get_flow_run_actions(self, env_id, wf_id, rid):
                self.actions_called = True
                return [
                    {
                        "name": "Update_Target_Application",
                        "properties": {
                            "status": "Succeeded",
                            "inputs": {
                                "applicationid": target_guid,
                                "stage": "Completed",
                            },
                        },
                    }
                ]

        mock_pa = MockPaClientWithActions()
        cf_runs = [
            {
                "flowrunid": "run-inspect-all",
                "workflowid": "wf-triggers-on-app",
                "Flow Name": "App Flow",
                "starttime": "2026-10-01T11:59:00Z",
                "endtime": "2026-10-01T12:01:00Z",
                "status": "Succeeded",
            }
        ]

        # Define raw workflow that triggers on the application table
        raw_workflows = [
            {
                "workflowid": "wf-triggers-on-app",
                "name": "App Flow",
                "category": 5,
                "clientdata": '{"properties": {"definition": {"triggers": {"When_a_row_is_added": {"inputs": {"host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"}, "parameters": {"dataset": "default", "table": "application"}}}}}}}',
            }
        ]

        correlator = AuditProcessCorrelator(environment_id="env-1")
        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=raw_workflows,
            flow_runs=cf_runs,
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="application",
            attr_metadata={"stage": "Stage"},
            tolerance_seconds=120,
            only_automations=True,
            target_guids={target_guid},
            pa_client=mock_pa,
            max_workers=4,
        )

        # Confirm actions were downloaded and inspected despite trigger referencing another GUID
        self.assertTrue(mock_pa.actions_called)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].confidence, "Exact Run Match")
        self.assertEqual(items[0].matched_run.run_id, "run-inspect-all")

    def test_logic_app_sas_link_inspected_even_with_inline_metadata(self):
        """Verify that when inline inputs contain only metadata/headers, Azure blob SAS links are still downloaded and inspected."""
        target_guid = "98765432-1111-2222-3333-444455556666"
        target_id_code = "140375569"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-la-sas",
                    "createdon": "2026-10-09T09:40:00Z",
                    "action": 2,
                    "_regardingobjectid_value": target_guid,
                },
                "OldValue": {"crimson_extractedtobanner": None},
                "NewValue": {"crimson_extractedtobanner": "Contact Create Complete"},
            }
        ]

        mock_la = {
            "workflowid": "la-complex-1",
            "name": "DYN-PROD-LA-ContactCreated",
            "category": 6,
            "raw_definition": {
                "properties": {
                    "definition": {
                        "actions": {
                            "Patch_Contact": {
                                "type": "ApiConnection",
                                "inputs": {
                                    "host": {"connection": {"name": "cds"}},
                                    "method": "patch",
                                    "path": "/v2/datasets/crm/tables/contacts/items",
                                }
                            }
                        }
                    }
                }
            }
        }

        mock_runs = [
            {
                "name": "run-sas-01",
                "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Logic/workflows/DYN-PROD-LA-ContactCreated/runs/run-sas-01",
                "flow_name": "DYN-PROD-LA-ContactCreated",
                "properties": {
                    "startTime": "2026-10-09T09:39:44Z",
                    "endTime": "2026-10-09T09:39:48Z",
                    "status": "Succeeded",
                }
            }
        ]

        class MockClientSasCheck:
            def __init__(self):
                self.sas_fetched = False

            def get_run_details(self, rid):
                return {"properties": {"trigger": {"outputs": {"headers": {"Host": "prod"}}}}}

            def get_run_actions(self, rid):
                return [
                    {
                        "name": "Patch_Contact",
                        "properties": {
                            # Inline inputs only has URI/connection metadata - NO target GUID!
                            "inputs": {"host": {"connection": "cds"}, "method": "patch"},
                            "inputsLink": {"uri": "https://sas.blob.core.windows.net/full-body"},
                        }
                    }
                ]

            def get_content_from_link(self, uri):
                self.sas_fetched = True
                return {
                    "CRM_Person_GUID": target_guid,
                    "Student_Number": target_id_code,
                    "crimson_extractedtobanner": "Contact Create Complete",
                }

        client_sas = MockClientSasCheck()
        correlator = AuditProcessCorrelator()
        items = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[mock_la],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"crimson_extractedtobanner": "Extracted to Banner"},
            tolerance_seconds=120,
            only_automations=True,
            target_guids={target_guid, target_id_code},
            logic_runs=mock_runs,
            logic_client=client_sas,
        )

        self.assertTrue(client_sas.sas_fetched, "Expected SAS link to be downloaded even when inline inputs is present!")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].confidence, "Exact Run Match")
        self.assertEqual(items[0].matched_run.flow_name, "DYN-PROD-LA-ContactCreated")

    def test_worker_pool_allocation_logic_apps_vs_cloud_flows(self):
        """Verify that Logic Apps verification receives 20 workers while Cloud Flows receives 4 workers."""
        from unittest.mock import patch

        target_guid = "11111111-2222-3333-4444-555555555555"
        mock_audit = [
            {
                "AuditRecord": {
                    "createdon": "2026-10-09T14:15:00Z",
                    "_userid_value": "Automation",
                    "action": 2,
                },
                "OldValue": {"stage": "1"},
                "NewValue": {"stage": "2"},
            }
        ]
        # One logic app run and one cloud flow run
        mock_la_runs = [
            {
                "id": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Logic/workflows/LA1/runs/la-1",
                "flow_name": "LA1",
                "workflow_id": "/subscriptions/s/resourceGroups/r/providers/Microsoft.Logic/workflows/LA1",
                "properties": {
                    "startTime": "2026-10-09T14:14:50Z",
                    "status": "Succeeded",
                },
            }
        ]
        mock_cf_runs = [
            {
                "flowrunid": "cf-1",
                "Flow Name": "CF1",
                "workflowid": "wf-1",
                "starttime": "2026-10-09T14:14:50Z",
                "status": "Succeeded",
            }
        ]

        class DummyLAClient:
            def get_run_actions(self, *args, **kwargs):
                return []
            def get_sas_blob_content(self, *args, **kwargs):
                return None

        class DummyPAClient:
            def get_flow_run_details(self, *args, **kwargs):
                return {}
            def get_flow_run_actions(self, *args, **kwargs):
                return []

        correlator = AuditProcessCorrelator()

        captured_workers = []
        import concurrent.futures
        orig_executor = concurrent.futures.ThreadPoolExecutor

        def mock_executor_init(max_workers=None, *args, **kwargs):
            captured_workers.append(max_workers)
            return orig_executor(max_workers=max_workers, *args, **kwargs)

        with patch("concurrent.futures.ThreadPoolExecutor", side_effect=mock_executor_init):
            correlator.correlate(
                raw_audit_details=mock_audit,
                raw_workflows=[],
                flow_runs=mock_cf_runs,
                flow_sessions=[],
                async_operations=[],
                target_entity_logical="contact",
                attr_metadata={"stage": "Stage"},
                tolerance_seconds=120,
                only_automations=True,
                target_guids={target_guid},
                logic_runs=mock_la_runs,
                logic_client=DummyLAClient(),
                pa_client=DummyPAClient(),
            )

        # Expected: captured_workers has 20 for Phase 1 (Logic Apps) and 4 for Phase 2 (Cloud Flows)
        self.assertIn(20, captured_workers, "Logic Apps phase should use 20 workers")
        self.assertIn(4, captured_workers, "Cloud Flows phase should use 4 workers")

    def test_portal_webform_session_exact_match(self):
        """Test exact match correlation with a Power Pages webform session."""
        target_guid = "11112222-3333-4444-5555-666677778888"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-portal-1",
                    "createdon": "2026-10-12T14:30:00Z",
                    "_userid_value": "SYSTEM",
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "CRM Portals",
                    "action": 2,  # Update
                    "transactionid": "tx-port-1",
                },
                "OldValue": {"telephone1": "123"},
                "NewValue": {"telephone1": "999"},
            }
        ]

        mock_portal_sessions = [
            {
                "adx_webformsessionid": "sess-guid-1",
                "adx_primaryrecordid": target_guid,
                "createdon": "2026-10-12T14:29:58Z",
                "modifiedon": "2026-10-12T14:30:02Z",
                "_adx_webform_value": "wf-guid-1",
                "_adx_webform_value@OData.Community.Display.V1.FormattedValue": "Profile Update Wizard",
                "_adx_webformstep_value": "step-guid-1",
                "_adx_webformstep_value@OData.Community.Display.V1.FormattedValue": "Contact Info Step",
                "_adx_contact_value@OData.Community.Display.V1.FormattedValue": "John PortalUser",
            }
        ]

        correlator = AuditProcessCorrelator(org_url="https://org.crm.dynamics.com")
        results = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"telephone1": "Phone"},
            tolerance_seconds=60,
            only_automations=True,
            target_guids={target_guid},
            portal_sessions=mock_portal_sessions,
        )

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.confidence, "Exact Run Match")
        self.assertEqual(item.confidence_badge, "🟢")
        self.assertIsNotNone(item.matched_run)
        self.assertEqual(item.matched_run.source, "portals")
        self.assertIn("John PortalUser", item.attribution_summary)
        self.assertIn("Profile Update Wizard", item.attribution_summary)
        self.assertIn("Open Web Form Session", item.process_link_label)

    def test_portal_form_candidate_insert_on_create(self):
        """Test candidate portal form matching on Create event (mode=100000000 Insert)."""
        target_guid = "22223333-4444-5555-6666-777788889999"
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-portal-create",
                    "createdon": "2026-10-12T10:00:00Z",
                    "_userid_value": "Power Pages - Portal User",
                    "action": 1,  # Create
                },
                "OldValue": {},
                "NewValue": {"firstname": "Alice", "lastname": "Smith"},
            }
        ]

        mock_portal_forms = [
            {
                "form_id": "form-insert-1",
                "name": "Registration Basic Form",
                "form_type": "Basic Form",
                "entity_name": "contact",
                "mode": 100000000,  # Insert
                "source_table": "adx_entityforms",
            },
            {
                "form_id": "form-edit-1",
                "name": "Profile Edit Form",
                "form_type": "Basic Form",
                "entity_name": "contact",
                "mode": 100000001,  # Edit
                "source_table": "adx_entityforms",
            },
        ]

        correlator = AuditProcessCorrelator(org_url="https://org.crm.dynamics.com")
        results = correlator.correlate(
            raw_audit_details=mock_audit,
            raw_workflows=[],
            flow_runs=[],
            flow_sessions=[],
            async_operations=[],
            target_entity_logical="contact",
            attr_metadata={"firstname": "First Name", "lastname": "Last Name"},
            tolerance_seconds=60,
            only_automations=True,
            target_guids={target_guid},
            portal_forms=mock_portal_forms,
        )

        self.assertEqual(len(results), 2)
        for item in results:
            self.assertEqual(item.confidence, "Probable Portal Form Match")
            self.assertEqual(item.confidence_badge, "🟡")
            self.assertIsNotNone(item.matched_flow)
            self.assertEqual(item.matched_flow.category, 7)
            self.assertIn("Registration Basic Form", item.matched_flow.name)
            self.assertIn("Insert", item.matched_flow.name)
            self.assertIn("Open Portal Form", item.process_link_label)

    def test_correlate_outbound(self):
        correlator = AuditProcessCorrelator(
            environment_id="env-123",
            org_url="https://org.crm.dynamics.com",
        )

        # Audit mutation on crimson_application modifying crimson_status and crimson_studentid
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-100",
                    "createdon": "2026-10-14T12:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Koon Hung Luk",
                    "transactionid": "tx-100",
                },
                "Field": "crimson_status",
                "OldValue": 1,
                "NewValue": 2,
            },
            {
                "AuditRecord": {
                    "auditid": "aud-100",
                    "createdon": "2026-10-14T12:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Koon Hung Luk",
                    "transactionid": "tx-100",
                },
                "Field": "crimson_studentid",
                "OldValue": None,
                "NewValue": "140375569",
            },
        ]

        # 1. Flow A: Active, triggers on crimson_status, has confirmed downstream run
        flow_a = CorrelatedFlow(
            workflow_id="flow-a",
            name="Notify Student on Status Change",
            category=5,
            category_label="Modern Cloud Flow",
            state_code=1,
            primary_entity="crimson_application",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=["crimson_status"],
        )

        run_a = FlowRunExecution(
            run_id="run-001",
            workflow_id="flow-a",
            flow_name="Notify Student on Status Change",
            started_on=datetime(2026, 10, 14, 12, 0, 5, tzinfo=timezone.utc),
            status="Succeeded",
            source="flowruns",
            duration_str="2.5s",
        )

        # 2. Flow B: Active, triggers on crimson_studentid, has filter conditions, no run logged
        flow_b = CorrelatedFlow(
            workflow_id="flow-b",
            name="Sync to SAP Banner",
            category=5,
            category_label="Modern Cloud Flow",
            state_code=1,
            primary_entity="crimson_application",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=["crimson_studentid"],
            trigger_conditions=["@equals(triggerOutputs()?['body/nuit_requiresreview'], true)"],
        )

        # 3. Flow C: Inactive / Turned Off (state_code=0), triggers on crimson_status
        flow_c = CorrelatedFlow(
            workflow_id="flow-c",
            name="Legacy Welcome Email",
            category=5,
            category_label="Modern Cloud Flow",
            state_code=0,
            primary_entity="crimson_application",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=["crimson_status"],
        )

        # 4. Flow D: Active, triggers on telephone1 only (should NOT match)
        flow_d = CorrelatedFlow(
            workflow_id="flow-d",
            name="Phone Number Validation",
            category=5,
            category_label="Modern Cloud Flow",
            state_code=1,
            primary_entity="crimson_application",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=["telephone1"],
        )

        outbound_items = correlator.correlate_outbound(
            raw_audit_details=mock_audit,
            candidate_flows=[flow_a, flow_b, flow_c, flow_d],
            executions=[run_a],
            target_entity_logical="crimson_application",
            attr_metadata={"crimson_status": "Application Status", "crimson_studentid": "Student ID"},
            tolerance_seconds=45,
        )

        # Flow D should be excluded (telephone1 was not modified)
        self.assertEqual(len(outbound_items), 3)

        # Check Flow A (Confirmed Triggered)
        item_a = next(i for i in outbound_items if i.flow_id == "flow-a")
        self.assertEqual(item_a.qualification_status, "🟢 Confirmed Triggered")
        self.assertEqual(item_a.status_badge, "🟢")
        self.assertEqual(item_a.run_id, "run-001")
        self.assertTrue(item_a.is_active)
        self.assertIn("crimson_status", item_a.matched_columns)

        # Check Flow B (Conditional Candidate)
        item_b = next(i for i in outbound_items if i.flow_id == "flow-b")
        self.assertEqual(item_b.qualification_status, "🟡 Conditional Candidate")
        self.assertEqual(item_b.status_badge, "🟡")
        self.assertEqual(item_b.run_status, "Requires Condition")
        self.assertTrue(item_b.is_active)
        self.assertIn("crimson_studentid", item_b.matched_columns)

        # Check Flow C (Turned Off Alert)
        item_c = next(i for i in outbound_items if i.flow_id == "flow-c")
        self.assertEqual(item_c.qualification_status, "🔴 Suppressed (Turned Off)")
        self.assertEqual(item_c.status_badge, "🔴")
        self.assertFalse(item_c.is_active)
        self.assertIn("statecode=0", item_c.status_reason)

        # Check grouping and HTML table rendering
        grouped = group_outbound_triggers(outbound_items)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].field_count, 2)
        self.assertEqual(len(grouped[0].triggers), 3)
        self.assertEqual(grouped[0].triggered_count, 1)
        self.assertEqual(grouped[0].suppressed_count, 1)

        html = render_outbound_triggers_table_html(grouped)
        self.assertIn("Notify Student on Status Change", html)
        self.assertIn("Legacy Welcome Email", html)
        self.assertIn("Suppressed", html)
        self.assertIn("Triggered", html)

    def test_multi_field_condition_parsing_and_evaluation(self):
        # 1. Single OData clause
        clauses_single = parse_condition_clauses("nuit_reinstateoffer eq true")
        self.assertEqual(len(clauses_single), 1)
        self.assertEqual(clauses_single[0]["field"], "nuit_reinstateoffer")
        self.assertEqual(clauses_single[0]["op"], "eq")
        self.assertEqual(clauses_single[0]["val"], True)

        # 2. Multi-field OData clauses
        multi_odata = "nuit_reinstateoffer eq true and statuscode eq 1 and new_score gt 50"
        clauses_multi = parse_condition_clauses(multi_odata)
        self.assertEqual(len(clauses_multi), 3)
        fields = [c["field"] for c in clauses_multi]
        self.assertIn("nuit_reinstateoffer", fields)
        self.assertIn("statuscode", fields)
        self.assertIn("new_score", fields)

        # 3. Power Automate expression
        pa_expr = "@and(equals(triggerOutputs()?['body/nuit_reinstateoffer'], true), equals(triggerOutputs()?['body/statuscode'], 1))"
        clauses_pa = parse_condition_clauses(pa_expr)
        self.assertEqual(len(clauses_pa), 2)
        pa_fields = [c["field"] for c in clauses_pa]
        self.assertIn("nuit_reinstateoffer", pa_fields)
        self.assertIn("statuscode", pa_fields)

        # 4. Evaluation: All satisfied
        res_met = evaluate_condition_rules(
            filter_rule=multi_odata,
            trigger_conditions=[],
            changed_fields={"nuit_reinstateoffer", "statuscode", "new_score"},
            new_values={"nuit_reinstateoffer": True, "statuscode": 1, "new_score": 75},
        )
        self.assertTrue(res_met["is_satisfied"])
        self.assertFalse(res_met["is_violated"])
        self.assertEqual(len(res_met["unmutated_requirements"]), 0)

        # 5. Evaluation: Contradicted / Violated
        res_violated = evaluate_condition_rules(
            filter_rule=multi_odata,
            trigger_conditions=[],
            changed_fields={"nuit_reinstateoffer", "statuscode", "new_score"},
            new_values={"nuit_reinstateoffer": True, "statuscode": 2, "new_score": 75},
        )
        self.assertFalse(res_violated["is_satisfied"])
        self.assertTrue(res_violated["is_violated"])
        self.assertIn("statuscode", res_violated["evaluation_summary"])

        # 6. Evaluation: Partial / Unmutated dependencies
        res_partial = evaluate_condition_rules(
            filter_rule=multi_odata,
            trigger_conditions=[],
            changed_fields={"nuit_reinstateoffer"},
            new_values={"nuit_reinstateoffer": True},
        )
        self.assertFalse(res_partial["is_satisfied"])
        self.assertFalse(res_partial["is_violated"])
        self.assertEqual(len(res_partial["unmutated_requirements"]), 2)
        self.assertTrue(any("statuscode" in r for r in res_partial["unmutated_requirements"]))

    def test_outbound_deduplication_and_flow_summary(self):
        # Simulate 5 consecutive audit timestamps where 2 flows were evaluated
        timestamps = [
            ("06/10/2026 12:06:51", datetime(2026, 10, 6, 12, 6, 51)),
            ("06/10/2026 12:07:01", datetime(2026, 10, 6, 12, 7, 1)),
            ("06/10/2026 12:07:05", datetime(2026, 10, 6, 12, 7, 5)),
            ("06/10/2026 12:07:07", datetime(2026, 10, 6, 12, 7, 7)),
            ("06/10/2026 12:07:25", datetime(2026, 10, 6, 12, 7, 25)),
        ]

        items = []
        for dt_str, dt_obj in timestamps:
            # Flow 1: Suppressed (Turned off)
            items.append(
                OutboundTriggerItem(
                    audit_id=f"aud-{dt_str}-1",
                    timestamp=dt_obj,
                    changed_date_str=dt_str,
                    user_name="# System",
                    event="Update",
                    changed_fields=["statuscode"],
                    changed_field_labels=["Status Reason"],
                    old_values={},
                    new_values={"statuscode": 1},
                    transaction_id=f"tx-{dt_str}",
                    flow_id="abf70da2-97b4-ea11-a812-000d3a2abf56",
                    flow_name="Clearing - Send Incomplete Email to Student",
                    category=5,
                    category_label="Modern Cloud Flow",
                    is_active=False,
                    state_label="🔴 Turned Off / Draft",
                    qualification_status="🔴 Suppressed (Turned Off)",
                    status_badge="🔴",
                    status_reason="Flow is turned off in Dataverse (statecode=0)",
                    matched_columns=["statuscode"],
                    filter_expression="None (Unconditional)",
                    flow_history_url="https://make.powerautomate.com/flows/abf70da2",
                    run_id="",
                    run_status="Turned Off",
                )
            )
            # Flow 2: Conditional Candidate
            items.append(
                OutboundTriggerItem(
                    audit_id=f"aud-{dt_str}-2",
                    timestamp=dt_obj,
                    changed_date_str=dt_str,
                    user_name="# System",
                    event="Update",
                    changed_fields=["nuit_reinstateoffer"],
                    changed_field_labels=["Reinstate Offer"],
                    old_values={},
                    new_values={"nuit_reinstateoffer": True},
                    transaction_id=f"tx-{dt_str}",
                    flow_id="0d065c4b-2a4f-ef11-a316-6045bd0d4a30",
                    flow_name="PG Reset Applicant Response",
                    category=5,
                    category_label="Modern Cloud Flow",
                    is_active=True,
                    state_label="Active",
                    qualification_status="🟡 Conditional Candidate",
                    status_badge="🟡",
                    status_reason="Requires existing state: nuit_reinstateoffer eq true",
                    matched_columns=["nuit_reinstateoffer"],
                    filter_expression="nuit_reinstateoffer eq true",
                    flow_history_url="https://make.powerautomate.com/flows/0d065c4b",
                    run_id="",
                    run_status="Requires Condition",
                )
            )

        self.assertEqual(len(items), 10)

        # Deduplicate by flow
        flow_summaries = group_outbound_by_flow(items)
        # MUST reduce to exactly 2 unique flows!
        self.assertEqual(len(flow_summaries), 2)

        summary_suppressed = next(s for s in flow_summaries if s.flow_id == "abf70da2-97b4-ea11-a812-000d3a2abf56")
        self.assertEqual(summary_suppressed.flow_name, "Clearing - Send Incomplete Email to Student")
        self.assertEqual(summary_suppressed.event_count, 5)
        self.assertFalse(summary_suppressed.is_active)
        self.assertEqual(summary_suppressed.qualification_status, "🔴 Suppressed (Turned Off)")
        self.assertEqual(len(summary_suppressed.timestamps), 5)

        summary_conditional = next(s for s in flow_summaries if s.flow_id == "0d065c4b-2a4f-ef11-a316-6045bd0d4a30")
        self.assertEqual(summary_conditional.flow_name, "PG Reset Applicant Response")
        self.assertEqual(summary_conditional.event_count, 5)
        self.assertTrue(summary_conditional.is_active)
        self.assertEqual(summary_conditional.qualification_status, "🟡 Conditional Candidate")

        # Verify HTML rendering of FlowOutboundSummary list
        html = render_outbound_triggers_table_html(flow_summaries)
        self.assertIn("Flow / Process Name", html)
        self.assertIn("Impacted Mutations", html)
        self.assertIn("Clearing - Send Incomplete Email to Student", html)
        self.assertIn("PG Reset Applicant Response", html)
        self.assertIn("5 events", html)
        self.assertIn("nuit_reinstateoffer eq true", html)

    def test_outbound_focus_keywords_and_plural_entity(self):
        correlator = AuditProcessCorrelator(environment_id="env-123", org_url="https://org.crm.dynamics.com")

        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-interview-1",
                    "createdon": "2026-10-13T10:05:08Z",
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "John Admissions",
                    "action": 2,
                    "transactionid": "tx-interview-1",
                },
                "OldValue": {"crimson_interview": 143570000},
                "NewValue": {"crimson_interview": 321000002},
            }
        ]

        flow_row = {
            "workflowid": "b88f6f9c-276d-eb11-a812-000d3a498ef9",
            "name": "Interview Management - Send Email to Student to Book Interview",
            "category": 5,
            "statecode": 0,
            "primaryentity": "none",
            "clientdata": json.dumps({
                "properties": {
                    "definition": {
                        "triggers": {
                            "When_a_row_is_added,_modified_or_deleted": {
                                "type": "OpenApiConnectionWebhook",
                                "inputs": {
                                    "host": {"operationId": "SubscribeWebhookTrigger"},
                                    "parameters": {
                                        "subscriptionRequest/entityname": "crimson_choices",
                                        "subscriptionRequest/message": 3,
                                        "subscriptionRequest/filteringattributes": ["crimson_interview"],
                                    },
                                },
                            }
                        }
                    }
                }
            }),
        }

        # 1. extract_flow_definition matches target entity 'crimson_choice' via plural handling
        pf = extract_flow_definition(flow_row, "crimson_choice")
        self.assertTrue(pf.triggers_on_entity)
        self.assertIn("crimson_interview", pf.trigger_attributes)

        # 2. correlate_outbound evaluates the flow and flags it as Suppressed (Turned Off)
        items = correlator.correlate_outbound(
            raw_audit_details=mock_audit,
            candidate_flows=[pf],
            target_entity_logical="crimson_choice",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].flow_name, "Interview Management - Send Email to Student to Book Interview")
        self.assertEqual(items[0].qualification_status, "🔴 Suppressed (Turned Off)")
        self.assertIn("crimson_interview", items[0].matched_columns)

    def test_outbound_lookup_attribute_matching(self):
        """Verify that mutations on lookup fields (e.g. _parentcustomerid_value) match flows triggered on parentcustomerid."""
        correlator = AuditProcessCorrelator(environment_id="env-123")
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-lookup-1",
                    "createdon": "2026-10-10T10:00:00Z",
                    "action": 2,  # Update
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Sales Rep",
                    "transactionid": "tx-lk-1",
                },
                "OldValue": {"_parentcustomerid_value": "11111111-2222-3333-4444-555555555555"},
                "NewValue": {"_parentcustomerid_value": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
            }
        ]

        flow_def = CorrelatedFlow(
            workflow_id="wf-lookup",
            name="Account Parent Changed Flow",
            category=5,
            category_label="Cloud Flow",
            state_code=1,
            primary_entity="contact",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=["parentcustomerid"],
        )

        items = correlator.correlate_outbound(
            raw_audit_details=mock_audit,
            candidate_flows=[flow_def],
            target_entity_logical="contact",
        )
        self.assertEqual(len(items), 1)
        self.assertIn("parentcustomerid", items[0].matched_columns)
        self.assertEqual(items[0].qualification_status, "🔵 Qualified to Trigger")

    def test_outbound_action_code_mapping_deactivate_and_assign(self):
        """Verify that Deactivate (action 5) and Assign (action 10) map to Update flow triggers."""
        correlator = AuditProcessCorrelator(environment_id="env-123")
        mock_audit_deact = [
            {
                "AuditRecord": {
                    "auditid": "aud-deact-1",
                    "createdon": "2026-10-10T10:00:00Z",
                    "action": 5,  # Deactivate
                    "operation": 2,  # Update
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Manager",
                    "transactionid": "tx-deact",
                },
                "OldValue": {"statecode": 0, "statuscode": 1},
                "NewValue": {"statecode": 1, "statuscode": 2},
            }
        ]
        mock_audit_assign = [
            {
                "AuditRecord": {
                    "auditid": "aud-assign-1",
                    "createdon": "2026-10-10T10:05:00Z",
                    "action": 10,  # Assign
                    "operation": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Manager",
                    "transactionid": "tx-assign",
                },
                "OldValue": {"_ownerid_value": "11111111-1111-1111-1111-111111111111"},
                "NewValue": {"_ownerid_value": "22222222-2222-2222-2222-222222222222"},
            }
        ]

        flow_update = CorrelatedFlow(
            workflow_id="wf-upd",
            name="General Update Flow",
            category=5,
            category_label="Cloud Flow",
            state_code=1,
            primary_entity="opportunity",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=[],  # All columns
        )

        items_deact = correlator.correlate_outbound(
            raw_audit_details=mock_audit_deact,
            candidate_flows=[flow_update],
            target_entity_logical="opportunity",
        )
        self.assertEqual(len(items_deact), 1)
        self.assertEqual(items_deact[0].qualification_status, "🔵 Qualified to Trigger")

        items_assign = correlator.correlate_outbound(
            raw_audit_details=mock_audit_assign,
            candidate_flows=[flow_update],
            target_entity_logical="opportunity",
        )
        self.assertEqual(len(items_assign), 1)
        self.assertEqual(items_assign[0].qualification_status, "🔵 Qualified to Trigger")

    def test_outbound_condition_rules_evaluation(self):
        """Verify that condition clauses evaluate correctly against audit mutation values."""
        correlator = AuditProcessCorrelator(environment_id="env-123")
        mock_audit = [
            {
                "AuditRecord": {
                    "auditid": "aud-cond-1",
                    "createdon": "2026-10-10T10:00:00Z",
                    "action": 2,
                    "_userid_value@OData.Community.Display.V1.FormattedValue": "Sales Rep",
                    "transactionid": "tx-cond-1",
                },
                "OldValue": {"statuscode": 1},
                "NewValue": {"statuscode": 100000000},
            }
        ]

        flow_satisfied = CorrelatedFlow(
            workflow_id="wf-cond-met",
            name="Status 100000000 Flow",
            category=5,
            category_label="Cloud Flow",
            state_code=1,
            primary_entity="lead",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_conditions=["@equals(triggerOutputs()?['body/statuscode'], 100000000)"],
        )

        items = correlator.correlate_outbound(
            raw_audit_details=mock_audit,
            candidate_flows=[flow_satisfied],
            target_entity_logical="lead",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].qualification_status, "🟢 Condition Satisfied")

        # Now test when condition rule field was NOT touched in the mutation
        flow_unrelated_rule = CorrelatedFlow(
            workflow_id="wf-unrelated-rule",
            name="VIP Customer Lead Flow",
            category=5,
            category_label="Cloud Flow",
            state_code=1,
            primary_entity="lead",
            triggers_on_entity=True,
            trigger_events=["Update"],
            trigger_attributes=[],  # All columns
            trigger_conditions=["@equals(triggerOutputs()?['body/is_vip'], true)"],
        )

        items_unrel = correlator.correlate_outbound(
            raw_audit_details=mock_audit,
            candidate_flows=[flow_unrelated_rule],
            target_entity_logical="lead",
        )
        self.assertEqual(len(items_unrel), 1)
        self.assertEqual(items_unrel[0].qualification_status, "🟡 Conditional Candidate")

    def test_group_outbound_by_flow_representative_status_reason(self):
        """Verify that FlowOutboundSummary selects the status_reason and run link from the confirmed item."""
        dt1 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 10, 10, 10, 15, 0, tzinfo=timezone.utc)

        # Mutation 1: Flow qualified, but no run logged in window
        item1 = OutboundTriggerItem(
            audit_id="aud-1",
            transaction_id="tx-1",
            timestamp=dt1,
            changed_date_str="2026-10-10 10:00:00",
            user_name="User A",
            event="Update",
            changed_fields=["totalamount"],
            changed_field_labels=["Total Amount"],
            old_values={"totalamount": 100},
            new_values={"totalamount": 200},
            flow_id="wf-1",
            flow_name="Order Process Flow",
            category=5,
            category_label="Automated Flow",
            is_active=True,
            state_label="Active",
            qualification_status="🔵 Qualified to Trigger",
            status_badge="🔵",
            status_reason="No run logged within 45s",
            matched_columns=["totalamount"],
        )

        # Mutation 2: Flow confirmed triggered with actual run
        mock_run = FlowRunExecution(
            run_id="run-order-99",
            workflow_id="wf-1",
            flow_name="Order Process Flow",
            started_on=dt2,
            completed_on=dt2,
            status="Succeeded",
            source="flowruns",
            duration_str="2.1s",
            maker_url="https://make.powerautomate.com/run-order-99",
        )

        item2 = OutboundTriggerItem(
            audit_id="aud-2",
            transaction_id="tx-2",
            timestamp=dt2,
            changed_date_str="2026-10-10 10:15:00",
            user_name="User B",
            event="Update",
            changed_fields=["totalamount"],
            changed_field_labels=["Total Amount"],
            old_values={"totalamount": 200},
            new_values={"totalamount": 500},
            flow_id="wf-1",
            flow_name="Order Process Flow",
            category=5,
            category_label="Automated Flow",
            is_active=True,
            state_label="Active",
            qualification_status="🟢 Confirmed Triggered",
            status_badge="🟢",
            status_reason="Confirmed triggered run #run-order-99 (Succeeded, 2.1s)",
            matched_columns=["totalamount"],
            matched_run=mock_run,
            process_url=mock_run.maker_url,
        )

        summaries = group_outbound_by_flow([item1, item2])
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary.qualification_status, "🟢 Confirmed Triggered")
        # Ensure status_reason is taken from confirmed item, not the first item!
        self.assertIn("Confirmed triggered run", summary.status_reason)
        self.assertEqual(summary.process_url, "https://make.powerautomate.com/run-order-99")
        self.assertEqual(summary.mutation_count, 2)


if __name__ == "__main__":
    unittest.main()





