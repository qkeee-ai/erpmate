#!/usr/bin/env python3
"""Issue 08 / agents ADR 0003: the agent never widens a Service Account's
rights — not the Bot Account's, not the Admin Account's, whichever
credential makes the write. No confirm-to-proceed path. And a Bot Account
holding any stock role makes the connector refuse to run."""

import unittest
from unittest.mock import patch

import testsupport
from test_operations import connector
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
from qkeee_erp_plugin.qkeee_erp.domains import system_admin  # noqa: F401 — registers system_admin.*

BOT = {"user": "qkeee-erp-bot@example.com", "roles": ["Qkeee Bot"]}
ADMIN = {"user": "qkeee-erp-admin@example.com", "roles": ["System Manager", "Qkeee Admin"]}


def render_and_run(key, args):
    with connector(accounts=[BOT, ADMIN]):
        full_args, ctx, _ = testsupport.render(key, dict(args), session_id="S", channel="CLI",
                                               channel_metadata={"t": 1}, latest_prompt="p")
    with connector(accounts=[BOT, ADMIN]) as m:
        return operations.run_operation(key, full_args, ctx), m


class SelfEscalationRefusedTests(unittest.TestCase):

    def assertRefused(self, key, args, permission_rows=None):
        with connector(accounts=[BOT, ADMIN]) as m:
            if permission_rows is not None:
                m["fetch_permission_rows"].return_value = permission_rows
            with self.assertRaises(core_client.SelfEscalationError) as ctx:
                testsupport.render(key, dict(args))
            self.assertFalse(m["_do_mutate"].called or m["_request"].called)
        self.assertIn("ERPNext UI", str(ctx.exception))
        self.assertIn("ADR 0003", str(ctx.exception))
        self.assertIsInstance(ctx.exception, core_client.GateRefusal)
        return ctx.exception

    def test_set_user_roles_on_the_bot_account_is_refused(self):
        self.assertRefused("system_admin.set_user_roles",
                           {"name": BOT["user"], "roles": ["Qkeee Bot", "HR User"],
                            "reason": "let it create employees", "roles_before": ["Qkeee Bot"]})

    def test_set_user_roles_on_the_admin_account_is_refused(self):
        self.assertRefused("system_admin.set_user_roles",
                           {"name": ADMIN["user"].upper(), "roles": ["System Manager"],
                            "reason": "x", "roles_before": ["System Manager"]})

    def test_disable_or_delete_of_a_service_account_user_is_refused(self):
        self.assertRefused("system_admin.disable_user", {"name": BOT["user"], "reason": "x"})
        self.assertRefused("system_admin.delete",
                           {"doctype": "User", "name": BOT["user"], "reason": "x"})

    def test_write_to_a_role_a_service_account_holds_is_refused(self):
        self.assertRefused("system_admin.generic",
                           {"doctype": "Role", "action": "update", "name": "Qkeee Bot",
                            "payload": {"desk_access": 1}})
        self.assertRefused("system_admin.delete",
                           {"doctype": "Role", "name": "Qkeee Bot", "reason": "x"})

    def test_custom_docperm_add_on_qkeee_bot_is_refused(self):
        self.assertRefused("system_admin.permission_add",
                           {"doctype": "Employee", "role": "Qkeee Bot", "reason": "x"})

    def test_custom_docperm_update_and_remove_on_qkeee_bot_are_refused(self):
        self.assertRefused("system_admin.permission_update",
                           {"doctype": "Employee", "role": "Qkeee Bot", "ptype": "create",
                            "value": 1, "reason": "x", "current_value": 0})
        self.assertRefused("system_admin.permission_remove",
                           {"doctype": "Employee", "role": "Qkeee Bot", "reason": "x"})

    def test_permission_reset_touching_a_service_account_role_is_refused(self):
        self.assertRefused("system_admin.permission_reset", {"doctype": "Module Def", "reason": "x"},
                           permission_rows=[{"role": "Qkeee Bot", "permlevel": 0, "read": 1}])

    def test_custom_docperm_add_on_hr_user_is_allowed(self):
        result, m = render_and_run("system_admin.permission_add",
                                   {"doctype": "Employee", "role": "HR User", "reason": "x"})
        self.assertEqual(result["_operation"], "system_admin.permission_add")
        self.assertTrue(m["_request"].called)

    def test_a_write_to_another_users_roles_still_works(self):
        result, m = render_and_run("system_admin.set_user_roles",
                                   {"name": "staff@example.com", "roles": ["HR User"],
                                    "reason": "x"})
        self.assertTrue(m["_do_mutate"].called)

    def test_execute_also_refuses_when_render_was_skipped(self):
        with connector(accounts=[BOT, ADMIN]) as m:
            with self.assertRaises(core_client.SelfEscalationError):
                operations.run_operation("system_admin.disable_user",
                                         {"name": BOT["user"], "reason": "x"}, testsupport.ctx())
            self.assertFalse(m["_do_mutate"].called)

    def test_unresolvable_service_accounts_fail_closed(self):
        with connector() as m:
            m["service_account_identities"].side_effect = core_client.SelfEscalationError("cannot resolve")
            with self.assertRaises(core_client.SelfEscalationError):
                testsupport.render("system_admin.permission_add",
                                   {"doctype": "Employee", "role": "HR User", "reason": "x"})


class ServiceAccountIdentityTests(unittest.TestCase):

    def setUp(self):
        for cache in (core_client._BOT_IDENTITY_CACHE, core_client._ADMIN_IDENTITY_CACHE):
            cache.clear()
            self.addCleanup(cache.clear)

    def test_both_accounts_are_resolved(self):
        def fake_request(cfg, method, path, params=None, payload=None):
            if path.endswith("get_logged_user"):
                return {"message": ADMIN["user"]}
            return {"data": {"roles": [{"role": r} for r in ADMIN["roles"]]}}
        with patch.object(core_client, "get_user_roles", return_value=dict(BOT)), \
                patch.object(core_client, "get_env_config", return_value={"tag": "t"}), \
                patch.object(core_client, "_request", side_effect=fake_request):
            accounts = core_client.service_account_identities("t")
        self.assertEqual([a["user"] for a in accounts], [BOT["user"], ADMIN["user"]])
        self.assertEqual(accounts[1]["roles"], set(ADMIN["roles"]))

    def test_unknown_bot_identity_fails_closed(self):
        with patch.object(core_client, "get_user_roles",
                          side_effect=core_client.ConnectorError("down")):
            with self.assertRaises(core_client.SelfEscalationError):
                core_client.service_account_identities("t")


class BotStockRoleInvariantTests(unittest.TestCase):
    """ADR 0003: Service Accounts hold only dedicated roles. A Bot Account
    holding a stock role (e.g. HR User) makes the connector refuse every
    gated call, so the self-escalation rule never has to block normal
    admin edits to stock roles."""

    def test_bot_holding_hr_user_makes_the_connector_refuse(self):
        trust = {"reliable": True, "bot_user": BOT["user"], "bot_roles": ["HR User", "Qkeee Bot"],
                 "bot_stock_roles": ["HR User"], "privileged_identity": False,
                 "precheck_discriminates": True}
        with patch.object(core_client, "resource_exists", return_value=True), \
                patch.object(core_client, "verify_rbac_precheck_reliable", return_value=trust), \
                patch.object(core_client, "check_user_permission") as perm, \
                patch.object(core_client, "_log_gate_decision"):
            with self.assertRaises(core_client.ServiceAccountRoleError) as ctx:
                core_client._validate_prod_requester("t", "priya@org.com", "Employee", "read")
        perm.assert_not_called()
        self.assertIn("HR User", str(ctx.exception))

    def test_stock_roles_are_everything_but_dedicated_and_automatic_roles(self):
        self.assertEqual(core_client.stock_roles(["Qkeee Bot", "All", "Guest", "Desk User",
                                                  "HR User", "System Manager"]),
                         ["HR User", "System Manager"])

    def test_verify_reports_the_bots_stock_roles(self):
        with patch.object(core_client, "_bot_identity",
                          return_value={"user": BOT["user"], "roles": ["Qkeee Bot", "HR User"]}), \
                patch.object(core_client, "_probe_rbac_precheck_discriminates", return_value=True):
            trust = core_client.verify_rbac_precheck_reliable("t")
        self.assertEqual(trust["bot_stock_roles"], ["HR User"])


if __name__ == "__main__":
    unittest.main()
