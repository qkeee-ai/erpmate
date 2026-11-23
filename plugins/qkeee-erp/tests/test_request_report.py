"""Tests for core.request_report: the pure weekly/org-report compute
layer over the Request Registry and hermes-agent's state.db (agents
issue 11)."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from qkeee_erp_plugin.qkeee_erp.core import request_registry as rr
from qkeee_erp_plugin.qkeee_erp.core import request_report as rpt

ALICE = "alice@org.com"
BOB = "bob@org.com"
EMAIL_LINK = {"platform": "email", "chat_id": "alice@org.com", "thread_id": None, "session_id": "s1"}


def _us(dt) -> int:
    return int(dt.timestamp() * 1_000_000)


# A fixed Monday, well clear of any DST/leap-year edge, for deterministic
# calendar math.
_MONDAY = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


class WorkingDaysElapsedTests(unittest.TestCase):
    def test_same_day_is_zero(self):
        self.assertEqual(rpt.working_days_elapsed(_us(_MONDAY), _us(_MONDAY)), 0)

    def test_monday_to_tuesday_is_one(self):
        tue = _MONDAY + timedelta(days=1)
        self.assertEqual(rpt.working_days_elapsed(_us(_MONDAY), _us(tue)), 1)

    def test_friday_to_following_monday_is_one(self):
        # Code review concern (D11-b): a request untouched since Friday is
        # not "2 (or 3) days stale" Monday morning once weekends don't count.
        fri = _MONDAY + timedelta(days=4)
        next_monday = _MONDAY + timedelta(days=7)
        self.assertEqual(rpt.working_days_elapsed(_us(fri), _us(next_monday)), 1)

    def test_a_full_work_week_is_five(self):
        next_monday = _MONDAY + timedelta(days=7)
        self.assertEqual(rpt.working_days_elapsed(_us(_MONDAY), _us(next_monday)), 5)

    def test_end_before_start_is_zero(self):
        tue = _MONDAY + timedelta(days=1)
        self.assertEqual(rpt.working_days_elapsed(_us(tue), _us(_MONDAY)), 0)


class WeekWindowTests(unittest.TestCase):
    def test_window_is_seven_days(self):
        since, until = rpt.last_completed_week_window(_us(_MONDAY))
        self.assertEqual(until - since, 7 * 24 * 3600 * 1_000_000)

    def test_window_starts_on_a_monday(self):
        since, _ = rpt.last_completed_week_window(_us(_MONDAY))
        start = datetime.fromtimestamp(since / 1_000_000, tz=timezone.utc)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((start.hour, start.minute, start.second), (0, 0, 0))

    def test_window_is_the_week_before_a_monday_run(self):
        # Run at 09:00 on a Monday (D11-e) -> window is the Monday before it,
        # not the current (just-starting) week.
        since, until = rpt.last_completed_week_window(_us(_MONDAY))
        until_dt = datetime.fromtimestamp(until / 1_000_000, tz=timezone.utc)
        monday_date = _MONDAY.date()
        self.assertEqual(until_dt.date(), monday_date)


class MonthWindowTests(unittest.TestCase):
    def test_last_full_calendar_month(self):
        at = datetime(2026, 12, 3, tzinfo=timezone.utc)
        since, until = rpt.last_completed_month_window(_us(at))
        since_dt = datetime.fromtimestamp(since / 1_000_000, tz=timezone.utc)
        until_dt = datetime.fromtimestamp(until / 1_000_000, tz=timezone.utc)
        self.assertEqual((since_dt.year, since_dt.month, since_dt.day), (2026, 11, 1))
        self.assertEqual((until_dt.year, until_dt.month, until_dt.day), (2026, 12, 1))

    def test_crosses_a_year_boundary(self):
        at = datetime(2027, 1, 5, tzinfo=timezone.utc)
        since, until = rpt.last_completed_month_window(_us(at))
        since_dt = datetime.fromtimestamp(since / 1_000_000, tz=timezone.utc)
        self.assertEqual((since_dt.year, since_dt.month), (2026, 12))


class BuildReportTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = os.path.join(tmp.name, "request_registry.db")
        self.state_db = os.path.join(tmp.name, "state.db")
        # Wide enough to contain whatever wall-clock time open_request()'s
        # own _now() actually stamps during the test, without the test
        # depending on matching a hardcoded date.
        self.since = 0
        self.until = rpt.now_us() + 10 ** 15

    def _seed_sessions(self, rows: dict) -> None:
        """rows: {session_id: {input_tokens, output_tokens, estimated_cost_usd,
        actual_cost_usd, cost_status, cost_source}}"""
        conn = sqlite3.connect(self.state_db)
        try:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, input_tokens INTEGER, "
                        "output_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, "
                        "cost_status TEXT, cost_source TEXT)")
            for sid, v in rows.items():
                conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (sid, v.get("input_tokens", 0), v.get("output_tokens", 0),
                             v.get("estimated_cost_usd"), v.get("actual_cost_usd"),
                             v.get("cost_status"), v.get("cost_source")))
            conn.commit()
        finally:
            conn.close()


class MetricsTests(BuildReportTestCase):
    def test_received_counts_requests_created_in_window(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(report["metrics"]["received"], 1)

    def test_a_request_created_before_the_window_is_not_received(self):
        row = rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, row["created_at"] + 1,
                                  row["created_at"] + 1000)
        self.assertEqual(report["metrics"]["received"], 0)

    def test_completed_counts_done_requests_closed_in_window(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        rr.update_status(self.store, row["request_id"], agent_status="done")
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(report["metrics"]["completed"], 1)

    def test_confirmed_and_disputed_and_done_unconfirmed_partition_completed(self):
        a = rr.open_request(self.store, ALICE, "a", EMAIL_LINK)
        b = rr.open_request(self.store, BOB, "b", EMAIL_LINK)
        c = rr.open_request(self.store, ALICE, "c", EMAIL_LINK)
        rr.update_status(self.store, a["request_id"], agent_status="done",
                         requester_confirmed="confirmed")
        rr.update_status(self.store, b["request_id"], agent_status="done",
                         requester_confirmed="disputed")
        rr.update_status(self.store, c["request_id"], agent_status="done")
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        m = report["metrics"]
        self.assertEqual((m["completed"], m["confirmed"], m["disputed"], m["done_unconfirmed"]),
                        (3, 1, 1, 1))
        self.assertAlmostEqual(m["confirmed_rate"], 1 / 3)

    def test_confirmed_rate_is_none_with_no_completions(self):
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertIsNone(report["metrics"]["confirmed_rate"])

    def test_carried_over_counts_pre_window_still_open(self):
        row = rr.open_request(self.store, ALICE, "old", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, row["created_at"] + 10_000_000,
                                  row["created_at"] + 20_000_000)
        self.assertEqual(report["metrics"]["carried_over"], 1)
        self.assertEqual(report["metrics"]["received"], 0)

    def test_carried_over_excludes_done_requests(self):
        row = rr.open_request(self.store, ALICE, "old", {**EMAIL_LINK, "session_id": None})
        rr.update_status(self.store, row["request_id"], agent_status="done")
        report = rpt.build_report(self.store, self.state_db, row["created_at"] + 10_000_000,
                                  row["created_at"] + 20_000_000)
        self.assertEqual(report["metrics"]["carried_over"], 0)

    def test_stale_open_uses_working_days_not_calendar_days(self):
        row = rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        friday = _us(_MONDAY + timedelta(days=4))
        monday_after = _us(_MONDAY + timedelta(days=7))
        conn = __import__("sqlite3").connect(self.store)
        conn.execute("UPDATE request SET updated_at = ? WHERE request_id = ?",
                    (friday, row["request_id"]))
        conn.commit()
        conn.close()
        report = rpt.build_report(self.store, self.state_db, self.since, self.until,
                                  at_us=monday_after)
        self.assertEqual(report["metrics"]["stale_open"], 0)  # only 1 working day since Friday

    def test_time_to_close_computes_median_and_p90(self):
        a = rr.open_request(self.store, ALICE, "a", {**EMAIL_LINK, "session_id": None})
        conn = __import__("sqlite3").connect(self.store)
        conn.execute("UPDATE request SET created_at = ? WHERE request_id = ?",
                    (self.since + 1000, a["request_id"]))
        conn.commit()
        conn.close()
        rr.update_status(self.store, a["request_id"], agent_status="done")
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        tt = report["metrics"]["time_to_close"]
        self.assertEqual(tt["n"], 1)
        self.assertGreaterEqual(tt["median_hours"], 0)
        self.assertEqual(tt["median_hours"], tt["p90_hours"])

    def test_time_to_close_is_none_with_no_completions(self):
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(report["metrics"]["time_to_close"]["n"], 0)
        self.assertIsNone(report["metrics"]["time_to_close"]["median_hours"])


class PerSenderTableTests(BuildReportTestCase):
    def test_rows_are_sorted_received_desc(self):
        for i in range(3):
            rr.open_request(self.store, ALICE, f"t{i}", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        rows = report["per_sender_table"]["rows"]
        created = [r["created_at"] for r in rows]
        self.assertEqual(created, sorted(created, reverse=True))

    def test_cap_and_has_more(self):
        for i in range(27):
            rr.open_request(self.store, ALICE, f"t{i}", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        table = report["per_sender_table"]
        self.assertEqual(len(table["rows"]), 25)
        self.assertTrue(table["has_more"])
        self.assertEqual(table["total"], 27)

    def test_no_overflow_under_the_cap(self):
        rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertFalse(report["per_sender_table"]["has_more"])


class PendingBreakdownTests(BuildReportTestCase):
    def test_a_request_with_no_pending_fact_is_no_blocker_recorded(self):
        rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(len(report["pending"]["no_blocker_recorded"]), 1)

    def test_a_request_with_a_pending_fact_is_grouped_by_type(self):
        row = rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        rr.merge_facts(self.store, row["request_id"],
                       {"pending": {"type": "approval", "detail": "waiting on sign-off",
                                    "since": "2026-11-23"}})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(len(report["pending"]["approval"]), 1)
        self.assertEqual(report["pending"]["approval"][0]["detail"], "waiting on sign-off")
        self.assertEqual(len(report["pending"]["no_blocker_recorded"]), 0)

    def test_pending_is_a_backlog_snapshot_not_window_scoped(self):
        row = rr.open_request(self.store, ALICE, "old", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, row["created_at"] + 10_000_000,
                                  row["created_at"] + 20_000_000)
        self.assertEqual(len(report["pending"]["no_blocker_recorded"]), 1)

    def test_a_done_request_is_excluded_from_pending(self):
        row = rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        rr.update_status(self.store, row["request_id"], agent_status="done")
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        total_pending = sum(len(v) for v in report["pending"].values())
        self.assertEqual(total_pending, 0)


class UsageJoinTests(BuildReportTestCase):
    def test_request_with_no_session_shows_no_usage(self):
        rr.open_request(self.store, ALICE, "t", {**EMAIL_LINK, "session_id": None})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        usage = report["per_sender_table"]["rows"][0]["usage"]
        self.assertIsNone(usage["tokens_in"])

    def test_missing_state_db_shows_no_usage(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        report = rpt.build_report(self.store, self.state_db + ".absent", self.since, self.until)
        usage = report["per_sender_table"]["rows"][0]["usage"]
        self.assertIsNone(usage["tokens_in"])
        self.assertEqual(report["org_usage"]["tokens_in"], 0)

    def test_first_report_run_delta_is_the_full_cumulative_total(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 1000, "output_tokens": 200,
                                    "estimated_cost_usd": 0.05, "cost_status": "ok"}})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        usage = report["per_sender_table"]["rows"][0]["usage"]
        self.assertEqual(usage["tokens_in"], 1000)
        self.assertEqual(usage["tokens_out"], 200)
        self.assertAlmostEqual(usage["cost_usd"], 0.05)

    def test_second_report_run_only_counts_the_delta(self):
        row = rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 1000, "output_tokens": 200,
                                    "estimated_cost_usd": 0.05, "cost_status": "ok"}})
        rpt.build_report(self.store, self.state_db, self.since, self.until, advance_checkpoint=True)
        # More usage accrues against the same session between runs.
        conn = sqlite3.connect(self.state_db)
        conn.execute("UPDATE sessions SET input_tokens = 1500, output_tokens = 260, "
                    "estimated_cost_usd = 0.08 WHERE id = 's1'")
        conn.commit()
        conn.close()
        report2 = rpt.build_report(self.store, self.state_db, self.since, self.until)
        usage = report2["per_sender_table"]["rows"][0]["usage"]
        self.assertEqual(usage["tokens_in"], 500)
        self.assertEqual(usage["tokens_out"], 60)
        self.assertAlmostEqual(usage["cost_usd"], 0.03)

    def test_advance_checkpoint_false_does_not_persist(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 1000, "output_tokens": 200,
                                    "estimated_cost_usd": 0.05, "cost_status": "ok"}})
        rpt.build_report(self.store, self.state_db, self.since, self.until, advance_checkpoint=False)
        self.assertEqual(rr.get_cost_checkpoints(self.store, ["s1"]), {})

    def test_actual_cost_preferred_over_estimated_when_present(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 100, "output_tokens": 10,
                                    "estimated_cost_usd": 0.05, "actual_cost_usd": 0.03,
                                    "cost_status": "ok"}})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertAlmostEqual(report["per_sender_table"]["rows"][0]["usage"]["cost_usd"], 0.03)

    def test_non_ok_cost_status_is_surfaced(self):
        rr.open_request(self.store, ALICE, "t", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 100, "output_tokens": 10,
                                    "estimated_cost_usd": 0.0, "cost_status": "included"}})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(report["per_sender_table"]["rows"][0]["usage"]["cost_status"], "included")

    def test_org_usage_sums_every_linked_session_once(self):
        rr.open_request(self.store, ALICE, "a", {**EMAIL_LINK, "session_id": "s1"})
        rr.open_request(self.store, BOB, "b", {**EMAIL_LINK, "session_id": "s2"})
        self._seed_sessions({
            "s1": {"input_tokens": 100, "output_tokens": 10, "estimated_cost_usd": 0.01,
                  "cost_status": "ok"},
            "s2": {"input_tokens": 200, "output_tokens": 20, "estimated_cost_usd": 0.02,
                  "cost_status": "ok"}})
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        self.assertEqual(report["org_usage"]["tokens_in"], 300)
        self.assertAlmostEqual(report["org_usage"]["cost_usd"], 0.03)


class FormatReportTextTests(BuildReportTestCase):
    def test_renders_without_crashing_on_an_empty_window(self):
        report = rpt.build_report(self.store, self.state_db, self.since, self.until)
        text = rpt.format_report_text(report)
        self.assertIn("Received: 0", text)
        self.assertIn("Completed: 0", text)
        self.assertIn("Time-to-close: no completed requests", text)

    def test_includes_requests_and_usage_in_the_table(self):
        rr.open_request(self.store, ALICE, "GST report", EMAIL_LINK)
        self._seed_sessions({"s1": {"input_tokens": 10, "output_tokens": 5,
                                    "estimated_cost_usd": 0.001, "cost_status": "ok"}})
        text = rpt.format_report_text(rpt.build_report(self.store, self.state_db, self.since,
                                                        self.until), tag="default")
        self.assertIn("GST report", text)
        self.assertIn(ALICE, text)
        self.assertIn("10in/5out", text)
        self.assertIn("[default]", text)


if __name__ == "__main__":
    unittest.main()
