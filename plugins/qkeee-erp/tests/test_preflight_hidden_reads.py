"""Preflight and init-bot: reads ERPNext does inside its own checks (HRMS
demo run review, issue 08).

Company create returned 403 on GST Settings: India Compliance reads it
while it validates the Company, with the user's rights. Preflight said
bot_ready. doctype_defs.HIDDEN_READS lists such reads per installed app."""

import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp import init_bot
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
import test_discover
from test_init_bot_provisioning import AllBut, bundle

GAP = {"capability": "hidden_read:Company.GST Settings", "doctype": "GST Settings"}
COMPANY_META = {"doctype": "Company", "module": "Setup", "fields": [], "active_workflows": [],
                "conditional_mandatory": [], "custom_fields_merged": True}


class PreflightHiddenReadTests(unittest.TestCase):
    run_preflight = test_discover.PreflightTests.run_preflight
    READY = test_discover.PreflightTests.READY
    REQ = test_discover.PreflightTests.REQ

    def _run(self, apps, probe_result):
        with patch.object(core_client, "probe_bot_hidden_reads",
                          return_value=probe_result) as probe:
            result = self.run_preflight(
                meta=dict(COMPANY_META), payload={}, doctype="Company",
                modules={"apps_seen_via_modules": apps, "modules": [], "has_more": False})
        return result, probe

    def test_missing_hidden_read_with_the_app_installed_is_a_blocker(self):
        result, probe = self._run(["erpnext", "frappe", "india_compliance"], [GAP])
        self.assertFalse(result["ready"])
        self.assertIn(GAP, result["gaps"])
        self.assertTrue(any("GST Settings" in b for b in result["blockers"]))
        self.assertEqual(probe.call_args.args[1:], ("Company", {"GST Settings": "india_compliance"}))

    def test_hidden_read_present_is_ready(self):
        result, _ = self._run(["erpnext", "frappe", "india_compliance"], [])
        self.assertTrue(result["ready"], result["blockers"])

    def test_without_the_app_there_is_no_probe(self):
        result, probe = self._run(["erpnext", "frappe"], [GAP])
        self.assertFalse(probe.called)
        self.assertTrue(result["ready"], result["blockers"])


class ProbeTests(unittest.TestCase):

    def test_gap_names_the_app_and_init_bot(self):
        cfg = {"tag": "t", "base_url": "https://erp.example"}
        denied = core_client.ERPNextAPIError(403, "GET", "/api/resource/GST Settings", cfg, "")
        with patch.object(core_client, "get_env_config", return_value=cfg), \
                patch.object(core_client, "_bot_identity", return_value={"user": "bot@x.com"}), \
                patch.object(core_client, "_request", side_effect=denied):
            gaps = core_client.probe_bot_hidden_reads("t", "Company",
                                                      {"GST Settings": "india_compliance"})
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["doctype"], "GST Settings")
        self.assertIn("india_compliance", gaps[0]["effect"])
        self.assertIn("init-bot", gaps[0]["grant_steps"])


class InitBotTests(unittest.TestCase):

    def _matrix(self, missing=()):
        with patch.object(init_bot, "provisionable_domains", return_value={"accounts": ["Company"]}), \
                patch.object(init_bot, "_doctype_names", return_value=AllBut(missing)), \
                patch.object(init_bot, "_role_permissions", return_value=[]), \
                patch.object(init_bot, "_meta_bundle", side_effect=lambda tag, dt: bundle(dt)):
            return init_bot.bot_permission_matrix("qa", ["accounts"])

    def test_init_bot_plans_read_on_the_hidden_read(self):
        matrix = self._matrix()[0]
        self.assertEqual(matrix["GST Settings"], {"read"})

    def test_without_the_app_its_doctype_is_not_planned(self):
        matrix, _, not_found, _ = self._matrix(missing={"GST Settings"})
        self.assertNotIn("GST Settings", matrix)
        self.assertIn("GST Settings", not_found)


if __name__ == "__main__":
    unittest.main()
