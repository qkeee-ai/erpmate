"""scripts/qkeee_erp_weekly_request_report.py: a no_agent cron script
that reports tasks-received-vs-completed and agent performance by email
and/or Google Chat webhook (agents issue 11). Mirrors
tests/test_lcm_corruption_check.py's shape.

Run with `python -m pytest tests` from the repo root.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "qkeee_erp_weekly_request_report", REPO / "scripts" / "qkeee_erp_weekly_request_report.py")
report_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report_mod)

from qkeee_erp.core import request_registry  # noqa: E402  (path set up by the module above)


class ReadEnvFileTests(unittest.TestCase):
    def test_parses_key_value_lines(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data='EMAIL_ADDRESS=bot@example.com\n# comment\n\nEMAIL_SMTP_HOST="smtp.example.com"\n')):
            env = report_mod.read_env_file("/x/.env")
        self.assertEqual(env["EMAIL_ADDRESS"], "bot@example.com")
        self.assertEqual(env["EMAIL_SMTP_HOST"], "smtp.example.com")

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(report_mod.read_env_file("/does/not/exist.env"), {})


class AdminEmailsAndWebhooksTests(unittest.TestCase):
    def test_dedupes_across_tags_and_separates_kinds(self):
        fake_env = {
            "QKEEE_ERP_PROD_ADMIN_EMAIL": "admin@example.com",
            "QKEEE_ERP_DEV_ADMIN_EMAIL": "admin@example.com",
            "QKEEE_ERP_PROD_OPS_CHAT_WEBHOOK": "https://chat.googleapis.com/x",
            "QKEEE_ERP_PROD_BASE_URL": "https://erp.example",
        }
        with patch.object(report_mod, "read_env_file", return_value=fake_env):
            emails, webhooks = report_mod.admin_emails_and_webhooks()
        self.assertEqual(emails, ["admin@example.com"])
        self.assertEqual(webhooks, ["https://chat.googleapis.com/x"])


class StateDbPathTests(unittest.TestCase):
    def test_defaults_to_sibling_of_hermes_home(self):
        with patch.dict(os.environ, {"HERMES_HOME": "/home/x", "HERMES_STATE_DB_PATH": ""}):
            self.assertEqual(report_mod.state_db_path(), os.path.join("/home/x", "state.db"))

    def test_explicit_override_wins(self):
        with patch.dict(os.environ, {"HERMES_STATE_DB_PATH": "/custom/state.db"}):
            self.assertEqual(report_mod.state_db_path(), "/custom/state.db")


class BuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def test_fresh_profile_with_no_store_is_silent(self):
        with patch.dict(os.environ, {"HERMES_HOME": self.home}):
            self.assertEqual(report_mod.build(), "")

    def test_an_existing_store_with_zero_activity_still_reports(self):
        # Issue 11 §5: unlike lcm_corruption_check's "silent when healthy",
        # a week with zero activity is itself worth one line, not a skip.
        store = request_registry.default_store_path(self.home)
        request_registry.open_request(store, "a@org.com", "t", {"platform": "email",
                                       "chat_id": "a@org.com", "thread_id": None, "session_id": None})
        with patch.dict(os.environ, {"HERMES_HOME": self.home}):
            report = report_mod.build()
        self.assertIn("Weekly request report", report)

    def test_build_advances_the_cost_checkpoint(self):
        store = request_registry.default_store_path(self.home)
        request_registry.open_request(store, "a@org.com", "t", {"platform": "email",
                                       "chat_id": "a@org.com", "thread_id": None,
                                       "session_id": "s1"})
        with patch.dict(os.environ, {"HERMES_HOME": self.home}):
            report_mod.build()
        # No state.db exists, so there is no usage to checkpoint — the call
        # must not raise either way.
        self.assertEqual(request_registry.get_cost_checkpoints(store, ["s1"]), {})


class MainDeliveryTests(unittest.TestCase):
    def test_no_report_sends_nothing(self):
        with patch.object(report_mod, "build", return_value=""), \
             patch.object(report_mod, "send_email") as send_email, \
             patch.object(report_mod, "send_chat") as send_chat:
            report_mod.main()
        send_email.assert_not_called()
        send_chat.assert_not_called()

    def test_report_with_no_recipients_skips_delivery_without_crashing(self):
        with patch.object(report_mod, "build", return_value="trouble"), \
             patch.object(report_mod, "admin_emails_and_webhooks", return_value=([], [])), \
             patch.object(report_mod, "send_email") as send_email, \
             patch.object(report_mod, "send_chat") as send_chat:
            report_mod.main()
        send_email.assert_not_called()
        send_chat.assert_not_called()

    def test_report_dispatches_to_configured_channels(self):
        with patch.object(report_mod, "build", return_value="the report"), \
             patch.object(report_mod, "admin_emails_and_webhooks",
                          return_value=(["admin@example.com"], ["https://chat.googleapis.com/x"])), \
             patch.object(report_mod, "send_email") as send_email, \
             patch.object(report_mod, "send_chat") as send_chat:
            report_mod.main()
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args[0][0], ["admin@example.com"])
        send_chat.assert_called_once_with(["https://chat.googleapis.com/x"], "the report")


if __name__ == "__main__":
    unittest.main()
