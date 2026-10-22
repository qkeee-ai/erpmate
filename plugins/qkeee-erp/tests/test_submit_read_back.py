"""Read-back after submit and cancel (HRMS demo run review, issue 03).

HRMS submits a Payroll Entry in a background job: the submit call returns,
and on failure the record stays docstatus 0, status Failed, with an
error_message. A step counts as done only when the record shows it."""

import json
import unittest

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import connector

SUBMIT = {"doctype": "Payroll Entry", "action": "submit", "name": "HR-PRUN-0001"}
CANCEL = {"doctype": "Payroll Entry", "action": "cancel", "name": "HR-PRUN-0001"}


def _read_back(data=None, error=None):
    def request(cfg, method, path, params=None, payload=None):
        if method == "GET" and path.endswith("/HR-PRUN-0001"):
            if error:
                raise error
            return {"data": data}
        return {"message": {}}
    return request


class ReadBackTests(unittest.TestCase):

    def _run(self, args, read_back, steps=1):
        with connector() as m:
            m["_request"].side_effect = read_back
            full, exec_ctx, _ = testsupport.render("unscoped.generic", dict(args))
            batch = [{"op": "unscoped.generic", "args": full,
                      "confirmation_token": exec_ctx.confirmation_token,
                      "issued_at": exec_ctx.issued_at,
                      "user_confirmation_text": exec_ctx.user_confirmation_text}] * steps
            report = operations.run_batch(batch, testsupport.ctx())
            return report, m

    def test_background_failure_is_a_failed_step_with_error_message(self):
        report, m = self._run(SUBMIT, _read_back({"name": "HR-PRUN-0001", "docstatus": 0,
                                                   "status": "Failed",
                                                   "error_message": "Payroll Payable missing"}),
                              steps=2)
        first, second = report["steps"]
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["outcome"], "not_applied")
        self.assertIs(first["state_confirmed"], False)
        self.assertEqual(first["read_back"], {"docstatus": 0, "status": "Failed",
                                              "error_message": "Payroll Payable missing"})
        self.assertIn("Payroll Payable missing", first["error"])
        self.assertEqual(second["status"], "not_attempted")
        self.assertEqual(report["stopped_at"], 1)
        finish = m["record_audit_log_finish"].call_args
        self.assertEqual(finish.kwargs["status"], "Failure")

    def test_normal_submit_is_state_confirmed(self):
        report, _ = self._run(SUBMIT, _read_back({"name": "HR-PRUN-0001", "docstatus": 1,
                                                   "status": "Submitted"}))
        self.assertEqual(report["steps"][0]["status"], "succeeded")
        self.assertIs(report["steps"][0]["state_confirmed"], True)

    def test_cancel_expects_docstatus_2(self):
        report, _ = self._run(CANCEL, _read_back({"name": "HR-PRUN-0001", "docstatus": 1}))
        self.assertEqual(report["steps"][0]["status"], "failed")
        report, _ = self._run(CANCEL, _read_back({"name": "HR-PRUN-0001", "docstatus": 2}))
        self.assertIs(report["steps"][0]["state_confirmed"], True)

    def test_read_back_error_is_null_with_warning_and_nothing_undone(self):
        with connector() as m:
            m["_request"].side_effect = _read_back(error=core_client.ConnectorError("network down"))
            full, exec_ctx, _ = testsupport.render("unscoped.generic", dict(SUBMIT))
            result = operations.run_operation("unscoped.generic", full, exec_ctx)
        self.assertIsNone(result["state_confirmed"])
        self.assertTrue(any("could not read" in n for n in result["_notes"]))
        self.assertEqual(m["record_audit_log_finish"].call_args.kwargs["status"], "Success")

    def test_create_has_no_read_back(self):
        with connector() as m:
            full, exec_ctx, _ = testsupport.render(
                "unscoped.generic", {"doctype": "Payroll Entry", "action": "create", "payload": {}})
            result = operations.run_operation("unscoped.generic", full, exec_ctx)
        self.assertNotIn("state_confirmed", result)
        self.assertFalse(m["_request"].called)


class ToolResultTests(unittest.TestCase):

    def test_single_execute_reports_not_applied(self):
        with connector() as m:
            m["_request"].side_effect = _read_back({"name": "HR-PRUN-0001", "docstatus": 0,
                                                    "status": "Failed", "error_message": "boom"})
            full, exec_ctx, _ = testsupport.render("unscoped.generic", dict(SUBMIT))
            tools = erp_tools.ErpTools(lambda: {"active_env": "test", "mode": "read-write"},
                                       requester=lambda: testsupport.REQ,
                                       audit=lambda args: {})
            out = json.loads(tools.handler("erp_execute_write")({
                "phase": "execute", "op": "unscoped.generic", "args": full,
                "confirmation_token": exec_ctx.confirmation_token, "issued_at": exec_ctx.issued_at,
                "user_confirmation_text": exec_ctx.user_confirmation_text,
                "latest_prompt": exec_ctx.user_confirmation_text}))
        self.assertFalse(out["ok"])
        self.assertIs(out["state_confirmed"], False)
        self.assertEqual(out["outcome"], "not_applied")
        self.assertEqual(out["read_back"]["error_message"], "boom")


if __name__ == "__main__":
    unittest.main()
