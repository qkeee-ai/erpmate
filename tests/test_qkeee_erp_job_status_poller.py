"""qkeee_erp_job_status_poller.py (agents ADR 0010, issue 23): a thin
no_agent cron script over the "Qkeee Bot Job Approval" doctype's own
durable state, calling the Operator CLI (not importing plugin internals)
to query, render/send one notify.job_status_update, and mark the
execution_log watermark — never twice for the same window, never a mark
without a send that actually went out.

Run with `python -m pytest tests` from the repo root.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "qkeee_erp_job_status_poller", REPO / "scripts" / "qkeee_erp_job_status_poller.py")
poller = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(poller)


class TagEnvVarTests(unittest.TestCase):

    def test_matches_the_client_module_naming_scheme(self):
        self.assertEqual(poller.tag_env_var("prod", "ADMIN_EMAIL"), "QKEEE_ERP_PROD_ADMIN_EMAIL")

    def test_non_alnum_characters_become_underscores(self):
        self.assertEqual(poller.tag_env_var("demo-erp.1", "ADMIN_EMAIL"),
                         "QKEEE_ERP_DEMO_ERP_1_ADMIN_EMAIL")

    def test_empty_tag_falls_back_to_default(self):
        self.assertEqual(poller.tag_env_var("", "ADMIN_EMAIL"), "QKEEE_ERP_DEFAULT_ADMIN_EMAIL")


class ReadEnvFileTests(unittest.TestCase):

    def test_parses_key_value_lines(self):
        with patch("builtins.open", unittest.mock.mock_open(
                read_data='QKEEE_ERP_PROD_ADMIN_EMAIL=admin@example.com\n'
                         '# a comment\n\nQKEEE_ERP_PROD_BASE_URL="https://erp.example"\n')):
            env = poller.read_env_file("/x/qkeee-erp.env")
        self.assertEqual(env["QKEEE_ERP_PROD_ADMIN_EMAIL"], "admin@example.com")
        self.assertEqual(env["QKEEE_ERP_PROD_BASE_URL"], "https://erp.example")

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(poller.read_env_file("/does/not/exist.env"), {})


class ItemForLogEntryTests(unittest.TestCase):

    def test_missing_fields_name_the_decision(self):
        entry = {"batch_index": 2, "step_index": 1, "outcome": "parked", "error": "boom",
                 "write_failure": {"missing_fields": ["department"], "invalid_links": []}}
        item = poller.item_for_log_entry(entry)
        self.assertEqual(item["step"], "batch 2 step 1")
        self.assertEqual(item["reason"], "boom")
        self.assertIn("provide a value for: department", item["decision_needed"])

    def test_invalid_links_name_the_decision(self):
        entry = {"batch_index": 1, "step_index": 3, "outcome": "parked", "error": "bad link",
                 "write_failure": {"missing_fields": [],
                                   "invalid_links": [{"field": "Customer", "value": "X"}]}}
        item = poller.item_for_log_entry(entry)
        self.assertIn("fix invalid link(s)", item["decision_needed"])

    def test_checked_names_the_decision_when_no_write_failure(self):
        entry = {"batch_index": 1, "step_index": 1, "outcome": "parked", "error": "ERPNext API error (403)",
                 "checked": "erp_discover meta(Sales Order): module='Selling' — came up empty, "
                           "no auto-fix attempted."}
        item = poller.item_for_log_entry(entry)
        self.assertIn("review (erp_discover meta", item["decision_needed"])

    def test_plain_fallback_when_nothing_else_is_recorded(self):
        entry = {"batch_index": 1, "step_index": 1, "outcome": "failed"}
        item = poller.item_for_log_entry(entry)
        self.assertEqual(item["reason"], "unknown reason")
        self.assertEqual(item["decision_needed"], "needs human review — see reason")


LOG = [
    {"batch_index": 1, "step_index": 1, "outcome": "succeeded", "name": "SO-1"},
    {"batch_index": 1, "step_index": 2, "outcome": "parked", "error": "boom"},
    {"batch_index": 2, "step_index": 1, "outcome": "failed", "error": "network blip"},
]


class BuildReportTests(unittest.TestCase):

    def test_items_are_only_parked_or_failed(self):
        items, summary = poller.build_report(LOG)
        self.assertEqual([i["step"] for i in items], ["batch 1 step 2", "batch 2 step 1"])
        self.assertEqual(summary, {"parked": 1, "blocked": 1, "completed": 1})

    def test_empty_log_is_the_good_news_case(self):
        items, summary = poller.build_report([])
        self.assertEqual(items, [])
        self.assertEqual(summary, {"parked": 0, "blocked": 0, "completed": 0})


class CardStatusTests(unittest.TestCase):

    def test_no_card_id_returns_empty_without_a_subprocess_call(self):
        with patch.object(poller, "_run_hermes_cli") as run:
            self.assertEqual(poller.card_status(""), "")
        run.assert_not_called()

    def test_reads_the_tasks_own_status(self):
        with patch.object(poller, "_run_hermes_cli",
                          return_value={"task": {"status": "blocked"}}) as run:
            self.assertEqual(poller.card_status("t_abc"), "blocked")
        run.assert_called_once_with("kanban", "show", "t_abc", "--json")

    def test_a_failed_lookup_degrades_to_no_change_not_a_raise(self):
        with patch.object(poller, "_run_hermes_cli", side_effect=poller.HermesCliError("no such task")):
            self.assertEqual(poller.card_status("t_gone"), "")

    def test_malformed_json_also_degrades(self):
        with patch.object(poller, "_run_hermes_cli", side_effect=ValueError("bad json")):
            self.assertEqual(poller.card_status("t_x"), "")


RENDERED = {"ok": True, "result": {"args": {"job_id": "JOB-1", "items": [], "summary": {}},
                                   "confirmation_token": "tok", "issued_at": 1000}}


class PollTagTests(unittest.TestCase):

    def _hermes_calls(self, query_data, render_result=RENDERED, notify_fails=False):
        calls = []

        def side_effect(*args):
            calls.append(args)
            if args[0] == "query":
                return {"ok": True, "result": {"data": query_data}}
            if args[0] == "render":
                return render_result
            if args[0] == "execute-write":
                op = args[args.index("--op") + 1]
                if op == "notify.job_status_update" and notify_fails:
                    raise poller.HermesCliError("send failed")
                return {"ok": True, "result": {}}
            raise AssertionError(f"unexpected call: {args}")
        return calls, side_effect

    def test_skips_a_job_with_no_new_log_entries(self):
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log,
              "step_count": 3, "last_notified_log_index": len(LOG)}
        calls, side_effect = self._hermes_calls([row])
        with patch.object(poller, "run_hermes", side_effect=side_effect):
            reported = poller.poll_tag("prod", "admin@example.com")
        self.assertEqual(reported, [])
        self.assertEqual([c[0] for c in calls], ["query"])

    def test_reports_a_changed_job_then_marks_it_notified(self):
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log,
              "step_count": 3, "last_notified_log_index": 0}
        calls, side_effect = self._hermes_calls([row])
        with patch.object(poller, "run_hermes", side_effect=side_effect):
            reported = poller.poll_tag("prod", "admin@example.com")
        self.assertEqual(reported, ["JOB-1"])
        self.assertEqual([c[0] for c in calls], ["query", "render", "execute-write", "execute-write"])
        notify_call = calls[2]
        self.assertIn("notify.job_status_update", notify_call)
        self.assertIn("--confirmation-token", notify_call)
        self.assertIn("tok", notify_call)
        mark_call = calls[3]
        self.assertIn("job_approval.mark_notified", mark_call)
        mark_args = json.loads(mark_call[mark_call.index("--args") + 1])
        self.assertEqual(mark_args, {"job_id": "JOB-1", "last_notified_log_index": 3,
                                     "last_notified_card_status": ""})

    def test_a_failed_send_never_marks_the_job_notified(self):
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log,
              "step_count": 3, "last_notified_log_index": 0}
        calls, side_effect = self._hermes_calls([row], notify_fails=True)
        with patch.object(poller, "run_hermes", side_effect=side_effect):
            with self.assertRaises(poller.HermesCliError):
                poller.poll_tag("prod", "admin@example.com")
        self.assertEqual([c[0] for c in calls], ["query", "render", "execute-write"])
        self.assertNotIn("job_approval.mark_notified", [a for c in calls for a in c])

    def test_a_newly_blocked_card_reports_even_with_no_new_log_entries(self):
        # ADR 0010's "polls active ... rows and their cards' kanban
        # state": a card can be blocked without any new execution_log
        # entry of its own (e.g. a human blocked it directly).
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log, "step_count": 3,
              "last_notified_log_index": len(LOG), "kanban_card_id": "t_abc",
              "last_notified_card_status": ""}
        calls, side_effect = self._hermes_calls([row])
        with patch.object(poller, "run_hermes", side_effect=side_effect), \
                patch.object(poller, "card_status", return_value="blocked"):
            reported = poller.poll_tag("prod", "admin@example.com")
        self.assertEqual(reported, ["JOB-1"])
        render_call = calls[1]
        render_args = json.loads(render_call[render_call.index("--args") + 1])
        self.assertIn({"step": "kanban card", "reason": "the card's own board status is 'blocked'",
                      "decision_needed": "review the card on the board — this didn't come from "
                                         "a specific execution_log step"}, render_args["items"])
        mark_call = calls[-1]
        mark_args = json.loads(mark_call[mark_call.index("--args") + 1])
        self.assertEqual(mark_args["last_notified_card_status"], "blocked")

    def test_a_still_blocked_card_is_not_reported_again(self):
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log, "step_count": 3,
              "last_notified_log_index": len(LOG), "kanban_card_id": "t_abc",
              "last_notified_card_status": "blocked"}
        calls, side_effect = self._hermes_calls([row])
        with patch.object(poller, "run_hermes", side_effect=side_effect), \
                patch.object(poller, "card_status", return_value="blocked"):
            reported = poller.poll_tag("prod", "admin@example.com")
        self.assertEqual(reported, [])
        self.assertEqual([c[0] for c in calls], ["query"])

    def test_a_card_that_resumes_is_reported_once(self):
        log = json.dumps(LOG)
        row = {"job_id": "JOB-1", "status": "Approved", "execution_log": log, "step_count": 3,
              "last_notified_log_index": len(LOG), "kanban_card_id": "t_abc",
              "last_notified_card_status": "blocked"}
        calls, side_effect = self._hermes_calls([row])
        with patch.object(poller, "run_hermes", side_effect=side_effect), \
                patch.object(poller, "card_status", return_value="ready"):
            reported = poller.poll_tag("prod", "admin@example.com")
        self.assertEqual(reported, ["JOB-1"])
        render_call = calls[1]
        render_args = json.loads(render_call[render_call.index("--args") + 1])
        # Not "blocked", so no synthetic card item is added — build_report()
        # still reports the full open set from the log itself (ADR 0010
        # point 3: "the full open set", not just what changed this pass).
        self.assertEqual(len(render_args["items"]), 2)
        self.assertNotIn("kanban card", [i["step"] for i in render_args["items"]])
        mark_call = calls[-1]
        mark_args = json.loads(mark_call[mark_call.index("--args") + 1])
        self.assertEqual(mark_args["last_notified_card_status"], "ready")


class MainTests(unittest.TestCase):

    def test_skips_tags_with_no_admin_email_configured(self):
        with patch.object(poller, "read_env_file", return_value={}), \
                patch.object(poller, "run_hermes",
                            return_value={"ok": True, "result": {"configured_tags": ["prod"]}}) as rh, \
                patch("builtins.print") as mock_print:
            poller.main()
        # Only the list-envs call: no admin email, so prod is skipped before any query.
        rh.assert_called_once_with("list-envs")
        warnings = [c.args[0] for c in mock_print.call_args_list]
        self.assertTrue(any("ADMIN_EMAIL" in w for w in warnings))

    def test_no_configured_tags_is_a_silent_tick(self):
        with patch.object(poller, "run_hermes", return_value={"ok": True, "result": {}}), \
                patch("builtins.print") as mock_print:
            poller.main()
        mock_print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
