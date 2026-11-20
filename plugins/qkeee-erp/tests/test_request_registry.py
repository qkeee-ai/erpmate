"""Tests for core.request_registry: the cross-channel Request Registry
(agents/.scratch/qkeee-erp-email-channel-reliability, issue 02)."""

import os
import tempfile
import unittest

from qkeee_erp_plugin.qkeee_erp.core import request_registry as rr

ALICE = "alice@org.com"
BOB = "bob@org.com"
EMAIL_LINK = {"platform": "email", "chat_id": "alice@org.com", "thread_id": None, "session_id": "s1"}
CHAT_LINK = {"platform": "google_chat", "chat_id": "spaces/A", "thread_id": "threads/T", "session_id": "s2"}


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = os.path.join(tmp.name, "plugin-data", "qkeee-erp", "request_registry.db")


class OpenRequestTests(RegistryTestCase):
    def test_open_creates_a_fresh_id_and_open_status(self):
        row = rr.open_request(self.store, ALICE, "GST report - Oct", EMAIL_LINK)
        self.assertTrue(row["request_id"].startswith("REQ-"))
        self.assertEqual(row["agent_status"], "open")
        self.assertIsNone(row["requester_confirmed"])
        self.assertIsNone(row["closed_at"])
        self.assertEqual(row["sender_email"], ALICE)
        self.assertEqual(row["topic"], "GST report - Oct")

    def test_open_links_the_opening_session(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        self.assertEqual(len(row["linked_sessions"]), 1)
        self.assertEqual(row["linked_sessions"][0]["platform"], "email")

    def test_open_with_anchor_record(self):
        row = rr.open_request(self.store, ALICE, "PO for Acme", EMAIL_LINK,
                              anchor_doctype="Purchase Order", anchor_name="PO-0001")
        self.assertEqual((row["anchor_doctype"], row["anchor_name"]), ("Purchase Order", "PO-0001"))

    def test_two_requests_from_the_same_sender_get_different_ids(self):
        a = rr.open_request(self.store, ALICE, "first", EMAIL_LINK)
        b = rr.open_request(self.store, ALICE, "second", EMAIL_LINK)
        self.assertNotEqual(a["request_id"], b["request_id"])


class LinkSessionTests(RegistryTestCase):
    def test_link_attaches_a_second_channel(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        linked = rr.link_session(self.store, row["request_id"], CHAT_LINK)
        self.assertEqual(len(linked["linked_sessions"]), 2)
        platforms = {s["platform"] for s in linked["linked_sessions"]}
        self.assertEqual(platforms, {"email", "google_chat"})

    def test_link_same_session_twice_is_idempotent(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.link_session(self.store, row["request_id"], CHAT_LINK)
        linked_again = rr.link_session(self.store, row["request_id"], CHAT_LINK)
        self.assertEqual(len(linked_again["linked_sessions"]), 2)

    def test_link_to_unknown_request_returns_none(self):
        self.assertIsNone(rr.link_session(self.store, "REQ-NOPE", CHAT_LINK))


class UpdateStatusTests(RegistryTestCase):
    def test_agent_status_done_stamps_closed_at(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        updated = rr.update_status(self.store, row["request_id"], agent_status="done")
        self.assertEqual(updated["agent_status"], "done")
        self.assertIsNotNone(updated["closed_at"])

    def test_reopening_clears_closed_at(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.update_status(self.store, row["request_id"], agent_status="done")
        reopened = rr.update_status(self.store, row["request_id"], agent_status="in_progress")
        self.assertIsNone(reopened["closed_at"])

    def test_requester_confirmed_is_independent_of_agent_status(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        updated = rr.update_status(self.store, row["request_id"], requester_confirmed="confirmed")
        self.assertEqual(updated["agent_status"], "open")
        self.assertEqual(updated["requester_confirmed"], "confirmed")

    def test_invalid_agent_status_raises(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        with self.assertRaises(ValueError):
            rr.update_status(self.store, row["request_id"], agent_status="finished")

    def test_update_unknown_request_returns_none(self):
        self.assertIsNone(rr.update_status(self.store, "REQ-NOPE", agent_status="done"))


class OpenForSenderTests(RegistryTestCase):
    def test_lists_only_this_sender_open_requests(self):
        rr.open_request(self.store, ALICE, "alice's ask", EMAIL_LINK)
        rr.open_request(self.store, BOB, "bob's ask", EMAIL_LINK)
        rows = rr.open_for_sender(self.store, ALICE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic"], "alice's ask")

    def test_done_requests_are_excluded(self):
        row = rr.open_request(self.store, ALICE, "finished", EMAIL_LINK)
        rr.update_status(self.store, row["request_id"], agent_status="done")
        self.assertEqual(rr.open_for_sender(self.store, ALICE), [])

    def test_most_recently_updated_first(self):
        a = rr.open_request(self.store, ALICE, "older", EMAIL_LINK)
        b = rr.open_request(self.store, ALICE, "newer", EMAIL_LINK)
        rr.update_status(self.store, a["request_id"], agent_status="in_progress")
        rows = rr.open_for_sender(self.store, ALICE)
        self.assertEqual(rows[0]["request_id"], a["request_id"])
        self.assertEqual(rows[1]["request_id"], b["request_id"])

    def test_missing_store_is_an_empty_list(self):
        self.assertEqual(rr.open_for_sender(self.store + ".absent", ALICE), [])


if __name__ == "__main__":
    unittest.main()
