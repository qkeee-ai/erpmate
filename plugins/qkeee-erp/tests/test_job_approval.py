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
import time
import unittest
from unittest.mock import patch

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
        with connector() as m, patch.object(
                core_client, "monitor_channel",
                return_value={"platform": "google_chat", "channel_id": "spaces/A"}):
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
        # Issue 16: the dispatching session's channel, captured at render
        # time, is the job's monitor of record (agents ADR 0010 point 4).
        self.assertEqual(body["monitor_platform"], "google_chat")
        self.assertEqual(body["monitor_channel_id"], "spaces/A")
        # Issue 23: no Kanban worker env in this test process, so "".
        self.assertEqual(body["kanban_card_id"], "")

    def test_a_kanban_worker_stores_its_own_task_as_the_card_id(self):
        with connector() as m, patch.object(core_client, "kanban_card_id", return_value="t_abc123"):
            _render(BATCHES)
        body = m["_do_mutate"].call_args.args[3]
        self.assertEqual(body["kanban_card_id"], "t_abc123")

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

    def test_no_resolvable_monitor_channel_still_stores_the_job(self):
        # Issue 22's fallback: a dispatching session with no channel
        # context (e.g. CLI) stores None/None rather than failing — the
        # job still goes out, addressed to requested_by by email only,
        # same as notify.job_approval_request's existing default.
        with connector() as m, patch.object(
                core_client, "monitor_channel", return_value={"platform": None, "channel_id": None}):
            _render(BATCHES)
        body = m["_do_mutate"].call_args.args[3]
        self.assertIsNone(body["monitor_platform"])
        self.assertIsNone(body["monitor_channel_id"])
        self.assertEqual(body["requested_by"], testsupport.REQ)
        self.assertEqual(body["kanban_card_id"], "")


class JobResumeTests(unittest.TestCase):
    """Issue 17: a kanban worker respawns as a fresh process on every
    dispatch; re-rendering the identical batches must return the FIRST
    render's job_id/token/code unchanged, never mint a fresh one and
    orphan a code the Requester may have already replied to (the
    confirmed-recurring 28/29-Oct staleness)."""

    def test_rendering_the_same_batches_twice_resumes_the_first_job(self):
        with connector() as m1:
            first = _render(BATCHES, job_id="JOB-FIRST")
        stored_body = m1["_do_mutate"].call_args.args[3]

        with connector() as m2, patch.object(
                core_client, "query_resource",
                return_value={"data": [stored_body]}) as mocked_query:
            second = _render(BATCHES, job_id="JOB-SECOND")

        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(second["job_token"], first["job_token"])
        self.assertEqual(second["confirmation_code"], first["confirmation_code"])
        self.assertEqual(second["step_count"], first["step_count"])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["status"], "Pending")
        mocked_query.assert_called_once()
        self.assertEqual(mocked_query.call_args.kwargs["filters"],
                         [["requested_by", "=", testsupport.REQ],
                          ["status", "in", ["Pending", "Approved"]]])
        self.assertTrue(mocked_query.call_args.kwargs["internal"])
        # No second job_approval.create — the existing row is reused as is.
        m2["_do_mutate"].assert_not_called()

    def test_a_genuinely_different_plan_mints_a_fresh_job(self):
        with connector() as m1:
            first = _render(BATCHES, job_id="JOB-FIRST")
        stored_body = m1["_do_mutate"].call_args.args[3]

        with connector() as m2, patch.object(
                core_client, "query_resource", return_value={"data": [stored_body]}):
            second = _render([[GATED_STEP]], job_id="JOB-SECOND")  # a different plan

        self.assertNotEqual(second["job_id"], first["job_id"])
        self.assertNotIn("resumed", second)
        m2["_do_mutate"].assert_called_once()  # a genuinely new job_approval.create


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


class JobApprovalAppendLogOpTests(unittest.TestCase):
    """Issue 16's per-step execution ledger (agents ADR 0010 point 1):
    append_execution_log() is the one place that reads-modifies-writes
    the stored row's execution_log; job_approval.append_log is its
    dedicated (cli=False, internal-only) write operation."""

    def test_not_a_cli_operation(self):
        self.assertIn("job_approval.append_log", operations.REGISTRY)
        self.assertNotIn("job_approval.append_log", {op.key for op in operations.list_operations()})

    def test_does_not_claim_ownership_already_held_by_mark_approved(self):
        # register_operation() raises at import if two DIFFERENT operations
        # claim the same (doctype, action) pair — append_log must not try.
        self.assertEqual(operations.owned_by("Qkeee Bot Job Approval", "update"),
                         "job_approval.mark_approved")

    def test_wrong_args_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.run_operation("job_approval.append_log", {"job_id": "J-1"},
                                         testsupport.ctx())

    def test_extends_the_stored_log_and_saves_it(self):
        with connector() as m:
            m["get_resource"].return_value = {
                "data": {"job_id": "JOB-1",
                        "execution_log": json.dumps([{"batch_index": 1, "step_index": 1,
                                                      "name": "SO-1", "modified": "2026-01-01",
                                                      "outcome": "succeeded"}])}}
            operations.append_execution_log(
                "JOB-1", [{"batch_index": 2, "step_index": 1, "name": "SO-2",
                          "modified": "2026-01-02", "outcome": "succeeded"}], testsupport.ctx())
        self.assertEqual(m["_do_mutate"].call_args.args[1], "Qkeee Bot Job Approval")
        self.assertEqual(m["_do_mutate"].call_args.args[2], "update")
        self.assertEqual(m["_do_mutate"].call_args.args[4], "JOB-1")
        saved = json.loads(m["_do_mutate"].call_args.args[3]["execution_log"])
        self.assertEqual(saved, [{"batch_index": 1, "step_index": 1, "name": "SO-1",
                                  "modified": "2026-01-01", "outcome": "succeeded"},
                                 {"batch_index": 2, "step_index": 1, "name": "SO-2",
                                  "modified": "2026-01-02", "outcome": "succeeded"}])

    def test_starts_the_log_fresh_when_the_stored_row_has_none_yet(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": {"job_id": "JOB-1"}}
            operations.append_execution_log(
                "JOB-1", [{"batch_index": 1, "step_index": 1, "name": "SO-1",
                          "modified": "2026-01-01", "outcome": "succeeded"}], testsupport.ctx())
        saved = json.loads(m["_do_mutate"].call_args.args[3]["execution_log"])
        self.assertEqual(len(saved), 1)

    def test_no_entries_writes_nothing(self):
        with connector() as m:
            operations.append_execution_log("JOB-1", [], testsupport.ctx())
        self.assertFalse(m["get_resource"].called)
        self.assertFalse(m["_do_mutate"].called)


class JobApprovalMarkNotifiedOpTests(unittest.TestCase):
    """Issue 23's watermark: job_approval.mark_notified records
    len(execution_log) as of the scheduled poller's last report, so the
    next poll can tell "nothing new" from "something happened" without a
    separate per-event flag."""

    def test_not_a_cli_operation(self):
        self.assertIn("job_approval.mark_notified", operations.REGISTRY)
        self.assertNotIn("job_approval.mark_notified", {op.key for op in operations.list_operations()})

    def test_does_not_claim_ownership_already_held_by_mark_approved(self):
        self.assertEqual(operations.owned_by("Qkeee Bot Job Approval", "update"),
                         "job_approval.mark_approved")

    def test_wrong_args_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.run_operation("job_approval.mark_notified", {"job_id": "J-1"},
                                         testsupport.ctx())

    def test_negative_or_non_int_index_refused(self):
        with connector():
            for bad in (-1, "3", 1.5, True):
                with self.subTest(bad=bad), self.assertRaises(core_client.InvalidArgumentsError):
                    operations.run_operation(
                        "job_approval.mark_notified",
                        {"job_id": "J-1", "last_notified_log_index": bad,
                         "last_notified_card_status": ""}, testsupport.ctx())

    def test_non_string_card_status_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.run_operation(
                    "job_approval.mark_notified",
                    {"job_id": "J-1", "last_notified_log_index": 1, "last_notified_card_status": None},
                    testsupport.ctx())

    def test_mark_job_notified_saves_the_index_and_card_status(self):
        with connector() as m:
            operations.mark_job_notified("JOB-1", 4, "blocked", testsupport.ctx())
        self.assertEqual(m["_do_mutate"].call_args.args[1], "Qkeee Bot Job Approval")
        self.assertEqual(m["_do_mutate"].call_args.args[2], "update")
        self.assertEqual(m["_do_mutate"].call_args.args[4], "JOB-1")
        self.assertEqual(m["_do_mutate"].call_args.args[3],
                         {"last_notified_log_index": 4, "last_notified_card_status": "blocked"})


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


def _row(**over):
    row = dict(job_id="JOB-9", requested_by=testsupport.REQ, token="deadbeefcafef00d",
              step_count=1, status="Pending", issued_at=int(time.time()),
              plan=json.dumps([[NO_CODE_STEP]]))
    row.update(over)
    return row


class GetJobApprovalTests(unittest.TestCase):
    """Issue 04/05's one shared read of the authoritative stored row —
    internal=True, same bookkeeping-doctype bypass _check_job_still_pending
    already uses (ADR 0009: a business Requester holds no direct
    permission on this doctype)."""

    def test_reads_the_stored_row_bypassing_the_requester_gate(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": _row()}
            data = operations.get_job_approval("JOB-9", testsupport.ctx())
        self.assertEqual(data["status"], "Pending")
        self.assertEqual(m["get_resource"].call_args.args[1], core_client.JOB_APPROVAL_DOCTYPE)
        self.assertEqual(m["get_resource"].call_args.args[2], "JOB-9")
        self.assertTrue(m["get_resource"].call_args.kwargs["internal"])

    def test_missing_job_is_an_empty_dict(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": None}
            self.assertEqual(operations.get_job_approval("JOB-9", testsupport.ctx()), {})


class RenderStoredJobTests(unittest.TestCase):
    """Issue 04: the email's plan/lines/code are rebuilt from the STORED
    row, never from caller-supplied args — the display can't drift from
    what job_token() actually bound."""

    def test_rebuilds_lines_and_code_from_the_stored_token(self):
        with connector() as m:
            m["get_resource"].return_value = {"data": _row()}
            out = operations.render_stored_job("JOB-9", testsupport.ctx())
        self.assertEqual(out["confirmation_code"], core_client.confirmation_code("deadbeefcafef00d"))
        self.assertEqual(out["status"], "Pending")
        self.assertEqual(out["requested_by"], testsupport.REQ)
        self.assertEqual(out["step_count"], 1)
        self.assertEqual(len(out["batches"]), 1)
        self.assertIn("hr_payroll.generic", out["batches"][0]["steps"][0]["line"])

    def test_confirmation_code_ignores_live_drift(self):
        """Re-rendering never recomputes a fresh token from current live
        state — only the confirmation_code derived from the ORIGINAL
        stored token is ever shown, so it can't diverge from what issue
        06's later check will validate against."""
        with connector() as m:
            m["get_resource"].return_value = {"data": _row(token="00000000deadbeef")}
            out = operations.render_stored_job("JOB-9", testsupport.ctx())
        self.assertEqual(out["confirmation_code"], core_client.confirmation_code("00000000deadbeef"))


class VerifyJobReplyTests(unittest.TestCase):
    """Issue 05/ADR 0008's two-factor proof: the reply's code AND its
    resolved sender must both match the stored row; either alone, or
    neither, is refused and the message says which."""

    CODE = core_client.confirmation_code("deadbeefcafef00d")

    def _verify(self, reply_text, sender=testsupport.REQ, row=None):
        data = row if row is not None else _row()
        with connector() as m, patch.object(core_client, "session_identity",
                                            return_value={"resolved_sender_email": sender}):
            m["get_resource"].return_value = {"data": data}
            result = operations.verify_job_reply("JOB-9", reply_text, testsupport.ctx())
        return result, m

    def test_code_and_sender_both_match_approves(self):
        result, m = self._verify(f"yes {self.CODE}")
        self.assertEqual(m["_do_mutate"].call_args.args[1:4],
                         ("Qkeee Bot Job Approval", "update", {"status": "Approved"}))
        self.assertEqual(m["_do_mutate"].call_args.args[4], "JOB-9")
        self.assertEqual(result["_audit_log_status"], "ok")

    def test_code_matching_is_case_insensitive(self):
        self._verify(f"yes {self.CODE.lower()}")  # no raise

    def test_code_missing_refuses_naming_only_that_check(self):
        with self.assertRaises(core_client.UnconfirmedByUserError) as e:
            self._verify("yes, approved, no code here")
        msg = str(e.exception)
        self.assertIn("does not contain confirmation code", msg)
        self.assertNotIn("does not match the Requester", msg)

    def test_wrong_sender_refuses_naming_only_that_check(self):
        with self.assertRaises(core_client.UnconfirmedByUserError) as e:
            self._verify(f"yes {self.CODE}", sender="someone.else@example.com")
        msg = str(e.exception)
        self.assertIn("does not match the Requester", msg)
        self.assertNotIn("does not contain confirmation code", msg)

    def test_no_resolved_sender_refuses(self):
        with self.assertRaises(core_client.UnconfirmedByUserError):
            self._verify(f"yes {self.CODE}", sender="")

    def test_both_checks_failing_names_both(self):
        with self.assertRaises(core_client.UnconfirmedByUserError) as e:
            self._verify("no code at all", sender="someone.else@example.com")
        msg = str(e.exception)
        self.assertIn("does not contain confirmation code", msg)
        self.assertIn("does not match the Requester", msg)

    def test_already_approved_job_is_refused(self):
        with self.assertRaises(core_client.PreconditionFailedError):
            self._verify(f"yes {self.CODE}", row=_row(status="Approved"))

    def test_expired_approval_window_is_refused(self):
        stale = _row(issued_at=int(time.time()) - operations.JOB_TOKEN_TTL_SECONDS - 3600)
        with self.assertRaises(core_client.StaleConfirmationError):
            self._verify(f"yes {self.CODE}", row=stale)

    def test_unknown_job_id_is_refused(self):
        with self.assertRaises(core_client.PreconditionFailedError):
            self._verify(f"yes {self.CODE}", row={})

    def test_empty_job_id_is_refused(self):
        with connector():
            with self.assertRaises(core_client.InvalidArgumentsError):
                operations.verify_job_reply("", "yes", testsupport.ctx())


if __name__ == "__main__":
    unittest.main()
