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

    def test_open_starts_with_an_empty_facts_dict(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        self.assertEqual(row["facts"], {})

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
        linked = rr.link_session(self.store, row["request_id"], CHAT_LINK, BOB)
        self.assertEqual(len(linked["linked_sessions"]), 2)
        platforms = {s["platform"] for s in linked["linked_sessions"]}
        self.assertEqual(platforms, {"email", "google_chat"})

    def test_link_records_the_linking_senders_email(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        linked = rr.link_session(self.store, row["request_id"], CHAT_LINK, BOB)
        emails = {s["sender_email"] for s in linked["linked_sessions"]}
        self.assertEqual(emails, {ALICE, BOB})

    def test_link_same_session_twice_is_idempotent(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.link_session(self.store, row["request_id"], CHAT_LINK, BOB)
        linked_again = rr.link_session(self.store, row["request_id"], CHAT_LINK, BOB)
        self.assertEqual(len(linked_again["linked_sessions"]), 2)

    def test_link_to_unknown_request_returns_none(self):
        self.assertIsNone(rr.link_session(self.store, "REQ-NOPE", CHAT_LINK, BOB))


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


class LinkedSendersTests(RegistryTestCase):
    """Code review, issue 04: identity-based "already linked" check,
    replacing an earlier (platform, chat_id, thread_id) comparison that
    couldn't tell two different people apart on a shared-chat_id platform."""

    def test_includes_the_opener(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        self.assertEqual(rr.linked_senders(row), {ALICE})

    def test_includes_a_later_linker(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        linked = rr.link_session(self.store, row["request_id"], CHAT_LINK, BOB)
        self.assertEqual(rr.linked_senders(linked), {ALICE, BOB})

    def test_someone_who_never_linked_is_not_included(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        self.assertNotIn("priya@org.com", rr.linked_senders(row))

    def test_is_case_insensitive(self):
        row = rr.open_request(self.store, "Alice@Org.com", "topic", EMAIL_LINK)
        self.assertEqual(rr.linked_senders(row), {"alice@org.com"})


class MergeFactsTests(RegistryTestCase):
    """Issue 04: a durable, compaction-proof bag of resolved task state."""

    def test_facts_are_added(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        updated = rr.merge_facts(self.store, row["request_id"], {"supplier_id": "SUP-001"})
        self.assertEqual(updated["facts"], {"supplier_id": "SUP-001"})

    def test_new_facts_merge_with_existing_ones(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.merge_facts(self.store, row["request_id"], {"supplier_id": "SUP-001"})
        updated = rr.merge_facts(self.store, row["request_id"], {"item_code": "ITEM-42"})
        self.assertEqual(updated["facts"], {"supplier_id": "SUP-001", "item_code": "ITEM-42"})

    def test_a_repeated_key_overwrites_only_that_key(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.merge_facts(self.store, row["request_id"], {"supplier_id": "SUP-001", "item_code": "OLD"})
        updated = rr.merge_facts(self.store, row["request_id"], {"item_code": "ITEM-42"})
        self.assertEqual(updated["facts"], {"supplier_id": "SUP-001", "item_code": "ITEM-42"})

    def test_merge_on_unknown_request_returns_none(self):
        self.assertIsNone(rr.merge_facts(self.store, "REQ-NOPE", {"a": "b"}))

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


class OpenInSessionTests(RegistryTestCase):
    """Issue 03: finding a request already open in THIS room, regardless of
    who is asking — the lookup direction a second participant's turn uses."""

    def test_finds_a_request_opened_by_someone_else_in_the_same_room(self):
        rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rows = rr.open_in_session(self.store, "google_chat", "spaces/A", "threads/T")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sender_email"], ALICE)

    def test_a_different_thread_in_the_same_space_does_not_match(self):
        rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rows = rr.open_in_session(self.store, "google_chat", "spaces/A", "threads/OTHER")
        self.assertEqual(rows, [])

    def test_a_different_chat_id_does_not_match(self):
        rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rows = rr.open_in_session(self.store, "google_chat", "spaces/B", "threads/T")
        self.assertEqual(rows, [])

    def test_no_thread_id_on_either_side_never_matches(self):
        # Adversarial review: matching "both null" would surface every
        # unrelated conversation in an unthreaded group space to anyone
        # posting there. A missing thread_id means "don't match", not
        # "match anything else missing one".
        rr.open_request(self.store, ALICE, "alice's ask", EMAIL_LINK)
        rows = rr.open_in_session(self.store, "email", "alice@org.com")
        self.assertEqual(rows, [])

    def test_a_query_with_no_thread_id_matches_nothing_even_in_a_threaded_room(self):
        rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rows = rr.open_in_session(self.store, "google_chat", "spaces/A")
        self.assertEqual(rows, [])

    def test_done_requests_are_excluded(self):
        row = rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rr.update_status(self.store, row["request_id"], agent_status="done")
        self.assertEqual(rr.open_in_session(self.store, "google_chat", "spaces/A", "threads/T"), [])

    def test_a_second_session_linked_to_the_same_room_also_matches(self):
        row = rr.open_request(self.store, ALICE, "alice's ask", EMAIL_LINK)
        bob_link = {"platform": "google_chat", "chat_id": "spaces/A", "thread_id": "threads/T",
                   "session_id": "s-bob"}
        rr.link_session(self.store, row["request_id"], bob_link, BOB)
        rows = rr.open_in_session(self.store, "google_chat", "spaces/A", "threads/T")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], row["request_id"])

    def test_missing_store_is_an_empty_list(self):
        self.assertEqual(rr.open_in_session(self.store + ".absent", "google_chat", "spaces/A"), [])

    def test_no_chat_id_is_an_empty_list(self):
        self.assertEqual(rr.open_in_session(self.store, "google_chat", ""), [])

    def test_a_different_platform_with_the_same_chat_id_and_thread_id_does_not_match(self):
        rr.open_request(self.store, ALICE, "alice's ask", CHAT_LINK)
        rows = rr.open_in_session(self.store, "slack", "spaces/A", "threads/T")
        self.assertEqual(rows, [])


class RequestsInWindowTests(RegistryTestCase):
    """Issue 11 §2: every request touched in [since, until), any sender,
    any status — unlike open_for_sender()/open_in_session()."""

    def test_empty_store_is_an_empty_list(self):
        self.assertEqual(rr.requests_in_window(self.store + ".absent", 0, 10), [])

    def test_a_request_created_in_window_is_included(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        rows = rr.requests_in_window(self.store, row["created_at"] - 1, row["created_at"] + 1)
        self.assertEqual([r["request_id"] for r in rows], [row["request_id"]])

    def test_a_request_outside_the_window_is_excluded(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        rows = rr.requests_in_window(self.store, row["created_at"] + 1000, row["created_at"] + 2000)
        self.assertEqual(rows, [])

    def test_boundary_since_is_inclusive(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        rows = rr.requests_in_window(self.store, row["created_at"], row["created_at"] + 1)
        self.assertEqual(len(rows), 1)

    def test_boundary_until_is_exclusive(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        rows = rr.requests_in_window(self.store, row["created_at"] - 1, row["created_at"])
        self.assertEqual(rows, [])

    def test_a_request_merely_touched_in_window_is_included(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        before = row["created_at"] - 10_000_000
        touched = rr.merge_facts(self.store, row["request_id"], {"a": "b"})
        rows = rr.requests_in_window(self.store, touched["updated_at"] - 1, touched["updated_at"] + 1)
        self.assertEqual([r["request_id"] for r in rows], [row["request_id"]])
        # not created in this narrow window, only touched
        self.assertNotEqual(rows[0]["created_at"], touched["updated_at"])

    def test_a_request_closed_in_window_is_included_by_closed_at(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        closed = rr.update_status(self.store, row["request_id"], agent_status="done")
        rows = rr.requests_in_window(self.store, closed["closed_at"], closed["closed_at"] + 1)
        self.assertEqual([r["request_id"] for r in rows], [row["request_id"]])

    def test_mixed_statuses_and_senders_all_included(self):
        a = rr.open_request(self.store, ALICE, "a", EMAIL_LINK)
        b = rr.open_request(self.store, BOB, "b", EMAIL_LINK)
        rr.update_status(self.store, b["request_id"], agent_status="done")
        rows = rr.requests_in_window(self.store, 0, a["created_at"] + 10_000_000_000)
        self.assertEqual({r["request_id"] for r in rows}, {a["request_id"], b["request_id"]})


class OpenRequestsTests(RegistryTestCase):
    """Issue 11 §7: a backlog snapshot — every open/in_progress request,
    any sender, no window."""

    def test_empty_store_is_an_empty_list(self):
        self.assertEqual(rr.open_requests(self.store + ".absent"), [])

    def test_includes_open_and_in_progress_across_senders(self):
        a = rr.open_request(self.store, ALICE, "a", EMAIL_LINK)
        b = rr.open_request(self.store, BOB, "b", EMAIL_LINK)
        rr.update_status(self.store, b["request_id"], agent_status="in_progress")
        rows = rr.open_requests(self.store)
        self.assertEqual({r["request_id"] for r in rows}, {a["request_id"], b["request_id"]})

    def test_done_requests_are_excluded(self):
        row = rr.open_request(self.store, ALICE, "a", EMAIL_LINK)
        rr.update_status(self.store, row["request_id"], agent_status="done")
        self.assertEqual(rr.open_requests(self.store), [])


class CostCheckpointTests(RegistryTestCase):
    """Issue 11 §8: the delta-over-cumulative checkpoint for hermes-
    agent's lifetime-cumulative token/cost counters."""

    def test_unknown_session_has_no_checkpoint(self):
        self.assertEqual(rr.get_cost_checkpoints(self.store, ["s1"]), {})

    def test_missing_store_has_no_checkpoint(self):
        self.assertEqual(rr.get_cost_checkpoints(self.store + ".absent", ["s1"]), {})

    def test_set_then_get_round_trips(self):
        rr.set_cost_checkpoints(self.store, {"s1": {"tokens_in_as_of": 100, "tokens_out_as_of": 50,
                                                     "cost_as_of_usd": 0.01}})
        checkpoints = rr.get_cost_checkpoints(self.store, ["s1"])
        self.assertEqual(checkpoints["s1"]["tokens_in_as_of"], 100)
        self.assertEqual(checkpoints["s1"]["tokens_out_as_of"], 50)
        self.assertEqual(checkpoints["s1"]["cost_as_of_usd"], 0.01)

    def test_setting_again_overwrites_not_accumulates(self):
        rr.set_cost_checkpoints(self.store, {"s1": {"tokens_in_as_of": 100, "tokens_out_as_of": 50,
                                                     "cost_as_of_usd": 0.01}})
        rr.set_cost_checkpoints(self.store, {"s1": {"tokens_in_as_of": 200, "tokens_out_as_of": 90,
                                                     "cost_as_of_usd": 0.02}})
        checkpoints = rr.get_cost_checkpoints(self.store, ["s1"])
        self.assertEqual(checkpoints["s1"]["tokens_in_as_of"], 200)

    def test_empty_checkpoints_is_a_noop(self):
        rr.set_cost_checkpoints(self.store, {})
        self.assertFalse(os.path.exists(self.store))


class LogEventTests(RegistryTestCase):
    """Adversarial review, issue 03: a forensic trail of who did what to a
    request and when, since none of the lookups/mutations above leave one."""

    def _events(self, request_id):
        import sqlite3
        conn = sqlite3.connect(self.store)
        try:
            return conn.execute(
                "SELECT action, sender_email, note FROM request_event WHERE request_id = ? "
                "ORDER BY id", (request_id,)).fetchall()
        finally:
            conn.close()

    def test_an_event_is_recorded(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.log_event(self.store, row["request_id"], "open", ALICE)
        self.assertEqual(self._events(row["request_id"]), [("open", ALICE, None)])

    def test_an_optional_note_is_recorded(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.log_event(self.store, row["request_id"], "status", ALICE, note="confirmed by requester")
        self.assertEqual(self._events(row["request_id"])[0][2], "confirmed by requester")

    def test_events_accumulate_in_order(self):
        row = rr.open_request(self.store, ALICE, "topic", EMAIL_LINK)
        rr.log_event(self.store, row["request_id"], "open", ALICE)
        rr.log_event(self.store, row["request_id"], "link", BOB)
        self.assertEqual([e[0] for e in self._events(row["request_id"])], ["open", "link"])


if __name__ == "__main__":
    unittest.main()
