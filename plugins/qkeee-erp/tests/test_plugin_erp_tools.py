"""ERPNext plugin tools (requester identity binding, issue 04): the requester
comes only from the gateway session (or a Kanban task's recorded origin),
never from a tool argument; mode comes from the profile's skill config."""

import json
import os
import unittest
from unittest.mock import patch

import erp_tools
from core import client, operations

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

    def test_meta_is_gated_on_the_session_sender(self):
        import discover
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

    def test_list_ops_needs_no_requester(self):
        out = self.call(self.tools({}), "erp_execute_write", {"phase": "list_ops"})
        self.assertTrue(any(o["op"] == "sales.generic" for o in out["result"]))


if __name__ == "__main__":
    unittest.main()
