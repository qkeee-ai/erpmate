"""Shorter write and read results (HRMS demo run review, issue 05).

A Holiday List create returned all 59 child rows, 26 KB. A write result
now carries name, doctype, docstatus, modified and the fields the payload
set, with a child table as a row count; `full_result` gives the whole
document. The Audit Log keeps the full document either way."""

import json
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client as core_client
import testsupport
from test_operations import connector

HOLIDAYS = [{"name": f"row-{i}", "holiday_date": f"2026-01-{i % 28 + 1:02d}",
             "description": "Sunday — weekly off for every employee of the company",
             "weekly_off": 1, "idx": i, "parent": "HL-2026", "parentfield": "holidays",
             "parenttype": "Holiday List", "doctype": "Holiday", "owner": "bot@example.com",
             "creation": "2026-10-22 10:00:00", "modified": "2026-10-22 10:00:00"}
            for i in range(59)]
CREATED = {"name": "HL-2026", "doctype": "Holiday List", "docstatus": 0,
           "modified": "2026-10-22 10:00:00.1", "holiday_list_name": "HL-2026",
           "from_date": "2026-01-01", "to_date": "2026-12-31", "total_holidays": 59,
           "owner": "bot@example.com", "creation": "2026-10-22 10:00:00", "color": "#fff",
           "holidays": HOLIDAYS}
ARGS = {"doctype": "Holiday List", "action": "create",
        "payload": {"holiday_list_name": "HL-2026", "from_date": "2026-01-01",
                    "to_date": "2026-12-31", "holidays": [{"holiday_date": "2026-01-04"}]}}


def _tools():
    return erp_tools.ErpTools(lambda: {"active_env": "test", "mode": "read-write"},
                              requester=lambda: testsupport.REQ, audit=lambda args: {})


class WriteResultTests(unittest.TestCase):

    def _execute(self, **extra):
        with connector() as m:
            m["_do_mutate"].return_value = {"data": json.loads(json.dumps(CREATED))}
            full, exec_ctx, _ = testsupport.render("unscoped.generic", json.loads(json.dumps(ARGS)))
            raw = _tools().handler("erp_execute_write")(dict({
                "phase": "execute", "op": "unscoped.generic", "args": full,
                "confirmation_token": exec_ctx.confirmation_token, "issued_at": exec_ctx.issued_at,
                "user_confirmation_text": exec_ctx.user_confirmation_text,
                "latest_prompt": "yes"}, **extra))
            return raw, m

    def test_create_with_59_rows_returns_under_2kb_with_row_count(self):
        raw, _ = self._execute()
        self.assertLess(len(raw), 2048)
        data = json.loads(raw)["result"]["data"]
        self.assertEqual(data, {"name": "HL-2026", "doctype": "Holiday List", "docstatus": 0,
                                "modified": "2026-10-22 10:00:00.1",
                                "holiday_list_name": "HL-2026", "from_date": "2026-01-01",
                                "to_date": "2026-12-31", "holidays": "59 rows"})

    def test_full_result_returns_the_full_document(self):
        raw, _ = self._execute(full_result=True)
        self.assertEqual(json.loads(raw)["result"]["data"]["holidays"], HOLIDAYS)

    def test_audit_log_keeps_the_full_document(self):
        _, m = self._execute()
        self.assertEqual(m["record_audit_log_finish"].call_args.kwargs["payload_after"]["holidays"],
                         HOLIDAYS)


class QueryNoteTests(unittest.TestCase):

    def test_large_query_result_carries_note(self):
        rows = [{"name": f"HR-ATT-{i:05d}", "employee": "HR-EMP-00008", "status": "Present",
                 "attendance_date": "2026-08-01", "company": "QKEEE LLP"} for i in range(102)]
        with patch.object(core_client, "query_resource",
                          return_value={"data": rows, "has_more": False, "limit": 200}):
            out = json.loads(_tools().handler("erp_query")({"doctype": "Attendance",
                                                             "limit": 200}))
        self.assertIn("count_only", out["result"]["_note"])

    def test_small_query_result_has_no_note(self):
        with patch.object(core_client, "query_resource",
                          return_value={"data": [{"name": "A"}], "has_more": False, "limit": 20}):
            out = json.loads(_tools().handler("erp_query")({"doctype": "Attendance"}))
        self.assertNotIn("_note", out["result"])


if __name__ == "__main__":
    unittest.main()
