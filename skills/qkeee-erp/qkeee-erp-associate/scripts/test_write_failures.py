#!/usr/bin/env python3
"""Issue 07: structured create/update failures and the batch stop rule.

Preflight cannot see server scripts or custom-app validate hooks; they run
only on save. When ERPNext rejects a write, the agent gets one structured
object instead of raw Frappe text, so it asks the user instead of guessing
a fix. A batch stops at the first failure and reports every step."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

import execute_write
import testsupport
from core import client as core_client
from core import operations


def frappe_error(exc_type, exception, messages=(), status=417):
    body = {"exc_type": exc_type, "exception": exception, "exc": "[\"Traceback ...\"]"}
    if messages:
        body["_server_messages"] = json.dumps([json.dumps({"message": m}) for m in messages])
    return core_client.ERPNextAPIError(status, "POST", "/api/resource/Employee",
                                       {"tag": "t", "base_url": "https://t.example.com"},
                                       json.dumps(body))


FIXTURES = {
    "MandatoryError": (
        frappe_error("MandatoryError",
                     "frappe.exceptions.MandatoryError: [Employee, new-employee-abc]: gender, "
                     "date_of_birth",
                     ["Error: Value missing for Employee: Gender"]),
        {"error_class": "MandatoryError", "missing_fields": ["gender", "date_of_birth"],
         "invalid_links": [], "message": "Error: Value missing for Employee: Gender"}),
    "LinkValidationError": (
        frappe_error("LinkValidationError",
                     "frappe.exceptions.LinkValidationError: Could not find Department: Engg",
                     ["Could not find <strong>Department</strong>: Engg"]),
        {"error_class": "LinkValidationError", "missing_fields": [],
         "invalid_links": [{"field": "Department", "value": "Engg"}],
         "message": "Could not find Department: Engg"}),
    "ValidationError": (
        frappe_error("ValidationError",
                     "frappe.exceptions.ValidationError: Date of Birth cannot be greater than today.",
                     ["Date of Birth cannot be greater than today."]),
        {"error_class": "ValidationError", "missing_fields": [], "invalid_links": [],
         "message": "Date of Birth cannot be greater than today."}),
    "DuplicateEntryError": (
        frappe_error("DuplicateEntryError",
                     "frappe.exceptions.DuplicateEntryError: ('Employee', 'HR-EMP-00006', "
                     "IntegrityError(1062, \"Duplicate entry 'HR-EMP-00006' for key 'PRIMARY'\"))",
                     status=409),
        {"error_class": "DuplicateEntryError", "missing_fields": [], "invalid_links": [],
         "message": "('Employee', 'HR-EMP-00006', IntegrityError(1062, \"Duplicate entry "
                    "'HR-EMP-00006' for key 'PRIMARY'\"))"}),
    "UniqueValidationError": (
        frappe_error("UniqueValidationError",
                     "frappe.exceptions.UniqueValidationError: ('Employee', 'new', IntegrityError())",
                     ["Personal Email must be unique"]),
        {"error_class": "UniqueValidationError", "missing_fields": [], "invalid_links": [],
         "message": "Personal Email must be unique"}),
}


class ParseFrappeFailureTests(unittest.TestCase):

    def test_each_error_class_maps_to_the_structured_object(self):
        for name, (exc, expected) in FIXTURES.items():
            with self.subTest(error_class=name):
                self.assertEqual(operations.parse_write_failure(exc), expected)

    def test_unknown_class_or_non_json_body_is_not_structured(self):
        self.assertIsNone(operations.parse_write_failure(
            frappe_error("PermissionError", "frappe.exceptions.PermissionError: no")))
        waf = core_client.ERPNextAPIError(403, "POST", "/x", {"tag": "t", "base_url": "u"},
                                          "<html>blocked</html>")
        self.assertIsNone(operations.parse_write_failure(waf))
        self.assertIsNone(operations.parse_write_failure(core_client.ConnectorError("boom")))

    def test_api_error_keeps_the_old_message_shape(self):
        exc = FIXTURES["MandatoryError"][0]
        self.assertTrue(str(exc).startswith("ERPNext API error (417) on POST /api/resource/Employee"))
        self.assertIsInstance(exc, core_client.ConnectorError)


class _PipelineMixin(testsupport.OfflineSchemaMixin):
    """Pipeline with the gate, audit and transport stubbed: only the send
    result varies per test."""

    def setUp(self):
        super().setUp()
        for target, kw in ((core_client, dict(attribute="_validate_prod_requester")),
                           (core_client, dict(attribute="record_audit_log_start", return_value="AL-1")),
                           (core_client, dict(attribute="record_audit_log_finish", return_value=True)),
                           (core_client, dict(attribute="get_env_config",
                                              return_value={"tag": "t", "base_url": "https://t"}))):
            p = patch.object(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def mutate_results(self, *results):
        it = iter(results)

        def fake(cfg, doctype, action, payload, name, requested_by, **kw):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return {"data": {"name": r}}
        p = patch.object(core_client, "_do_mutate", side_effect=fake)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock


class StructuredFailureInPipelineTests(_PipelineMixin, unittest.TestCase):

    def test_rejected_create_raises_write_rejected_with_the_structured_object(self):
        self.mutate_results(FIXTURES["MandatoryError"][0])
        with self.assertRaises(core_client.WriteRejectedError) as ctx:
            operations.run_operation("hr_payroll.generic",
                                     {"doctype": "Employee", "action": "create",
                                      "payload": {"first_name": "Demo"}}, testsupport.ctx())
        self.assertEqual(ctx.exception.failure, FIXTURES["MandatoryError"][1])
        self.assertNotIsInstance(ctx.exception, core_client.GateRefusal)

    def test_cli_prints_the_object_as_json_on_failure(self):
        self.mutate_results(FIXTURES["LinkValidationError"][0])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = execute_write.main([
                "--tag", "t", "--mode", "read-write", "--requested-by", testsupport.REQ,
                "--domain", "hr_payroll", "--doctype", "Employee", "--action", "create",
                "--payload", '{"first_name": "Demo", "department": "Engg"}',
                "--session-id", "S", "--channel-metadata", "{}", "--latest-prompt", "p"])
        self.assertEqual(code, execute_write.EXIT_ERROR)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["write_failure"], FIXTURES["LinkValidationError"][1])
        self.assertIn("Never fill a value the user did not give", err.getvalue())


class BatchStopRuleTests(_PipelineMixin, unittest.TestCase):

    STEPS = [{"op": "hr_payroll.generic",
              "args": {"doctype": "Employee", "action": "create", "payload": {"first_name": n}}}
             for n in ("One", "Two", "Three")]

    def test_second_of_three_fails_one_created_one_failed_one_not_attempted(self):
        mutate = self.mutate_results("HR-EMP-1", FIXTURES["MandatoryError"][0], "HR-EMP-3")
        report = operations.run_batch(self.STEPS, testsupport.ctx())
        self.assertEqual(mutate.call_count, 2)
        self.assertEqual([(r["step"], r["status"], r.get("name")) for r in report["steps"]],
                         [(1, "succeeded", "HR-EMP-1"), (2, "failed", None),
                          (3, "not_attempted", None)])
        self.assertEqual(report["steps"][1]["write_failure"], FIXTURES["MandatoryError"][1])
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual(report["counts"], {"succeeded": 1, "failed": 1, "not_attempted": 1})
        self.assertTrue(all(r["doctype"] == "Employee" and r["action"] == "create"
                            for r in report["steps"]))

    def test_clean_batch_runs_every_step(self):
        self.mutate_results("A", "B", "C")
        report = operations.run_batch(self.STEPS, testsupport.ctx())
        self.assertIsNone(report["stopped_at"])
        self.assertEqual(report["counts"], {"succeeded": 3, "failed": 0, "not_attempted": 0})

    def test_a_gate_refusal_also_stops_the_batch(self):
        self.mutate_results("A", "B", "C")
        steps = [self.STEPS[0], {"op": "hr_payroll.generic",
                                 "args": {"doctype": "Sales Order", "action": "create",
                                          "payload": {}}}, self.STEPS[2]]
        report = operations.run_batch(steps, testsupport.ctx())
        self.assertEqual([r["status"] for r in report["steps"]],
                         ["succeeded", "failed", "not_attempted"])
        self.assertTrue(report["steps"][1]["refused"])

    def test_cli_batch_prints_the_report_and_exits_with_the_failing_steps_code(self):
        self.mutate_results("A", FIXTURES["ValidationError"][0], "C")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = execute_write.main([
                "--tag", "t", "--mode", "read-write", "--requested-by", testsupport.REQ,
                "--batch", json.dumps(self.STEPS),
                "--session-id", "S", "--channel-metadata", "{}", "--latest-prompt", "p"])
        self.assertEqual(code, execute_write.EXIT_ERROR)
        report = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in report["steps"]],
                         ["succeeded", "failed", "not_attempted"])

    def test_cli_batch_cannot_be_combined_with_a_single_op(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = execute_write.main([
                    "--tag", "t", "--mode", "read-write", "--requested-by", testsupport.REQ,
                    "--batch", json.dumps(self.STEPS), "--op", "hr_payroll.generic"])
            except SystemExit as e:
                code = e.code
        self.assertEqual(code, execute_write.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
