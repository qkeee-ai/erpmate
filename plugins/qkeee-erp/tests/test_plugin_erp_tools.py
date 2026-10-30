"""ERPNext plugin tools (requester identity binding, issue 04): the requester
comes only from the gateway session (or a Kanban task's recorded origin),
never from a tool argument; mode comes from the plugin settings."""

import json
import os
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client, operations

SENDER = {"HERMES_SESSION_PLATFORM": "google_chat", "HERMES_SESSION_USER_ID": "nikhil@org.com",
          "HERMES_SESSION_ID": "sess-1", "HERMES_SESSION_CHAT_ID": "spaces/A",
          "HERMES_SESSION_THREAD_ID": "threads/T"}


def reader(values):
    return lambda name, default="": values.get(name, default)


class ToolTestCase(unittest.TestCase):
    settings = {"active_env": "dev", "mode": "read-write"}

    def tools(self, session):
        client.set_session_env_reader(reader(session))
        self.addCleanup(client.set_session_env_reader, None)
        env = patch.dict(os.environ, {"HERMES_KANBAN_TASK": ""})
        env.start()
        self.addCleanup(env.stop)
        return erp_tools.ErpTools(settings=lambda: dict(self.settings))

    def call(self, tools, name, args):
        return json.loads(tools.handler(name)(args))


class SchemaTests(unittest.TestCase):
    def test_no_tool_takes_a_requester_argument(self):
        for name, schema in erp_tools.SCHEMAS.items():
            props = schema["parameters"]["properties"]
            with self.subTest(tool=name):
                self.assertFalse({"requested_by", "requester", "user_id"} & set(props))
                self.assertNotIn("mode", props)

    def test_the_five_tools(self):
        self.assertEqual(set(erp_tools.SCHEMAS),
                         {"erp_query", "erp_get", "erp_report", "erp_discover", "erp_execute_write"})


class ReadToolTests(ToolTestCase):
    @patch.object(client, "query_resource", return_value=[{"name": "SO-1"}])
    def test_query_binds_the_session_sender_and_audit_context(self, mocked):
        out = self.call(self.tools(SENDER), "erp_query",
                        {"doctype": "Sales Order", "filters": [["status", "=", "Open"]],
                         "prompt_summary": "open orders"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"], [{"name": "SO-1"}])
        args, kw = mocked.call_args
        self.assertEqual(args[:3], ("dev", "Sales Order", [["status", "=", "Open"]]))
        self.assertEqual(kw["requested_by"], "nikhil@org.com")
        self.assertEqual(kw["session_id"], "sess-1")
        self.assertEqual(kw["channel"], "google_chat")
        self.assertEqual(kw["channel_metadata"], {"chat_id": "spaces/A", "thread_id": "threads/T"})
        self.assertEqual(kw["prompt_summary"], "open orders")

    @patch.object(client, "query_resource")
    def test_requester_smuggled_in_args_is_ignored(self, mocked):
        self.call(self.tools(SENDER), "erp_query",
                  {"doctype": "Sales Order", "requested_by": "priya@org.com"})
        self.assertEqual(mocked.call_args.kwargs["requested_by"], "nikhil@org.com")

    @patch.object(client, "get_resource")
    def test_no_session_sender_is_refused_before_any_call(self, mocked):
        out = self.call(self.tools({"HERMES_SESSION_PLATFORM": "cli"}), "erp_get",
                        {"doctype": "Sales Order", "name": "SO-1"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])
        mocked.assert_not_called()

    @patch.object(client, "run_query_report")
    def test_kanban_worker_without_origin_is_told_to_block_needs_input(self, mocked):
        tools = self.tools({})
        with patch.dict(os.environ, {"HERMES_KANBAN_TASK": "t_orphan", "HERMES_KANBAN_DB": ""}):
            out = self.call(tools, "erp_report", {"report_name": "Accounts Receivable"})
        self.assertTrue(out["refused"])
        self.assertIn("needs_input", out["error"])
        mocked.assert_not_called()

    @patch.object(client, "get_resource", side_effect=client.UnvalidatedProdRequesterError("no perm"))
    def test_gate_refusal_is_reported_as_refused(self, _):
        out = self.call(self.tools(SENDER), "erp_get", {"doctype": "Sales Order", "name": "SO-1"})
        self.assertEqual((out["ok"], out["refused"], out["error"]), (False, True, "no perm"))

    def test_tag_argument_overrides_the_active_env(self):
        with patch.object(client, "get_resource") as mocked:
            self.call(self.tools(SENDER), "erp_get", {"doctype": "Item", "name": "I-1", "tag": "qa"})
        self.assertEqual(mocked.call_args.args[0], "qa")


class DiscoverToolTests(ToolTestCase):
    def test_whoami_needs_no_network(self):
        with patch.object(client, "_request") as mocked:
            out = self.call(self.tools(SENDER), "erp_discover", {"action": "whoami"})
        self.assertEqual(out["result"]["resolved_sender_email"], "nikhil@org.com")
        mocked.assert_not_called()

    def test_health_reports_the_instance_and_the_mode(self):
        self.settings = {"active_env": "dev", "mode": "read-only"}
        with patch.object(client, "health_check", return_value={"tag": "dev", "base_url": "https://e"}):
            out = self.call(self.tools({}), "erp_discover", {"action": "health"})
        self.assertEqual(out["result"], {"tag": "dev", "base_url": "https://e", "mode": "read-only"})

    def test_promotion_plan_needs_no_requester_and_no_network(self):
        findings = {"findings": {"frappe_version": "15.4.0", "notes": "SSN 123-45-6789"},
                    "gaps": [{"key": "module_def_read"}]}
        with patch.object(client, "_request") as network:
            out = self.call(self.tools({}), "erp_discover",
                            {"action": "promotion_plan", "findings": findings, "summary": "v15"})
        network.assert_not_called()
        self.assertTrue(out["ok"])
        plan = out["result"]
        self.assertEqual(plan[0]["action"], "create")
        self.assertEqual(plan[-1]["tool"], "memory")
        self.assertIn("PARTIAL", plan[-1]["content"])
        self.assertNotIn("123-45-6789", json.dumps(plan))
        self.assertIn("dev", plan[0]["name"])  # the active Instance tag

    def test_promotion_plan_accepts_doctype_names(self):
        # dev-erp 2026-10-15: doctypes passed as names crashed the plan
        # ('str' object has no attribute 'get').
        out = self.call(self.tools({}), "erp_discover", {
            "action": "promotion_plan",
            "findings": {"findings": {}, "doctypes": ["Item", {"name": "Loan", "app": "lending"}]}})
        self.assertTrue(out["ok"], out)
        catalog = next(s["file_content"] for s in out["result"]
                       if s.get("file_path") == "references/doctypes-catalog.md")
        self.assertIn("**Item**", catalog)
        self.assertIn("**Loan** — module `?`, app `lending`", catalog)

    def test_promotion_plan_malformed_findings_is_a_clear_refusal(self):
        for bad in ({"findings": "text"}, {"findings": {}, "doctypes": "Item"},
                    {"findings": {}, "doctypes": [42]}, {"findings": {}, "custom_apps": ["crm"]},
                    {"findings": {}, "gaps": "none"}, "flat string"):
            with self.subTest(bad=bad):
                out = self.call(self.tools({}), "erp_discover",
                                {"action": "promotion_plan", "findings": bad})
                self.assertFalse(out["ok"])
                self.assertTrue(out.get("invalid_arguments"), out)
                self.assertIn("promotion_plan", out["error"])

    def test_promotion_plan_for_an_existing_learned_skill_edits_it(self):
        out = self.call(self.tools({}), "erp_discover",
                        {"action": "promotion_plan", "findings": {}, "skill_exists": True, "tag": "qa"})
        self.assertEqual(out["result"][0]["action"], "edit")
        self.assertIn("qa", out["result"][0]["name"])

    def test_meta_is_gated_on_the_session_sender(self):
        from qkeee_erp_plugin.qkeee_erp import discover
        with patch.object(discover, "doctype_meta", return_value={"fields": []}) as mocked:
            out = self.call(self.tools(SENDER), "erp_discover", {"action": "meta", "doctype": "Item"})
        self.assertTrue(out["ok"])
        self.assertEqual(mocked.call_args.kwargs["requested_by"], "nikhil@org.com")


class ExecuteWriteToolTests(ToolTestCase):
    @patch.object(operations, "run_operation", return_value={"name": "SO-9", "_audit_log_status": "ok"})
    def test_mode_comes_from_settings_never_from_args(self, mocked):
        self.settings = {"active_env": "dev", "mode": "read-only"}
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "execute", "op": "sales.generic", "mode": "read-write",
                         "args": {"doctype": "Sales Order", "action": "create", "payload": {}}})
        self.assertTrue(out["ok"])
        ctx = mocked.call_args.args[2]
        self.assertEqual(ctx.mode, "read-only")
        self.assertEqual(ctx.requested_by, "nikhil@org.com")
        self.assertEqual(ctx.tag, "dev")

    @patch.object(operations, "prepare_only", return_value={"policy": "always", "args": {},
                                                             "confirmation_code": "AB12"})
    def test_render_binds_the_session_sender(self, mocked):
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "render", "op": "system_admin.disable_user", "args": {"name": "a@b.c"}})
        self.assertEqual(out["result"]["confirmation_code"], "AB12")
        self.assertEqual(mocked.call_args.args[2].requested_by, "nikhil@org.com")

    def test_gated_execute_without_latest_prompt_is_refused(self):
        with patch.object(operations, "run_operation") as mocked:
            out = self.call(self.tools(SENDER), "erp_execute_write",
                            {"phase": "execute", "op": "system_admin.disable_user",
                             "args": {"name": "a@b.c", "reason": "left"}})
        self.assertFalse(out["ok"])
        self.assertIn("latest_prompt", out["error"])
        mocked.assert_not_called()

    def test_write_rejection_returns_the_structured_failure(self):
        failure = {"error_class": "MandatoryError", "missing_fields": ["customer"],
                   "invalid_links": [], "message": "customer is mandatory"}
        with patch.object(operations, "run_operation", side_effect=client.WriteRejectedError(failure["message"], failure)):
            out = self.call(self.tools(SENDER), "erp_execute_write",
                            {"phase": "execute", "op": "sales.generic",
                             "args": {"doctype": "Sales Order", "action": "create", "payload": {}}})
        self.assertEqual(out["write_failure"]["missing_fields"], ["customer"])

    def test_kanban_session_refuses_a_plain_gated_batch_instead_of_a_job(self):
        # issue 20: a kanban-dispatched worker has no live chat session to
        # receive a batch/op confirmation code — it must be told to use
        # `batches` (the job-level email approval) instead.
        kanban_sender = {**SENDER, "HERMES_SESSION_SOURCE": "kanban"}
        with patch.object(operations, "prepare_batch") as mocked:
            out = self.call(self.tools(kanban_sender), "erp_execute_write",
                            {"phase": "render",
                             "batch": [{"op": "system_admin.disable_user",
                                        "args": {"name": "a@b.c", "reason": "left"}}]})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])
        self.assertIn("batches", out["error"])
        mocked.assert_not_called()

    def test_kanban_session_allows_a_job_approved_batch_execute(self):
        # A batch bound to an already-approved job_id (ADR 0008) is exactly
        # the alternative the refusal above points to — it must not also
        # be refused by the same guard.
        kanban_sender = {**SENDER, "HERMES_SESSION_SOURCE": "kanban"}
        with patch.object(operations, "run_batch",
                         return_value={"stopped_at": None, "counts": {"parked": 0}}) as mocked:
            out = self.call(self.tools(kanban_sender), "erp_execute_write",
                            {"phase": "execute",
                             "batch": [{"op": "system_admin.disable_user",
                                        "args": {"name": "a@b.c", "reason": "left"}}],
                             "job_id": "JOB-1", "job_batch_index": 1})
        self.assertTrue(out["ok"])
        mocked.assert_called_once()

    def test_kanban_session_allows_a_plain_batch_needing_no_confirmation(self):
        kanban_sender = {**SENDER, "HERMES_SESSION_SOURCE": "kanban"}
        with patch.object(operations, "prepare_batch", return_value={"policy": operations.POLICY_NONE}):
            out = self.call(self.tools(kanban_sender), "erp_execute_write",
                            {"phase": "render", "batch": [{"op": "sales.generic",
                                                            "args": {"doctype": "Sales Order",
                                                                     "action": "create",
                                                                     "payload": {}}}]})
        self.assertTrue(out["ok"])

    def test_list_ops_needs_no_requester(self):
        out = self.call(self.tools({}), "erp_execute_write", {"phase": "list_ops"})
        self.assertTrue(any(o["op"] == "sales.generic" for o in out["result"]))

    def test_list_ops_passes_op_and_domain_filters(self):
        tools = self.tools({})
        out = self.call(tools, "erp_execute_write", {"phase": "list_ops", "op": "unscoped.generic"})
        self.assertEqual([o["op"] for o in out["result"]], ["unscoped.generic"])
        out = self.call(tools, "erp_execute_write", {"phase": "list_ops", "domain": "sales"})
        self.assertTrue(out["result"])
        self.assertTrue(all(o["op"].startswith("sales.") for o in out["result"]))


class JobToolTests(ToolTestCase):
    """agents ADR 0008 issues 07/09: the entry points that actually start a
    job and bind the Requester's reply — erp_tools.py is the only thing
    that was missing; operations.prepare_job()/verify_job_reply() are
    already covered by tests/test_job_approval.py."""

    @patch.object(operations, "prepare_job")
    def test_render_batches_starts_a_job_with_a_generated_id(self, mocked):
        mocked.return_value = {"policy": operations.POLICY_TOKEN_USER_CODE, "job": [],
                               "batches": [], "job_id": "placeholder", "job_token": "t",
                               "confirmation_code": "AB12CD", "expires_at": "2026-10-25T00:00:00Z"}
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "render", "batches": [[{"op": "sales.generic", "args": {}}]]})
        self.assertTrue(out["ok"])
        args, kw = mocked.call_args
        self.assertEqual(args[0], [[{"op": "sales.generic", "args": {}}]])
        self.assertEqual(args[1].requested_by, "nikhil@org.com")
        generated_id = args[2]
        self.assertTrue(generated_id.startswith("JOB-"))
        self.assertNotIn("AB12CD", out["result"]["_note"])
        self.assertIn("Do NOT show", out["result"]["_note"])
        self.assertIn("notify.job_approval_request", out["result"]["_note"])
        # The note names the job_id prepare_job() actually returned
        # ("placeholder" here) — never the caller's own generated id,
        # which prepare_job() discards when it resumes an existing job
        # (issue 17).
        self.assertIn("placeholder", out["result"]["_note"])
        self.assertNotIn(generated_id, out["result"]["_note"])

    @patch.object(operations, "prepare_job")
    def test_render_batches_resuming_an_approved_job_skips_the_email_note(self, mocked):
        # Issue 17: when prepare_job() resumes an existing, already-
        # Approved job, the agent must not be told to send a SECOND
        # approval email for it.
        mocked.return_value = {"policy": operations.POLICY_TOKEN_USER_CODE, "job": [],
                               "batches": [], "job_id": "JOB-OLD", "job_token": "t",
                               "confirmation_code": "AB12CD", "expires_at": "2026-10-25T00:00:00Z",
                               "resumed": True, "status": "Approved"}
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "render", "batches": [[{"op": "sales.generic", "args": {}}]]})
        self.assertTrue(out["ok"])
        self.assertIn("already has an Approved job", out["result"]["_note"])
        self.assertIn("JOB-OLD", out["result"]["_note"])
        self.assertNotIn("notify.job_approval_request", out["result"]["_note"])
        self.assertNotIn("AB12CD", out["result"]["_note"])

    @patch.object(operations, "prepare_job")
    def test_render_batches_resuming_a_pending_job_still_sends_the_email_note(self, mocked):
        # A resumed job still awaiting the Requester's reply gets the same
        # "send the approval email" instruction as a brand-new one — a
        # resend of the SAME job_id/code to someone who hasn't answered
        # yet is a harmless nudge (contrast the Approved case above,
        # which this deliberately skips).
        mocked.return_value = {"policy": operations.POLICY_TOKEN_USER_CODE, "job": [],
                               "batches": [], "job_id": "JOB-OLD", "job_token": "t",
                               "confirmation_code": "AB12CD", "expires_at": "2026-10-25T00:00:00Z",
                               "resumed": True, "status": "Pending"}
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "render", "batches": [[{"op": "sales.generic", "args": {}}]]})
        self.assertTrue(out["ok"])
        self.assertIn("notify.job_approval_request", out["result"]["_note"])
        self.assertIn("JOB-OLD", out["result"]["_note"])
        self.assertNotIn("AB12CD", out["result"]["_note"])

    @patch.object(operations, "prepare_job", return_value={"policy": operations.POLICY_NONE,
                                                            "job": [], "batches": []})
    def test_render_batches_no_confirmation_needed_skips_the_email_note(self, _):
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "render", "batches": [[{"op": "mis.generic", "args": {}}]]})
        self.assertIn("No step in this job needs a confirmation", out["result"]["_note"])
        self.assertNotIn("notify.job_approval_request", out["result"]["_note"])

    def test_batches_rejects_execute_phase(self):
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "execute", "batches": [[{"op": "sales.generic", "args": {}}]]})
        self.assertFalse(out["ok"])
        self.assertIn("only supports phase=render", out["error"])

    def test_batches_mutually_exclusive_with_batch_and_op(self):
        for extra in ({"batch": []}, {"op": "sales.generic"}):
            with self.subTest(extra=extra):
                out = self.call(self.tools(SENDER), "erp_execute_write",
                                {"phase": "render", "batches": [[{"op": "a", "args": {}}]], **extra})
                self.assertFalse(out["ok"])
                self.assertIn("batches replaces batch/op/args", out["error"])

    @patch.object(operations, "verify_job_reply", return_value={"name": "JOB-1", "status": "Approved"})
    def test_confirm_job_binds_the_session_sender(self, mocked):
        out = self.call(self.tools(SENDER), "erp_execute_write",
                        {"phase": "confirm_job", "job_id": "JOB-1", "reply_text": "yes AB12CD"})
        self.assertTrue(out["ok"])
        args = mocked.call_args.args
        self.assertEqual(args[0], "JOB-1")
        self.assertEqual(args[1], "yes AB12CD")
        self.assertEqual(args[2].requested_by, "nikhil@org.com")

    def test_confirm_job_needs_job_id_and_reply_text(self):
        with patch.object(operations, "verify_job_reply") as mocked:
            out = self.call(self.tools(SENDER), "erp_execute_write",
                            {"phase": "confirm_job", "job_id": "JOB-1"})
        self.assertFalse(out["ok"])
        self.assertIn("reply_text", out["error"])
        mocked.assert_not_called()

    def test_confirm_job_needs_no_live_chat_session_signal(self):
        # The point of ADR 0008: a kanban worker has none of these, but
        # confirm_job must still work from a plain gateway email turn.
        with patch.object(operations, "verify_job_reply", return_value={"status": "Approved"}):
            out = self.call(self.tools(SENDER), "erp_execute_write",
                            {"phase": "confirm_job", "job_id": "JOB-1", "reply_text": "AB12CD"})
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
