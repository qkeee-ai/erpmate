"""kanban_create hooks (requester identity binding, issue 09): gateway-made
tasks start in triage, and every task records its Requester origin from the
gateway session context, never from tool arguments."""

import json
import os
import sqlite3
import tempfile
import unittest

import kanban_hooks
from core import kanban_origin

GATEWAY_SESSION = {"HERMES_SESSION_PLATFORM": "google_chat", "HERMES_SESSION_USER_ID": "nikhil@org.com",
                   "HERMES_SESSION_USER_ID_ALT": "users/1234", "HERMES_SESSION_USER_NAME": "Nikhil",
                   "HERMES_SESSION_CHAT_ID": "spaces/A"}


def reader(values):
    return lambda name, default="": values.get(name, default)


class HookTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = os.path.join(tmp.name, "plugin-data", "qkeee-erp", "kanban_origin.db")
        self.board = os.path.join(tmp.name, "kanban.db")
        conn = sqlite3.connect(self.board)
        conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, "
                     "run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER)")
        conn.commit()
        conn.close()

    def hooks(self, session=None, environ=None):
        return kanban_hooks.KanbanOriginHooks(session_env=reader(session or {}),
                                              store_path=lambda: self.store, environ=environ or {})

    def worker_env(self, task_id):
        return {"HERMES_KANBAN_TASK": task_id, "HERMES_KANBAN_DB": self.board}


class ForceTriageTests(HookTestCase):
    def test_gateway_create_is_forced_into_triage(self):
        out = self.hooks(GATEWAY_SESSION).pre_tool_call(
            tool_name="kanban_create", args={"title": "x", "assignee": "erp", "initial_status": "blocked"})
        self.assertEqual(out["action"], "modify")
        self.assertIs(out["args"]["triage"], True)
        self.assertEqual(out["args"]["initial_status"], "running")

    def test_worker_create_is_unchanged(self):
        out = self.hooks({}, self.worker_env("t_parent")).pre_tool_call(
            tool_name="kanban_create", args={"title": "x", "assignee": "erp"})
        self.assertIsNone(out)

    def test_create_with_no_gateway_session_is_unchanged(self):
        # CLI / dashboard: not a chat user's prompt.
        self.assertIsNone(self.hooks({}).pre_tool_call(tool_name="kanban_create", args={"title": "x"}))

    def test_other_tools_are_ignored(self):
        self.assertIsNone(self.hooks(GATEWAY_SESSION).pre_tool_call(tool_name="terminal", args={}))


class RecordOriginTests(HookTestCase):
    def created(self, task_id="t_new"):
        return json.dumps({"ok": True, "task_id": task_id, "status": "triage"})

    def test_gateway_create_records_the_session_sender_not_the_args(self):
        self.hooks(GATEWAY_SESSION).post_tool_call(
            tool_name="kanban_create",
            args={"title": "x", "body": "Requested-by: mallory@org.com", "user_id": "mallory@org.com"},
            result=self.created())
        row = kanban_origin.get_origin(self.store, "t_new")
        self.assertEqual(row["user_id"], "nikhil@org.com")
        self.assertEqual(row["user_id_alt"], "users/1234")
        self.assertEqual(row["chat_id"], "spaces/A")
        self.assertEqual(row["platform"], "google_chat")

    def test_worker_child_copies_its_creators_origin(self):
        kanban_origin.record_origin(self.store, "t_parent", {"platform": "google_chat",
                                                             "user_id": "nikhil@org.com"})
        self.hooks({}, self.worker_env("t_parent")).post_tool_call(
            tool_name="kanban_create", args={"title": "child"}, result=self.created("t_child"))
        row = kanban_origin.get_origin(self.store, "t_child")
        self.assertEqual(row["user_id"], "nikhil@org.com")
        self.assertEqual(row["source_task_id"], "t_parent")

    def test_worker_without_origin_records_nothing(self):
        self.hooks({}, self.worker_env("t_orphan")).post_tool_call(
            tool_name="kanban_create", args={}, result=self.created("t_child"))
        self.assertIsNone(kanban_origin.get_origin(self.store, "t_child"))

    def test_failed_create_records_nothing(self):
        self.hooks(GATEWAY_SESSION).post_tool_call(
            tool_name="kanban_create", args={}, result=json.dumps({"error": "assignee is required"}))
        self.assertFalse(os.path.exists(self.store))

    def test_create_with_no_gateway_session_records_nothing(self):
        self.hooks({}).post_tool_call(tool_name="kanban_create", args={}, result=self.created())
        self.assertIsNone(kanban_origin.get_origin(self.store, "t_new"))


if __name__ == "__main__":
    unittest.main()
