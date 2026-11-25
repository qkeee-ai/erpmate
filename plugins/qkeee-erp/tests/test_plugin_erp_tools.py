"""ERPNext plugin tools (requester identity binding, issue 04): the requester
comes only from the gateway session (or a Kanban task's recorded origin),
never from a tool argument; mode comes from the plugin settings."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client, operations

SENDER = {"HERMES_SESSION_PLATFORM": "google_chat", "HERMES_SESSION_USER_ID": "nikhil@org.com",
          "HERMES_SESSION_ID": "sess-1", "HERMES_SESSION_CHAT_ID": "spaces/A",
          "HERMES_SESSION_THREAD_ID": "threads/T"}
PRIYA = {"HERMES_SESSION_PLATFORM": "email", "HERMES_SESSION_USER_ID": "priya@org.com",
         "HERMES_SESSION_ID": "sess-2", "HERMES_SESSION_CHAT_ID": "priya@org.com"}


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

    def test_the_six_tools(self):
        self.assertEqual(set(erp_tools.SCHEMAS),
                         {"erp_query", "erp_get", "erp_report", "erp_discover", "erp_execute_write",
                          "erp_request"})


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


class RequestToolTests(ToolTestCase):
    """erp_request: the cross-channel Request Registry (agents issue 02)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        home = patch.dict(os.environ, {"HERMES_HOME": tmp.name})
        home.start()
        self.addCleanup(home.stop)

    def _events(self, request_id):
        import sqlite3
        from qkeee_erp_plugin.qkeee_erp.core import request_registry
        conn = sqlite3.connect(request_registry.default_store_path(self.home))
        try:
            return conn.execute(
                "SELECT action, sender_email, note FROM request_event WHERE request_id = ? "
                "ORDER BY id", (request_id,)).fetchall()
        finally:
            conn.close()

    def test_open_binds_the_session_sender_and_returns_topic_and_id(self):
        out = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "GST report"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["sender_email"], "nikhil@org.com")
        self.assertEqual(out["result"]["topic"], "GST report")
        self.assertTrue(out["result"]["request_id"].startswith("REQ-"))

    def test_open_needs_a_topic(self):
        out = self.call(self.tools(SENDER), "erp_request", {"action": "open"})
        self.assertFalse(out["ok"])

    def test_open_with_no_session_sender_is_refused(self):
        out = self.call(self.tools({"HERMES_SESSION_PLATFORM": "cli"}), "erp_request",
                        {"action": "open", "topic": "x"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])

    def test_lookup_lists_this_senders_open_requests(self):
        tools = self.tools(SENDER)
        self.call(tools, "erp_request", {"action": "open", "topic": "first"})
        out = self.call(tools, "erp_request", {"action": "lookup"})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["result"]["requests"]), 1)
        self.assertIn("_note", out["result"])

    def test_lookup_never_sees_another_senders_requests(self):
        self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "nikhil's ask"})
        out = self.call(self.tools(PRIYA), "erp_request", {"action": "lookup"})
        self.assertEqual(out["result"]["requests"], [])
        self.assertNotIn("_note", out["result"])

    def test_link_attaches_a_second_channel_to_the_same_request(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        request_id = opened["result"]["request_id"]
        chat_session = dict(SENDER, HERMES_SESSION_PLATFORM="email",
                            HERMES_SESSION_CHAT_ID="nikhil@org.com", HERMES_SESSION_THREAD_ID="",
                            HERMES_SESSION_ID="sess-3")
        out = self.call(self.tools(chat_session), "erp_request", {"action": "link", "request_id": request_id})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["result"]["linked_sessions"]), 2)

    def test_link_from_another_sender_is_allowed_as_input_provider(self):
        # Issue 03: an input-provider joining someone else's ask is expected,
        # not a violation — only status (below) stays Requester-only.
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(PRIYA), "erp_request",
                        {"action": "link", "request_id": opened["result"]["request_id"]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["your_role"], "input_provider")
        self.assertEqual(out["result"]["topic"], "t")

    def test_link_from_another_sender_does_not_leak_other_sessions(self):
        # Adversarial review: an input-provider must not be able to read
        # back every other participant's platform/chat_id/session_id just
        # by linking their own, unrelated session to a known request_id.
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(PRIYA), "erp_request",
                        {"action": "link", "request_id": opened["result"]["request_id"]})
        self.assertNotIn("linked_sessions", out["result"])

    def test_open_tags_your_role_as_requester(self):
        out = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        self.assertEqual(out["result"]["your_role"], "requester")

    def test_link_to_unknown_request_is_an_error_not_a_crash(self):
        out = self.call(self.tools(SENDER), "erp_request", {"action": "link", "request_id": "REQ-NOPE"})
        self.assertFalse(out["ok"])

    def test_status_sets_agent_status(self):
        tools = self.tools(SENDER)
        opened = self.call(tools, "erp_request", {"action": "open", "topic": "t"})
        out = self.call(tools, "erp_request",
                        {"action": "status", "request_id": opened["result"]["request_id"],
                         "agent_status": "done"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["agent_status"], "done")
        self.assertIsNotNone(out["result"]["closed_at"])

    def test_status_needs_at_least_one_field(self):
        tools = self.tools(SENDER)
        opened = self.call(tools, "erp_request", {"action": "open", "topic": "t"})
        out = self.call(tools, "erp_request",
                        {"action": "status", "request_id": opened["result"]["request_id"]})
        self.assertFalse(out["ok"])

    def test_status_on_another_senders_request_is_refused(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(PRIYA), "erp_request",
                        {"action": "status", "request_id": opened["result"]["request_id"],
                         "agent_status": "done"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])

    def test_in_thread_finds_a_request_opened_by_someone_else_in_the_same_room(self):
        # Issue 03: two different senders, same google_chat space/thread —
        # the substrate already shared by the gateway's own session keying.
        self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "nikhil's ask"})
        bob_in_same_thread = dict(SENDER, HERMES_SESSION_USER_ID="priya@org.com")
        out = self.call(self.tools(bob_in_same_thread), "erp_request", {"action": "in_thread"})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["result"]["requests"]), 1)
        self.assertEqual(out["result"]["requests"][0]["your_role"], "input_provider")
        self.assertIn("_note", out["result"])

    def test_in_thread_sees_nothing_in_a_different_thread(self):
        self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(PRIYA), "erp_request", {"action": "in_thread"})
        self.assertEqual(out["result"]["requests"], [])
        self.assertNotIn("_note", out["result"])

    def test_in_thread_on_email_resolves_nothing_without_a_references_var(self):
        # Issue 03 follow-up: no session var populates email references yet
        # (client.request_in_email_thread()'s own docstring) — same [] as
        # the pre-existing email case above, exercised explicitly so a
        # future hermes-agent change that starts setting the var is the
        # ONLY thing that can turn this test's own behavior "on".
        self.call(self.tools(PRIYA), "erp_request", {"action": "open", "topic": "alice's ask"})
        bob_email = dict(PRIYA, HERMES_SESSION_USER_ID="bob@org.com", HERMES_SESSION_CHAT_ID="bob@org.com",
                         HERMES_SESSION_ID="sess-bob")
        out = self.call(self.tools(bob_email), "erp_request", {"action": "in_thread"})
        self.assertEqual(out["result"]["requests"], [])

    def test_in_thread_on_email_resolves_via_the_references_var_once_set(self):
        # Simulates the hermes-agent side of the follow-up landing: once
        # HERMES_SESSION_EMAIL_REFERENCES is populated, this already works
        # end to end with no further erpnext-hermes change needed.
        alice_opens = dict(PRIYA, HERMES_SESSION_MESSAGE_ID="<alice-opens@mail>")
        opened = self.call(self.tools(alice_opens), "erp_request", {"action": "open", "topic": "alice's ask"})
        bob_replying = dict(PRIYA, HERMES_SESSION_USER_ID="bob@org.com", HERMES_SESSION_CHAT_ID="bob@org.com",
                            HERMES_SESSION_ID="sess-bob", HERMES_SESSION_EMAIL_REFERENCES="<alice-opens@mail>")
        out = self.call(self.tools(bob_replying), "erp_request", {"action": "in_thread"})
        self.assertEqual(len(out["result"]["requests"]), 1)
        self.assertEqual(out["result"]["requests"][0]["request_id"], opened["result"]["request_id"])
        self.assertEqual(out["result"]["requests"][0]["your_role"], "input_provider")

    def test_in_thread_on_email_ignores_a_references_var_matching_nothing(self):
        alice_opens = dict(PRIYA, HERMES_SESSION_MESSAGE_ID="<alice-opens@mail>")
        self.call(self.tools(alice_opens), "erp_request", {"action": "open", "topic": "alice's ask"})
        bob_replying = dict(PRIYA, HERMES_SESSION_USER_ID="bob@org.com", HERMES_SESSION_CHAT_ID="bob@org.com",
                            HERMES_SESSION_ID="sess-bob", HERMES_SESSION_EMAIL_REFERENCES="<unrelated@mail>")
        out = self.call(self.tools(bob_replying), "erp_request", {"action": "in_thread"})
        self.assertEqual(out["result"]["requests"], [])

    def test_open_indexes_the_sessions_message_id(self):
        from qkeee_erp_plugin.qkeee_erp.core import request_registry
        session = dict(SENDER, HERMES_SESSION_MESSAGE_ID="<opened@mail>")
        out = self.call(self.tools(session), "erp_request", {"action": "open", "topic": "t"})
        resolved = request_registry.resolve_request_by_message_ids(
            request_registry.default_store_path(self.home), ["<opened@mail>"])
        self.assertEqual(resolved, out["result"]["request_id"])

    def test_open_with_no_message_id_var_indexes_nothing(self):
        from qkeee_erp_plugin.qkeee_erp.core import request_registry
        self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        self.assertIsNone(request_registry.resolve_request_by_message_ids(
            request_registry.default_store_path(self.home), [""]))

    def test_link_indexes_the_sessions_message_id(self):
        from qkeee_erp_plugin.qkeee_erp.core import request_registry
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        linker = dict(PRIYA, HERMES_SESSION_MESSAGE_ID="<linked@mail>")
        self.call(self.tools(linker), "erp_request",
                 {"action": "link", "request_id": opened["result"]["request_id"]})
        resolved = request_registry.resolve_request_by_message_ids(
            request_registry.default_store_path(self.home), ["<linked@mail>"])
        self.assertEqual(resolved, opened["result"]["request_id"])

    def test_open_records_a_forensic_event(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        events = self._events(opened["result"]["request_id"])
        self.assertEqual(events, [("open", "nikhil@org.com", None)])

    def test_open_with_a_note_records_it(self):
        opened = self.call(self.tools(SENDER), "erp_request",
                           {"action": "open", "topic": "t", "note": "raised in standup"})
        events = self._events(opened["result"]["request_id"])
        self.assertEqual(events[0][2], "raised in standup")

    def test_link_and_status_each_record_their_own_event(self):
        # self.tools() installs a module-global session reader (client.
        # set_session_env_reader), so each identity needs its own fresh
        # self.tools(...) call right before use — reusing an earlier
        # `tools` object after switching identity re-reads the LATEST
        # global, not the one bound when it was created.
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        request_id = opened["result"]["request_id"]
        self.call(self.tools(PRIYA), "erp_request", {"action": "link", "request_id": request_id})
        out = self.call(self.tools(SENDER), "erp_request",
                        {"action": "status", "request_id": request_id, "agent_status": "done",
                         "note": "requester confirmed by reply"})
        self.assertTrue(out["ok"])
        events = self._events(request_id)
        self.assertEqual([e[0] for e in events], ["open", "link", "status"])
        self.assertEqual(events[1][1], "priya@org.com")
        self.assertEqual(events[2][2], "requester confirmed by reply")

    def test_facts_merge_for_the_requester(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(SENDER), "erp_request",
                        {"action": "facts", "request_id": opened["result"]["request_id"],
                         "facts": {"supplier_id": "SUP-001"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["facts"], {"supplier_id": "SUP-001"})

    def test_facts_needs_a_non_empty_object(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(SENDER), "erp_request",
                        {"action": "facts", "request_id": opened["result"]["request_id"], "facts": {}})
        self.assertFalse(out["ok"])

    def test_facts_from_an_unlinked_sender_is_refused(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        out = self.call(self.tools(PRIYA), "erp_request",
                        {"action": "facts", "request_id": opened["result"]["request_id"],
                         "facts": {"a": "b"}})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])

    def test_facts_from_a_linked_input_provider_succeeds(self):
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        request_id = opened["result"]["request_id"]
        self.call(self.tools(PRIYA), "erp_request", {"action": "link", "request_id": request_id})
        out = self.call(self.tools(PRIYA), "erp_request",
                        {"action": "facts", "request_id": request_id, "facts": {"item_code": "I-1"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["facts"], {"item_code": "I-1"})

    def test_facts_survive_being_read_back_via_open_result_shape(self):
        # The point of issue 04: facts live on the row, so every later
        # open/lookup/in_thread/link/status result already carries them --
        # no separate "get" action needed.
        opened = self.call(self.tools(SENDER), "erp_request", {"action": "open", "topic": "t"})
        request_id = opened["result"]["request_id"]
        self.call(self.tools(SENDER), "erp_request",
                 {"action": "facts", "request_id": request_id, "facts": {"supplier_id": "SUP-001"}})
        out = self.call(self.tools(SENDER), "erp_request", {"action": "lookup"})
        self.assertEqual(out["result"]["requests"][0]["facts"], {"supplier_id": "SUP-001"})


class OrgReportToolTests(RequestToolTests):
    """erp_request action=org_report (agents issue 11 §9): admin-gated,
    cross-sender — the one erp_request action not scoped to the caller's
    own identity."""

    def test_non_admin_is_refused(self):
        with patch.object(client, "_qkeee_env", return_value={}):
            out = self.call(self.tools(SENDER), "erp_request", {"action": "org_report"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])

    def test_configured_admin_succeeds(self):
        env = {"QKEEE_ERP_DEFAULT_ADMIN_EMAIL": "nikhil@org.com"}
        with patch.object(client, "_qkeee_env", return_value=env):
            out = self.call(self.tools(SENDER), "erp_request", {"action": "org_report"})
        self.assertTrue(out["ok"])
        self.assertIn("metrics", out["result"])

    def test_period_month_is_forwarded(self):
        env = {"QKEEE_ERP_DEFAULT_ADMIN_EMAIL": "nikhil@org.com"}
        with patch.object(client, "_qkeee_env", return_value=env), \
             patch.object(client.request_report, "build_report",
                          wraps=client.request_report.build_report) as build_report:
            out = self.call(self.tools(SENDER), "erp_request", {"action": "org_report", "period": "month"})
        self.assertTrue(out["ok"])
        since, until = client.request_report.last_completed_month_window()
        self.assertEqual(build_report.call_args[0][2:4], (since, until))

    def test_explicit_since_until_are_parsed_as_utc_iso8601(self):
        env = {"QKEEE_ERP_DEFAULT_ADMIN_EMAIL": "nikhil@org.com"}
        with patch.object(client, "_qkeee_env", return_value=env):
            out = self.call(self.tools(SENDER), "erp_request",
                            {"action": "org_report", "since": "2026-01-01T00:00:00",
                             "until": "2026-01-02T00:00:00"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["window"],
                         {"since_us": 1767225600000000, "until_us": 1767312000000000})

    def test_since_without_until_is_invalid(self):
        env = {"QKEEE_ERP_DEFAULT_ADMIN_EMAIL": "nikhil@org.com"}
        with patch.object(client, "_qkeee_env", return_value=env):
            out = self.call(self.tools(SENDER), "erp_request",
                            {"action": "org_report", "since": "2026-01-01T00:00:00"})
        self.assertFalse(out["ok"])

    def test_malformed_since_is_invalid_arguments(self):
        env = {"QKEEE_ERP_DEFAULT_ADMIN_EMAIL": "nikhil@org.com"}
        with patch.object(client, "_qkeee_env", return_value=env):
            out = self.call(self.tools(SENDER), "erp_request",
                            {"action": "org_report", "since": "not-a-date", "until": "2026-01-02"})
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("invalid_arguments"))


if __name__ == "__main__":
    unittest.main()
