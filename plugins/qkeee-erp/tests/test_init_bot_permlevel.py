"""init-bot: permlevel-1 rows for status fields that gate submit (HRMS demo
run review, issue 07, D3; agents ADR 0003 amendment 2026-10-22).

Leave Application submit needs status = Approved, and `status` is a
permlevel-1 field. init-bot copies a template role's row at that level,
read and write only, for the fields in doctype_defs.SUBMIT_GATE_FIELDS."""

import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp import init_bot
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
from test_init_bot_provisioning import AllBut, CTX, ROLE, bundle, row

LEAVE_META = bundle("Leave Application", True, [("employee", "Employee")])
LEAVE_META[0]["fields"].append({"fieldname": "status", "fieldtype": "Select", "permlevel": 1})
EMPLOYEE_META = bundle("Employee")
EMPLOYEE_META[0]["fields"].append({"fieldname": "salary", "fieldtype": "Currency", "permlevel": 1})
META = {"Leave Application": LEAVE_META, "Employee": EMPLOYEE_META}
ROWS = {"HR Manager": [
    row("Leave Application", read=1, create=1, write=1, submit=1, cancel=1),
    row("Leave Application", permlevel=1, read=1, write=1, delete=1),
    row("Employee", read=1, create=1, write=1),
    row("Employee", permlevel=1, read=1, write=1),
]}


def matrix(rows=ROWS):
    with patch.object(init_bot, "provisionable_domains",
                      return_value={"hr_payroll": ["Employee", "Leave Application"]}), \
            patch.object(init_bot, "_doctype_names", return_value=AllBut()), \
            patch.object(init_bot, "_role_permissions",
                         side_effect=lambda tag, role: rows.get(role, [])), \
            patch.object(init_bot, "_meta_bundle",
                         side_effect=lambda tag, dt: META.get(dt, bundle(dt))):
        return init_bot.bot_permission_matrix("qa", ["hr_payroll"])[0]


class PlanTests(unittest.TestCase):

    def test_template_level_1_row_on_leave_application_is_planned(self):
        m = matrix()
        self.assertEqual(m["Leave Application@1"], {"read", "write"})
        self.assertNotIn("Employee@1", m)       # not a listed submit-gate field

    def test_no_template_row_at_level_1_plans_nothing(self):
        rows = {"HR Manager": [r for r in ROWS["HR Manager"]
                               if not (r["parent"] == "Leave Application" and r["permlevel"])]}
        self.assertNotIn("Leave Application@1", matrix(rows))

    def test_business_plan_steps_and_extras_use_the_level_key(self):
        m = {"Leave Application": {"read", "create", "write", "submit", "cancel"},
             "Leave Application@1": {"read", "write"}}
        live = [{"parent": "Leave Application", "permlevel": 0, "read": 1, "create": 1, "write": 1,
                 "submit": 1, "cancel": 1},
                {"parent": "Employee", "permlevel": 1, "read": 1}]
        with patch.object(init_bot, "bot_permission_matrix", return_value=(m, [], [], [])), \
                patch.object(init_bot, "_live_role_permissions", return_value=live):
            plan = init_bot._business_plan("qa", ["hr_payroll"])
        self.assertEqual(plan["business_grants"], ["Leave Application@1: add",
                                                   "Leave Application@1: write"])
        self.assertEqual(plan["extra_rights"], ["Employee@1: read"])
        live.append({"parent": "Leave Application", "permlevel": 1, "read": 1, "write": 1})
        with patch.object(init_bot, "bot_permission_matrix", return_value=(m, [], [], [])), \
                patch.object(init_bot, "_live_role_permissions", return_value=live):
            plan = init_bot._business_plan("qa", ["hr_payroll"])
        self.assertEqual(plan["business_grants"], [])
        self.assertEqual(plan["extra_rights"], ["Employee@1: read"])

    def test_dry_run_token_changes_with_the_level_1_plan(self):
        base = {"role_needed": False, "doctypes_needed": [], "fields_to_migrate": [],
                "bot_read_grants": [], "bot_role_change": None,
                "business_grants": ["Leave Application: add"]}
        level = dict(base, business_grants=base["business_grants"] + ["Leave Application@1: add"])
        self.assertNotEqual(init_bot._plan_token("qa", "a@b.c", base, 1),
                            init_bot._plan_token("qa", "a@b.c", level, 1))


class GrantOperationTests(unittest.TestCase):
    prep = staticmethod(operations.get_operation("provisioning.grant_bot_permission").prepare)

    def test_level_1_on_a_submit_gate_doctype(self):
        req = self.prep({"doctype": "Leave Application", "step": "add", "permlevel": 1}, CTX)
        self.assertEqual(req.body, {"parent": "Leave Application", "role": ROLE, "permlevel": 1})
        req = self.prep({"doctype": "Leave Application", "step": "write", "permlevel": 1}, CTX)
        self.assertEqual(req.body["permlevel"], 1)
        self.assertEqual(req.body["ptype"], "write")

    def test_level_1_refused_elsewhere_or_beyond_read_write(self):
        for args in ({"doctype": "Employee", "step": "write", "permlevel": 1},
                     {"doctype": "Leave Application", "step": "submit", "permlevel": 1},
                     {"doctype": "Leave Application", "step": "write", "permlevel": "1"}):
            with self.subTest(args=args), self.assertRaises(core_client.DoctypeNotAllowedError):
                self.prep(args, CTX)

    def test_level_must_match_the_live_meta(self):
        op = operations.get_operation("provisioning.grant_bot_permission")
        check = op.preconditions[0]
        for level, ok in ((1, True), (2, False)):
            args = {"doctype": "Leave Application", "step": "write", "permlevel": level}
            req = op.prepare(args, CTX)
            with self.subTest(level=level), \
                    patch.object(init_bot, "_meta_bundle", return_value=LEAVE_META):
                if ok:
                    check(req, args, CTX)
                else:
                    with self.assertRaises(core_client.DoctypeNotAllowedError):
                        check(req, args, CTX)

    def test_grant_business_permissions_passes_the_level(self):
        with patch.object(operations, "run_operation") as run:
            init_bot.grant_business_permissions("qa", "a@b.c", ["Leave Application@1: write",
                                                               "Item: add"], "note")
        self.assertEqual(run.call_args_list[0].args[1],
                         {"doctype": "Leave Application", "step": "write", "permlevel": 1})
        self.assertEqual(run.call_args_list[1].args[1], {"doctype": "Item", "step": "add"})


if __name__ == "__main__":
    unittest.main()
