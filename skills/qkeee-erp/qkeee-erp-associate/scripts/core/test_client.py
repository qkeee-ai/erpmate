#!/usr/bin/env python3
"""Regression tests for core.client. Imports the module under both the
`erp_client` and `ec` aliases.

Regression tests for the audit-logging silent-failure bug: session_id
missing must never produce an empty `session` field on Qkeee Bot Audit Log
(that field is mandatory, and _audit_insert() swallows the resulting
MandatoryError silently)."""

import json
import time
import unittest
import unittest.mock
from unittest.mock import patch

import client as erp_client
import client as ec
from confirm_token import advisory_write_token, confirmation_code


class GetEnvConfigNoRequesterDefaultTests(unittest.TestCase):
    """There is no QKEEE_ERP_<TAG>_REQUESTED_BY (removed) — requested_by is
    always caller-supplied, resolved from the live channel identity, never
    a config default. get_env_config() must not expose any such key.

    There is also no QKEEE_ERP_<TAG>_DEBUG / `debug_default`: read audit
    logging is always on, so there is no debug flag to resolve."""

    ENV_BASE = {
        "QKEEE_ERP_DEFAULT_BASE_URL": "https://org.erpnext.com",
        "QKEEE_ERP_DEFAULT_API_KEY": "key",
        "QKEEE_ERP_DEFAULT_API_SECRET": "secret",
    }

    def test_no_requested_by_default_key(self):
        with patch.dict("os.environ", self.ENV_BASE, clear=True):
            cfg = ec.get_env_config("default")
        self.assertNotIn("requested_by_default", cfg)
        self.assertNotIn("debug_default", cfg)

    def test_stray_requested_by_env_var_is_ignored(self):
        env = dict(self.ENV_BASE, QKEEE_ERP_DEFAULT_REQUESTED_BY="priya@org.com")
        with patch.dict("os.environ", env, clear=True):
            cfg = ec.get_env_config("default")
        self.assertNotIn("requested_by_default", cfg)


class SessionFallbackTests(unittest.TestCase):
    def test_session_or_fallback_passthrough(self):
        self.assertEqual(ec._session_or_fallback("sess-123"), "sess-123")

    def test_session_or_fallback_generates_nonempty_id(self):
        for missing in (None, ""):
            with self.subTest(missing=missing):
                result = ec._session_or_fallback(missing)
                self.assertTrue(result)
                self.assertTrue(result.startswith("local-"))

    @patch.object(ec, "_audit_insert")
    def test_log_read_never_sends_empty_session(self, mock_insert):
        ec._log_read(
            {"tag": "default"}, "Sales Order", "SO-0001",
            requested_by="user@example.com", session_id=None, domain_code=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])

    @patch.object(ec, "_audit_insert")
    def test_record_audit_log_start_never_sends_empty_session(self, mock_insert):
        ec.record_audit_log_start(
            {"tag": "default"}, action="Create", doctype="Sales Order", name=None,
            requested_by="user@example.com", session_id=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])


class AuditInsertFailureVisibilityTests(unittest.TestCase):
    @patch.object(ec, "_request", side_effect=RuntimeError("boom"))
    def test_audit_insert_warns_on_stderr_and_returns_none(self, mock_request):
        with patch("sys.stderr") as mock_stderr:
            result = ec._audit_insert({"tag": "default"}, {"session": "s1"})
        self.assertIsNone(result)
        self.assertTrue(mock_stderr.write.called)


class AuditSubmitSkipsWhenInsertFailedTests(unittest.TestCase):
    """_audit_submit(None) — reached whenever the preceding insert already
    failed and warned — must not raise a second, confusing warning
    (urllib.parse.quote(None) -> "quote_from_bytes() expected bytes") that
    would mask the real cause. Guard: log_name falsy short-circuits to
    False before any request."""

    def test_returns_false_without_a_request_when_log_name_is_none(self):
        with patch.object(ec, "_request") as mocked_request:
            result = ec._audit_submit({"tag": "default"}, None)
        self.assertFalse(result)
        mocked_request.assert_not_called()


class PersonaDoctypeRemovedTests(unittest.TestCase):
    """There is no `Qkeee Bot Persona` doctype — `ensure_persona_registered()`,
    `PERSONA_DOCTYPE`, and the `register-persona` CLI subcommand don't
    exist in core/client.py. These are guard tests, not coverage of a
    capability: they fail loudly if a future edit reintroduces
    persona-registration code without a deliberate decision to do so."""

    def test_ensure_persona_registered_does_not_exist(self):
        self.assertFalse(hasattr(ec, "ensure_persona_registered"))

    def test_persona_doctype_constant_does_not_exist(self):
        self.assertFalse(hasattr(ec, "PERSONA_DOCTYPE"))

    def test_register_persona_not_a_cli_subcommand(self):
        # argparse raises SystemExit(2) for an unrecognized subcommand —
        # confirm "register-persona" is one, not just that it errors for
        # some other reason (e.g. a missing required flag).
        with patch("sys.argv", ["client.py", "--tag", "default", "register-persona",
                                 "--domain-code", "x"]), \
                patch("sys.stderr", new_callable=__import__("io").StringIO) as mock_stderr:
            with self.assertRaises(SystemExit):
                ec._cli()
        self.assertIn("invalid choice", mock_stderr.getvalue())
        self.assertIn("register-persona", mock_stderr.getvalue())


class AuditLogDomainCodeTests(unittest.TestCase):
    """Qkeee Bot Audit Log's `domain_code` field — a denormalized string
    naming the active qkeee-erp-associate domain reference (e.g.
    'qkeee-erp-associate/hr-payroll'). Confirms the write payload carries
    `domain_code`, not `persona_code`."""

    @patch.object(ec, "_audit_insert")
    def test_log_read_writes_domain_code_field(self, mock_insert):
        ec._log_read(
            {"tag": "default"}, "Sales Order", "SO-0001",
            requested_by="user@example.com", session_id="s1",
            domain_code="qkeee-erp-associate/sales",
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertEqual(sent_fields["domain_code"], "qkeee-erp-associate/sales")
        self.assertNotIn("persona_code", sent_fields)

    @patch.object(ec, "_audit_insert")
    def test_record_audit_log_start_writes_domain_code_field(self, mock_insert):
        ec.record_audit_log_start(
            {"tag": "default"}, action="Create", doctype="Sales Order", name=None,
            requested_by="user@example.com", session_id="s1",
            domain_code="qkeee-erp-associate/sales",
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertEqual(sent_fields["domain_code"], "qkeee-erp-associate/sales")
        self.assertNotIn("persona_code", sent_fields)


class RunQueryReportTests(unittest.TestCase):
    """Fully-qualified `import erp_client` + `unittest.mock.patch.object`
    throughout, deliberately not relying on the `ec`/`patch` aliases used
    elsewhere in this file."""

    @unittest.mock.patch.object(erp_client, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @unittest.mock.patch.object(erp_client, "check_user_permission", return_value=True)
    @unittest.mock.patch.object(erp_client, "resource_exists", return_value=True)
    @unittest.mock.patch.object(erp_client, "_log_read")
    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_uses_get_and_wraps_message(self, mock_get_env_config, mock_request, mock_log_read,
                                         mock_resource_exists, mock_check_perm, mock_trust):
        mock_request.return_value = {
            "message": {"columns": [{"fieldname": "customer"}], "result": [{"customer": "Acme"}]}
        }
        result = erp_client.run_query_report("default", "Sales Order Analysis", {"company": "Acme"},
                                              requested_by="user@example.com")
        method, path = mock_request.call_args[0][1:3]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/api/method/frappe.desk.query_report.run")
        self.assertEqual(mock_request.call_args[1]["params"]["report_name"], "Sales Order Analysis")
        self.assertEqual(result["report_name"], "Sales Order Analysis")
        self.assertEqual(result["result"], [{"customer": "Acme"}])
        # Read logging is unconditional, not debug-gated — every read logs.
        mock_log_read.assert_called_once()

    @unittest.mock.patch.object(erp_client, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @unittest.mock.patch.object(erp_client, "check_user_permission", return_value=True)
    @unittest.mock.patch.object(erp_client, "resource_exists", return_value=True)
    @unittest.mock.patch.object(erp_client, "_log_read")
    @unittest.mock.patch.object(erp_client, "_request", return_value={"message": {}})
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_read_logs_against_report_doctype(self, mock_get_env_config, mock_request, mock_log_read,
                                               mock_resource_exists, mock_check_perm, mock_trust):
        erp_client.run_query_report("default", "Trial Balance", requested_by="user@example.com")
        mock_log_read.assert_called_once()
        self.assertEqual(mock_log_read.call_args[0][1], "Report")
        self.assertEqual(mock_log_read.call_args[0][2], "Trial Balance")


class GetUserRolesTests(unittest.TestCase):
    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_resolves_given_user_roles(self, mock_get_env_config, mock_request):
        mock_request.return_value = {"data": {"roles": [{"role": "Purchase Manager"}, {"role": "Employee"}]}}
        result = erp_client.get_user_roles("default", "priya@org.com")
        self.assertEqual(result["user"], "priya@org.com")
        self.assertEqual(result["roles"], ["Purchase Manager", "Employee"])
        self.assertEqual(result["warning"], "")

    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_self_resolves_when_no_user_given(self, mock_get_env_config, mock_request):
        mock_request.side_effect = [
            {"message": "bot@org.com"},
            {"data": {"roles": [{"role": "System Manager"}]}},
        ]
        result = erp_client.get_user_roles("default")
        self.assertEqual(result["user"], "bot@org.com")
        self.assertEqual(mock_request.call_args_list[0][0][2], "/api/method/frappe.auth.get_logged_user")

    @unittest.mock.patch.object(erp_client, "_request", return_value={"data": {"roles": []}})
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_empty_roles_surfaces_ambiguity_warning(self, mock_get_env_config, mock_request):
        result = erp_client.get_user_roles("default", "priya@org.com")
        self.assertEqual(result["roles"], [])
        self.assertTrue(result["warning"])
        self.assertIn("not confirmed", result["warning"])


class UpdatePreImageAttributionTests(unittest.TestCase):
    """F13, .scratch/hermes-erp-bot-reliability/spec.md: mutate_resource()'s
    Update pre-image fetch (for field_diff) used to omit `requested_by`
    entirely, so get_resource()'s own gate refused it every single time —
    caught and discarded by the surrounding `except ConnectorError`, so
    `payload_before`/`field_diff` had been silently null on every past
    Update against a non-exempt doctype. Live-caught against
    demo.qkeee.in 2026-09-13. This locks in the fix: the pre-image call
    must carry the write's own requested_by and context."""

    @patch.object(ec, "record_comment")
    @patch.object(ec, "record_audit_log_finish")
    @patch.object(ec, "record_audit_log_start", return_value="AUDITLOG-PREIMG")
    @patch.object(ec, "_do_mutate", return_value={"data": {"name": "SO-0001", "status": "Closed"}})
    @patch.object(ec, "get_resource")
    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_preimage_fetch_carries_requested_by_and_context(
            self, mocked_cfg, mocked_gate, mocked_get_resource, mocked_do_mutate,
            mocked_start, mocked_finish, mocked_comment):
        mocked_get_resource.return_value = {"data": {"name": "SO-0001", "status": "Draft"}}
        ec.mutate_resource("prod", "Sales Order", "update", payload={"status": "Closed"},
                            name="SO-0001", mode="read-write", requested_by="priya@org.com",
                            session_id="sess-1", domain_code="sales", channel="Slack",
                            channel_metadata={"x": 1}, prompt_summary="close it",
                            latest_prompt="please close SO-0001")
        mocked_get_resource.assert_called_once_with(
            "prod", "Sales Order", "SO-0001", strip_noise=False,
            requested_by="priya@org.com", session_id="sess-1", domain_code="sales",
            channel="Slack", channel_metadata={"x": 1},
            prompt_summary="close it", latest_prompt="please close SO-0001",
        )
        # And NOT internal=True — this is a real access made on the
        # requester's behalf, not connector plumbing, so it should be
        # gated and logged like any other read.
        self.assertNotIn("internal", mocked_get_resource.call_args.kwargs)


class UpdateAuditRowVolumeTests(unittest.TestCase):
    """Issue 03, .scratch/hermes-erp-bot-reliability/issues/
    03-update-audit-row-volume.md: F13's fix (mutate_resource()'s Update
    pre-image fetch now carries requested_by, so it's a real, gated,
    non-internal read, not connector plumbing) means one allowed Update
    now writes FOUR Qkeee Bot Audit Log documents, not the two spec.md's
    own "Comments (F13)" section claims (that claim holds for a plain
    read/create, not Update): a gate-decision row for the write itself, a
    gate-decision row for the pre-image read, the pre-image Read row
    itself, and the write's own Attempted->Success row. This pins that
    count with a real (mocked-HTTP-only) run through mutate_resource() —
    not a mocked _validate_prod_requester() — so a future change to this
    path can't silently change the count again without a test failing.
    Intentionally takes no side on issue 03's own open question (accept
    the volume vs. fold the two gate-decision rows into one); it only
    locks in the currently-accepted behavior."""

    ENV = {
        "QKEEE_ERP_ROWVOL_BASE_URL": "https://example.com",
        "QKEEE_ERP_ROWVOL_API_KEY": "key",
        "QKEEE_ERP_ROWVOL_API_SECRET": "secret",
    }

    @patch.object(ec, "_audit_submit", return_value=True)
    @patch.object(ec, "_audit_update", return_value=True)
    @patch.object(ec, "_audit_insert", return_value="AUDITLOG-ROWVOL")
    @patch.object(ec, "_do_mutate", return_value={"data": {"name": "SO-0001", "status": "Closed"}})
    @patch.object(ec, "_request", return_value={"data": {"name": "SO-0001", "status": "Draft"}})
    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": True, "bot_user": "qkeee-erp-bot@org.com",
                                  "bot_roles": ["Accounts User"], "privileged_identity": False,
                                  "precheck_discriminates": True})
    @patch.object(ec, "resource_exists", return_value=True)
    def test_allowed_update_writes_four_audit_rows(
            self, mocked_exists, mocked_trust, mocked_perm, mocked_request,
            mocked_do_mutate, mocked_insert, mocked_update, mocked_submit):
        with patch.dict("os.environ", self.ENV, clear=True):
            ec.mutate_resource(
                "rowvol", "Sales Order", "update", payload={"status": "Closed"},
                name="SO-0001", mode="read-write", requested_by="priya@org.com",
                session_id="sess-1", domain_code="sales", channel="Slack",
                channel_metadata={"x": 1}, prompt_summary="close it",
                latest_prompt="please close SO-0001",
            )

        # 4 distinct documents, in the order mutate_resource() actually
        # produces them: the write's own gate-decision, then the
        # pre-image read's gate-decision + its real Read row, then the
        # write's Attempted row (flipped to Success via _audit_update,
        # not a 5th _audit_insert document).
        self.assertEqual(mocked_insert.call_count, 4)
        fields = [call.args[1] for call in mocked_insert.call_args_list]
        self.assertEqual([f["action"] for f in fields], ["Update", "Read", "Read", "Update"])
        self.assertEqual(
            ["gate_check" in (f.get("response_payload") or "") for f in fields],
            [True, True, False, False],
        )
        self.assertEqual(fields[3]["status"], "Attempted")
        # Exactly one row (the write's own) ever makes the Attempted ->
        # Success transition — the gate-decision/read rows are each
        # written once, final, never revisited.
        mocked_update.assert_called_once()
        self.assertEqual(mocked_update.call_args.args[2]["status"], "Success")


class TestGatedMutateResource(unittest.TestCase):
    """gated_mutate_resource() is this skill's own write entry point,
    merged in from the former qkeee-erp-catch-all skill (2026-08-18) —
    this skill's own extra layer on top of mutate_resource()'s
    mode/requested_by gate, enforced in code, not just prompt."""

    QA_ENV = {
        "QKEEE_ERP_QA_BASE_URL": "https://example.com",
        "QKEEE_ERP_QA_API_KEY": "key",
        "QKEEE_ERP_QA_API_SECRET": "secret",
    }

    def test_refuses_without_token(self):
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ConnectorError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com")
            mocked_request.assert_not_called()

    def test_refuses_with_stale_token(self):
        old_issued_at = int(time.time()) - 10_000  # well past DEFAULT_TOKEN_TTL_SECONDS
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", old_issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.StaleConfirmationError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=old_issued_at)
            mocked_request.assert_not_called()

    def test_refuses_with_mismatched_payload(self):
        """The rendered token is bound to the exact payload — a caller
        can't render one draft and execute a different one under the
        same token."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ConnectorError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 2}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()

    def test_refuses_without_user_confirmation_text(self):
        """F5, .scratch/hermes-erp-bot-reliability/spec.md: a matching
        token alone is no longer enough — a self-computed-and-self-
        verified token proves payload integrity, never that a human saw
        the render."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.UnconfirmedByUserError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()

    def test_refuses_when_confirmation_text_lacks_the_code(self):
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.UnconfirmedByUserError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at,
                                          user_confirmation_text="yes, go ahead")
            mocked_request.assert_not_called()

    def test_confirmation_text_check_is_case_insensitive(self):
        issued_at = int(time.time())
        payload = {"lead_name": "Acme"}
        token = advisory_write_token("create", "CRM Lead", None, payload, "priya@org.com", issued_at)
        code = confirmation_code(token)  # already uppercase
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission", return_value=True), \
                patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True}), \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "CRM-LEAD-0001"}}):
            result = ec.gated_mutate_resource("qa", "CRM Lead", "create", payload, mode="read-write",
                                               requested_by="priya@org.com",
                                               confirmation_token=token, issued_at=issued_at,
                                               user_confirmation_text=f"yes {code.lower()} confirmed")
        self.assertEqual(result["data"]["name"], "CRM-LEAD-0001")

    def test_confirmation_text_for_a_different_tokens_code_is_refused(self):
        """A reply confirming a DIFFERENT rendered draft's code must not
        satisfy this gate — the code has to match THIS write's token."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        other_token = advisory_write_token("create", "CRM Lead", None, {"x": 999}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.UnconfirmedByUserError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at,
                                          user_confirmation_text=f"yes {confirmation_code(other_token)}")
            mocked_request.assert_not_called()

    def test_succeeds_with_matching_fresh_token(self):
        # The requester-permission check runs on every tag, not PROD only
        # — a write on non-PROD 'qa' with a requested_by present still gets
        # validated as a real User with has_permission, so those two
        # need mocking here even though 'qa' isn't PROD.
        issued_at = int(time.time())
        payload = {"lead_name": "Acme"}
        token = advisory_write_token("create", "CRM Lead", None, payload, "priya@org.com", issued_at)
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission", return_value=True), \
                patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True}), \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "CRM-LEAD-0001"}}) as mocked:
            result = ec.gated_mutate_resource("qa", "CRM Lead", "create", payload, mode="read-write",
                                               requested_by="priya@org.com",
                                               confirmation_token=token, issued_at=issued_at,
                                               user_confirmation_text=f"yes {confirmation_code(token)}")
        self.assertEqual(result["data"]["name"], "CRM-LEAD-0001")
        mocked.assert_called_once()

    def test_verified_token_alone_no_longer_rescues_an_unreliable_precheck(self):
        # 2026-09-11 decision (F7 reinforcement): a verified advisory
        # token attests the write's SHAPE was reviewed ahead of time, but
        # no longer counts as proof requested_by specifically can do it
        # once has_permission itself is known unreliable — a role_verdict
        # of None (couldn't be locally confirmed either) now refuses
        # outright, even for a domain-less write with a genuinely fresh,
        # freshly-verified token. Supersedes this test's old name/premise
        # ("succeeds despite unreliable precheck via verified token").
        issued_at = int(time.time())
        payload = {"company_name": "DVSISTEMS"}
        token = advisory_write_token("create", "Company", None, payload, "priya@org.com", issued_at)
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission") as mocked_perm, \
                patch.object(ec, "_requester_has_role_permission", return_value=None), \
                patch.object(ec, "verify_rbac_precheck_reliable",
                              return_value={"reliable": False, "bot_user": "Administrator",
                                            "bot_roles": [], "privileged_identity": True,
                                            "precheck_discriminates": True}), \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "DVSISTEMS"}}) as mocked:
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec.gated_mutate_resource("qa", "Company", "create", payload, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at,
                                          user_confirmation_text=f"confirmed, code {confirmation_code(token)}")
        mocked.assert_not_called()  # refused before the actual write ever fired
        mocked_perm.assert_not_called()  # never trust a permission answer we know is meaningless

    def test_succeeds_when_role_verdict_confirms_it_alongside_a_verified_token(self):
        # The companion case: a verified token PLUS a locally-confirmed
        # role grant (True) does still succeed — the token proves the
        # payload wasn't tampered with, the role/DocPerm check proves
        # requested_by can actually do it; together that's real evidence,
        # unlike the token alone above.
        issued_at = int(time.time())
        payload = {"company_name": "DVSISTEMS"}
        token = advisory_write_token("create", "Company", None, payload, "priya@org.com", issued_at)
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission") as mocked_perm, \
                patch.object(ec, "_requester_has_role_permission", return_value=True), \
                patch.object(ec, "verify_rbac_precheck_reliable",
                              return_value={"reliable": False, "bot_user": "Administrator",
                                            "bot_roles": [], "privileged_identity": True,
                                            "precheck_discriminates": True}), \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "DVSISTEMS"}}) as mocked:
            result = ec.gated_mutate_resource("qa", "Company", "create", payload, mode="read-write",
                                               requested_by="priya@org.com",
                                               confirmation_token=token, issued_at=issued_at,
                                               user_confirmation_text=f"confirmed, code {confirmation_code(token)}")
        self.assertEqual(result["data"]["name"], "DVSISTEMS")
        mocked.assert_called_once()
        mocked_perm.assert_not_called()  # never trust a permission answer we know is meaningless

    def test_still_refuses_read_only_even_with_valid_token(self):
        """The token gate (and the user_confirmation_text gate alongside
        it) is additive, not a replacement for the mode/requested_by gate
        mutate_resource() already enforces."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ReadOnlyModeError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-only",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at,
                                          user_confirmation_text=f"yes {confirmation_code(token)}")
            mocked_request.assert_not_called()


class QkeeeEnvFileTests(unittest.TestCase):
    """qkeee-erp.env is the isolated, execute_code-sandbox-safe credential
    source (see get_env_config()'s _qkeee_env() call) — these exercise the
    file parser and its precedence over os.environ directly, without
    touching the real filesystem location HERMES_HOME would resolve to.
    Fully-qualified `erp_client.*`/`unittest.mock.*` and a locally-imported
    `os` throughout, deliberately not relying on the aliases used
    elsewhere in this file."""

    def setUp(self):
        import os
        erp_client._QKEEE_ENV_FILE_CACHE = None
        self.addCleanup(setattr, erp_client, "_QKEEE_ENV_FILE_CACHE", None)

    def _with_file(self, contents):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contents)
        self.addCleanup(os.remove, path)
        return path

    def test_file_values_take_precedence_over_os_environ(self):
        import os
        path = self._with_file(
            "QKEEE_ERP_QA_BASE_URL=https://from-file.example.com\n"
        )
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://from-environ.example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value=path), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://from-file.example.com")

    def test_missing_file_falls_back_to_os_environ(self):
        import os
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value="/nonexistent/qkeee-erp.env"), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://example.com")

    def test_comments_blank_lines_and_quoted_values(self):
        import os
        path = self._with_file(
            "# a comment\n"
            "\n"
            "QKEEE_ERP_QA_BASE_URL=\"https://quoted.example.com\"\n"
            "QKEEE_ERP_QA_API_KEY=key\n"
            "QKEEE_ERP_QA_API_SECRET='secret'\n"
        )
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value=path), \
                unittest.mock.patch.dict(os.environ, {}, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://quoted.example.com")
        self.assertEqual(cfg["api_key"], "key")
        self.assertEqual(cfg["api_secret"], "secret")


class ValidateProdRequesterTests(unittest.TestCase):
    """_validate_prod_requester(): presence of requested_by is mandatory
    on EVERY tag now — no PROD/non-PROD distinction, no tag default to
    fall back to. Whenever a requested_by IS present it is validated — as
    a real ERPNext User, with actual has_permission on the doctype/action
    — on every tag. Never proceeds unverified. See
    UniversalRequesterValidationTests below for the non-PROD-tag cases."""

    def test_noop_on_exempt_doctype_even_without_requester(self):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("prod", None, "User", "read")
        mocked_exists.assert_not_called()

    def test_refuses_missing_requester(self):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", None, "Sales Order", "read")
        self.assertIn("no requester was given", str(ctx.exception))

    def test_refuses_missing_requester_even_if_empty_string(self):
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("prod", "", "Sales Order", "read")

    @patch.object(ec, "resource_exists", return_value=False)
    def test_refuses_unknown_user(self, mocked_exists):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", "nobody@org.com", "Sales Order", "read")
        mocked_exists.assert_called_once_with("prod", "User", "nobody@org.com")
        self.assertIn("not a known ERPNext User", str(ctx.exception))

    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_refuses_when_permission_check_returns_false(self, mocked_exists, mocked_perm, mocked_trust):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", "priya@org.com", "Sales Order", "write", docname="SO-0001")
        mocked_perm.assert_called_once_with("prod", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertIn("does not have 'write' permission", str(ctx.exception))

    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_proceeds_when_validated_and_permitted(self, mocked_exists, mocked_perm, mocked_trust):
        ec._validate_prod_requester("prod", "priya@org.com", "Sales Order", "read")  # no raise

    def test_missing_requester_refused_regardless_of_tag_name(self):
        # No more PROD/non-PROD carve-out — a tag name like "client-a-prod"
        # (or any other) gates identically now.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("client-a-prod", None, "Sales Order", "read")


class UniversalRequesterValidationTests(unittest.TestCase):
    """RBAC pre-check, every environment: a requested_by on ANY tag gets
    the same real-User + has_permission validation, and its absence is
    refused the same way, so a bogus or missing requester is never
    silently accepted on any tag."""

    def test_missing_requester_refused_on_non_prod_tag_too(self):
        # Presence is mandatory everywhere now — no tag gets a pass.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("qa", None, "Sales Order", "read")

    @patch.object(ec, "resource_exists", return_value=False)
    def test_non_prod_refuses_unknown_user(self, mocked_exists):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("qa", "nobody@org.com", "Sales Order", "read")
        mocked_exists.assert_called_once_with("qa", "User", "nobody@org.com")
        self.assertIn("not a known ERPNext User", str(ctx.exception))

    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_non_prod_refuses_when_permission_check_returns_false(self, mocked_exists, mocked_perm, mocked_trust):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("qa", "priya@org.com", "Sales Order", "write", docname="SO-0001")
        mocked_perm.assert_called_once_with("qa", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertIn("does not have 'write' permission", str(ctx.exception))

    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_non_prod_proceeds_when_validated_and_permitted(self, mocked_exists, mocked_perm, mocked_trust):
        ec._validate_prod_requester("qa", "priya@org.com", "Sales Order", "read")  # no raise

    def test_noop_on_exempt_doctype_on_non_prod_too(self):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("qa", "priya@org.com", "User", "read")
        mocked_exists.assert_not_called()


class ResolveRequestedByTests(unittest.TestCase):
    """Thin pass-through now — no tag default exists anywhere to fall
    back to. See GetEnvConfigNoRequesterDefaultTests for the config side
    of this removal."""

    def test_cli_value_passes_through(self):
        self.assertEqual(ec.resolve_requested_by("priya@org.com"), "priya@org.com")

    def test_absent_value_resolves_to_empty_string(self):
        self.assertEqual(ec.resolve_requested_by(None), "")
        self.assertEqual(ec.resolve_requested_by(""), "")


class RedactPiiTests(unittest.TestCase):
    def test_redacts_ssn(self):
        self.assertEqual(ec.redact_pii("my SSN is 123-45-6789 ok"), "my SSN is [REDACTED-SSN] ok")

    def test_redacts_luhn_valid_card(self):
        # 4111 1111 1111 1111 is a well-known Luhn-valid test card number.
        self.assertEqual(
            ec.redact_pii("card 4111 1111 1111 1111 please"),
            "card [REDACTED-CARD] please",
        )
        self.assertEqual(ec.redact_pii("card 4111-1111-1111-1111"), "card [REDACTED-CARD]")

    def test_does_not_redact_non_luhn_digit_runs(self):
        # 15 consecutive digits that don't pass Luhn should be left alone
        # (e.g. a PO/invoice number) — narrow by design, not general DLP.
        text = "PO number 123456789012345 attached"
        self.assertEqual(ec.redact_pii(text), text)

    def test_passthrough_on_empty_or_none(self):
        self.assertEqual(ec.redact_pii(""), "")
        self.assertIsNone(ec.redact_pii(None))

    def test_redact_pii_deep_handles_nested_structures(self):
        result = ec._redact_pii_deep({
            "a": "123-45-6789",
            "b": ["4111 1111 1111 1111", "clean text"],
            "c": {"d": "123-45-6789"},
            "e": 42,
        })
        self.assertEqual(result["a"], "[REDACTED-SSN]")
        self.assertEqual(result["b"], ["[REDACTED-CARD]", "clean text"])
        self.assertEqual(result["c"]["d"], "[REDACTED-SSN]")
        self.assertEqual(result["e"], 42)


class RecordCommentRedactsPiiTests(unittest.TestCase):
    @patch.object(ec, "_request", return_value={})
    def test_comment_content_is_redacted_before_posting(self, mocked_request):
        ec.record_comment({"tag": "qa"}, "Employee", "HR-0001",
                           "please update, my SSN is 123-45-6789")
        payload = mocked_request.call_args[1]["payload"]
        self.assertEqual(payload["content"], "please update, my SSN is [REDACTED-SSN]")


class RecordCommentEmailAndByFieldsTests(unittest.TestCase):
    """Live-confirmed against a real Frappe 16 instance (2026-09-11):
    frappe.desk.form.utils.add_comment now requires comment_email/
    comment_by as positional args with no default (worked without them
    on the Frappe 15 instance this connector was originally built
    against) — omitting them 500s with a TypeError, and record_comment()
    swallows that as a silent False (best-effort), so this bug shipped
    invisibly: every write's attribution Comment silently failed to
    post."""

    def setUp(self):
        ec._BOT_IDENTITY_CACHE.clear()
        self.addCleanup(ec._BOT_IDENTITY_CACHE.clear)

    @patch.object(ec, "_bot_identity", return_value={"user": "dev-erp-hermes@qkeee.in", "roles": []})
    @patch.object(ec, "_request", return_value={})
    def test_comment_email_and_by_sent_as_bot_identity(self, mocked_request, mocked_identity):
        ec.record_comment({"tag": "qa"}, "Item", "ITEM-0001", "created via bot")
        payload = mocked_request.call_args[1]["payload"]
        self.assertEqual(payload["comment_email"], "dev-erp-hermes@qkeee.in")
        self.assertEqual(payload["comment_by"], "dev-erp-hermes@qkeee.in")
        mocked_identity.assert_called_once_with("qa")

    @patch.object(ec, "_bot_identity", return_value={"user": "", "roles": []})
    @patch.object(ec, "_request", return_value={})
    def test_falls_back_to_skill_label_when_bot_identity_unresolved(self, mocked_request, mocked_identity):
        # An empty string here would just move the TypeError somewhere
        # else (or a different Frappe-side rejection) -- never send it.
        ec.record_comment({"tag": "qa"}, "Item", "ITEM-0001", "created via bot")
        payload = mocked_request.call_args[1]["payload"]
        self.assertEqual(payload["comment_email"], ec.SKILL_LABEL)
        self.assertEqual(payload["comment_by"], ec.SKILL_LABEL)


class CheckUserPermissionTests(unittest.TestCase):
    @patch.object(ec, "_request", return_value={"message": True})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_true_response(self, mocked_cfg, mocked_request):
        result = ec.check_user_permission("prod", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertTrue(result)
        params = mocked_request.call_args[1]["params"]
        self.assertEqual(params["doctype"], "Sales Order")
        self.assertEqual(params["perm_type"], "write")
        self.assertEqual(params["user"], "priya@org.com")
        self.assertEqual(params["docname"], "SO-0001")

    @patch.object(ec, "_request", return_value={"message": False})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_false_response(self, mocked_cfg, mocked_request):
        self.assertFalse(ec.check_user_permission("prod", "Sales Order", "read", "priya@org.com"))

    @patch.object(ec, "_request", return_value={"message": True})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_docname_sent_as_empty_string_when_not_given(self, mocked_cfg, mocked_request):
        # Live-confirmed against a real ERPNext instance: some Frappe builds'
        # frappe.client.has_permission have no default for docname — omitting
        # the param entirely 500s. docname="" is the live-confirmed working
        # doctype-level-only shape; see check_user_permission_raw()'s docstring.
        ec.check_user_permission("prod", "Sales Order", "read", "priya@org.com")
        params = mocked_request.call_args[1]["params"]
        self.assertEqual(params["docname"], "")


class RbacPrecheckReliabilityTests(unittest.TestCase):
    """verify_rbac_precheck_reliable() / _validate_prod_requester()'s
    PrivilegedBotAccountError guard — see client.py's module docstring and
    00-conventions.md's bot-account bullet. Live-confirmed root cause:
    under a privileged (Administrator/System-Manager) bot identity, some
    Frappe builds' frappe.client.has_permission returns true for ANY
    user= value, including a nonexistent one — the pre-check silently
    rubber-stamps every requested_by rather than checking it."""

    def setUp(self):
        # Per-tag caches are module-global by design (see client.py) — clear
        # them before/after every test so one test's tag can't leak a
        # cached result into another.
        ec._BOT_IDENTITY_CACHE.clear()
        ec._RBAC_PRECHECK_TRUST_CACHE.clear()
        ec._PRECHECK_WARNED_TAGS.clear()
        self.addCleanup(ec._BOT_IDENTITY_CACHE.clear)
        self.addCleanup(ec._RBAC_PRECHECK_TRUST_CACHE.clear)
        self.addCleanup(ec._PRECHECK_WARNED_TAGS.clear)

    @patch.object(ec, "check_user_permission", return_value=True)
    def test_probe_flags_broken_precheck_when_bogus_user_is_allowed(self, mocked_perm):
        # Root-cause reproduction: has_permission says "true" for a
        # deliberately nonexistent user — the exact live-observed failure.
        self.assertFalse(ec._probe_rbac_precheck_discriminates("tag-a"))
        mocked_perm.assert_called_once_with("tag-a", "Role", "write", ec._RBAC_PROBE_BOGUS_USER)

    @patch.object(ec, "check_user_permission", return_value=False)
    def test_probe_confirms_working_precheck_when_bogus_user_is_denied(self, mocked_perm):
        self.assertTrue(ec._probe_rbac_precheck_discriminates("tag-b"))

    @patch.object(ec, "check_user_permission", side_effect=ec.ConnectorError("unreachable"))
    def test_probe_fails_closed_when_unreachable(self, mocked_perm):
        self.assertFalse(ec._probe_rbac_precheck_discriminates("tag-c"))

    def test_probe_result_is_cached_per_tag(self):
        with patch.object(ec, "check_user_permission", return_value=False) as mocked_perm:
            ec._probe_rbac_precheck_discriminates("tag-d")
            ec._probe_rbac_precheck_discriminates("tag-d")
        mocked_perm.assert_called_once()

    @patch.object(ec, "get_user_roles", return_value={"user": "Administrator", "roles": []})
    def test_identity_administrator_is_privileged(self, mocked_roles):
        with patch.object(ec, "check_user_permission", return_value=False):
            trust = ec.verify_rbac_precheck_reliable("tag-e")
        self.assertTrue(trust["privileged_identity"])
        self.assertFalse(trust["reliable"])

    @patch.object(ec, "get_user_roles",
                   return_value={"user": "qkeee-erp-bot@org.com", "roles": ["Accounts User", "System Manager"]})
    def test_identity_holding_system_manager_is_privileged(self, mocked_roles):
        with patch.object(ec, "check_user_permission", return_value=False):
            trust = ec.verify_rbac_precheck_reliable("tag-f")
        self.assertTrue(trust["privileged_identity"])
        self.assertFalse(trust["reliable"])

    @patch.object(ec, "get_user_roles",
                   return_value={"user": "qkeee-erp-bot@org.com", "roles": ["Accounts User"]})
    def test_narrow_role_identity_plus_discriminating_probe_is_reliable(self, mocked_roles):
        with patch.object(ec, "check_user_permission", return_value=False):
            trust = ec.verify_rbac_precheck_reliable("tag-g")
        self.assertFalse(trust["privileged_identity"])
        self.assertTrue(trust["precheck_discriminates"])
        self.assertTrue(trust["reliable"])

    @patch.object(ec, "get_user_roles",
                   return_value={"user": "qkeee-erp-bot@org.com", "roles": ["Accounts User"]})
    def test_narrow_role_identity_but_broken_probe_is_unreliable(self, mocked_roles):
        # A non-privileged identity doesn't save you if the instance's own
        # has_permission still doesn't discriminate.
        with patch.object(ec, "check_user_permission", return_value=True):
            trust = ec.verify_rbac_precheck_reliable("tag-h")
        self.assertFalse(trust["privileged_identity"])
        self.assertFalse(trust["precheck_discriminates"])
        self.assertFalse(trust["reliable"])

    # The four tests below all share one scenario: RBAC precheck is
    # unreliable AND the local role/DocPerm check can't reach a verdict
    # either (none of tag-i/i2/i3/i4/j have env configured, so
    # _requester_has_role_permission()'s own get_user_roles() call fails
    # fast with a ConnectorError and returns None — see that function's
    # own tests for the True/False cases). Per the 2026-09-11 decision,
    # an inconclusive local verdict refuses OUTRIGHT now, uniformly,
    # regardless of `domain`, `advisory_token_verified`, or read-vs-write
    # — none of those rescue it anymore. These four used to demonstrate
    # the opposite (domain-scoped/token-verified/read all proceeding on a
    # warning) before that decision; kept as four separate cases
    # specifically to confirm the override truly applies uniformly across
    # what used to be four differently-treated paths, not just the one
    # that was already a hard block.

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_write_refused_when_precheck_unreliable(self, mocked_exists, mocked_perm, mocked_trust):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("tag-i", "priya@org.com", "Sales Order", "write", docname="SO-0001")
        self.assertIn("Administrator", str(ctx.exception))
        mocked_perm.assert_not_called()  # never trust a permission answer we know is meaningless

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_domain_scoped_write_also_refused_when_role_verdict_inconclusive(
            self, mocked_exists, mocked_perm, mocked_trust):
        # A `domain=`-scoped write has cleared mutate_resource()'s
        # ALLOWED_WRITE_DOCTYPES gate before this function ever runs —
        # that used to be enough to proceed on a warning; per the
        # 2026-09-11 decision it no longer is, once the local role check
        # itself can't reach a verdict either.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-i2", "priya@org.com", "Sales Order", "write",
                                         docname="SO-0001", domain="sales")
        mocked_perm.assert_not_called()  # still never trust a meaningless permission answer

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_advisory_token_verified_write_also_refused_when_role_verdict_inconclusive(
            self, mocked_exists, mocked_perm, mocked_trust):
        # advisory_token_verified=True (only ever set by
        # gated_mutate_resource() after its own confirmation_token check
        # already passed) used to be its own sufficient fallback here too
        # — same 2026-09-11 change applies: it isn't anymore.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-i3", "priya@org.com", "Company", "write",
                                         advisory_token_verified=True)
        mocked_perm.assert_not_called()

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_neither_domain_nor_advisory_token_still_refused(self, mocked_exists, mocked_perm, mocked_trust):
        # Belt-and-suspenders: explicit domain=None, advisory_token_verified=False
        # (the true "nothing reviewed this" case) is still a hard block —
        # same outcome as the two tests above, different (already-hard-
        # blocked) starting point.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-i4", "priya@org.com", "Company", "write",
                                         domain=None, advisory_token_verified=False)
        mocked_perm.assert_not_called()

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_read_also_refused_when_role_verdict_inconclusive(self, mocked_exists, mocked_perm, mocked_trust):
        # Reads get the exact same treatment as writes now — a read used
        # to warn-and-proceed unconditionally once precheck was unreliable;
        # per the 2026-09-11 decision it refuses too when the local check
        # can't reach a verdict, uniformly with every write case above.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-j", "priya@org.com", "Sales Order", "read")
        mocked_perm.assert_not_called()

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "_requester_has_role_permission", return_value=None)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    @patch.object(ec, "_audit_insert", return_value=None)
    def test_mutate_resource_refuses_write_when_precheck_unreliable(
            self, mocked_audit_insert, mocked_exists, mocked_perm, mocked_role_verdict, mocked_trust):
        # _audit_insert mocked (F11's gate-decision row now fires on this
        # denial too) so this stays a real-network-free unit test — same
        # reasoning as every other _validate_prod_requester()-exercising
        # test in this file that configures a real-looking base_url.
        with patch.dict("os.environ", {
            "QKEEE_ERP_TAGK_BASE_URL": "https://example.com",
            "QKEEE_ERP_TAGK_API_KEY": "key",
            "QKEEE_ERP_TAGK_API_SECRET": "secret",
        }, clear=True):
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec.mutate_resource("tagk", "Sales Order", "create", payload={"x": 1},
                                    mode="read-write", requested_by="priya@org.com")

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "_requester_has_role_permission", return_value=None)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    @patch.object(ec, "_audit_insert", return_value=None)
    def test_mutate_resource_also_refuses_domain_scoped_write_when_role_verdict_inconclusive(
            self, mocked_audit_insert, mocked_exists, mocked_perm, mocked_role_verdict, mocked_trust):
        # Same broken-precheck scenario as
        # test_mutate_resource_refuses_write_when_precheck_unreliable
        # above, but with `domain=` set to an allowlisted, registered
        # domain — used to proceed on that allowlist alone (+ a warning);
        # per the 2026-09-11 decision it no longer does once the local
        # role/DocPerm check itself can't reach a verdict either.
        fake_domain = "test_fake_domain_kk"
        ec.register_domain_allowlist(fake_domain, ("Sales Order",))
        self.addCleanup(ec.DOMAIN_WRITE_ALLOWLISTS.pop, fake_domain, None)
        with patch.dict("os.environ", {
            "QKEEE_ERP_TAGKK_BASE_URL": "https://example.com",
            "QKEEE_ERP_TAGKK_API_KEY": "key",
            "QKEEE_ERP_TAGKK_API_SECRET": "secret",
        }, clear=True):
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec.mutate_resource("tagkk", "Sales Order", "create", payload={"x": 1},
                                    mode="read-write", requested_by="priya@org.com", domain=fake_domain)
        mocked_perm.assert_not_called()

    @patch.object(ec, "verify_rbac_precheck_reliable",
                   return_value={"reliable": False, "bot_user": "Administrator",
                                  "bot_roles": [], "privileged_identity": True,
                                  "precheck_discriminates": True})
    @patch.object(ec, "_requester_has_role_permission", return_value=True)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    @patch.object(ec, "record_comment")
    @patch.object(ec, "_audit_insert", return_value=None)
    def test_mutate_resource_proceeds_for_domain_scoped_write_when_role_verdict_confirms_it(
            self, mocked_audit_insert, mocked_comment, mocked_exists, mocked_perm,
            mocked_role_verdict, mocked_trust):
        # The success companion: same broken-precheck, same domain-scoped
        # write, but the local role/DocPerm check DOES reach a positive
        # verdict this time — that's real evidence, so it proceeds.
        fake_domain = "test_fake_domain_kk2"
        ec.register_domain_allowlist(fake_domain, ("Sales Order",))
        self.addCleanup(ec.DOMAIN_WRITE_ALLOWLISTS.pop, fake_domain, None)
        with patch.dict("os.environ", {
            "QKEEE_ERP_TAGKK2_BASE_URL": "https://example.com",
            "QKEEE_ERP_TAGKK2_API_KEY": "key",
            "QKEEE_ERP_TAGKK2_API_SECRET": "secret",
        }, clear=True), \
                patch.object(ec, "_do_mutate", return_value={"data": {"name": "SO-0001"}}), \
                patch.object(ec, "record_audit_log_start", return_value="AUDITLOG-0002"), \
                patch.object(ec, "record_audit_log_finish"):
            ec.mutate_resource("tagkk2", "Sales Order", "create", payload={"x": 1},
                                mode="read-write", requested_by="priya@org.com", domain=fake_domain)  # no raise
        mocked_perm.assert_not_called()

    @patch.object(ec, "get_user_roles",
                   return_value={"user": "qkeee-erp-bot@org.com", "roles": ["Accounts User"]})
    def test_health_check_surfaces_reliability(self, mocked_roles):
        with patch.object(ec, "check_user_permission", return_value=False), \
                patch.object(ec, "get_env_config", return_value={"tag": "tag-l", "base_url": "https://example.com"}), \
                patch.object(ec, "_request", return_value={"message": "qkeee-erp-bot@org.com"}):
            result = ec.health_check("tag-l")
        self.assertTrue(result["rbac_precheck_reliable"])
        self.assertNotIn("rbac_precheck_warning", result)

    @patch.object(ec, "get_user_roles", return_value={"user": "Administrator", "roles": []})
    def test_health_check_warns_when_unreliable(self, mocked_roles):
        with patch.object(ec, "check_user_permission", return_value=False), \
                patch.object(ec, "get_env_config", return_value={"tag": "tag-m", "base_url": "https://example.com"}), \
                patch.object(ec, "_request", return_value={"message": "Administrator"}):
            result = ec.health_check("tag-m")
        self.assertFalse(result["rbac_precheck_reliable"])
        self.assertIn("rbac_precheck_warning", result)


class RequesterRoleFallbackTests(unittest.TestCase):
    """F7 reinforcement: _requester_has_role_permission()/
    _fetch_doctype_role_permissions() — the local, RPC-independent
    corroborating check consulted from _validate_prod_requester() only
    when verify_rbac_precheck_reliable() already says the has_permission
    RPC can't be trusted. See _requester_has_role_permission()'s own
    docstring for what this can and can't see."""

    def setUp(self):
        ec._DOCTYPE_PERMISSIONS_CACHE.clear()
        self.addCleanup(ec._DOCTYPE_PERMISSIONS_CACHE.clear)

    @patch.object(ec, "get_resource")
    def test_fetch_permissions_filters_to_permlevel_zero(self, mocked_get):
        mocked_get.return_value = {"data": {"permissions": [
            {"role": "Accounts User", "read": 1, "write": 1, "permlevel": 0},
            {"role": "Accounts Manager", "write": 1, "permlevel": 1},  # field-level, excluded
        ]}}
        rows, err = ec._fetch_doctype_role_permissions("tag-n", "Sales Invoice")
        self.assertIsNone(err)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "Accounts User")

    @patch.object(ec, "get_resource", side_effect=ec.ConnectorError("ERPNext API error (403)"))
    def test_fetch_permissions_failure_is_cached_not_retried(self, mocked_get):
        rows1, err1 = ec._fetch_doctype_role_permissions("tag-o", "Sales Invoice")
        rows2, err2 = ec._fetch_doctype_role_permissions("tag-o", "Sales Invoice")
        self.assertIsNone(rows1)
        self.assertIn("403", err1)
        self.assertIsNone(rows2)
        mocked_get.assert_called_once()

    @patch.object(ec, "_fetch_doctype_role_permissions",
                   return_value=([{"role": "Sales User", "write": 1, "permlevel": 0}], None))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["Sales User"]})
    def test_matching_unconditional_role_grant_is_true(self, mocked_roles, mocked_perms):
        self.assertTrue(ec._requester_has_role_permission("tag-p", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "_fetch_doctype_role_permissions",
                   return_value=([{"role": "Sales User", "write": 1, "permlevel": 0}], None))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["HR User"]})
    def test_no_matching_role_is_false(self, mocked_roles, mocked_perms):
        self.assertFalse(ec._requester_has_role_permission("tag-q", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "_fetch_doctype_role_permissions",
                   return_value=([{"role": "Sales User", "read": 1, "permlevel": 0}], None))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["Sales User"]})
    def test_role_matches_but_wrong_perm_type_is_false(self, mocked_roles, mocked_perms):
        # Holds "Sales User", which can read Quotation, but this asks about write.
        self.assertFalse(ec._requester_has_role_permission("tag-r", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "_fetch_doctype_role_permissions",
                   return_value=([{"role": "Sales User", "write": 1, "permlevel": 0, "if_owner": 1}], None))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["Sales User"]})
    def test_if_owner_only_match_never_counts_as_true(self, mocked_roles, mocked_perms):
        # Ownership can't be cheaply verified here (and is meaningless for
        # create) -- an if_owner-only row must not be treated as a grant.
        self.assertFalse(ec._requester_has_role_permission("tag-s", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "get_user_roles", side_effect=ec.ConnectorError("unreachable"))
    def test_requester_role_fetch_failure_is_inconclusive(self, mocked_roles):
        self.assertIsNone(ec._requester_has_role_permission("tag-t", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": []})
    def test_empty_requester_roles_is_inconclusive_not_false(self, mocked_roles):
        # get_user_roles()'s own docstring: empty could mean "genuinely no
        # role" or "the lookup came back thin" -- never treated as a verdict.
        self.assertIsNone(ec._requester_has_role_permission("tag-u", "Quotation", "write", "priya@org.com"))

    @patch.object(ec, "_fetch_doctype_role_permissions", return_value=(None, "ERPNext API error (403)"))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["Sales User"]})
    def test_doctype_permission_fetch_failure_is_inconclusive(self, mocked_roles, mocked_perms):
        # Same F7/F8-acknowledged constraint as schema-mapping: a
        # correctly least-privileged bot can lack System-Manager-level
        # DocType read too -- degrades to "couldn't check," not a verdict.
        self.assertIsNone(ec._requester_has_role_permission("tag-v", "Quotation", "write", "priya@org.com"))


class RequesterRoleFallbackWiringTests(unittest.TestCase):
    """_validate_prod_requester()'s own consumption of the above. Per the
    2026-09-11 decision: only `True` (a locally-confirmed grant) lets a
    call through — `False` (a confirmed non-grant) and `None`
    (inconclusive: the local check itself couldn't complete) both refuse
    outright now, regardless of `domain`/`advisory_token_verified`/
    read-vs-write. A `domain` allowlist or a verified advisory token no
    longer rescues either case — they attest a write's shape was
    reviewed, never that requested_by specifically can do it."""

    def setUp(self):
        ec._PRECHECK_WARNED_TAGS.clear()
        self.addCleanup(ec._PRECHECK_WARNED_TAGS.clear)

    _UNRELIABLE = {"reliable": False, "bot_user": "Administrator", "bot_roles": [],
                   "privileged_identity": True, "precheck_discriminates": True}

    @patch.object(ec, "_requester_has_role_permission", return_value=False)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_false_verdict_refuses_even_a_domain_scoped_write(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("tag-w", "priya@org.com", "Sales Order", "write",
                                         docname="SO-0001", domain="sales")
        self.assertIn("holds no role", str(ctx.exception))
        mocked_perm.assert_not_called()

    @patch.object(ec, "_requester_has_role_permission", return_value=False)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_false_verdict_refuses_even_an_advisory_token_verified_write(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-x", "priya@org.com", "Company", "write",
                                         advisory_token_verified=True)

    @patch.object(ec, "_requester_has_role_permission", return_value=False)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_false_verdict_refuses_a_read_too(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-y", "priya@org.com", "Sales Order", "read")

    @patch.object(ec, "_requester_has_role_permission", return_value=True)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_true_verdict_proceeds_with_no_domain_and_no_token(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        # The whole point: a locally-confirmed role grant is itself
        # sufficient, even for an unscoped write that would otherwise be
        # PrivilegedBotAccountError'd outright.
        ec._validate_prod_requester("tag-z", "priya@org.com", "Company", "write")  # no raise
        mocked_perm.assert_not_called()

    @patch.object(ec, "_requester_has_role_permission", return_value=None)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_none_verdict_refuses_an_unscoped_write(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-z2", "priya@org.com", "Company", "write")

    @patch.object(ec, "_requester_has_role_permission", return_value=None)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_none_verdict_also_refuses_a_domain_scoped_write(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        # Per the 2026-09-11 decision, a `domain` allowlist no longer
        # rescues an inconclusive local verdict either -- same refusal as
        # the unscoped case above, despite this write having cleared the
        # allowlist gate.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("tag-z3", "priya@org.com", "Sales Order", "write",
                                         docname="SO-0001", domain="sales")


class NeverSubstituteRequesterGuardrailTests(unittest.TestCase):
    """F10, .scratch/hermes-erp-bot-reliability/spec.md — live-observed:
    an agent facing a permission-denied requester offered to re-run the
    same call as a DIFFERENT requested_by instead of reporting the gap.
    Every requester-permission-denial message must carry an explicit
    instruction against that, baked into the exception text itself (not
    just documented in 00-conventions.md, which an agent mid-incident may
    not re-read) — see _NEVER_SUBSTITUTE_REQUESTER."""

    _UNRELIABLE = {"reliable": False, "bot_user": "Administrator", "bot_roles": [],
                   "privileged_identity": True, "precheck_discriminates": True}

    @patch.object(ec, "_requester_has_role_permission", return_value=False)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_false_role_verdict_message_carries_guardrail(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("tag-nsr1", "priya@org.com", "Employee", "read")
        self.assertIn("Do not retry this call with a different requested_by", str(ctx.exception))

    @patch.object(ec, "_requester_has_role_permission", return_value=None)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value=_UNRELIABLE)
    @patch.object(ec, "check_user_permission")
    @patch.object(ec, "resource_exists", return_value=True)
    def test_none_role_verdict_message_carries_guardrail(
            self, mocked_exists, mocked_perm, mocked_trust, mocked_role_verdict):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("tag-nsr2", "priya@org.com", "Employee", "read")
        self.assertIn("Do not retry this call with a different requested_by", str(ctx.exception))

    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_reliable_rpc_denial_message_carries_guardrail(
            self, mocked_exists, mocked_perm, mocked_trust):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("tag-nsr3", "priya@org.com", "Employee", "read")
        self.assertIn("Do not retry this call with a different requested_by", str(ctx.exception))


class ProdGateWiringTests(unittest.TestCase):
    """Confirms the gate is actually called from every read/write entry
    point, with the right doctype/perm_type/docname — not just that the
    gate function itself works in isolation."""

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"data": []})
    def test_query_resource_gates_with_read(self, mocked_request, mocked_cfg, mocked_gate):
        ec.query_resource("prod", "Sales Order", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "read",
                                             session_id=None, domain_code=None,
                                             channel=None, channel_metadata=None,
                                             prompt_summary=None, latest_prompt=None)

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"data": {"name": "SO-0001"}})
    def test_get_resource_gates_with_read_and_docname(self, mocked_request, mocked_cfg, mocked_gate):
        ec.get_resource("prod", "Sales Order", "SO-0001", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "read", docname="SO-0001",
                                             session_id=None, domain_code=None,
                                             channel=None, channel_metadata=None,
                                             prompt_summary=None, latest_prompt=None)

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"message": {"result": []}})
    def test_run_query_report_gates_against_report_doctype(self, mocked_request, mocked_cfg, mocked_gate):
        ec.run_query_report("prod", "Sales Analytics", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Report", "read", docname="Sales Analytics",
                                             session_id=None, domain_code=None,
                                             channel=None, channel_metadata=None,
                                             prompt_summary=None, latest_prompt=None)

    @patch.object(ec, "record_audit_log_finish")
    @patch.object(ec, "record_audit_log_start", return_value="AUDITLOG-0001")
    @patch.object(ec, "_do_mutate", return_value={"data": {"name": "SO-0001"}})
    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_mutate_resource_gates_with_action_specific_ptype(self, mocked_cfg, mocked_gate,
                                                                mocked_do_mutate, mocked_start, mocked_finish):
        ec.mutate_resource("prod", "Sales Order", "submit", name="SO-0001", mode="read-write",
                            requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "submit",
                                             docname="SO-0001", domain=None, advisory_token_verified=False,
                                             session_id=None, domain_code=None,
                                             channel=None, channel_metadata=None,
                                             prompt_summary=None, latest_prompt=None)

    @patch.object(ec, "_validate_prod_requester", side_effect=ec.UnvalidatedProdRequesterError("nope"))
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_mutate_resource_blocks_before_any_write_when_gate_fails(self, mocked_cfg, mocked_gate):
        with patch.object(ec, "_do_mutate") as mocked_do_mutate, \
                patch.object(ec, "record_audit_log_start") as mocked_start:
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec.mutate_resource("prod", "Sales Order", "create", payload={"customer": "X"},
                                    mode="read-write", requested_by="priya@org.com")
            mocked_do_mutate.assert_not_called()
            mocked_start.assert_not_called()


class DomainTokenGateTests(unittest.TestCase):
    """mutate_resource()'s generic advisory-token gate for a domain that
    has opted submit/cancel (or delete) into it via
    register_domain_token_gate() — accounts/hr-payroll/sales/procurement/
    inventory's actual registrations, exercised here through a throwaway
    fake domain so this test doesn't depend on any real domain module's
    doctype list."""

    FAKE_DOMAIN = "test_fake_domain"

    QA_ENV = {
        "QKEEE_ERP_QA_BASE_URL": "https://example.com",
        "QKEEE_ERP_QA_API_KEY": "key",
        "QKEEE_ERP_QA_API_SECRET": "secret",
    }

    def setUp(self):
        ec.register_domain_allowlist(self.FAKE_DOMAIN, ("Fake Doctype",))
        ec.register_domain_token_gate(self.FAKE_DOMAIN, {"submit", "cancel"})
        self.addCleanup(ec.DOMAIN_WRITE_ALLOWLISTS.pop, self.FAKE_DOMAIN, None)
        self.addCleanup(ec.DOMAIN_TOKEN_GATED_ACTIONS.pop, self.FAKE_DOMAIN, None)
        ec._QKEEE_ENV_FILE_CACHE = None
        self.addCleanup(setattr, ec, "_QKEEE_ENV_FILE_CACHE", None)

    def _mocks(self):
        return (
            patch.object(ec, "record_comment"),
            patch.object(ec, "_audit_insert", return_value=None),
            patch.object(ec, "_audit_update", return_value=False),
            patch.object(ec, "_audit_submit", return_value=False),
            patch.object(ec, "resource_exists", return_value=True),
            patch.object(ec, "check_user_permission", return_value=True),
            patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True}),
        )

    def test_submit_refused_without_token(self):
        with self.assertRaises(ec.ConnectorError) as ctx:
            ec.mutate_resource("qa", "Fake Doctype", "submit", name="FD-0001", mode="read-write",
                                requested_by="priya@org.com", domain=self.FAKE_DOMAIN)
        self.assertIn("confirmation_token", str(ctx.exception))

    def test_cancel_refused_without_token(self):
        with self.assertRaises(ec.ConnectorError):
            ec.mutate_resource("qa", "Fake Doctype", "cancel", name="FD-0001", mode="read-write",
                                requested_by="priya@org.com", domain=self.FAKE_DOMAIN)

    def test_create_is_never_token_gated(self):
        """create/update aren't in the registered action set — they're the
        draft steps meant to be reviewed BEFORE this gate ever applies."""
        with patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "FD-0001"}}), \
                patch.object(ec, "record_comment"), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission", return_value=True), \
                patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True}):
            result = ec.mutate_resource("qa", "Fake Doctype", "create", payload={"x": 1},
                                         mode="read-write", requested_by="priya@org.com",
                                         domain=self.FAKE_DOMAIN)
        self.assertEqual(result["data"]["name"], "FD-0001")

    def test_submit_refused_with_stale_token(self):
        old_issued_at = int(time.time()) - 10_000
        token = advisory_write_token("submit", "Fake Doctype", "FD-0001", {}, "priya@org.com", old_issued_at)
        with self.assertRaises(ec.StaleConfirmationError):
            ec.mutate_resource("qa", "Fake Doctype", "submit", name="FD-0001", mode="read-write",
                                requested_by="priya@org.com", domain=self.FAKE_DOMAIN,
                                confirmation_token=token, issued_at=old_issued_at)

    def test_submit_refused_with_mismatched_token(self):
        issued_at = int(time.time())
        token = advisory_write_token("submit", "Fake Doctype", "FD-0001", {"amount": 1}, "priya@org.com", issued_at)
        with self.assertRaises(ec.ConnectorError):
            ec.mutate_resource("qa", "Fake Doctype", "submit", name="FD-0001", mode="read-write",
                                requested_by="priya@org.com", domain=self.FAKE_DOMAIN,
                                confirmation_token=token, issued_at=issued_at,
                                payload={"amount": 2})

    def test_submit_succeeds_with_matching_fresh_token(self):
        issued_at = int(time.time())
        token = advisory_write_token("submit", "Fake Doctype", "FD-0001", {}, "priya@org.com", issued_at)
        mocks = self._mocks()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6], \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "FD-0001"}}):
            result = ec.mutate_resource("qa", "Fake Doctype", "submit", name="FD-0001", mode="read-write",
                                         requested_by="priya@org.com", domain=self.FAKE_DOMAIN,
                                         confirmation_token=token, issued_at=issued_at)
        self.assertEqual(result["data"]["name"], "FD-0001")

    def test_ungated_domain_action_combination_is_unaffected(self):
        """A domain/action combination that was never registered (e.g. this
        fake domain's "delete", only submit/cancel were registered) gets no
        token check at all — same as before this gate existed."""
        with patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={}), \
                patch.object(ec, "record_comment"), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission", return_value=True), \
                patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True}):
            ec.mutate_resource("qa", "Fake Doctype", "delete", name="FD-0001", mode="read-write",
                                requested_by="priya@org.com", domain=self.FAKE_DOMAIN)


class AuditFailureStreakTests(unittest.TestCase):
    """A persistently failing audit path (bot-init never run, permission
    revoked, instance unreachable) should escalate past a single easy-to-
    miss stderr line once it's clearly not a one-off — see
    AUDIT_FAILURE_STREAK_WARN_THRESHOLD. Still never raises or blocks the
    real write either way."""

    def setUp(self):
        ec._AUDIT_FAILURE_STREAK.clear()
        self.addCleanup(ec._AUDIT_FAILURE_STREAK.clear)

    @patch.object(ec, "_request", side_effect=RuntimeError("boom"))
    def test_streak_escalates_after_threshold(self, mocked_request):
        with patch("sys.stderr") as mock_stderr:
            for _ in range(ec.AUDIT_FAILURE_STREAK_WARN_THRESHOLD):
                ec._audit_insert({"tag": "streak-tag"}, {"session": "s1"})
        combined = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("looks systemic", combined)

    @patch.object(ec, "_request", side_effect=[RuntimeError("boom"), {"data": {"name": "LOG-1"}}])
    def test_success_resets_the_streak(self, mocked_request):
        ec._audit_insert({"tag": "streak-tag-2"}, {"session": "s1"})
        self.assertEqual(ec._AUDIT_FAILURE_STREAK["streak-tag-2"], 1)
        ec._audit_insert({"tag": "streak-tag-2"}, {"session": "s1"})
        self.assertEqual(ec._AUDIT_FAILURE_STREAK["streak-tag-2"], 0)


class GateDecisionLoggingTests(unittest.TestCase):
    """F11, .scratch/hermes-erp-bot-reliability/spec.md: a denied
    requester-permission check used to leave zero trace in Qkeee Bot
    Audit Log — _validate_prod_requester() now logs one gate-decision row
    (via _log_gate_decision()) on every branch, allow or deny."""

    ENV = {
        "QKEEE_ERP_GATELOG_BASE_URL": "https://example.com",
        "QKEEE_ERP_GATELOG_API_KEY": "key",
        "QKEEE_ERP_GATELOG_API_SECRET": "secret",
    }

    @patch.object(ec, "_audit_submit", return_value=True)
    @patch.object(ec, "_audit_insert", return_value="AUDITLOG-GATE-1")
    def test_missing_requester_denial_is_logged(self, mocked_insert, mocked_submit):
        with patch.dict("os.environ", self.ENV, clear=True):
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec._validate_prod_requester("gatelog", None, "Sales Order", "read")
        mocked_insert.assert_called_once()
        fields = mocked_insert.call_args[0][1]
        self.assertEqual(fields["status"], "Failure")
        self.assertEqual(fields["action"], "Read")
        self.assertEqual(fields["reference_doctype"], "Sales Order")
        payload = json.loads(fields["response_payload"])
        self.assertTrue(payload["gate_check"])
        self.assertFalse(payload["allowed"])
        mocked_submit.assert_called_once_with({"tag": "gatelog", "base_url": "https://example.com",
                                                 "api_key": "key", "api_secret": "secret"},
                                                "AUDITLOG-GATE-1")

    @patch.object(ec, "_audit_submit", return_value=True)
    @patch.object(ec, "_audit_insert", return_value="AUDITLOG-GATE-2")
    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "resource_exists", return_value=True)
    def test_rpc_permission_denial_is_logged(self, mocked_exists, mocked_trust, mocked_perm,
                                              mocked_insert, mocked_submit):
        with patch.dict("os.environ", self.ENV, clear=True):
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec._validate_prod_requester("gatelog", "priya@org.com", "Sales Order", "write",
                                             docname="SO-0001")
        fields = mocked_insert.call_args[0][1]
        self.assertEqual(fields["status"], "Failure")
        self.assertEqual(fields["action"], "Update")  # write -> Update, see _PTYPE_TO_ACTION
        payload = json.loads(fields["response_payload"])
        self.assertEqual(payload["path"], "has_permission_rpc")

    @patch.object(ec, "_audit_submit", return_value=True)
    @patch.object(ec, "_audit_insert", return_value="AUDITLOG-GATE-3")
    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "verify_rbac_precheck_reliable", return_value={"reliable": True})
    @patch.object(ec, "resource_exists", return_value=True)
    def test_allowed_call_is_also_logged(self, mocked_exists, mocked_trust, mocked_perm,
                                          mocked_insert, mocked_submit):
        with patch.dict("os.environ", self.ENV, clear=True):
            ec._validate_prod_requester("gatelog", "priya@org.com", "Sales Order", "read")
        fields = mocked_insert.call_args[0][1]
        self.assertEqual(fields["status"], "Success")
        payload = json.loads(fields["response_payload"])
        self.assertTrue(payload["allowed"])

    @patch.object(ec, "_audit_insert")
    def test_exempt_doctype_is_never_logged(self, mocked_insert):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("gatelog", None, "User", "read")
        mocked_exists.assert_not_called()
        mocked_insert.assert_not_called()

    @patch.object(ec, "_audit_insert")
    def test_unresolvable_tag_never_raises_from_logging_itself(self, mocked_insert):
        # No env configured for this tag at all — get_env_config() inside
        # _log_gate_decision() fails; that must never surface as anything
        # other than the ORIGINAL UnvalidatedProdRequesterError.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("no-such-tag-at-all", None, "Sales Order", "read")
        mocked_insert.assert_not_called()


class GetResourceInternalFlagTests(unittest.TestCase):
    """F12, .scratch/hermes-erp-bot-reliability/spec.md: get_resource()'s
    `internal=True` replaces a blanket User/DocType/Role doctype
    exemption on the read path — a business-intent read of those
    doctypes now gets logged; only a caller that explicitly marks itself
    internal (resource_exists(), _fetch_doctype_role_permissions()) is
    skipped."""

    @patch.object(ec, "_log_read")
    @patch.object(ec, "_request", return_value={"data": {"name": "nikhil.sharma@qkeee.in"}})
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_validate_prod_requester")
    def test_business_read_of_user_doctype_is_logged(self, mocked_gate, mocked_cfg, mocked_request,
                                                       mocked_log_read):
        ec.get_resource("default", "User", "nikhil.sharma@qkeee.in", requested_by="admin@org.com")
        mocked_log_read.assert_called_once()
        self.assertFalse(mocked_log_read.call_args.kwargs.get("internal", False))

    @patch.object(ec, "_log_read")
    @patch.object(ec, "_request", return_value={"data": {"name": "Sales Order"}})
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_validate_prod_requester")
    def test_resource_exists_marks_its_own_read_internal(self, mocked_gate, mocked_cfg, mocked_request,
                                                           mocked_log_read):
        self.assertTrue(ec.resource_exists("default", "DocType", "Sales Order"))
        mocked_log_read.assert_called_once()
        self.assertTrue(mocked_log_read.call_args.kwargs.get("internal"))

    def test_log_read_skips_when_internal_regardless_of_doctype(self):
        with patch.object(ec, "_audit_insert") as mocked_insert:
            ec._log_read({"tag": "t"}, "Sales Order", "SO-0001", "priya@org.com", None, None,
                          internal=True)
        mocked_insert.assert_not_called()

    def test_log_read_still_exempts_true_recursion_doctypes_when_not_internal(self):
        with patch.object(ec, "_audit_insert") as mocked_insert:
            ec._log_read({"tag": "t"}, ec.AUDIT_LOG_DOCTYPE, "AL-1", "priya@org.com", None, None)
        mocked_insert.assert_not_called()


class GetUserRolesAuditLoggingTests(unittest.TestCase):
    """F12: get_user_roles() previously never routed through _log_read()
    at all — a business-intent `roles <user>` lookup (the CLI command, or
    a domain script) left no Audit Log row regardless of doctype
    exemptions. Now logs unless internal=True."""

    @patch.object(ec, "_log_read")
    @patch.object(ec, "_request", return_value={"data": {"roles": [{"role": "Purchase Manager"}]}})
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    def test_business_lookup_is_logged(self, mocked_cfg, mocked_request, mocked_log_read):
        ec.get_user_roles("default", "priya@org.com", requested_by="admin@org.com")
        mocked_log_read.assert_called_once()
        args = mocked_log_read.call_args[0]
        self.assertEqual(args[1], "User")
        self.assertEqual(args[2], "priya@org.com")
        self.assertEqual(args[3], "admin@org.com")  # requested_by = who's asking

    @patch.object(ec, "_log_read")
    @patch.object(ec, "_request", return_value={"data": {"roles": []}})
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    def test_internal_lookup_from_bot_identity_is_not_logged(self, mocked_cfg, mocked_request, mocked_log_read):
        ec.get_user_roles("default", internal=True)
        mocked_log_read.assert_not_called()

    @patch.object(ec, "get_user_roles", return_value={"user": "bot@org.com", "roles": []})
    def test_bot_identity_resolution_passes_internal_true(self, mocked_roles):
        ec._BOT_IDENTITY_CACHE.clear()
        self.addCleanup(ec._BOT_IDENTITY_CACHE.clear)
        ec._bot_identity("some-tag")
        mocked_roles.assert_called_once_with("some-tag", internal=True)

    @patch.object(ec, "_fetch_doctype_role_permissions", return_value=([], None))
    @patch.object(ec, "get_user_roles", return_value={"user": "priya@org.com", "roles": ["Sales User"]})
    def test_requester_role_fallback_passes_internal_true(self, mocked_roles, mocked_perms):
        ec._requester_has_role_permission("some-tag", "Quotation", "write", "priya@org.com")
        mocked_roles.assert_called_once_with("some-tag", "priya@org.com", internal=True)


if __name__ == "__main__":
    unittest.main()
