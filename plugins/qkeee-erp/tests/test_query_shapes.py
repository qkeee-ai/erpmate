"""erp_query: filter shapes, order_by and count_only (HRMS demo run review,
issue 04).

ERPNext v16 answers a dict `filters` such as {"company": "X"} with HTTP 500
`KeyError: 1`; the plugin turns the dict forms into list form first. A
count is asked for as a count, not as every row."""

import json
import unittest
from unittest.mock import patch

from qkeee_erp_plugin import erp_tools
from qkeee_erp_plugin.qkeee_erp.core import client as core_client

CFG = {"tag": "test", "base_url": "https://erp.example", "api_key": "k", "api_secret": "s",
       "credential": "bot"}


class QueryTests(unittest.TestCase):

    def setUp(self):
        for name, kw in (("_validate_prod_requester", {}), ("_log_read", {}),
                         ("get_env_config", {"return_value": CFG}),
                         ("_request", {"return_value": {"data": [{"name": "A"}]}})):
            p = patch.object(core_client, name, **kw)
            setattr(self, name.strip("_"), p.start())
            self.addCleanup(p.stop)

    def _sent_params(self):
        return self.request.call_args.kwargs["params"]

    def test_dict_value_becomes_equals_filter(self):
        core_client.query_resource("test", "Employee", {"company": "QKEEE LLP"},
                                   requested_by="u@example.com")
        self.assertEqual(json.loads(self._sent_params()["filters"]),
                         [["company", "=", "QKEEE LLP"]])

    def test_dict_operator_pair_becomes_filter(self):
        core_client.query_resource("test", "Attendance",
                                   {"employee": ["in", ["HR-EMP-00008", "HR-EMP-00010"]]},
                                   requested_by="u@example.com")
        self.assertEqual(json.loads(self._sent_params()["filters"]),
                         [["employee", "in", ["HR-EMP-00008", "HR-EMP-00010"]]])

    def test_list_of_lists_is_kept(self):
        filters = [["status", "=", "Open"], ["Attendance", "docstatus", "=", 1]]
        core_client.query_resource("test", "Attendance", filters, requested_by="u@example.com")
        self.assertEqual(json.loads(self._sent_params()["filters"]), filters)

    def test_bad_shapes_refused_before_any_call(self):
        for bad in ("status=Open", ["status", "=", "Open"], {"status": ["in"]},
                    [["status"]], {"status": {"nested": 1}}, 7):
            with self.subTest(filters=bad):
                with self.assertRaises(core_client.InvalidArgumentsError) as cm:
                    core_client.query_resource("test", "Employee", bad, requested_by="u@example.com")
                self.assertIn('[["field", "=", "value"]]', str(cm.exception))
        self.assertFalse(self.request.called)
        self.assertFalse(self.validate_prod_requester.called)

    def test_order_by_is_sent(self):
        core_client.query_resource("test", "Attendance", order_by="attendance_date desc",
                                   requested_by="u@example.com")
        self.assertEqual(self._sent_params()["order_by"], "attendance_date desc")

    def test_order_by_refuses_anything_but_field_and_direction(self):
        for bad in ("name; drop table tabUser", "name desc, creation", "Name", "name sideways",
                    "(select 1)"):
            with self.subTest(order_by=bad), self.assertRaises(core_client.InvalidArgumentsError):
                core_client.query_resource("test", "Attendance", order_by=bad,
                                           requested_by="u@example.com")
        self.assertFalse(self.request.called)

    def test_count_only_uses_get_count_gate_and_read_row(self):
        self.request.return_value = {"message": 102}
        out = core_client.query_resource("test", "Attendance", {"company": "QKEEE LLP"},
                                         count_only=True, requested_by="u@example.com")
        self.assertEqual(out, {"count": 102})
        args = self.request.call_args
        self.assertEqual(args.args[1:3], ("GET", "/api/method/frappe.client.get_count"))
        self.assertEqual(args.kwargs["params"]["doctype"], "Attendance")
        self.assertEqual(json.loads(args.kwargs["params"]["filters"]),
                         [["company", "=", "QKEEE LLP"]])
        self.validate_prod_requester.assert_called_once()
        self.assertEqual(self.validate_prod_requester.call_args.args[3], "read")
        self.log_read.assert_called_once()
        self.assertEqual(self.log_read.call_args.kwargs["response_payload"], {"count": 102})


class ToolTests(unittest.TestCase):

    def test_erp_query_passes_order_by_and_count_only(self):
        tools = erp_tools.ErpTools(lambda: {"active_env": "test"}, requester=lambda: "u@example.com",
                                   audit=lambda args: {})
        with patch.object(core_client, "query_resource", return_value={"count": 3}) as q:
            out = json.loads(tools.handler("erp_query")({
                "doctype": "Attendance", "filters": {"company": "X"},
                "order_by": "modified desc", "count_only": True}))
        self.assertEqual(out["result"], {"count": 3})
        self.assertEqual(q.call_args.kwargs["order_by"], "modified desc")
        self.assertTrue(q.call_args.kwargs["count_only"])

    def test_schema_accepts_object_filters(self):
        props = erp_tools.SCHEMAS["erp_query"]["parameters"]["properties"]
        self.assertEqual(set(props["filters"]["type"]), {"array", "object"})
        self.assertIn("order_by", props)
        self.assertIn("count_only", props)


if __name__ == "__main__":
    unittest.main()
