"""The requester permission check reads with the Admin Account (agents ADR
0003 amendment, 2026-10-13).

DEMO_ERP 2026-10-13: with stock roles removed from the bot, Frappe hid the
bot's own `roles` table, so the stock-role check passed a bot that held
System Manager; every business read was refused because the bot could not
read the requester's User; `health` showed no gap for it; preflight said
ready; and RECHECK ENV kept cached results from before the admin's fix.
"""

import json
import os
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp import discover
from qkeee_erp_plugin.qkeee_erp.core import client as ec

BOT = "bot@org.com"
REQ = "priya@org.com"
TAG = "t"


def cfg_for(admin_configured=True):
    def get_env_config(tag="default", credential="bot"):
        if credential == "admin" and not admin_configured:
            raise ec.ConnectorError("Missing environment variable(s) for tag 't': "
                                    "QKEEE_ERP_T_ADMIN_API_KEY, QKEEE_ERP_T_ADMIN_API_SECRET.")
        return {"tag": tag, "base_url": "https://erp.example.com", "cred": credential}
    return get_env_config


class FakeErp:
    """Answers like Frappe: a User read with the bot key hides `roles`."""

    def __init__(self, users, perms=None):
        self.users, self.perms, self.calls = users, perms or {}, []

    def __call__(self, cfg, method, path, params=None, payload=None):
        self.calls.append((cfg["cred"], path))
        if path.endswith("get_logged_user"):
            return {"message": BOT if cfg["cred"] == "bot" else "admin@org.com"}
        if path.startswith("/api/resource/User/"):
            user = path.rsplit("/", 1)[1].replace("%40", "@")
            if user not in self.users:
                raise ec.ERPNextAPIError(404, method, path, cfg, "DoesNotExistError")
            doc = dict(self.users[user])
            if cfg["cred"] == "bot":
                doc.pop("roles", None)
            return {"data": doc}
        if path == ec._MERGED_META_PATH:
            if cfg["cred"] != "admin":
                raise ec.ERPNextAPIError(403, method, path, cfg, "PermissionError")
            return {"docs": [{"name": params["doctype"],
                              "permissions": self.perms.get(params["doctype"], [])}]}
        if path.endswith("has_permission"):
            return {"message": True}  # never discriminates (stock Frappe ignores user=)
        if path.startswith("/api/resource/"):
            return {"data": []}
        if path.startswith("/api/method/"):
            return {"message": {}, "docs": []}
        raise AssertionError(f"unexpected call {path}")


def roles(*names):
    return [{"role": n} for n in names]


class MirrorTestCase(unittest.TestCase):
    def setUp(self):
        for tag in (TAG,):
            ec.reset_identity_caches(tag)
            self.addCleanup(ec.reset_identity_caches, tag)
        p = patch.object(ec, "_log_gate_decision")
        p.start()
        self.addCleanup(p.stop)

    def run_with(self, fake, admin_configured=True):
        return patch.object(ec, "get_env_config", side_effect=cfg_for(admin_configured)), \
            patch.object(ec, "_request", side_effect=fake)


class BotIdentityTests(MirrorTestCase):

    def test_stock_role_hidden_from_the_bot_is_seen_through_the_admin_read(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot", "System Manager")}})
        a, b = self.run_with(fake)
        with a, b:
            trust = ec.verify_rbac_precheck_reliable(TAG)
        self.assertEqual(trust["bot_stock_roles"], ["System Manager"])
        self.assertTrue(trust["privileged_identity"])
        self.assertIn(("admin", f"/api/resource/User/{BOT.replace('@', '%40')}"), fake.calls)

    def test_missing_admin_credential_fails_closed_with_a_gap(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}, REQ: {"roles": roles("Sales User")}})
        a, b = self.run_with(fake, admin_configured=False)
        with a, b, self.assertRaises(ec.ServiceAccountRoleError) as ctx:
            ec._validate_prod_requester(TAG, REQ, "Sales Order", "read")
        self.assertIn("could not be read with the Admin Account", str(ctx.exception))
        [gap] = ctx.exception.gaps
        self.assertEqual((gap["capability"], gap["who"]), ("rbac_mirror", "admin"))
        self.assertIn("QKEEE_ERP_T_ADMIN_API_KEY", gap["prompt"])

    def test_identity_failure_is_not_cached(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}})
        a, b = self.run_with(fake, admin_configured=False)
        with a, b:
            self.assertIn("roles_error", ec._bot_identity(TAG))
        a, b = self.run_with(fake)
        with a, b:
            self.assertEqual(ec._bot_identity(TAG)["roles"], ["Qkeee Bot"])

    def test_bot_without_qkeee_bot_role_is_refused_with_a_bot_gap(self):
        fake = FakeErp({BOT: {"roles": []}, REQ: {"roles": roles("Sales User")}})
        a, b = self.run_with(fake)
        with a, b, self.assertRaises(ec.ServiceAccountRoleError) as ctx:
            ec._validate_prod_requester(TAG, REQ, "Sales Order", "read")
        [gap] = ctx.exception.gaps
        self.assertEqual((gap["capability"], gap["who"], gap["user"]), ("bot_role", "bot", BOT))

    def test_unread_bot_roles_fail_the_self_escalation_check_closed(self):
        with patch.object(ec, "_bot_identity",
                          return_value={"user": BOT, "roles": [], "roles_error": "403"}), \
                patch.object(ec, "_admin_identity", return_value=None):
            with self.assertRaises(ec.SelfEscalationError):
                ec.service_account_identities(TAG)


class RequesterCheckTests(MirrorTestCase):
    PERMS = {"Item Group": [{"role": "All", "read": 1}],
             "Sales Order": [{"role": "Sales User", "read": 1, "create": 1}]}

    def verdict(self, users, doctype, perm):
        fake = FakeErp(users, self.PERMS)
        a, b = self.run_with(fake)
        with a, b:
            return ec._requester_has_role_permission(TAG, doctype, perm, REQ), fake

    def test_reads_roles_and_permission_rows_with_the_admin_account(self):
        verdict, fake = self.verdict({REQ: {"roles": roles("Sales User")}}, "Sales Order", "create")
        self.assertTrue(verdict)
        self.assertNotIn(("bot", f"/api/resource/User/{REQ.replace('@', '%40')}"), fake.calls)
        self.assertNotIn(("bot", ec._MERGED_META_PATH), fake.calls)

    def test_all_role_rows_grant_every_user(self):
        verdict, _ = self.verdict({REQ: {"roles": []}}, "Item Group", "read")
        self.assertTrue(verdict)

    def test_administrator_holds_every_permission(self):
        global REQ
        saved, REQ = REQ, "Administrator"
        try:
            verdict, _ = self.verdict({"Administrator": {"roles": []}}, "Sales Order", "create")
        finally:
            REQ = saved
        self.assertTrue(verdict)

    def test_disabled_requester_is_denied(self):
        verdict, _ = self.verdict({REQ: {"roles": roles("Sales User"), "enabled": 0}},
                                  "Sales Order", "create")
        self.assertFalse(verdict)

    def test_requester_without_a_granting_role_is_denied(self):
        verdict, _ = self.verdict({REQ: {"roles": roles("Stock User")}}, "Sales Order", "create")
        self.assertFalse(verdict)

    def test_gate_passes_a_narrow_bot_and_a_granted_requester(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}, REQ: {"roles": roles("Sales User")}},
                       self.PERMS)
        a, b = self.run_with(fake)
        with a, b:
            ec._validate_prod_requester(TAG, REQ, "Sales Order", "read")

    def test_failed_requester_lookup_is_a_logged_refusal_with_a_gap(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}})
        with patch.object(ec, "resource_exists",
                          side_effect=ec.ConnectorError("ERPNext API error (403)")):
            a, b = self.run_with(fake)
            with a, b, self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
                ec._validate_prod_requester(TAG, REQ, "Sales Order", "read")
        self.assertEqual(ctx.exception.gaps[0]["capability"], "rbac_mirror")
        ec._log_gate_decision.assert_called_once()
        self.assertEqual(ec._log_gate_decision.call_args.kwargs["detail"],
                         {"reason": "requester_lookup_failed"})

    def test_inconclusive_check_refuses_with_an_admin_gap(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}, REQ: {"roles": roles("Sales User")}})
        with patch.object(ec, "get_user_roles", side_effect=[
                {"user": BOT, "roles": ["Qkeee Bot"]}, ec.ConnectorError("admin 403")]):
            a, b = self.run_with(fake)
            with a, b, self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
                ec._validate_prod_requester(TAG, REQ, "Sales Order", "read")
        [gap] = ctx.exception.gaps
        self.assertEqual((gap["capability"], gap["doctype"]), ("rbac_mirror", "Sales Order"))


class HealthTests(MirrorTestCase):

    def health(self, fake, admin_configured=True):
        a, b = self.run_with(fake, admin_configured)
        with a, b:
            return ec.health_check(TAG)

    def test_unread_bot_roles_show_as_a_gap_and_none(self):
        result = self.health(FakeErp({BOT: {"roles": roles("Qkeee Bot")}}), admin_configured=False)
        self.assertIsNone(result["bot_roles"])
        self.assertFalse(result["capabilities"]["rbac_identity"])
        self.assertEqual(result["gaps"][0]["capability"], "rbac_mirror")

    def test_recheck_sees_an_admin_fix(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot", "Stock User")}})
        self.assertEqual(self.health(fake)["bot_roles"], ["Qkeee Bot", "Stock User"])
        fake.users[BOT] = {"roles": roles("Qkeee Bot")}
        result = self.health(fake)
        self.assertEqual(result["bot_roles"], ["Qkeee Bot"])
        self.assertNotIn("service_account_warning", result)
        self.assertTrue(result["capabilities"]["rbac_identity"])


class ReadinessTests(MirrorTestCase):

    def test_unconfirmed_identity_is_not_ready(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}})
        a, b = self.run_with(fake, admin_configured=False)
        with a, b:
            out = ec.requester_permission_readiness(TAG, "Item", "create", REQ)
        self.assertIsNone(out["ready"])
        self.assertEqual(out["gaps"][0]["capability"], "rbac_mirror")

    def test_granted_requester_is_ready(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}, REQ: {"roles": roles("Item Manager")}},
                       {"Item": [{"role": "Item Manager", "read": 1, "create": 1}]})
        a, b = self.run_with(fake)
        with a, b:
            out = ec.requester_permission_readiness(TAG, "Item", "create", REQ)
        self.assertTrue(out["ready"])


class LinkReadProbeTests(MirrorTestCase):

    def test_unreadable_target_is_one_bot_gap_naming_the_fields(self):
        fake = FakeErp({BOT: {"roles": roles("Qkeee Bot")}})

        def erp(cfg, method, path, params=None, payload=None):
            if path == "/api/resource/Item%20Group":
                raise ec.ERPNextAPIError(403, method, path, cfg, "PermissionError")
            return fake(cfg, method, path, params, payload)
        with patch.object(ec, "get_env_config", side_effect=cfg_for()), \
                patch.object(ec, "_request", side_effect=erp):
            gaps = ec.probe_bot_link_reads(TAG, "Item", {"Item Group": ["item_group"],
                                                         "UOM": ["stock_uom"]})
        [gap] = gaps
        self.assertEqual((gap["who"], gap["doctype"], gap["perm"]), ("bot", "Item Group", "read"))
        self.assertIn("item_group", gap["effect"])
        self.assertIn('Role "Qkeee Bot"', gap["prompt"])


class BotPermissionsTests(unittest.TestCase):

    def test_bot_needs_no_user_or_doctype_read(self):
        out = discover.bot_permissions()
        self.assertEqual(out["bot_role"], "Qkeee Bot")
        self.assertIn("User", out["not_needed_on_bot"])
        self.assertIn("DocType", out["not_needed_on_bot"])
        self.assertEqual(out["provisioned_by_init_bot"]["Module Def"], ["read"])
        self.assertIn("system_admin", out["admin_write_doctypes"])
        self.assertNotIn("system_admin", out["bot_write_doctypes"])
        self.assertNotIn("mis", out["bot_write_doctypes"])


class ToolRefusalGapTests(unittest.TestCase):

    def test_refusal_gaps_reach_the_agent(self):
        gap = {"capability": "rbac_mirror", "prompt": "⚠ ..."}
        tools = erp_tools.ErpTools(settings=lambda: {"active_env": "t", "mode": "read-write"})
        with patch.object(erp_tools.client, "health_check",
                          side_effect=ec.ServiceAccountRoleError("refused", gaps=[gap])), \
                patch.dict(os.environ, {"HERMES_KANBAN_TASK": ""}):
            out = json.loads(tools.handler("erp_discover")({"action": "health"}))
        self.assertTrue(out["refused"])
        self.assertEqual(out["gaps"], [gap])

    def test_bot_permissions_needs_no_requester(self):
        tools = erp_tools.ErpTools(settings=lambda: {"active_env": "t", "mode": "read-write"})
        out = json.loads(tools.handler("erp_discover")({"action": "bot_permissions"}))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["bot_role"], "Qkeee Bot")


if __name__ == "__main__":
    unittest.main()
