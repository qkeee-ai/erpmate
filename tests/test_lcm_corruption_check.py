"""scripts/lcm_corruption_check.py: a no_agent cron script (and boot-time
check) that runs adminops/lcm-db-recover.py diagnose/rebuild against this
profile's lcm.db and reports a finding by email and/or Google Chat
webhook. See skills/adminops/lcm-self-heal/SKILL.md for the scope
boundary this script shares (diagnose + rebuild only, never swap).

Run with `python -m pytest tests` from the repo root.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "lcm_corruption_check", REPO / "scripts" / "lcm_corruption_check.py")
check_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_mod)


class ReadEnvFileTests(unittest.TestCase):

    def test_parses_key_value_lines(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data='EMAIL_ADDRESS=bot@example.com\n# comment\n\nEMAIL_SMTP_HOST="smtp.example.com"\n')):
            env = check_mod.read_env_file("/x/.env")
        self.assertEqual(env["EMAIL_ADDRESS"], "bot@example.com")
        self.assertEqual(env["EMAIL_SMTP_HOST"], "smtp.example.com")

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(check_mod.read_env_file("/does/not/exist.env"), {})


class AdminEmailsAndWebhooksTests(unittest.TestCase):

    def test_dedupes_across_tags_and_separates_kinds(self):
        fake_env = {
            "QKEEE_ERP_PROD_ADMIN_EMAIL": "admin@example.com",
            "QKEEE_ERP_DEV_ADMIN_EMAIL": "admin@example.com",  # duplicate value
            "QKEEE_ERP_PROD_OPS_CHAT_WEBHOOK": "https://chat.googleapis.com/x",
            "QKEEE_ERP_PROD_BASE_URL": "https://erp.example",  # neither suffix
            "QKEEE_ERP_DEV_ADMIN_API_KEY": "secret",  # must not match _ADMIN_EMAIL
        }
        with patch.object(check_mod, "read_env_file", return_value=fake_env):
            emails, webhooks = check_mod.admin_emails_and_webhooks()
        self.assertEqual(emails, ["admin@example.com"])
        self.assertEqual(webhooks, ["https://chat.googleapis.com/x"])

    def test_empty_env_returns_empty_lists(self):
        with patch.object(check_mod, "read_env_file", return_value={}):
            self.assertEqual(check_mod.admin_emails_and_webhooks(), ([], []))


class SmtpSettingsTests(unittest.TestCase):

    def test_missing_required_field_returns_empty(self):
        self.assertEqual(check_mod._smtp_settings({"EMAIL_ADDRESS": "a@example.com"}), {})

    def test_valid_settings_default_port_and_security(self):
        cfg = check_mod._smtp_settings({
            "EMAIL_ADDRESS": "a@example.com", "EMAIL_PASSWORD": "x", "EMAIL_SMTP_HOST": "smtp.x.com"})
        self.assertEqual(cfg["port"], 587)
        self.assertEqual(cfg["security"], "starttls")
        self.assertTrue(cfg["verify"])

    def test_port_465_defaults_security_to_tls(self):
        cfg = check_mod._smtp_settings({
            "EMAIL_ADDRESS": "a@example.com", "EMAIL_PASSWORD": "x", "EMAIL_SMTP_HOST": "smtp.x.com",
            "EMAIL_SMTP_PORT": "465"})
        self.assertEqual(cfg["security"], "tls")

    def test_invalid_security_returns_empty(self):
        cfg = check_mod._smtp_settings({
            "EMAIL_ADDRESS": "a@example.com", "EMAIL_PASSWORD": "x", "EMAIL_SMTP_HOST": "smtp.x.com",
            "EMAIL_SMTP_SECURITY": "bogus"})
        self.assertEqual(cfg, {})

    def test_non_numeric_port_returns_empty(self):
        cfg = check_mod._smtp_settings({
            "EMAIL_ADDRESS": "a@example.com", "EMAIL_PASSWORD": "x", "EMAIL_SMTP_HOST": "smtp.x.com",
            "EMAIL_SMTP_PORT": "not-a-number"})
        self.assertEqual(cfg, {})


class CheckTests(unittest.TestCase):

    def test_missing_db_is_not_a_finding(self):
        with patch.object(check_mod.os.path, "isfile", return_value=False):
            self.assertEqual(check_mod.check(), "")

    def test_missing_tool_is_reported(self):
        def isfile_side_effect(path):
            return path == check_mod.hermes_lcm_db_path()
        with patch.object(check_mod.os.path, "isfile", side_effect=isfile_side_effect):
            report = check_mod.check()
        self.assertIn("is missing", report)

    def test_healthy_diagnose_is_silent(self):
        with patch.object(check_mod.os.path, "isfile", return_value=True), \
             patch.object(check_mod, "run_tool", return_value=(0, "verdict: healthy")) as run_tool:
            self.assertEqual(check_mod.check(), "")
        run_tool.assert_called_once()
        self.assertEqual(run_tool.call_args[0][0], "diagnose")

    def test_fts_only_damage_skips_rebuild(self):
        diag_out = "  only FTS index damaged - try `/lcm doctor repair apply` first"
        with patch.object(check_mod.os.path, "isfile", return_value=True), \
             patch.object(check_mod, "run_tool", return_value=(1, diag_out)) as run_tool:
            report = check_mod.check()
        run_tool.assert_called_once()  # rebuild never invoked
        self.assertIn("try `/lcm doctor repair apply`", report)

    def test_base_data_damage_runs_rebuild_and_reports_candidate(self):
        diag_out = "  base data damaged - stop the gateway, then run `rebuild`"
        rebuild_out = "RESULT: PASS"
        calls = [(1, diag_out), (0, rebuild_out)]
        with patch.object(check_mod.os.path, "isfile", return_value=True), \
             patch.object(check_mod.glob, "glob", return_value=[]), \
             patch.object(check_mod, "run_tool", side_effect=lambda *a: calls.pop(0)) as run_tool:
            report = check_mod.check()
        self.assertEqual(run_tool.call_count, 2)
        self.assertEqual(run_tool.call_args_list[1][0][0], "rebuild")
        self.assertIn("Candidate ready at", report)
        self.assertIn("RESULT: PASS", report)

    def test_failed_rebuild_warns_against_swapping(self):
        diag_out = "  base data damaged - stop the gateway, then run `rebuild`"
        calls = [(1, diag_out), (1, "RESULT: FAIL")]
        with patch.object(check_mod.os.path, "isfile", return_value=True), \
             patch.object(check_mod.glob, "glob", return_value=[]), \
             patch.object(check_mod, "run_tool", side_effect=lambda *a: calls.pop(0)):
            report = check_mod.check()
        self.assertIn("do not swap anything in", report)

    def test_stale_candidates_are_cleaned_up_before_rebuild(self):
        diag_out = "  base data damaged - stop the gateway, then run `rebuild`"
        calls = [(1, diag_out), (0, "RESULT: PASS")]
        with patch.object(check_mod.os.path, "isfile", return_value=True), \
             patch.object(check_mod.glob, "glob", return_value=["/tmp/lcm-clean-autocheck-old.db"]), \
             patch.object(check_mod.os, "remove") as remove, \
             patch.object(check_mod, "run_tool", side_effect=lambda *a: calls.pop(0)):
            check_mod.check()
        remove.assert_called_once_with("/tmp/lcm-clean-autocheck-old.db")


class MainDeliveryTests(unittest.TestCase):

    def test_no_report_sends_nothing(self):
        with patch.object(check_mod, "check", return_value=""), \
             patch.object(check_mod, "send_email") as send_email, \
             patch.object(check_mod, "send_chat") as send_chat:
            check_mod.main()
        send_email.assert_not_called()
        send_chat.assert_not_called()

    def test_report_with_no_recipients_skips_delivery_without_crashing(self):
        with patch.object(check_mod, "check", return_value="trouble"), \
             patch.object(check_mod, "admin_emails_and_webhooks", return_value=([], [])), \
             patch.object(check_mod, "send_email") as send_email, \
             patch.object(check_mod, "send_chat") as send_chat:
            check_mod.main()
        send_email.assert_not_called()
        send_chat.assert_not_called()

    def test_report_dispatches_to_configured_channels(self):
        with patch.object(check_mod, "check", return_value="trouble"), \
             patch.object(check_mod, "admin_emails_and_webhooks",
                          return_value=(["admin@example.com"], ["https://chat.googleapis.com/x"])), \
             patch.object(check_mod, "send_email") as send_email, \
             patch.object(check_mod, "send_chat") as send_chat:
            check_mod.main()
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args[0][0], ["admin@example.com"])
        send_chat.assert_called_once_with(["https://chat.googleapis.com/x"], "trouble")


class SendChatTests(unittest.TestCase):

    def test_one_webhook_failure_does_not_raise_or_stop_others(self):
        good, bad = MagicMock(), MagicMock()
        good.close = MagicMock()
        with patch.object(check_mod.urllib.request, "urlopen",
                          side_effect=[check_mod.urllib.error.URLError("boom"), good]) as urlopen:
            check_mod.send_chat(["https://a", "https://b"], "hello")
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
