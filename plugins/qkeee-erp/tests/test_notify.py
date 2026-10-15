"""notify.summary_email (item-run review issue 08): the task summary email
goes to the requester only, its HTML comes from the plugin template with
every value escaped, and execute logs exactly the rendered email."""

import copy
import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
from qkeee_erp_plugin.qkeee_erp import mailer
from qkeee_erp_plugin.qkeee_erp.domains import notify
import testsupport
from test_operations import connector

KEY = "notify.summary_email"
ARGS = {"title": "Supplier, Item and Purchase Invoice created",
        "records": [{"doctype": "Purchase Invoice", "name": "ACC-PINV-2026-00001", "action": "submit"},
                    {"doctype": "Supplier", "name": "Acme Media", "action": "create"},
                    {"doctype": "Item", "name": "Media Marketing Services", "action": "create"}],
        "notes": ["Expense account: Marketing Expenses - DL"],
        "follow_ups": ["Add SAC 998361 to the Item."]}

LIVE = {"Purchase Invoice": {"docstatus": 1, "status": "Unpaid", "supplier_name": "Acme Media",
                             "grand_total": 11800, "currency": "INR"},
        "Supplier": {"docstatus": 0, "supplier_name": "Acme Media Pvt Ltd"},
        "Item": {"docstatus": 0, "item_name": "Media Marketing Services"}}
META = {"Purchase Invoice": {"is_submittable": 1}, "Supplier": {}, "Item": {}}


def _live(tag, doctype, name, **kw):
    return {"data": dict(LIVE[doctype], name=name)}


def _meta(tag, doctype):
    return META[doctype]


class SummaryEmailTests(unittest.TestCase):

    def _render(self, args=None, **ctx_kw):
        with connector() as m, patch.object(core_client, "_fetch_merged_meta", side_effect=_meta):
            m["get_resource"].side_effect = _live
            return testsupport.render(KEY, copy.deepcopy(args or ARGS), **ctx_kw)

    def test_render_builds_html_for_the_requester_only(self):
        _, _, out = self._render()
        body = out["request"]["body"]
        self.assertEqual(out["policy"], operations.POLICY_TOKEN)
        self.assertNotIn("confirmation_code", out)
        self.assertEqual(body["recipients"], testsupport.REQ)
        self.assertEqual(body["reference_doctype"], "Purchase Invoice")
        self.assertEqual(body["reference_name"], "ACC-PINV-2026-00001")
        self.assertEqual(body["subject"], "[test] Supplier, Item and Purchase Invoice created")
        html = body["content"]
        self.assertIn("https://erp.example/app/purchase-invoice/ACC-PINV-2026-00001", html)
        self.assertIn("Submitted · Unpaid", html)
        self.assertIn("INR 11,800.00", html)
        self.assertIn("Saved (live)", html)            # Item / Supplier: not submittable
        self.assertIn("Acme Media Pvt Ltd", html)
        self.assertIn("Add SAC 998361 to the Item.", html)

    def test_no_argument_sets_the_recipient_or_the_link_host(self):
        args = dict(ARGS, recipients="other@example.com")
        with self.assertRaises(core_client.ConnectorError):
            self._render(args)
        _, _, out = self._render(dict(ARGS, instance="https://evil.example"))
        self.assertNotIn("evil.example", out["request"]["body"]["content"])

    def test_every_value_is_escaped(self):
        args = dict(ARGS, title="<script>x</script>", notes=['<img src=x onerror="y">'])
        _, _, out = self._render(args)
        html = out["request"]["body"]["content"]
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_execute_logs_the_rendered_email_as_a_communication(self):
        full_args, ctx, out = self._render()
        with connector() as m:
            result = operations.run_operation(KEY, full_args, ctx)
        self.assertEqual(result["_audit_log_status"], "ok")
        to, subject, html, text = m["send_html"].call_args.args
        body = out["request"]["body"]
        self.assertEqual((to, subject, html), (testsupport.REQ, body["subject"], body["content"]))
        self.assertIn("Task complete: Supplier, Item and Purchase Invoice created", text)
        self.assertIn("https://erp.example/app/item/Media%20Marketing%20Services", text)
        sent = m["_do_mutate"].call_args.args
        self.assertEqual(sent[1:3], ("Communication", "create"))
        self.assertEqual(sent[3], dict(body, message_id="<qkeee-1@bot.example>",
                                       sender="agent@bot.example"))
        self.assertTrue(any("Message-ID <qkeee-1@bot.example>" in n for n in result["_notes"]))
        start = m["record_audit_log_start"].call_args.kwargs
        self.assertEqual(start["doctype"], "Purchase Invoice")
        self.assertIn("[approval: token]", start["approval_note"])
        rbac = m["_validate_prod_requester"].call_args.args
        self.assertEqual(rbac[2:4], ("Purchase Invoice", "read"))

    def test_failed_send_logs_nothing(self):
        full_args, ctx, _ = self._render()
        for error in (mailer.EmailNotConfiguredError("no mailbox"), mailer.EmailSendError("smtp")):
            with self.subTest(error=type(error).__name__), connector() as m:
                m["send_html"].side_effect = error
                with self.assertRaises(type(error)):
                    operations.run_operation(KEY, full_args, ctx)
                self.assertFalse(m["_do_mutate"].called)
                m["record_audit_log_start"].assert_not_called()

    def test_no_send_before_the_token_and_rbac_gates(self):
        full_args, ctx, _ = self._render()
        full_args["title"] = "changed"
        with connector() as m:
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_operation(KEY, full_args, ctx)
            m["send_html"].assert_not_called()
        full_args, ctx, _ = self._render()
        with connector() as m:
            m["_validate_prod_requester"].side_effect = core_client.UnvalidatedProdRequesterError("no")
            with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                operations.run_operation(KEY, full_args, ctx)
            m["send_html"].assert_not_called()

    def test_changed_content_after_render_is_refused(self):
        full_args, ctx, _ = self._render()
        full_args["notes"] = ["something else"]
        with connector() as m:
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_operation(KEY, full_args, ctx)
            self.assertFalse(m["_do_mutate"].called)

    def test_execute_without_render_is_refused(self):
        with connector():
            with self.assertRaises(core_client.ConnectorError):
                operations.run_operation(KEY, copy.deepcopy(ARGS), testsupport.ctx())

    def test_deleted_primary_record_is_refused(self):
        args = dict(ARGS, records=[{"doctype": "Item", "name": "X", "action": "delete"}])
        with self.assertRaises(core_client.ConnectorError):
            self._render(args)

    def test_record_and_line_limits(self):
        for bad in ({"records": []}, {"records": [{"doctype": "Item", "name": "X"}]},
                    {"notes": ["x" * 301]}, {"follow_ups": ["x"] * 11}, {"title": "x" * 121}):
            with self.subTest(bad=bad), self.assertRaises(core_client.ConnectorError):
                self._render(dict(ARGS, **bad))

    def test_unscoped_cannot_write_communication(self):
        with connector() as m:
            with self.assertRaises(core_client.DoctypeNotAllowedError):
                operations.prepare_only("unscoped.generic", {
                    "doctype": "Communication", "action": "create", "payload": {"subject": "x"}},
                    testsupport.ctx())
            self.assertFalse(m["_do_mutate"].called)

    def test_render_summary_is_pure_and_escapes(self):
        subject, html = notify.render_summary(
            title="T & Co", records=[{"doctype": "Item", "name": "A/B", "action": "create",
                                      "facts": {"status": "Saved (live)"}}],
            notes=[], follow_ups=[], requester="r@x.com", tag="t", instance="https://e.x",
            completed_on="2026-10-14 06:30 UTC")
        self.assertEqual(subject, "[t] T & Co")
        self.assertIn("T &amp; Co", html)
        self.assertIn("/app/item/A%2FB", html)


if __name__ == "__main__":
    unittest.main()
