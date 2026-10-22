"""Confirmation lifetime per environment (HRMS demo run review, issue 01, D1).

`QKEEE_ERP_<TAG>_ENVIRONMENT`: `non-production` gives a 60-minute
confirmation; unset or any other value is production, 15 minutes. Every
render returns `expires_at` and `ttl_minutes`; `health` shows both."""

import datetime
import time
import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import connector

ARGS = {"doctype": "Sales Order", "action": "submit", "name": "SO-1"}


def env(value=None):
    values = {} if value is None else {"QKEEE_ERP_TEST_ENVIRONMENT": value}
    return patch.object(core_client, "_qkeee_env", return_value=values)


class LifetimeTests(unittest.TestCase):

    def test_unset_environment_is_production_15_minutes(self):
        with env():
            self.assertEqual(core_client.tag_environment("test"), "production")
            self.assertEqual(core_client.token_ttl_seconds("test"), 900)

    def test_non_production_is_60_minutes(self):
        with env("non-production"):
            self.assertEqual(core_client.tag_environment("test"), "non-production")
            self.assertEqual(core_client.token_ttl_seconds("test"), 3600)

    def test_any_other_value_is_production(self):
        for value in ("staging", "Non-Production ", "nonprod", "3600", ""):
            with self.subTest(value=value), env(value):
                self.assertEqual(core_client.token_ttl_seconds("test"), 900)


class RenderAndExecuteTests(unittest.TestCase):

    def _render_then_execute(self, environment, age_seconds):
        issued = int(time.time()) - age_seconds
        with env(environment), connector() as m:
            out = operations.prepare_only("sales.generic", dict(ARGS), testsupport.ctx(),
                                          issued_at=issued)
            exec_ctx = testsupport.ctx(confirmation_token=out["confirmation_token"],
                                       issued_at=out["issued_at"],
                                       user_confirmation_text=f"yes {out['confirmation_code']}")
            try:
                operations.run_operation("sales.generic", out["args"], exec_ctx)
            except core_client.StaleConfirmationError as e:
                return out, e, m
            return out, None, m

    def test_30_minute_old_token_accepted_on_non_production(self):
        _, err, m = self._render_then_execute("non-production", 30 * 60)
        self.assertIsNone(err)
        self.assertTrue(m["_do_mutate"].called)

    def test_30_minute_old_token_refused_on_production(self):
        out, err, m = self._render_then_execute(None, 30 * 60)
        self.assertIsNotNone(err)
        self.assertFalse(m["_do_mutate"].called)
        self.assertIn(out["expires_at"], str(err))
        self.assertIn("15 minutes", str(err))

    def test_render_returns_expires_at_issued_plus_lifetime(self):
        for environment, minutes in ((None, 15), ("non-production", 60)):
            with self.subTest(environment=environment), env(environment), connector():
                out = operations.prepare_only("sales.generic", dict(ARGS), testsupport.ctx(),
                                              issued_at=1_800_000_000)
            self.assertEqual(out["ttl_minutes"], minutes)
            expected = datetime.datetime.fromtimestamp(1_800_000_000 + minutes * 60,
                                                       tz=datetime.timezone.utc)
            self.assertEqual(out["expires_at"], expected.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_render_without_token_has_no_expiry(self):
        with env(), connector():
            out = operations.prepare_only("sales.generic", {"doctype": "Sales Order",
                                                            "action": "create", "payload": {}},
                                          testsupport.ctx())
        self.assertNotIn("expires_at", out)


class HealthTests(unittest.TestCase):

    def test_health_shows_environment_and_lifetime(self):
        cfg = {"tag": "test", "base_url": "https://erp.example", "api_key": "k",
               "api_secret": "s", "credential": "bot"}
        trust = {"reliable": True, "bot_user": "bot@example.com", "bot_roles": ["Qkeee Bot"]}
        with env("non-production"), \
                patch.object(core_client, "get_env_config", return_value=cfg), \
                patch.object(core_client, "_request", return_value={"message": "bot@example.com"}), \
                patch.object(core_client, "verify_rbac_precheck_reliable", return_value=trust), \
                patch.object(core_client, "probe_capabilities", return_value=({}, [])), \
                patch.object(core_client, "bot_identity_gaps", return_value=[]):
            out = core_client.health_check("test")
        self.assertEqual(out["environment"], "non-production")
        self.assertEqual(out["token_ttl_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
