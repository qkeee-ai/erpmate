"""One confirmation code per batch (HRMS demo run review, issue 02, D2).

A batch render returns one token and one code over the ordered steps;
execute refuses any difference before step 1, verifies the code once, and
runs each step through the normal pipeline. A step may take its
`expected_modified` from an earlier step's result (F3)."""

import copy
import json
import time
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp import discover
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
        self.assertEqual(report["counts"], {"succeeded": 5, "failed": 0, "parked": 0, "not_attempted": 0})
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


# =============================================================== job_id / job_batch_index
#
# Issue 06/ADR 0008: a kanban child task consumes an already-Approved job
# (issue 03's store, approved by operations.verify_job_reply()) instead of
# running its own fresh render-then-chat-code round trip — there is no
# live chat session inside a dispatched kanban worker to answer one.

JOB_ID = "JOB-7"


def _rendered_plan(batches):
    """prepare_job()'s own `out["job"]` construction: each step's args as
    render_defaults left them (e.g. a submit's `expected_modified` filled
    from the live record) — the stored plan is never the bare, unrendered
    MIXED steps."""
    with connector():
        prepared = [operations._batch_requests(copy.deepcopy(b), testsupport.ctx(), render=True)
                   for b in batches]
    return [[{"op": op.key, "args": args} for op, args, _, _ in batch] for batch in prepared]


# batch 1: one step; batch 2: the other four — exercises a non-1 index too.
JOB_PLAN = _rendered_plan([[MIXED[0]], MIXED[1:]])


def _job_token_for(requested_by, issued_at):
    """The real job_token() over JOB_PLAN — same recompute _verify_job_batch
    itself does (render=False; JOB_PLAN's args are already render-enriched),
    so `_job_row()`'s stored token is one a correct, unmodified batch
    submission actually matches, not an arbitrary placeholder string."""
    with connector():
        prepared = operations._job_requests(copy.deepcopy(JOB_PLAN),
                                            testsupport.ctx(requested_by=requested_by), render=False)
    return operations.job_token(prepared, requested_by, JOB_ID, issued_at)


def _job_row(**over):
    requested_by = over.get("requested_by", testsupport.REQ)
    issued_at = over.get("issued_at", int(time.time()))
    row = dict(job_id=JOB_ID, requested_by=requested_by,
              token=_job_token_for(requested_by, issued_at), step_count=5, status="Approved",
              issued_at=issued_at, plan=json.dumps(JOB_PLAN))
    row.update(over)
    return row


def _get_resource_side_effect(row):
    """get_resource is one mock shared by every doctype in connector(): the
    Job Approval lookup needs `row`, but a gated step's own live read
    (e.g. a submit's expected_modified check) still needs the ordinary
    LIVE record — never the Job Approval row."""
    def _side_effect(tag, doctype, name, **kw):
        if doctype == core_client.JOB_APPROVAL_DOCTYPE:
            return {"data": dict(row)}
        return {"data": copy.deepcopy(LIVE)}
    return _side_effect


class JobBatchTests(unittest.TestCase):

    def _run(self, index=2, steps=None, row=None, **ctx_kw):
        data = row if row is not None else _job_row()
        steps = steps if steps is not None else copy.deepcopy(JOB_PLAN[index - 1])
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(data)
            report = operations.run_batch(steps, testsupport.ctx(**ctx_kw), job_id=JOB_ID,
                                          job_batch_index=index)
        return report, m

    def test_executes_the_matching_batch_with_no_confirmation_fields_in_ctx(self):
        """ctx carries no confirmation_token/user_confirmation_text at all
        — the job's email approval is the proof, not a chat round trip."""
        ctx = testsupport.ctx()
        self.assertIsNone(ctx.confirmation_token)
        self.assertIsNone(ctx.user_confirmation_text)
        report, m = self._run(index=2)
        self.assertEqual(report["counts"], {"succeeded": 4, "failed": 0, "parked": 0, "not_attempted": 0})
        # 4 step writes + one durable execution-log append (issue 16).
        self.assertEqual(m["_do_mutate"].call_count, 5)

    def test_match_check_uses_the_stored_requester_not_ctxs(self):
        """The executing kanban worker's own ctx.requested_by (its service
        identity) need not equal the Requester the job was approved for —
        job_token() is recomputed with the STORED requested_by, so a
        worker is never required to impersonate that Requester."""
        report, m = self._run(index=2, requested_by="kanban-worker@example.com")
        self.assertEqual(report["counts"]["succeeded"], 4)
        self.assertFalse(report["stopped_at"])

    def test_every_audit_row_names_the_job_and_batch_position(self):
        # Excludes the one extra audit row from the batch's own durable
        # execution-log append (issue 16), which names the Job Approval
        # doctype, not a step's own doctype.
        _, m = self._run(index=2)
        notes = [c.kwargs["approval_note"] for c in m["record_audit_log_start"].call_args_list
                if c.kwargs["doctype"] != core_client.JOB_APPROVAL_DOCTYPE]
        self.assertEqual(len(notes), 4)
        for i, note in enumerate(notes, start=1):
            self.assertIn(f"job {JOB_ID} batch 2/2 step {i}/4", note)

    def test_first_batch_of_the_job_is_also_addressable(self):
        report, m = self._run(index=1)
        self.assertEqual(report["counts"]["succeeded"], 1)
        # The batch's own step audit row, not the trailing execution-log
        # append's (issue 16) — filter by doctype rather than "most recent".
        step_calls = [c for c in m["record_audit_log_start"].call_args_list
                     if c.kwargs["doctype"] != core_client.JOB_APPROVAL_DOCTYPE]
        self.assertIn(f"job {JOB_ID} batch 1/2 step 1/1", step_calls[-1].kwargs["approval_note"])

    def test_a_rejected_write_parks_instead_of_stopping_the_job(self):
        # agents ADR 0010/issue 19: ERPNext's own structured rejection
        # (a missing/invalid field the approved template didn't
        # anticipate) parks the step in a job batch — the job isn't
        # stopped by it, and issue 16's ledger must distinguish "this
        # step parked" from both "it succeeded" and "it never ran".
        # Issue 19 bullet 1's own live check (erp_discover preflight for
        # a create step) ran and is recorded alongside the park.
        with connector() as m, patch.object(
                discover, "preflight", return_value={"ready": False, "missing_fields": ["department"]}) as pf:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            m["_do_mutate"].side_effect = [
                core_client.WriteRejectedError(
                    "boom", {"error_class": "ValidationError", "missing_fields": [],
                            "invalid_links": [], "message": "boom"}),
                {"data": {"name": "JOB-7"}},
            ]
            report = operations.run_batch(copy.deepcopy(JOB_PLAN[0]), testsupport.ctx(),
                                          job_id=JOB_ID, job_batch_index=1)
        self.assertIsNone(report["stopped_at"])
        self.assertEqual(report["steps"][0]["status"], "parked")
        self.assertEqual(report["counts"], {"succeeded": 0, "failed": 0, "parked": 1, "not_attempted": 0})
        pf.assert_called_once()
        self.assertEqual(pf.call_args.kwargs["payload"], {"first_name": "Arjun"})
        checked = report["steps"][0]["checked"]
        self.assertIn("preflight", checked)
        self.assertIn("missing_fields=['department']", checked)
        append_call = next(c for c in m["_do_mutate"].call_args_list
                           if c.args[1] == core_client.JOB_APPROVAL_DOCTYPE)
        logged = json.loads(append_call.args[3]["execution_log"])
        self.assertEqual(logged, [{"batch_index": 1, "step_index": 1, "name": None,
                                   "modified": None, "outcome": "parked", "error": "boom",
                                   "checked": checked,
                                   "write_failure": {"error_class": "ValidationError",
                                                     "missing_fields": [], "invalid_links": [],
                                                     "message": "boom"}}])

    def test_discover_check_uses_preflight_for_create_and_meta_otherwise(self):
        # Issue 19 bullet 1 vs. bullet 2's "existing record" framing maps
        # to which live check is even meaningful: preflight is documented
        # as a write-readiness gate for "one create" only; a submit/
        # cancel/delete on an existing record checks its live schema
        # facts (doctype_meta) instead.
        ctx = testsupport.ctx()
        create_req = operations.PreparedRequest(transport="resource", doctype="Item",
                                                 action="create", body={"item_code": "X"})
        submit_req = operations.PreparedRequest(transport="resource", doctype="Sales Order",
                                                action="submit", name="SO-1")
        with connector(), patch.object(
                discover, "preflight",
                return_value={"ready": False, "missing_fields": ["item_group"]}) as pf:
            checked = operations._discover_check(create_req, ctx)
        pf.assert_called_once_with("test", "Item", payload={"item_code": "X"},
                                   requested_by=testsupport.REQ, **ctx.audit_kwargs())
        self.assertIn("preflight(Item)", checked)
        self.assertIn("missing_fields=['item_group']", checked)

        with connector(), patch.object(
                discover, "doctype_meta",
                return_value={"module": "Selling", "custom_fields_merged": True}) as meta:
            checked = operations._discover_check(submit_req, ctx)
        meta.assert_called_once_with("test", "Sales Order", requested_by=testsupport.REQ,
                                     **ctx.audit_kwargs())
        self.assertIn("meta(Sales Order)", checked)
        self.assertIn("module='Selling'", checked)

    def test_discover_check_failure_is_recorded_not_raised(self):
        with connector() as m, patch.object(discover, "preflight", side_effect=RuntimeError("timeout")):
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            m["_do_mutate"].side_effect = [
                core_client.PreconditionFailedError("locked"),
                {"data": {"name": "JOB-7"}},
            ]
            report = operations.run_batch(copy.deepcopy(JOB_PLAN[0]), testsupport.ctx(),
                                          job_id=JOB_ID, job_batch_index=1)
        self.assertEqual(report["steps"][0]["status"], "parked")
        self.assertIn("erp_discover check on Employee failed: timeout", report["steps"][0]["checked"])

    def test_a_systemic_refusal_still_stops_the_job_rather_than_parking(self):
        # Not every failure is "the template didn't anticipate this one
        # step" — read-only mode, a missing requester, self-escalation or
        # an outright disallowed doctype are systemic; parking one step
        # can't fix them, so the batch still stops exactly as before, and
        # no live check is spent on something a park can't fix anyway.
        with connector() as m, patch.object(discover, "preflight") as pf:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            steps = copy.deepcopy(JOB_PLAN[0])
            report = operations.run_batch(
                steps, testsupport.ctx(mode="read-only"), job_id=JOB_ID, job_batch_index=1)
        self.assertEqual(report["stopped_at"], 1)
        self.assertEqual(report["steps"][0]["status"], "failed")
        self.assertEqual(report["counts"], {"succeeded": 0, "failed": 1, "parked": 0, "not_attempted": 0})
        self.assertNotIn("checked", report["steps"][0])
        pf.assert_not_called()

    def test_a_plain_403_not_caught_by_any_gate_also_parks(self):
        # agents ADR 0010's own named example: a role-permission gap on
        # one action the template didn't flag, surfaced only as ERPNext's
        # own HTTP rejection (not a structured write_failure, not one of
        # this plugin's own precondition checks).
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            m["_do_mutate"].side_effect = [
                core_client.ERPNextAPIError(403, "POST", "/api/resource/Employee",
                                            {"tag": "t", "base_url": "u"}, "PermissionError"),
                {"data": {"name": "JOB-7"}},
            ]
            report = operations.run_batch(copy.deepcopy(JOB_PLAN[0]), testsupport.ctx(),
                                          job_id=JOB_ID, job_batch_index=1)
        self.assertIsNone(report["stopped_at"])
        self.assertEqual(report["steps"][0]["status"], "parked")
        self.assertEqual(report["counts"], {"succeeded": 0, "failed": 0, "parked": 1, "not_attempted": 0})

    def test_a_401_is_not_park_eligible(self):
        # Unlike a 403 (a permission gap on one flagged action — ADR
        # 0010's own example), a 401 means the bot's own credential is
        # bad — systemic, not something one park fixes.
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            m["_do_mutate"].side_effect = core_client.ERPNextAPIError(
                401, "POST", "/api/resource/Employee", {"tag": "t", "base_url": "u"}, "AuthError")
            report = operations.run_batch(copy.deepcopy(JOB_PLAN[0]), testsupport.ctx(),
                                          job_id=JOB_ID, job_batch_index=1)
        self.assertEqual(report["stopped_at"], 1)
        self.assertEqual(report["steps"][0]["status"], "failed")

    def test_execution_log_append_failure_does_not_discard_the_batchs_real_outcomes(self):
        # The batch's own writes already happened for real; a transient
        # failure saving the ledger entry must be reported, not raised
        # past run_batch() where it would hide those real outcomes.
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            m["_do_mutate"].side_effect = (
                [{"data": {"name": "I-1"}}] * 4 + [core_client.ConnectorError("network blip")])
            report = operations.run_batch(copy.deepcopy(JOB_PLAN[1]), testsupport.ctx(),
                                          job_id=JOB_ID, job_batch_index=2)
        self.assertEqual(report["counts"], {"succeeded": 4, "failed": 0, "parked": 0, "not_attempted": 0})
        self.assertIn("network blip", report["execution_log_error"])

    def test_job_not_approved_is_refused(self):
        with self.assertRaises(core_client.PreconditionFailedError):
            self._run(row=_job_row(status="Pending"))

    def test_unknown_job_is_refused(self):
        with self.assertRaises(core_client.PreconditionFailedError):
            self._run(row={})

    def test_expired_approval_window_is_refused(self):
        stale = _job_row(issued_at=int(time.time()) - operations.JOB_TOKEN_TTL_SECONDS - 3600)
        with self.assertRaises(core_client.StaleConfirmationError):
            self._run(row=stale)

    def test_out_of_range_index_is_refused(self):
        with self.assertRaises(core_client.InvalidArgumentsError):
            self._run(index=3, steps=copy.deepcopy(JOB_PLAN[1]))

    def test_job_batch_index_must_be_a_positive_int(self):
        for bad in (0, -1, "2", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(core_client.InvalidArgumentsError):
                    self._run(index=bad, steps=copy.deepcopy(JOB_PLAN[1]))

    def test_mismatched_batch_is_refused_before_any_write(self):
        tampered = copy.deepcopy(JOB_PLAN[1])
        tampered[3]["args"]["payload"]["customer_group"] = "Retail"  # the Customer update step
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_batch(tampered, testsupport.ctx(), job_id=JOB_ID, job_batch_index=2)
            self.assertFalse(m["_do_mutate"].called)

    def test_a_shorter_or_longer_batch_is_refused(self):
        for steps in (JOB_PLAN[1][:-1], JOB_PLAN[1] + [MIXED[0]]):
            with self.subTest(len=len(steps)), connector() as m:
                m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
                with self.assertRaises(core_client.TokenMismatchError):
                    operations.run_batch(copy.deepcopy(steps), testsupport.ctx(), job_id=JOB_ID,
                                         job_batch_index=2)
                self.assertFalse(m["_do_mutate"].called)

    def test_a_reordered_batch_is_refused(self):
        reordered = [JOB_PLAN[1][1], JOB_PLAN[1][0]] + JOB_PLAN[1][2:]
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_batch(copy.deepcopy(reordered), testsupport.ctx(), job_id=JOB_ID,
                                     job_batch_index=2)
            self.assertFalse(m["_do_mutate"].called)

    def test_batch_token_and_job_id_are_mutually_exclusive(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.run_batch(copy.deepcopy(JOB_PLAN[1]), testsupport.ctx(), batch_token="x",
                                     job_id=JOB_ID, job_batch_index=2)


class JobBatchToolTests(unittest.TestCase):

    def _tools(self):
        return erp_tools.ErpTools(lambda: {"active_env": "test", "mode": "read-write"},
                                  requester=lambda: testsupport.REQ, audit=lambda args: {})

    def test_job_id_without_batch_is_refused(self):
        handle = self._tools().handler("erp_execute_write")
        with connector():
            out = json.loads(handle({"phase": "execute", "op": "hr_payroll.generic",
                                     "args": {"doctype": "Employee", "action": "create",
                                              "payload": {}},
                                     "job_id": JOB_ID, "job_batch_index": 1}))
        self.assertFalse(out["ok"])
        self.assertIn("job_id goes with batch", out["error"])

    def test_job_id_and_batch_token_together_is_refused(self):
        handle = self._tools().handler("erp_execute_write")
        with connector():
            out = json.loads(handle({"phase": "execute", "batch": copy.deepcopy(JOB_PLAN[1]),
                                     "batch_token": "x", "job_id": JOB_ID, "job_batch_index": 2}))
        self.assertFalse(out["ok"])
        self.assertIn("mutually exclusive", out["error"])

    def test_executes_through_the_tool_with_no_latest_prompt(self):
        handle = self._tools().handler("erp_execute_write")
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(_job_row())
            out = json.loads(handle({"phase": "execute", "batch": copy.deepcopy(JOB_PLAN[1]),
                                     "job_id": JOB_ID, "job_batch_index": 2}))
        self.assertTrue(out["ok"], out)
        # 4 step writes + one durable execution-log append (issue 16).
        self.assertEqual(m["_do_mutate"].call_count, 5)


# =============================================================== cross-batch placeholders
#
# Issue 18/ADR 0010: <FROM_BATCH_N_STEP_M> is accepted and stored literally
# by render; this is its execute-time resolution, read from the job's
# durable execution_log — never from any variable shared between batches,
# since each batch may run in its own, separate kanban worker dispatch.

PLACEHOLDER_JOB = "JOB-PH"
PLACEHOLDER_PLAN = _rendered_plan([
    [{"op": "hr_payroll.generic", "args": {"doctype": "Employee", "action": "create",
                                           "payload": {"first_name": "Arjun"}}}],
    [{"op": "unscoped.generic", "args": {"doctype": "Interview Type", "action": "create",
                                         "payload": {"name": "<FROM_BATCH_1_STEP_1>"}}}],
])


def _placeholder_row(execution_log=None, **over):
    requested_by = over.get("requested_by", testsupport.REQ)
    issued_at = over.get("issued_at", int(time.time()))
    with connector():
        prepared = operations._job_requests(copy.deepcopy(PLACEHOLDER_PLAN),
                                            testsupport.ctx(requested_by=requested_by), render=False)
    token = operations.job_token(prepared, requested_by, PLACEHOLDER_JOB, issued_at)
    row = dict(job_id=PLACEHOLDER_JOB, requested_by=requested_by, token=token, step_count=2,
              status="Approved", issued_at=issued_at, plan=json.dumps(PLACEHOLDER_PLAN),
              execution_log=json.dumps(execution_log or []))
    row.update(over)
    return row


class CrossBatchPlaceholderTests(unittest.TestCase):

    def test_batch_2_resolves_batch_1s_name_from_the_stored_log_alone(self):
        # First dispatch: batch 1 runs and durably logs its own result.
        with connector() as m1:
            m1["get_resource"].side_effect = _get_resource_side_effect(_placeholder_row())
            m1["_do_mutate"].return_value = {"data": {"name": "EMP-1"}}
            operations.run_batch(copy.deepcopy(PLACEHOLDER_PLAN[0]), testsupport.ctx(),
                                 job_id=PLACEHOLDER_JOB, job_batch_index=1)
            stored_log = json.loads(m1["_do_mutate"].call_args.args[3]["execution_log"])
        self.assertEqual(stored_log, [{"batch_index": 1, "step_index": 1, "name": "EMP-1",
                                       "modified": None, "outcome": "succeeded"}])

        # Second dispatch: a brand new connector() — nothing from above is
        # shared — batch 2's placeholder must resolve purely from the row's
        # stored execution_log passed in here.
        with connector() as m2:
            m2["get_resource"].side_effect = _get_resource_side_effect(
                _placeholder_row(execution_log=stored_log))
            report = operations.run_batch(copy.deepcopy(PLACEHOLDER_PLAN[1]), testsupport.ctx(),
                                          job_id=PLACEHOLDER_JOB, job_batch_index=2)
        self.assertEqual(report["counts"], {"succeeded": 1, "failed": 0, "parked": 0, "not_attempted": 0})
        sent_payload = m2["_do_mutate"].call_args_list[0].args[3]
        self.assertEqual(sent_payload["name"], "EMP-1")

    def test_unresolved_placeholder_parks_rather_than_stopping_the_job(self):
        # agents ADR 0010/issue 19 bullet 3: a step referencing an earlier
        # one's <FROM_BATCH_N_STEP_M> that never produced a usable record
        # parks (the dependency graph is already implicit in the
        # placeholder structure) rather than stopping the whole job —
        # same convention as _resolve_from_step's own refusal
        # (test_from_step_refuses_a_different_record above), just no
        # longer a hard stop.
        with connector() as m:
            m["get_resource"].side_effect = _get_resource_side_effect(
                _placeholder_row(execution_log=[]))  # batch 1 never ran
            report = operations.run_batch(copy.deepcopy(PLACEHOLDER_PLAN[1]), testsupport.ctx(),
                                          job_id=PLACEHOLDER_JOB, job_batch_index=2)
        self.assertIsNone(report["stopped_at"])
        self.assertEqual(report["steps"][0]["status"], "parked")
        self.assertIn("FROM_BATCH_1_STEP_1", report["steps"][0]["error"])
        # The one _do_mutate call is the durable "this step parked" log
        # entry (issue 16) — never the Interview Type create itself.
        self.assertEqual(m["_do_mutate"].call_count, 1)
        self.assertEqual(m["_do_mutate"].call_args.args[1], core_client.JOB_APPROVAL_DOCTYPE)

    def test_a_parked_or_failed_batch_1_leaves_no_name_to_resolve(self):
        for outcome in ("failed", "parked"):
            with self.subTest(outcome=outcome):
                log = [{"batch_index": 1, "step_index": 1, "name": None, "modified": None,
                       "outcome": outcome}]
                with connector() as m:
                    m["get_resource"].side_effect = _get_resource_side_effect(
                        _placeholder_row(execution_log=log))
                    report = operations.run_batch(copy.deepcopy(PLACEHOLDER_PLAN[1]), testsupport.ctx(),
                                                  job_id=PLACEHOLDER_JOB, job_batch_index=2)
                self.assertEqual(report["steps"][0]["status"], "parked")
                self.assertIn("FROM_BATCH_1_STEP_1", report["steps"][0]["error"])
                self.assertEqual(m["_do_mutate"].call_count, 1)
                self.assertEqual(m["_do_mutate"].call_args.args[1], core_client.JOB_APPROVAL_DOCTYPE)


if __name__ == "__main__":
    unittest.main()
