"""notify.summary_email (item-run review issue 08): the task summary email
goes to the requester only, its HTML comes from the plugin template with
every value escaped, and execute logs exactly the rendered email."""

import copy
import json
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


class EmailLogSafetyTests(unittest.TestCase):
    """dev-erp 2026-10-15: the email went out, the Communication log then
    failed with 403, and the agent was told 'not sent' — so it offered to
    send again. Now: no send while the bot cannot log; a log failure after
    a send says the email WAS sent."""

    def _rendered(self):
        with connector() as m, patch.object(core_client, "_fetch_merged_meta", side_effect=_meta):
            m["get_resource"].side_effect = _live
            return testsupport.render(KEY, copy.deepcopy(ARGS))

    def test_render_refuses_when_the_bot_cannot_log_the_email(self):
        with connector() as m, patch.object(core_client, "_fetch_merged_meta", side_effect=_meta):
            m["get_resource"].side_effect = _live
            m["bot_role_grants"].return_value = (False, None)
            with self.assertRaises(core_client.GateRefusal) as e:
                testsupport.render(KEY, copy.deepcopy(ARGS))
            m["send_html"].assert_not_called()
        self.assertIn("init-bot --domains notify", str(e.exception))
        self.assertEqual(e.exception.gaps[0]["doctype"], "Communication")

    def test_execute_refuses_before_sending_when_the_bot_cannot_log(self):
        full_args, ctx, _ = self._rendered()
        with connector() as m:
            m["bot_role_grants"].return_value = (False, None)
            with self.assertRaises(core_client.GateRefusal):
                operations.run_operation(KEY, full_args, ctx)
            m["send_html"].assert_not_called()
            self.assertFalse(m["_do_mutate"].called)

    def test_log_failure_after_send_says_the_email_was_sent(self):
        full_args, ctx, _ = self._rendered()
        with connector(send_error=core_client.ConnectorError("ERPNext API error (403)")) as m:
            with self.assertRaises(core_client.PartialOutcomeError) as e:
                operations.run_operation(KEY, full_args, ctx)
            m["send_html"].assert_called_once()
        msg = str(e.exception)
        self.assertIn("WAS SENT", msg)
        self.assertIn("<qkeee-1@bot.example>", msg)
        self.assertIn("Do not send it again", msg)


SHARE = "notify.share_records"
ITEMS = [{"name": "IPD000205", "item_name": "APPLE IPAD <AIR>", "disabled": 0},
         {"name": "Media Marketing Services", "item_name": "Media Marketing Services", "disabled": 0}]


def share_args(**over):
    args = {"title": "Item list", "doctype": "Item", "fields": ["item_name", "disabled"],
            "filters": [], "limit": 50, "recipients": [testsupport.REQ]}
    args.update(over)
    return args


class ShareRecordsTests(unittest.TestCase):
    """notify.share_records (2026-10-15): a live query result emailed to
    recipients in the org domain, one email each."""

    def _render(self, args, domains=None):
        with connector() as m, \
                patch.object(core_client, "query_resource",
                             return_value={"data": copy.deepcopy(ITEMS), "has_more": False}) as q, \
                patch.object(mailer, "_setting",
                             side_effect=lambda n, d="": domains if n == "QKEEE_ERP_EMAIL_DOMAINS"
                             and domains is not None else d):
            out = testsupport.render(SHARE, copy.deepcopy(args))
        self.query = q
        return out

    def test_the_plugin_runs_the_query_as_the_requester(self):
        _, _, out = self._render(share_args())
        self.assertEqual(self.query.call_args.args[1:5], ("Item", [], ["name", "item_name", "disabled"], 50))
        self.assertEqual(self.query.call_args.kwargs["requested_by"], testsupport.REQ)
        self.assertEqual([r["name"] for r in out["args"]["rows"]], ["IPD000205", "Media Marketing Services"])
        html = out["request"]["body"]["content"]
        self.assertIn("APPLE IPAD &lt;AIR&gt;", html)
        self.assertIn("https://erp.example/app/item/IPD000205", html)

    def test_requester_alone_needs_the_token_only(self):
        _, _, out = self._render(share_args())
        self.assertEqual(out["policy"], operations.POLICY_TOKEN)

    def test_other_recipients_need_the_users_code(self):
        _, _, out = self._render(share_args(recipients=[testsupport.REQ, "sanjay@example.com"]))
        self.assertEqual(out["policy"], operations.POLICY_TOKEN_USER_CODE)
        self.assertIn("confirmation_code", out)

    def test_recipient_outside_the_org_domain_is_refused(self):
        for addr in ("x@gmail.com", "x@example.com.evil.io"):
            with self.subTest(addr=addr), self.assertRaises(core_client.DoctypeNotAllowedError):
                self._render(share_args(recipients=[addr]))

    def test_operator_domains_replace_the_requester_domain(self):
        _, _, out = self._render(share_args(recipients=["a@qkeee.in"]), domains="qkeee.in, qkeee.ai")
        self.assertEqual(out["request"]["body"]["recipients"], "a@qkeee.in")
        with self.assertRaises(core_client.DoctypeNotAllowedError):
            self._render(share_args(recipients=[testsupport.REQ]), domains="qkeee.in")

    def test_malformed_recipients_are_refused(self):
        for bad in (["a@example.com, b@example.com"], ["a b@example.com"], ["nobody"], [],
                    [f"u{i}@example.com" for i in range(11)]):
            with self.subTest(bad=bad), self.assertRaises(core_client.ConnectorError):
                self._render(share_args(recipients=bad))

    def test_one_email_per_recipient_then_one_communication(self):
        full_args, ctx, _ = self._render(share_args(recipients=[testsupport.REQ, "sanjay@example.com"]))
        with connector() as m:
            m["send_html"].side_effect = lambda to, *a: {"message_id": f"<{to}>", "from": "bot@x", "to": to}
            result = operations.run_operation(SHARE, full_args, ctx)
        self.assertEqual([c.args[0] for c in m["send_html"].call_args_list],
                         [testsupport.REQ, "sanjay@example.com"])
        body = m["_do_mutate"].call_args.args[3]
        self.assertEqual(body["recipients"], f"{testsupport.REQ}, sanjay@example.com")
        self.assertNotIn("reference_doctype", body)
        self.assertEqual(m["_validate_prod_requester"].call_args.args[2:4], ("Item", "read"))
        self.assertIn("[approval: token+code]", m["record_audit_log_start"].call_args.kwargs["approval_note"])
        self.assertEqual(result["_audit_log_status"], "ok")

    def test_partial_send_logs_the_sent_ones_and_warns(self):
        full_args, ctx, _ = self._render(share_args(recipients=[testsupport.REQ, "sanjay@example.com"]))

        def send(to, *a):
            if to.startswith("sanjay"):
                raise mailer.EmailSendError("550 no such user")
            return {"message_id": "<1>", "from": "bot@x", "to": to}
        with connector() as m:
            m["send_html"].side_effect = send
            result = operations.run_operation(SHARE, full_args, ctx)
        self.assertEqual(m["_do_mutate"].call_args.args[3]["recipients"], testsupport.REQ)
        self.assertTrue(any("not sent to sanjay@example.com" in n for n in result["_notes"]))

    def test_rows_changed_after_render_are_refused(self):
        full_args, ctx, _ = self._render(share_args())
        full_args["rows"].append({"name": "INJECTED"})
        with connector() as m:
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_operation(SHARE, full_args, ctx)
            m["send_html"].assert_not_called()


JOB_KEY = "notify.job_approval_request"
JOB_ID = "JOB-42"
JOB_ROW = {
    "job_id": JOB_ID, "requested_by": testsupport.REQ, "token": "deadbeefcafef00d",
    "issued_at": 1000, "step_count": 1, "status": "Pending",
    "plan": json.dumps([[{"op": "hr_payroll.generic",
                          "args": {"doctype": "Employee", "action": "create",
                                   "payload": {"first_name": "Arjun"}}}]]),
}


def _env_config(admin_email="admin@example.com"):
    return lambda tag, credential="bot": {"base_url": "https://erp.example",
                                          "admin_email": admin_email, "credential": credential}


class JobApprovalRequestTests(unittest.TestCase):
    """notify.job_approval_request (ADR 0008, agents issue 04): the kanban
    job's full plan as one email to the Requester, cc the ERP admin,
    logged against the Job Approval row (ADR 0009's resolved anchor
    question) instead of a business record."""

    def _render(self, job_row=None, admin_email="admin@example.com"):
        row = job_row if job_row is not None else JOB_ROW
        with connector() as m, patch.object(core_client, "get_env_config",
                                            side_effect=_env_config(admin_email)):
            m["get_resource"].side_effect = lambda tag, doctype, name, **kw: {"data": dict(row)}
            return testsupport.render(JOB_KEY, {"job_id": JOB_ID})

    def test_render_rebuilds_the_plan_from_the_stored_row(self):
        full_args, ctx, out = self._render()
        body = out["request"]["body"]
        self.assertEqual(out["policy"], operations.POLICY_TOKEN)
        self.assertEqual(body["recipients"], testsupport.REQ)
        self.assertEqual(body["cc"], "admin@example.com")
        self.assertEqual(body["reference_doctype"], core_client.JOB_APPROVAL_DOCTYPE)
        self.assertEqual(body["reference_name"], JOB_ID)
        self.assertIn(f"job {JOB_ID}", body["subject"])
        html_content = body["content"]
        self.assertIn("DEADBE", html_content)  # code derives from the stored token, not render-time
        self.assertIn("create Employee", html_content)

    def test_extra_or_fabricated_args_are_refused(self):
        """Only job_id is read: an agent cannot pass its own batches/token
        and have the email show something other than the stored plan."""
        with connector() as m, patch.object(core_client, "get_env_config",
                                            side_effect=_env_config()):
            m["get_resource"].side_effect = lambda tag, doctype, name, **kw: {"data": dict(JOB_ROW)}
            with self.assertRaises(core_client.ConnectorError):
                testsupport.render(JOB_KEY, {"job_id": JOB_ID, "batches": [{"fabricated": True}]})

    def test_refuses_when_the_job_is_not_pending(self):
        with self.assertRaises(core_client.PreconditionFailedError):
            self._render(dict(JOB_ROW, status="Approved"))

    def test_refuses_without_an_admin_email_configured(self):
        with self.assertRaises(core_client.ConnectorError):
            self._render(admin_email="")

    def test_execute_sends_with_cc_and_logs_against_the_job_approval_row(self):
        full_args, ctx, out = self._render()
        with connector() as m:
            m["get_resource"].side_effect = lambda tag, doctype, name, **kw: {"data": dict(JOB_ROW)}
            result = operations.run_operation(JOB_KEY, full_args, ctx)
        to, subject, html_content, text = m["send_html"].call_args.args
        body = out["request"]["body"]
        self.assertEqual(m["send_html"].call_args.kwargs.get("cc"), "admin@example.com")
        self.assertEqual((to, subject, html_content), (testsupport.REQ, body["subject"], body["content"]))
        self.assertIn("DEADBE", text)
        sent = m["_do_mutate"].call_args.args
        self.assertEqual(sent[1:3], ("Communication", "create"))
        start = m["record_audit_log_start"].call_args.kwargs
        self.assertEqual(start["doctype"], core_client.JOB_APPROVAL_DOCTYPE)
        self.assertEqual(start["name"], JOB_ID)
        self.assertEqual(result["_audit_log_status"], "ok")

    def test_execute_refuses_a_job_approved_since_render(self):
        """The live precondition (step 7) re-checks the stored row — a job
        approved, or re-rendered under the same job_id, between render and
        execute must not still get an approval email sent for it."""
        full_args, ctx, _ = self._render()
        with connector() as m:
            m["get_resource"].side_effect = lambda tag, doctype, name, **kw: {
                "data": dict(JOB_ROW, status="Approved")}
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.run_operation(JOB_KEY, full_args, ctx)
            m["send_html"].assert_not_called()
            self.assertFalse(m["_do_mutate"].called)

    def test_failed_send_logs_nothing(self):
        full_args, ctx, _ = self._render()
        for error in (mailer.EmailNotConfiguredError("no mailbox"), mailer.EmailSendError("smtp")):
            with self.subTest(error=type(error).__name__), connector() as m:
                m["get_resource"].side_effect = lambda tag, doctype, name, **kw: {"data": dict(JOB_ROW)}
                m["send_html"].side_effect = error
                with self.assertRaises(type(error)):
                    operations.run_operation(JOB_KEY, full_args, ctx)
                self.assertFalse(m["_do_mutate"].called)

    def test_render_job_approval_is_pure_and_escapes(self):
        subject, html_body, text = notify.render_job_approval(
            job_id="J & 1", confirmation_code="ABC123", step_count=1,
            batches=[{"batch": 1, "steps": [{"step": 1, "line": "1. create Item <X>"}]}],
            expires_at="2026-10-24T00:00:00Z", ttl_minutes=1440, requester="r@x.com", tag="t",
            instance="https://e.x")
        self.assertIn("[t] Approval needed", subject)
        self.assertIn("J &amp; 1", html_body)
        self.assertIn("1. create Item &lt;X&gt;", html_body)
        self.assertIn("ABC123", html_body)
        self.assertIn("ABC123", text)


if __name__ == "__main__":
    unittest.main()
