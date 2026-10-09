#!/usr/bin/env python3
"""Tests for core.kanban_origin: the Kanban task -> Requester origin store
and its resolution (requester identity binding, issue 09)."""

import json
import os
import sqlite3
import tempfile
import unittest

from qkeee_erp_plugin.qkeee_erp.core import kanban_origin as ko

ALICE = {"platform": "google_chat", "user_id": "alice@org.com", "user_id_alt": "users/1",
         "user_name": "Alice", "chat_id": "spaces/A"}
BOB = {"platform": "google_chat", "user_id": "bob@org.com", "user_id_alt": "users/2",
       "user_name": "Bob", "chat_id": "spaces/B"}


def make_kanban_db(path, events):
    """A kanban.db with only the task_events table the resolver reads.
    `events` = [(task_id, kind, payload_dict_or_None)]."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT "
                 "NULL, run_id INTEGER, kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL)")
    for task_id, kind, payload in events:
        conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, 0)",
                     (task_id, kind, json.dumps(payload) if payload is not None else None))
    conn.commit()
    conn.close()


class OriginTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = os.path.join(tmp.name, "plugin-data", "qkeee-erp", "kanban_origin.db")
        self.kanban_db = os.path.join(tmp.name, "kanban.db")
        make_kanban_db(self.kanban_db, [])

    def events(self, events):
        os.remove(self.kanban_db)
        make_kanban_db(self.kanban_db, events)


class ResolveOriginTests(OriginTestCase):
    def test_task_with_its_own_origin_resolves_directly(self):
        ko.record_origin(self.store, "t_root", ALICE)
        got = ko.resolve_origin(self.store, self.kanban_db, "t_root")
        self.assertEqual(got["user_id"], "alice@org.com")
        self.assertEqual(got["origin_task_id"], "t_root")

    def test_decomposer_child_resolves_through_from_decompose_of(self):
        ko.record_origin(self.store, "t_root", ALICE)
        self.events([("t_child", "created", {"by": "decomposer", "from_decompose_of": "t_root"})])
        got = ko.resolve_origin(self.store, self.kanban_db, "t_child")
        self.assertEqual(got["user_id"], "alice@org.com")
        self.assertEqual(got["origin_task_id"], "t_root")

    def test_nested_decomposition_resolves_to_the_outermost_root_with_an_origin(self):
        ko.record_origin(self.store, "t_root", ALICE)
        self.events([("t_mid", "created", {"from_decompose_of": "t_root"}),
                     ("t_leaf", "created", {"from_decompose_of": "t_mid"})])
        got = ko.resolve_origin(self.store, self.kanban_db, "t_leaf")
        self.assertEqual(got["user_id"], "alice@org.com")
        self.assertEqual(got["origin_task_id"], "t_root")

    def test_dependency_parent_with_another_requester_is_never_used(self):
        # t_child depends on Bob's t_other (a "linked" parent), but was
        # decomposed from Alice's t_root: Alice is the requester.
        ko.record_origin(self.store, "t_root", ALICE)
        ko.record_origin(self.store, "t_other", BOB)
        self.events([("t_child", "created", {"from_decompose_of": "t_root"}),
                     ("t_child", "linked", {"parent": "t_other", "child": "t_child"})])
        got = ko.resolve_origin(self.store, self.kanban_db, "t_child")
        self.assertEqual(got["user_id"], "alice@org.com")

    def test_task_without_origin_or_decompose_root_has_no_origin(self):
        self.events([("t_cli", "created", {"by": "cli"})])
        self.assertIsNone(ko.resolve_origin(self.store, self.kanban_db, "t_cli"))

    def test_missing_store_and_board_mean_no_origin(self):
        self.assertIsNone(ko.resolve_origin(self.store + ".absent", self.kanban_db + ".absent", "t_x"))

    def test_decompose_cycle_terminates_with_no_origin(self):
        self.events([("t_a", "created", {"from_decompose_of": "t_b"}),
                     ("t_b", "created", {"from_decompose_of": "t_a"})])
        self.assertIsNone(ko.resolve_origin(self.store, self.kanban_db, "t_a"))


class RecordOriginTests(OriginTestCase):
    def test_first_write_wins(self):
        self.assertTrue(ko.record_origin(self.store, "t_root", ALICE))
        self.assertFalse(ko.record_origin(self.store, "t_root", BOB))
        self.assertEqual(ko.get_origin(self.store, "t_root")["user_id"], "alice@org.com")

    def test_copied_origin_names_its_source_task(self):
        ko.record_origin(self.store, "t_child", ALICE, source_task_id="t_root")
        self.assertEqual(ko.get_origin(self.store, "t_child")["source_task_id"], "t_root")


if __name__ == "__main__":
    unittest.main()
