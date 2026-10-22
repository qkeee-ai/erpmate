"""One confirmation code per batch (HRMS demo run review, issue 02, D2).

A batch render returns one token and one code over the ordered steps;
execute refuses any difference before step 1, verifies the code once, and
runs each step through the normal pipeline. A step may take its
`expected_modified` from an earlier step's result (F3)."""

import copy
import json
import time
import unittest

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import LIVE, connector

MIXED = [
    {"op": "hr_payroll.generic", "args": {"doctype": "Employee", "action": "create",
                                          "payload": {"first_name": "Arjun"}}},
    {"op": "hr_payroll.generic", "args": {"doctype": "Leave Application", "action": "submit",
                                          "name": "HR-LAP-2026-00001"}},
    {"op": "auto", "args": {"doctype": "Interview Type", "action": "create",
                            "payload": {"name": "Technical"}}},
    {"op": "sales.generic", "args": {"doctype": "Sales Order", "action": "submit",
                                     "name": "SO-1"}},
    {"op": "sales.generic", "args": {"doctype": "Customer", "action": "update", "name": "C-1",
                                     "payload": {"customer_group": "Commercial"}}},
]


def _render(steps, **kw):
    return operations.prepare_batch(copy.deepcopy(steps), testsupport.ctx(), **kw)


def _execute(out, steps=None, reply=None):
    ctx = testsupport.ctx(issued_at=out["issued_at"],
                          user_confirmation_text=reply or f"yes {out['confirmation_code']}")
    return operations.run_batch(copy.deepcopy(steps if steps is not None else out["batch"]), ctx,
                                batch_token=out["batch_token"])


class RenderTests(unittest.TestCase):

    def test_one_token_one_code_and_a_line_per_step(self):
        with connector():
            out = _render(MIXED)
        self.assertEqual(len(out["confirmation_code"]), 6)
        self.assertIn("expires_at", out)
        self.assertEqual([s["step"] for s in out["steps"]], [1, 2, 3, 4, 5])
        self.assertIn("no code needed", out["steps"][0]["line"])
        self.assertIn("SUBMIT", out["steps"][1]["line"])
        self.assertIn("SUBMIT", out["steps"][3]["line"])
        self.assertEqual(out["batch"][2]["op"], "unscoped.generic")
        self.assertEqual(out["batch"][1]["args"]["expected_modified"], LIVE["modified"])

    def test_26_steps_refused(self):
        step = MIXED[0]
        with connector(), self.assertRaises(core_client.InvalidArgumentsError):
            _render([step] * 26)

    def test_unknown_step_key_refused(self):
        with connector(), self.assertRaises(core_client.InvalidArgumentsError):
            _render([dict(MIXED[0], confirmation_token="x")])


class ExecuteTests(unittest.TestCase):

    def test_one_code_executes_five_mixed_steps(self):
        with connector() as m:
            out = _render(MIXED)
            report = _execute(out)
        self.assertEqual(report["counts"], {"succeeded": 5, "failed": 0, "not_attempted": 0})
        self.assertEqual(m["_do_mutate"].call_count, 5)

    def test_any_change_refused_before_step_1(self):
        with connector():
            out = _render(MIXED)
        b = out["batch"]
        changed = copy.deepcopy(b)
        changed[4]["args"]["payload"]["customer_group"] = "Retail"
        cases = {"added": b + [b[0]], "removed": b[:-1], "reordered": [b[1], b[0]] + b[2:],
                 "changed": changed, "other op": [dict(b[0], op="sales.generic")] + b[1:]}
        for label, steps in cases.items():
            with self.subTest(label), connector() as m:
                with self.assertRaises(core_client.GateRefusal):
                    _execute(out, steps)
                self.assertFalse(m["_do_mutate"].called)

    def test_wrong_code_refused(self):
        with connector() as m:
            out = _render(MIXED)
            with self.assertRaises(core_client.UnconfirmedByUserError):
                _execute(out, reply="yes 000000")
        self.assertFalse(m["_do_mutate"].called)

    def test_expired_batch_refused(self):
        with connector() as m:
            out = _render(MIXED, issued_at=int(time.time()) - 16 * 60)
            with self.assertRaises(core_client.StaleConfirmationError):
                _execute(out)
        self.assertFalse(m["_do_mutate"].called)

    def test_every_audit_row_names_the_batch_code(self):
        with connector() as m:
            out = _render(MIXED)
            _execute(out)
        notes = [c.kwargs["approval_note"] for c in m["record_audit_log_start"].call_args_list]
        self.assertEqual(len(notes), 5)
        for i, note in enumerate(notes, start=1):
            self.assertIn(f"batch {out['confirmation_code']} step {i}/5", note)

    def test_stops_at_first_failure(self):
        with connector() as m:
            out = _render(MIXED)
            m["_do_mutate"].side_effect = [{"data": {"name": "E-1"}},
                                           core_client.ConnectorError("boom")]
            report = _execute(out)
        self.assertEqual([s["status"] for s in report["steps"]],
                         ["succeeded", "failed", "not_attempted", "not_attempted",
                          "not_attempted"])


class FromStepTests(unittest.TestCase):
    STEPS = [
        {"op": "unscoped.generic", "args": {"doctype": "Interview Feedback", "action": "update",
                                            "name": "IF-1", "payload": {"interview": "INT-2"}}},
        {"op": "unscoped.generic", "args": {"doctype": "Interview Feedback", "action": "submit",
                                            "name": "IF-1",
                                            "expected_modified": {"from_step": 1}}},
    ]

    def test_from_step_takes_step_k_modified(self):
        with connector() as m:
            out = _render(self.STEPS)
            self.assertEqual(out["batch"][1]["args"]["expected_modified"], {"from_step": 1})
            m["_do_mutate"].return_value = {"data": {"name": "IF-1", "modified": "M2"}}
            m["get_resource"].return_value = {"data": dict(LIVE, name="IF-1", modified="M2")}
            report = _execute(out)
        self.assertEqual(report["counts"]["succeeded"], 2, report)

    def test_from_step_refuses_a_different_record(self):
        steps = copy.deepcopy(self.STEPS)
        steps[0] = {"op": "unscoped.generic", "args": {"doctype": "Interview Feedback",
                                                       "action": "create", "payload": {}}}
        with connector() as m:
            out = _render(steps)
            m["_do_mutate"].return_value = {"data": {"name": "IF-9", "modified": "M2"}}
            report = _execute(out)
        self.assertEqual(report["steps"][1]["status"], "failed")
        self.assertIn("different record", report["steps"][1]["error"])
        self.assertEqual(m["_do_mutate"].call_count, 1)

    def test_from_step_must_point_back(self):
        steps = copy.deepcopy(self.STEPS)
        steps[1]["args"]["expected_modified"] = {"from_step": 2}
        with connector(), self.assertRaises(core_client.InvalidArgumentsError):
            _render(steps)

    def test_from_step_refuses_a_named_different_record_at_render(self):
        steps = copy.deepcopy(self.STEPS)
        steps[0]["args"]["name"] = "IF-2"
        with connector(), self.assertRaises(core_client.InvalidArgumentsError):
            _render(steps)


class ToolTests(unittest.TestCase):

    def test_render_and_execute_through_erp_execute_write(self):
        tools = erp_tools.ErpTools(lambda: {"active_env": "test", "mode": "read-write"},
                                   requester=lambda: testsupport.REQ, audit=lambda args: {})
        handle = tools.handler("erp_execute_write")
        with connector() as m:
            rendered = json.loads(handle({"phase": "render", "batch": copy.deepcopy(MIXED)}))
            self.assertTrue(rendered["ok"], rendered)
            r = rendered["result"]
            out = json.loads(handle({"phase": "execute", "batch": r["batch"],
                                     "batch_token": r["batch_token"], "issued_at": r["issued_at"],
                                     "user_confirmation_text": r["confirmation_code"],
                                     "latest_prompt": r["confirmation_code"]}))
        self.assertTrue(out["ok"], out)
        self.assertEqual(m["_do_mutate"].call_count, 5)


if __name__ == "__main__":
    unittest.main()
