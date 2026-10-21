"""init-bot provisions the bot user and its business rights (agents ADR 0003
amendment, 2026-10-13): assigns `Qkeee Bot`, optionally strips stock
roles, and grants the permissions the chosen domains need, from code. Never
delete, never a privileged doctype, never reachable by the agent."""

import time
import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp import init_bot
from qkeee_erp_plugin.qkeee_erp.doctype_defs import DOMAIN_MASTER_DOCTYPES
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
    "Supplier": bundle("Supplier", links=[("country", "Country")]),
}
# ERPNext v15: supplier_group is NOT mandatory (dev-erp 2026-10-15: 403 on
# Supplier Group although procurement was provisioned). A Link the user
# can set gets read; a hidden, read-only or fetched one does not.
META["Supplier"][0]["fields"] += [
    {"fieldname": "supplier_group", "fieldtype": "Link", "options": "Supplier Group"},
    {"fieldname": "default_price_list", "fieldtype": "Link", "options": "Price List"},
    {"fieldname": "hidden_link", "fieldtype": "Link", "options": "Hidden Target", "hidden": 1},
    {"fieldname": "ro_link", "fieldtype": "Link", "options": "Read Only Target", "read_only": 1},
    {"fieldname": "fetched", "fieldtype": "Link", "options": "Fetched Target",
     "fetch_from": "supplier_group.parent_supplier_group"},
]


class AllBut:
    """_doctype_names() stand-in: every DocType exists except `missing`."""

    def __init__(self, missing=()):
        self.missing = set(missing)

    def __contains__(self, doctype):
        return doctype not in self.missing


class MatrixTests(unittest.TestCase):

    def matrix(self, domains, allow, role_rows=None, meta=None, missing=()):
        meta = {**META, **(meta or {})}
        with patch.object(init_bot, "provisionable_domains", return_value=allow), \
                patch.object(init_bot, "_doctype_names", return_value=AllBut(missing)), \
                patch.object(init_bot, "_role_permissions",
                             side_effect=lambda tag, role: (role_rows or {}).get(role, [])), \
                patch.object(init_bot, "_meta_bundle",
                             side_effect=lambda tag, dt: meta.get(dt, bundle(dt))):
            self.result = init_bot.bot_permission_matrix("qa", domains)
            return self.result[:3]

    def test_domain_doctypes_links_and_masters(self):
        matrix, skipped, _ = self.matrix(["procurement"],
                                      {"procurement": ["Purchase Order", "Supplier"]})
        self.assertEqual(matrix["Purchase Order"], {"read", "create", "write", "submit", "cancel"})
        self.assertEqual(matrix["Supplier"], {"read", "create", "write"})
        self.assertEqual(matrix["Country"], {"read"})              # mandatory Link
        self.assertEqual(matrix["Supplier Group"], {"read", "create", "write"})  # procurement master
        self.assertEqual(matrix["Price List"], {"read"})           # optional, user-settable
        for target in ("Hidden Target", "Read Only Target", "Fetched Target"):
            self.assertNotIn(target, matrix)
        self.assertIn("read", matrix["Item"])                       # child-table Link + master
        self.assertEqual(matrix["Item Price"], {"read", "create", "write"})
        self.assertEqual(skipped, ["User"])                         # privileged, never granted
        self.assertNotIn("User", matrix)
        self.assertFalse(any("delete" in p for p in matrix.values()))

    def test_domain_masters_get_create_only_with_their_domain(self):
        # dev-erp 2026-10-15: Supplier Group create refused (403) after
        # procurement was provisioned: masters were read-only.
        matrix, _, _ = self.matrix(["procurement"], {"procurement": ["Supplier"], "sales": ["Quotation"]})
        self.assertEqual(matrix["Supplier Group"], {"read", "create", "write"})
        self.assertEqual(matrix["Payment Terms Template"], {"read", "create", "write"})
        self.assertNotIn("create", matrix.get("Customer Group", set()))
        matrix, _, _ = self.matrix(["sales"], {"procurement": ["Supplier"], "sales": ["Quotation"]})
        self.assertEqual(matrix["Customer Group"], {"read", "create", "write"})
        self.assertNotIn("Supplier Group", matrix)

    def test_domain_masters_are_never_privileged_or_domain_owned(self):
        from qkeee_erp_plugin.qkeee_erp import execute_write  # noqa: F401 — registers allowlists
        owned = {dt for dts in core_client.DOMAIN_WRITE_ALLOWLISTS.values() for dt in dts}
        for domain, masters in DOMAIN_MASTER_DOCTYPES.items():
            for dt in masters:
                with self.subTest(domain=domain, doctype=dt):
                    self.assertNotIn(dt, init_bot._privileged_doctypes())
                    self.assertNotIn(dt, owned)        # else unscoped.generic refuses it

    def test_a_doctype_missing_on_the_instance_is_skipped(self):
        def meta(tag, dt):
            if dt == "Employee":
                raise core_client.ConnectorError("ERPNext API error (404) DoesNotExistError")
            return bundle(dt)
        with patch.object(init_bot, "provisionable_domains", return_value={"hr": ["Employee"]}), \
                patch.object(init_bot, "_role_permissions", return_value=[]), \
                patch.object(init_bot, "_doctype_names", return_value=AllBut()), \
                patch.object(init_bot, "_meta_bundle", side_effect=meta):
            matrix, _, not_found, _ = init_bot.bot_permission_matrix("qa", ["hr"])
        self.assertEqual(not_found, ["Employee"])
        self.assertNotIn("Employee", matrix)

    def test_unknown_domain_is_refused(self):
        with self.assertRaises(core_client.ConnectorError) as ctx:
            self.matrix(["system_admin"], {"procurement": ["Supplier"]})
        self.assertIn("procurement", str(ctx.exception))


def row(parent, permlevel=0, if_owner=0, **ticks):
    return {"parent": parent, "permlevel": permlevel, "if_owner": if_owner,
            **{p: 1 for p in ticks}}


# dev-erp 2026-10-21: 403 on Salary Structure / Attendance / Job Opening:
# no code list named them. HR Manager's live rows cover them.
HR_ROWS = {
    "HR Manager": [
        row("Salary Structure", read=1, create=1, write=1, submit=1, cancel=1, delete=1, amend=1),
        row("Attendance", read=1, create=1, write=1, submit=1, cancel=1),
        row("Job Opening", read=1, create=1, write=1, submit=1),     # not submittable
        row("HR Settings", read=1, write=1),                          # Single
        row("Salary Detail", read=1, write=1),                        # child table
        row("User", read=1, write=1),                                 # privileged
        row("Employee", permlevel=1, read=1, write=1),                # permlevel 1
        row("Expense Claim", if_owner=1, read=1, create=1),          # own records only
        row("Holiday List", read=1, export=1, report=1),
    ],
    "HR User": [row("Attendance", read=1), row("Leave Allocation", read=1, create=1)],
}
HR_META = {
    "Salary Structure": bundle("Salary Structure", True, [("company", "Company")],
                               child=("Salary Detail", [("salary_component", "Salary Component")])),
    "Attendance": bundle("Attendance", True, [("employee", "Employee")]),
    "Leave Allocation": bundle("Leave Allocation", True),
    "Holiday List": bundle("Holiday List", links=[("not_followed", "Never Read")]),
}
HR_META["HR Settings"] = [{"name": "HR Settings", "issingle": 1, "fields": []}]
HR_META["Salary Detail"] = [{"name": "Salary Detail", "istable": 1, "fields": []}]


class TemplateRoleTests(unittest.TestCase):
    """agents ADR 0003 amendment 2026-10-21: a domain copies its template
    roles' permlevel-0 rows (doctype_defs.DOMAIN_TEMPLATE_ROLES)."""

    matrix = MatrixTests.matrix

    def hr(self, rows=HR_ROWS):
        return self.matrix(["hr_payroll"], {"hr_payroll": ["Employee"]}, rows, HR_META)

    def test_template_rows_are_copied_without_delete(self):
        matrix, _, _ = self.hr()
        self.assertEqual(matrix["Salary Structure"], {"read", "create", "write", "submit", "cancel"})
        self.assertEqual(matrix["Attendance"], {"read", "create", "write", "submit", "cancel"})
        self.assertEqual(matrix["Leave Allocation"], {"read", "create"})      # HR User row
        self.assertEqual(matrix["Holiday List"], {"read"})
        self.assertFalse(any(p - set(init_bot.PROVISION_PTYPES) for p in matrix.values()))

    def test_submit_only_on_submittable_and_read_only_on_singles(self):
        matrix, _, _ = self.hr()
        self.assertEqual(matrix["Job Opening"], {"read", "create", "write"})
        self.assertEqual(matrix["HR Settings"], {"read"})

    def test_child_tables_privileged_permlevel_and_owner_rows_are_left_out(self):
        matrix, skipped, _ = self.hr()
        self.assertNotIn("Salary Detail", matrix)
        self.assertNotIn("User", matrix)
        self.assertIn("User", skipped)
        self.assertNotIn("Expense Claim", matrix)
        self.assertEqual(matrix["Employee"], {"read", "create", "write"})   # code row only

    def test_link_targets_of_writable_template_doctypes_get_read(self):
        matrix, _, _ = self.hr()
        self.assertEqual(matrix["Company"], {"read"})
        self.assertEqual(matrix["Salary Component"], {"read"})            # child-table Link
        self.assertNotIn("Never Read", matrix)                              # read-only doctype

    def test_templates_come_only_with_their_domain(self):
        matrix, _, _ = self.matrix(["procurement"], {"procurement": ["Supplier"],
                                                     "hr_payroll": ["Employee"]}, HR_ROWS, HR_META)
        self.assertNotIn("Salary Structure", matrix)

    def test_a_doctype_without_a_doctype_record_is_not_planned(self):
        # demo_erp 2026-10-21: Accounts Manager kept rows on Payment Gateway
        # (app removed); getdoctype answered, the `add` RPC then 404'd mid-run.
        rows = {"HR Manager": HR_ROWS["HR Manager"] + [row("Payment Gateway", read=1, write=1)]}
        matrix, _, not_found = self.matrix(["hr_payroll"], {"hr_payroll": ["Employee"]}, rows,
                                           HR_META, missing={"Payment Gateway", "Company"})
        self.assertNotIn("Payment Gateway", matrix)
        self.assertNotIn("Company", matrix)                                 # dangling Link target
        self.assertEqual(not_found, ["Company", "Payment Gateway"])
        self.assertIn("Salary Structure", matrix)

    def test_a_template_role_with_no_rows_is_reported(self):
        self.hr({"HR Manager": HR_ROWS["HR Manager"]})
        self.assertEqual(self.result[3], ["HR User"])

    def test_template_domains_names_the_domain_whose_role_holds_the_right(self):
        live = [{"role": "HR Manager", "permlevel": 0, "create": 1},
                {"role": "Stock User", "permlevel": 0, "if_owner": 1, "create": 1},
                {"role": "Purchase User", "permlevel": 0, "read": 1}]
        allow = {"hr_payroll": ["Employee"], "inventory": ["Stock Entry"],
                 "procurement": ["Supplier"]}
        with patch.object(init_bot, "provisionable_domains", return_value=allow), \
                patch.object(init_bot, "_live_permissions", return_value=live):
            self.assertEqual(init_bot.template_domains("qa", "Salary Structure"), ["hr_payroll"])
            self.assertEqual(init_bot.template_domains("qa", "User"), [])

    def test_template_roles_are_never_privileged_and_cover_only_bot_domains(self):
        from qkeee_erp_plugin.qkeee_erp import execute_write  # noqa: F401 — registers allowlists
        from qkeee_erp_plugin.qkeee_erp.doctype_defs import DOMAIN_TEMPLATE_ROLES
        bot_domains = set(init_bot.provisionable_domains())
        self.assertLessEqual(set(DOMAIN_TEMPLATE_ROLES), bot_domains)
        for roles in DOMAIN_TEMPLATE_ROLES.values():
            self.assertFalse({"System Manager", "Administrator", init_bot.ROLE_NAME} & set(roles))


class BusinessPlanTests(unittest.TestCase):

    def plan(self, live_rows):
        matrix = {"Item": {"read", "create", "write"}, "UOM": {"read"},
                  "Purchase Order": {"read", "create", "write", "submit", "cancel"}}
        with patch.object(init_bot, "bot_permission_matrix", return_value=(matrix, ["User"], [], [])), \
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
