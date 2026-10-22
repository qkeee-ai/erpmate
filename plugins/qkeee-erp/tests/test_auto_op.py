"""`op: "auto"` routes a write to the owning operation (HRMS demo run
review, issue 12, D5).

The agent picked the wrong operation 8 times in the run. `auto` resolves
at render to the bot-credential `<domain>.generic` whose allowlist holds
the doctype, else `unscoped.generic`; the token binds the resolved key,
and every gate runs on it unchanged."""

import json
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import connector

LEAVE_SUBMIT = {"doctype": "Leave Application", "action": "submit", "name": "HR-LAP-2026-00001"}


class ResolveTests(unittest.TestCase):

    def test_domain_doctype_resolves_to_its_generic(self):
        self.assertEqual(operations.resolve_op_key("auto", {"doctype": "Leave Application"}),
                         "hr_payroll.generic")

    def test_unowned_doctype_resolves_to_unscoped(self):
        self.assertEqual(operations.resolve_op_key("auto", {"doctype": "Interview Type"}),
                         "unscoped.generic")

    def test_named_key_is_kept(self):
        self.assertEqual(operations.resolve_op_key("sales.generic", {"doctype": "Item"}),
                         "sales.generic")

    def test_never_resolves_to_an_admin_key_operation(self):
        self.assertEqual(operations.resolve_op_key("auto", {"doctype": "Custom Field"}),
                         "unscoped.generic")

    def test_doctype_in_two_allowlists_is_ambiguous(self):
        lists = dict(core_client.DOMAIN_WRITE_ALLOWLISTS,
                     sales=core_client.DOMAIN_WRITE_ALLOWLISTS["sales"] + ("Leave Application",))
        with patch.object(core_client, "DOMAIN_WRITE_ALLOWLISTS", lists):
            with self.assertRaises(core_client.DoctypeNotAllowedError) as cm:
                operations.resolve_op_key("auto", {"doctype": "Leave Application"})
        self.assertIn("ambiguous", str(cm.exception))

    def test_auto_needs_a_doctype(self):
        with self.assertRaises(core_client.InvalidArgumentsError):
            operations.resolve_op_key("auto", {})


class PipelineTests(unittest.TestCase):

    def test_render_shows_resolved_op_and_execute_with_auto_runs(self):
        with connector() as m:
            out = operations.prepare_only("auto", dict(LEAVE_SUBMIT), testsupport.ctx())
            self.assertEqual(out["resolved_op"], "hr_payroll.generic")
            self.assertEqual(out["op"], "hr_payroll.generic")
            exec_ctx = testsupport.ctx(confirmation_token=out["confirmation_token"],
                                       issued_at=out["issued_at"],
                                       user_confirmation_text=out["confirmation_code"])
            result = operations.run_operation("auto", out["args"], exec_ctx)
        self.assertEqual(result["_operation"], "hr_payroll.generic")
        self.assertTrue(m["_do_mutate"].called)

    def test_deny_list_doctype_refused_as_before(self):
        with connector() as m:
            with self.assertRaises(core_client.DoctypeNotAllowedError):
                operations.prepare_only("auto", {"doctype": "User", "action": "create",
                                                 "payload": {"email": "x@example.com"}},
                                        testsupport.ctx())
        self.assertFalse(m["_do_mutate"].called)

    def test_token_rendered_with_auto_refuses_another_operation(self):
        args = {"doctype": "Interview Type", "action": "create", "payload": {"name": "Tech"}}
        with connector() as m:
            out = operations.prepare_only("auto", args, testsupport.ctx())
            exec_ctx = testsupport.ctx(confirmation_token=out["confirmation_token"],
                                       issued_at=out["issued_at"],
                                       user_confirmation_text=out["confirmation_code"])
            with self.assertRaises(core_client.GateRefusal):
                operations.run_operation("hr_payroll.generic", out["args"], exec_ctx)
        self.assertFalse(m["_do_mutate"].called)


class ToolTests(unittest.TestCase):

    def test_erp_execute_write_accepts_auto(self):
        tools = erp_tools.ErpTools(lambda: {"active_env": "test", "mode": "read-write"},
                                   requester=lambda: testsupport.REQ, audit=lambda args: {})
        with connector():
            out = json.loads(tools.handler("erp_execute_write")({
                "phase": "execute", "op": "auto",
                "args": {"doctype": "Employee", "action": "create", "payload": {}}}))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["result"]["_operation"], "hr_payroll.generic")


if __name__ == "__main__":
    unittest.main()
