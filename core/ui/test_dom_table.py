"""Unit tests for DOM table link/button column configuration."""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd

from core.ui.dom_table import render_dom_table


class TestDomTableColumnConfig(unittest.TestCase):
    @patch("core.ui.dom_table.st")
    def test_dom_table_button_column(self, mock_st):
        mock_st.columns.side_effect = lambda spec, **kw: [MagicMock() for _ in range(len(spec) if isinstance(spec, list) else spec)]
        mock_st.text_input.return_value = ""
        mock_st.selectbox.return_value = "20"
        mock_st.session_state = {}

        df = pd.DataFrame([
            {
                "Flow Name": "Test Flow",
                "status": "Succeeded",
                "🔗 Run": "https://make.powerautomate.com/environments/env1/flows/flow1/runs/run1",
            }
        ])

        render_dom_table(
            df,
            key_prefix="test_dom",
            columns=[("Flow Name", "Flow Name"), ("status", "Status"), ("🔗 Run", "Open Run")],
            column_config={
                "🔗 Run": {"type": "button", "text": "Open Run", "icon": "↗"},
            },
        )

        # Check that st.markdown was called with a link styled as a button
        rendered_htmls = [
            call.args[0] for call in mock_st.markdown.call_args_list if call.args and isinstance(call.args[0], str)
        ]
        button_rendered = any(
            "href='https://make.powerautomate.com/environments/env1/flows/flow1/runs/run1'" in h
            and "Open Run ↗" in h
            and "display: inline-flex" in h
            for h in rendered_htmls
        )
        self.assertTrue(button_rendered, f"Expected button link in rendered HTMLs, got: {rendered_htmls}")

    @patch("core.ui.dom_table.st")
    def test_dom_table_empty_url_fallback(self, mock_st):
        mock_st.columns.side_effect = lambda spec, **kw: [MagicMock() for _ in range(len(spec) if isinstance(spec, list) else spec)]
        mock_st.text_input.return_value = ""
        mock_st.selectbox.return_value = "20"
        mock_st.session_state = {}

        df = pd.DataFrame([
            {
                "Flow Name": "Empty Link Flow",
                "🔗 Run": "",
            }
        ])

        render_dom_table(
            df,
            key_prefix="test_dom_empty",
            columns=[("Flow Name", "Flow Name"), ("🔗 Run", "Open Run")],
            column_config={
                "Open Run": {"type": "link", "text": "Open Run"},
            },
        )

        rendered_htmls = [
            call.args[0] for call in mock_st.markdown.call_args_list if call.args and isinstance(call.args[0], str)
        ]
        dash_rendered = any("—" in h for h in rendered_htmls)
        self.assertTrue(dash_rendered, f"Expected em-dash for empty link in: {rendered_htmls}")

    @patch("core.ui.dom_table.st")
    def test_dom_table_badge_status_colors(self, mock_st):
        mock_st.columns.side_effect = lambda spec, **kw: [MagicMock() for _ in range(len(spec) if isinstance(spec, list) else spec)]
        mock_st.text_input.return_value = ""
        mock_st.selectbox.return_value = "20"
        mock_st.session_state = {}

        df = pd.DataFrame([
            {"flow": "Flow A", "status": "Succeeded"},
            {"flow": "Flow B", "status": "Cancelled"},
            {"flow": "Flow C", "status": "Failed"},
            {"flow": "Flow D", "status": "Running"},
        ])

        render_dom_table(
            df,
            key_prefix="test_dom_badges",
            columns=[("flow", "Flow"), ("status", "Status")],
            column_config={
                "status": {"type": "badge"},
            },
        )

        rendered_htmls = [
            call.args[0] for call in mock_st.markdown.call_args_list if call.args and isinstance(call.args[0], str)
        ]

        # Succeeded -> green (#22c55e)
        succeeded_badge = any("Succeeded" in h and "#22c55e" in h for h in rendered_htmls)
        self.assertTrue(succeeded_badge, "Expected green badge for Succeeded")

        # Cancelled -> orange (#f59e0b)
        cancelled_badge = any("Cancelled" in h and "#f59e0b" in h for h in rendered_htmls)
        self.assertTrue(cancelled_badge, "Expected orange badge for Cancelled")

        # Failed -> red (#ef4444)
        failed_badge = any("Failed" in h and "#ef4444" in h for h in rendered_htmls)
        self.assertTrue(failed_badge, "Expected red badge for Failed")

        # Running -> blue (#3b82f6)
        running_badge = any("Running" in h and "#3b82f6" in h for h in rendered_htmls)
        self.assertTrue(running_badge, "Expected blue badge for Running")


if __name__ == "__main__":
    unittest.main()
