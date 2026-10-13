"""init-bot provisions the bot user and its business rights (agents ADR 0003
amendment, 2026-10-13): assigns `Qkeee Bot`, optionally strips stock
roles, and grants the permissions the chosen domains need, from code. Never
delete, never a privileged doctype, never reachable by the agent."""

import time
import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp import init_bot
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations

ROLE = init_bot.ROLE_NAME
BOT = "bot@org.com"
CTX = operations.WriteContext(tag="qa", mode="read-write", requested_by="admin@org.com")


def op(key):
    return operations.get_operation(key)


class OperationShapeTests(unittest.TestCase):

    def test_both_ops_are_operator_only_and_admin_keyed(self):
        for key in ("provisioning.assign_bot_role", "provisioning.grant_bot_permission"):
            with self.subTest(key=key):
                self.assertFalse(op(key).cli)
                self.assertEqual(op(key).credential, "admin")
                self.assertTrue(op(key).self_escalation_exempt)

    def test_assign_may_add_qkeee_bot_and_remove_roles(self):
        req = op("provisioning.assign_bot_role").prepare(
            {"user": BOT, "roles_before": ["Stock User"], "roles_after": [ROLE]}, CTX)
        self.assertEqual((req.doctype, req.action, req.name), ("User", "update", BOT))
        self.assertEqual(req.body, {"roles": [{"role": ROLE}]})

    def test_assign_never_adds_another_role_or_drops_qkeee_bot(self):
        for after in ([ROLE, "System Manager"], ["Stock User"]):
            with self.subTest(after=after), self.assertRaises(core_client.DoctypeNotAllowedError):
                op("provisioning.assign_bot_role").prepare(
                    {"user": BOT, "roles_before": ["Stock User"], "roles_after": after}, CTX)

    def test_assign_precondition_checks_the_bot_user_and_its_live_roles(self):
        args = {"user": BOT, "roles_before": [], "roles_after": [ROLE]}
        for identity in ({"user": "someone@org.com", "roles": []},
                         {"user": BOT, "roles": ["Stock User"]},
                         {"user": BOT, "roles": [], "roles_error": "403"}):
            with self.subTest(identity=identity), \
                    patch.object(core_client, "_bot_identity", return_value=identity), \
                    self.assertRaises(core_client.PreconditionFailedError):
                init_bot._is_the_bot_with_roles_before(None, args, CTX)

    def test_grant_adds_or_ticks_one_business_ptype(self):
        prep = op("provisioning.grant_bot_permission").prepare
        req = prep({"doctype": "Item", "step": "add"}, CTX)
        self.assertEqual(req.body, {"parent": "Item", "role": ROLE, "permlevel": 0})
        req = prep({"doctype": "Purchase Order", "step": "submit"}, CTX)
        self.assertEqual(req.body["ptype"], "submit")
        self.assertEqual(req.body["if_owner"], 0)

    def test_grant_never_deletes_or_touches_a_privileged_doctype(self):
        prep = op("provisioning.grant_bot_permission").prepare
        for args in ({"doctype": "Item", "step": "delete"},
                     {"doctype": "User", "step": "read"},
                     {"doctype": "Custom DocPerm", "step": "add"},
                     {"doctype": "Role", "step": "write"},
                     {"doctype": "Item", "step": "add", "permlevel": 1}):
            with self.subTest(args=args), self.assertRaises(core_client.DoctypeNotAllowedError):
                prep(args, CTX)


def bundle(doctype, submittable=False, links=(), child=None):
    main = {"name": doctype, "is_submittable": int(submittable),
            "fields": [{"fieldname": f, "fieldtype": "Link", "options": t, "reqd": 1}
                       for f, t in links]}
    docs = [main]
    if child:
        docs.append({"name": child[0], "fields": [
            {"fieldname": f, "fieldtype": "Link", "options": t, "reqd": 1} for f, t in child[1]]})
    return docs


META = {
    "Purchase Order": bundle("Purchase Order", True, [("supplier", "Supplier"),
                                                      ("owner_user", "User")],
                             child=("Purchase Order Item", [("item_code", "Item")])),
    "Supplier": bundle("Supplier", links=[("supplier_group", "Supplier Group")]),
}


class MatrixTests(unittest.TestCase):

    def matrix(self, domains, allow):
        with patch.object(init_bot, "provisionable_domains", return_value=allow), \
                patch.object(init_bot, "_meta_bundle",
                             side_effect=lambda tag, dt: META.get(dt, bundle(dt))):
            return init_bot.bot_permission_matrix("qa", domains)

    def test_domain_doctypes_links_and_masters(self):
        matrix, skipped, _ = self.matrix(["procurement"],
                                      {"procurement": ["Purchase Order", "Supplier"]})
        self.assertEqual(matrix["Purchase Order"], {"read", "create", "write", "submit", "cancel"})
        self.assertEqual(matrix["Supplier"], {"read", "create", "write"})
        self.assertEqual(matrix["Supplier Group"], {"read"})       # mandatory Link
        self.assertIn("read", matrix["Item"])                       # child-table Link + master
        self.assertEqual(matrix["Item Price"], {"read", "create", "write"})
        self.assertEqual(skipped, ["User"])                         # privileged, never granted
        self.assertNotIn("User", matrix)
        self.assertFalse(any("delete" in p for p in matrix.values()))

    def test_a_doctype_missing_on_the_instance_is_skipped(self):
        def meta(tag, dt):
            if dt == "Employee":
                raise core_client.ConnectorError("ERPNext API error (404) DoesNotExistError")
            return bundle(dt)
        with patch.object(init_bot, "provisionable_domains", return_value={"hr": ["Employee"]}), \
                patch.object(init_bot, "_meta_bundle", side_effect=meta):
            matrix, _, not_found = init_bot.bot_permission_matrix("qa", ["hr"])
        self.assertEqual(not_found, ["Employee"])
        self.assertNotIn("Employee", matrix)

    def test_unknown_domain_is_refused(self):
        with self.assertRaises(core_client.ConnectorError) as ctx:
            self.matrix(["system_admin"], {"procurement": ["Supplier"]})
        self.assertIn("procurement", str(ctx.exception))


class BusinessPlanTests(unittest.TestCase):

    def plan(self, live_rows):
        matrix = {"Item": {"read", "create", "write"}, "UOM": {"read"},
                  "Purchase Order": {"read", "create", "write", "submit", "cancel"}}
        with patch.object(init_bot, "bot_permission_matrix", return_value=(matrix, ["User"], [])), \
                patch.object(init_bot, "_live_role_permissions", return_value=live_rows):
            return init_bot._business_plan("qa", ["procurement"])

    def test_missing_rows_and_ptypes_become_steps(self):
        plan = self.plan([
            {"parent": "Purchase Order", "permlevel": 0, "read": 1, "create": 1},
            {"parent": "UOM", "permlevel": 0, "read": 1},
        ])
        self.assertEqual(plan["business_grants"], [
            "Item: add", "Item: create", "Item: write",
            "Purchase Order: write", "Purchase Order: submit", "Purchase Order: cancel"])
        self.assertEqual(plan["skipped_privileged"], ["User"])

    def test_rights_beyond_the_plan_are_reported_not_removed(self):
        plan = self.plan([
            {"parent": "Item", "permlevel": 0, "read": 1, "create": 1, "write": 1, "delete": 1},
            {"parent": "Sales Invoice", "permlevel": 0, "read": 1},
            {"parent": "Workflow", "permlevel": 0, "read": 1},           # init-bot read grant
            {"parent": "Qkeee Bot Audit Log", "permlevel": 0, "read": 1, "create": 1,
             "write": 1, "submit": 1},
        ])
        self.assertEqual(plan["extra_rights"], ["Item: delete", "Sales Invoice: read"])


class BotRolePlanTests(unittest.TestCase):

    def plan(self, identity, strip=False):
        with patch.object(core_client, "_bot_identity", return_value=identity):
            return init_bot._bot_role_plan("qa", strip)

    def test_no_change_when_the_bot_holds_only_qkeee_bot(self):
        self.assertIsNone(self.plan({"user": BOT, "roles": [ROLE]})["bot_role_change"])

    def test_adds_qkeee_bot_and_keeps_stock_roles_without_the_flag(self):
        plan = self.plan({"user": BOT, "roles": ["Stock User"]})
        self.assertEqual(plan["bot_role_change"]["roles_after"], [ROLE, "Stock User"])
        self.assertEqual(plan["bot_stock_roles"], ["Stock User"])

    def test_strip_flag_removes_stock_roles(self):
        plan = self.plan({"user": BOT, "roles": [ROLE, "System Manager", "Stock User"]}, strip=True)
        self.assertEqual(plan["bot_role_change"]["roles_after"], [ROLE])

    def test_unreadable_identity_is_reported_not_guessed(self):
        plan = self.plan({"user": BOT, "roles": [], "roles_error": "no admin key"})
        self.assertIsNone(plan["bot_role_change"])
        self.assertEqual(plan["bot_identity_error"], "no admin key")


class RunTests(unittest.TestCase):
    PLAN = {"role_needed": False, "doctypes_needed": [], "fields_to_migrate": [],
            "bot_read_grants": ["Workflow: add"],
            "bot_role_change": {"user": BOT, "roles_before": [], "roles_after": [ROLE]},
            "bot_stock_roles": [], "bot_identity_error": None,
            "business_grants": ["Item: add", "Item: create"],
            "skipped_privileged": [], "extra_rights": []}

    def test_token_binds_role_change_and_grants(self):
        base = init_bot._plan_token("qa", "a@b.c", self.PLAN, 1)
        for key, value in (("business_grants", ["Item: add"]), ("bot_role_change", None)):
            with self.subTest(key=key):
                self.assertNotEqual(base, init_bot._plan_token("qa", "a@b.c",
                                                               dict(self.PLAN, **{key: value}), 1))

    def test_run_real_order_reads_then_role_then_business(self):
        issued_at = int(time.time())
        token = init_bot._plan_token("qa", "admin@org.com", self.PLAN, issued_at)
        calls = []
        with patch.object(init_bot.core_client, "health_check", return_value={}), \
                patch.object(init_bot, "compute_plan", return_value=dict(self.PLAN)), \
                patch.object(init_bot, "ensure_role", return_value=False), \
                patch.object(init_bot, "ensure_doctype", return_value=False), \
                patch.object(init_bot, "log_role_provisioning"), \
                patch.object(init_bot, "ensure_qkeee_env_file_skeleton", return_value=False), \
                patch.object(init_bot.operations, "run_operation",
                             side_effect=lambda key, args, ctx: calls.append(key) or {}):
            summary = init_bot.run_real("qa", "admin@org.com", token, issued_at, ["procurement"])
        self.assertEqual(calls, ["provisioning.grant_bot_read", "provisioning.assign_bot_role",
                                 "provisioning.grant_bot_permission",
                                 "provisioning.grant_bot_permission"])
        self.assertEqual(summary["business_grants"], self.PLAN["business_grants"])

    def test_dry_run_warns_on_kept_stock_roles(self):
        plan = dict(self.PLAN, bot_stock_roles=["Stock User"],
                    bot_role_change={"user": BOT, "roles_before": ["Stock User"],
                                     "roles_after": [ROLE, "Stock User"]})
        with patch.object(init_bot.core_client, "health_check", return_value={}), \
                patch.object(init_bot, "compute_plan", return_value=plan), \
                patch("builtins.print") as out:
            init_bot.run_dry_run("qa", "admin@org.com", ["procurement"])
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)
        self.assertIn("--strip-stock-roles", printed)
        self.assertIn("a new row (read) on 'Item'", printed)


class AdminKeyReadsTests(unittest.TestCase):
    """DEMO_ERP 2026-10-13: the plan checked the audit DocType with the bot
    key and got 403 once the bot held only Qkeee Bot. Every existence check
    init-bot makes uses the admin key."""

    def test_plan_and_ensure_steps_check_existence_with_the_admin_key(self):
        with patch.object(core_client, "resource_exists", return_value=True) as exists, \
                patch.object(init_bot, "_live_doctype", return_value=init_bot.AUDIT_LOG), \
                patch.object(init_bot, "_bot_read_grants_needed", return_value=[]), \
                patch.object(init_bot, "_bot_role_plan", return_value={}), \
                patch("builtins.print"):
            init_bot.compute_plan("qa")
            init_bot.ensure_role("qa", "admin@org.com", "n")
            init_bot.ensure_doctype("qa", {"name": init_bot.AUDIT_LOG["name"]}, "admin@org.com", "n")
        self.assertTrue(exists.call_args_list)
        for call in exists.call_args_list:
            self.assertEqual(call.kwargs.get("credential"), "admin", call)


class AdminKeyGateTests(unittest.TestCase):
    """init-bot must run while the bot lacks `Qkeee Bot`: the bot-identity
    checks guard bot-key calls only."""

    TRUST = {"reliable": True, "bot_user": BOT, "bot_roles": [], "bot_stock_roles": ["Stock User"],
             "bot_identity_error": "the Bot Account does not hold 'Qkeee Bot'",
             "bot_identity_problem": "role_missing"}

    def gate(self, credential):
        with patch.object(core_client, "verify_rbac_precheck_reliable", return_value=dict(self.TRUST)), \
                patch.object(core_client, "resource_exists", return_value=True), \
                patch.object(core_client, "check_user_permission", return_value=True), \
                patch.object(core_client, "_log_gate_decision"):
            core_client._validate_prod_requester("qa", "admin@org.com", "Custom DocPerm", "create",
                                                 for_write=True, credential=credential)

    def test_admin_key_call_passes(self):
        self.gate("admin")

    def test_bot_key_call_is_refused(self):
        with self.assertRaises(core_client.ServiceAccountRoleError):
            self.gate("bot")


if __name__ == "__main__":
    unittest.main()
