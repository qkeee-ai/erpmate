"""One token for a whole kanban job, durably stored (agents ADR 0008/0009;
issue 03 of .scratch/qkeee-erp-kanban-execution-steering).

Unlike batch_token()/run_batch() (test_batch_confirmation.py), a job's
approval is rendered once but confirmed later, in its own turn/process (the
Requester's email reply) and consumed by separate kanban child-task workers
— so prepare_job() stores the plan + token in the "Qkeee Bot Job Approval"
doctype via job_approval.create instead of only handing it back to the
caller. These tests cover job_token()'s binding, prepare_job()'s render +
store, and the two dedicated (cli=False, internal-only) operations."""

import copy
import json
import unittest

from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import connector

# POLICY_NONE step: hr_payroll.generic create is not a finalizing action,
# same as MIXED[0] in test_batch_confirmation.py ("no code needed").
NO_CODE_STEP = {"op": "hr_payroll.generic",
                "args": {"doctype": "Employee", "action": "create",
                        "payload": {"first_name": "Arjun"}}}

# POLICY_TOKEN_USER_CODE step: unscoped.generic token-gates every action.
GATED_STEP = {"op": "unscoped.generic",
             "args": {"doctype": "Interview Type", "action": "create",
                     "payload": {"name": "Technical"}}}

BATCHES = [[GATED_STEP], [dict(GATED_STEP, args=dict(GATED_STEP["args"],
                                                      payload={"name": "Screening"}))]]


def _render(batches, job_id="JOB-1", **kw):
    return operations.prepare_job(copy.deepcopy(batches), testsupport.ctx(), job_id, **kw)


class JobTokenTests(unittest.TestCase):
    """job_token() itself — pure, no I/O."""

    def _prepared(self, batches):
        with connector():
            ctx = testsupport.ctx()
            return operations._job_requests(copy.deepcopy(batches), ctx, render=True)

    def test_same_inputs_same_token(self):
        with connector():
            prepared = self._prepared(BATCHES)
            t1 = operations.job_token(prepared, testsupport.REQ, "JOB-1", 1000)
            t2 = operations.job_token(prepared, testsupport.REQ, "JOB-1", 1000)
        self.assertEqual(t1, t2)

    def test_changing_job_id_changes_token(self):
        with connector():
            prepared = self._prepared(BATCHES)
            t1 = operations.job_token(prepared, testsupport.REQ, "JOB-1", 1000)
            t2 = operations.job_token(prepared, testsupport.REQ, "JOB-2", 1000)
        self.assertNotEqual(t1, t2)

    def test_changing_requester_changes_token(self):
        with connector():
            prepared = self._prepared(BATCHES)
            t1 = operations.job_token(prepared, "a@example.com", "JOB-1", 1000)
            t2 = operations.job_token(prepared, "b@example.com", "JOB-1", 1000)
        self.assertNotEqual(t1, t2)

    def test_reordering_batches_changes_token(self):
        with connector():
            prepared = self._prepared(BATCHES)
            reordered = self._prepared(list(reversed(BATCHES)))
            t1 = operations.job_token(prepared, testsupport.REQ, "JOB-1", 1000)
            t2 = operations.job_token(reordered, testsupport.REQ, "JOB-1", 1000)
        self.assertNotEqual(t1, t2)

    def test_changing_a_step_changes_token(self):
        changed = copy.deepcopy(BATCHES)
        changed[1][0]["args"]["payload"]["name"] = "Different"
        with connector():
            prepared = self._prepared(BATCHES)
            prepared2 = self._prepared(changed)
            t1 = operations.job_token(prepared, testsupport.REQ, "JOB-1", 1000)
            t2 = operations.job_token(prepared2, testsupport.REQ, "JOB-1", 1000)
        self.assertNotEqual(t1, t2)


class PrepareJobTests(unittest.TestCase):

    def test_renders_every_batch_and_stores_the_plan(self):
        with connector() as m:
            out = _render(BATCHES)
        self.assertEqual([b["batch"] for b in out["batches"]], [1, 2])
        self.assertEqual(len(out["batches"][0]["steps"]), 1)
        self.assertEqual(out["step_count"], 2)
        self.assertEqual(len(out["confirmation_code"]), 6)
        self.assertEqual(out["ttl_minutes"], 24 * 60)
        self.assertIn("expires_at", out)
        m["_do_mutate"].assert_called_once()
        self.assertEqual(m["_do_mutate"].call_args.args[1], "Qkeee Bot Job Approval")
        self.assertEqual(m["_do_mutate"].call_args.args[2], "create")
        body = m["_do_mutate"].call_args.args[3]
        self.assertEqual(body["job_id"], "JOB-1")
        self.assertEqual(body["token"], out["job_token"])
        self.assertEqual(body["step_count"], 2)
        self.assertEqual(body["status"], "Pending")
        self.assertEqual(body["requested_by"], testsupport.REQ)
        self.assertEqual(json.loads(body["plan"]), out["job"])

    def test_no_job_id_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                _render(BATCHES, job_id="")

    def test_empty_batches_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                _render([])

    def test_job_needing_no_confirmation_stores_nothing(self):
        with connector() as m:
            out = _render([[NO_CODE_STEP]])
        self.assertEqual(out["policy"], operations.POLICY_NONE)
        self.assertNotIn("job_token", out)
        m["_do_mutate"].assert_not_called()

    def test_mixed_batches_still_need_the_job_level_code(self):
        """Even when only SOME steps are gated, the stored job as a whole
        always carries a confirmation_code — ADR 0008's two-factor proof is
        at the job level, not per-step."""
        with connector():
            out = _render([[NO_CODE_STEP], [GATED_STEP]])
        self.assertNotEqual(out["policy"], operations.POLICY_NONE)
        self.assertEqual(len(out["confirmation_code"]), 6)


class JobApprovalCreateOpTests(unittest.TestCase):

    def test_not_a_cli_operation(self):
        self.assertIn("job_approval.create", operations.REGISTRY)
        self.assertNotIn("job_approval.create", {op.key for op in operations.list_operations()})

    def test_owns_create_on_its_doctype(self):
        self.assertEqual(operations.owned_by("Qkeee Bot Job Approval", "create"),
                         "job_approval.create")

    def test_wrong_args_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.run_operation("job_approval.create", {"job_id": "J-1"},
                                         testsupport.ctx())


class JobApprovalMarkApprovedOpTests(unittest.TestCase):

    def test_not_a_cli_operation(self):
        self.assertIn("job_approval.mark_approved", operations.REGISTRY)
        self.assertNotIn("job_approval.mark_approved",
                         {op.key for op in operations.list_operations()})

    def test_owns_update_on_its_doctype(self):
        self.assertEqual(operations.owned_by("Qkeee Bot Job Approval", "update"),
                         "job_approval.mark_approved")

    def test_marks_a_pending_job_approved(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": {"job_id": "JOB-1", "status": "Pending"}}
            operations.run_operation("job_approval.mark_approved", {"job_id": "JOB-1"},
                                     testsupport.ctx())
        self.assertEqual(m["_do_mutate"].call_args.args[1], "Qkeee Bot Job Approval")
        self.assertEqual(m["_do_mutate"].call_args.args[2], "update")
        self.assertEqual(m["_do_mutate"].call_args.args[3], {"status": "Approved"})
        self.assertEqual(m["_do_mutate"].call_args.args[4], "JOB-1")

    def test_refuses_an_already_approved_job(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": {"job_id": "JOB-1", "status": "Approved"}}
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.run_operation("job_approval.mark_approved", {"job_id": "JOB-1"},
                                         testsupport.ctx())
        self.assertFalse(m["_do_mutate"].called)


class GateExemptionTests(unittest.TestCase):
    """Job Approval is this connector's own bookkeeping — same posture as
    Qkeee Bot Audit Log — so a business Requester is never expected to hold
    direct ERPNext permission on it (ADR 0008/0009)."""

    def test_exempt_from_requester_permission_checks(self):
        self.assertIn("Qkeee Bot Job Approval", core_client.WRITE_GATE_EXEMPT_DOCTYPES)
        self.assertIn("Qkeee Bot Job Approval", core_client.PROD_GATE_EXEMPT_DOCTYPES)

    def test_denied_to_the_unscoped_operation(self):
        self.assertIn("Qkeee Bot Job Approval", operations.UNSCOPED_DENY)

    def test_still_audited(self):
        """Unlike Audit Log/Comment, Job Approval writes are NOT exempt
        from audit logging — issue 03 explicitly wants an audit row."""
        self.assertNotIn("Qkeee Bot Job Approval", core_client.AUDIT_EXEMPT_DOCTYPES)
        self.assertTrue(operations.get_operation("job_approval.create").audit)
        self.assertTrue(operations.get_operation("job_approval.mark_approved").audit)


if __name__ == "__main__":
    unittest.main()
