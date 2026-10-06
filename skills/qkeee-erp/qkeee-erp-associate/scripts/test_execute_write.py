#!/usr/bin/env python3
"""Tests for execute_write.py (the only write CLI) and confirm_token.py's
render CLI (write-path hardening ticket 11).

The pipeline itself is covered by domains/test_operations.py; these cover
what the CLIs add: operation naming (--op / shorthand), flag validation,
mandatory audit context for gated operations, warnings, exit codes, and a
real render -> execute round trip through both CLIs with the token check
NOT mocked (W05's regression test at CLI level)."""

import contextlib
import copy
import io
import json
import sys
import unittest
from unittest.mock import patch

import execute_write
from core import client as core_client
from core import confirm_token, operations
from core.client import DOMAIN_WRITE_ALLOWLISTS
import schema_mapping
import testsupport

BASE = ["--tag", "t", "--mode", "read-write", "--requested-by", testsupport.REQ]
CONTEXT = ["--session-id", "S-1", "--channel-metadata", '{"thread": "T"}', "--latest-prompt", "do it"]


def run(argv, main=execute_write.main):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class ImportRegistersEverythingTests(unittest.TestCase):

    def test_every_known_domain_has_a_registered_allowlist_and_generic_op(self):
        expected = {"accounts", "fixed_assets", "hr_payroll", "inventory", "mis",
                    "procurement", "sales", "system_admin"}
        self.assertEqual(execute_write._KNOWN_DOMAINS, expected)
        for domain in expected:
            with self.subTest(domain=domain):
                self.assertIn(domain, DOMAIN_WRITE_ALLOWLISTS)
                self.assertIn(f"{domain}.generic", operations.REGISTRY)

    def test_list_ops_needs_no_other_flags(self):
        code, out, _ = run(["--list-ops"])
        self.assertEqual(code, 0)
        ops = {o["op"]: o for o in json.loads(out)}
        keys = set(ops)
        self.assertEqual(ops["system_admin.generic"]["confirmation"], "always")
        self.assertEqual(ops["sales.generic"]["confirmation"], "per action: submit/cancel/delete")
        self.assertEqual(ops["system_admin.disable_user"]["confirmation"], "always")
        self.assertIn("system_admin.create_user", keys)
        self.assertIn("unscoped.generic", keys)
        self.assertFalse(any(k.startswith("provisioning.") for k in keys))


class ArgumentTests(unittest.TestCase):

    def setUp(self):
        p = patch.object(operations, "run_operation", return_value={"_audit_log_status": "ok"})
        self.run_op = p.start()
        self.addCleanup(p.stop)

    def test_required_flags(self):
        for missing in ("--tag", "--mode", "--requested-by"):
            with self.subTest(missing=missing):
                argv = list(BASE)
                i = argv.index(missing)
                del argv[i:i + 2]
                code, _, err = run(argv + ["--op", "sales.generic"])
                self.assertEqual(code, 2)
                self.assertIn(missing, err)

    def test_shorthand_with_domain_maps_to_domain_generic(self):
        code, _, _ = run(BASE + ["--domain", "sales", "--doctype", "Sales Order", "--action", "create",
                                 "--payload", '{"customer": "A"}'])
        self.assertEqual(code, 0)
        key, args, ctx = self.run_op.call_args.args
        self.assertEqual(key, "sales.generic")
        self.assertEqual(args, {"doctype": "Sales Order", "action": "create", "payload": {"customer": "A"}})
        self.assertEqual(ctx.mode, "read-write")

    def test_shorthand_without_domain_maps_to_unscoped(self):
        run(BASE + CONTEXT + ["--doctype", "Item", "--action", "create", "--payload", "{}",
                              "--purchase-sourced-item"])
        key, args, _ = self.run_op.call_args.args
        self.assertEqual(key, "unscoped.generic")
        self.assertTrue(args["purchase_sourced_item"])

    def test_op_and_args(self):
        run(BASE + CONTEXT + ["--op", "system_admin.disable_user",
                              "--args", '{"name": "u@x.com", "reason": "left"}',
                              "--confirmation-token", "tok", "--issued-at", "123",
                              "--user-confirmation-text", "yes ABC123"])
        key, args, ctx = self.run_op.call_args.args
        self.assertEqual((key, args), ("system_admin.disable_user", {"name": "u@x.com", "reason": "left"}))
        self.assertEqual((ctx.confirmation_token, ctx.issued_at, ctx.user_confirmation_text),
                         ("tok", "123", "yes ABC123"))
        self.assertEqual(ctx.channel_metadata, {"thread": "T"})

    def test_op_cannot_mix_with_shorthand(self):
        code, _, err = run(BASE + ["--op", "sales.generic", "--doctype", "X"])
        self.assertEqual(code, 2)
        self.assertIn("--op cannot be combined", err)
        self.run_op.assert_not_called()

    def test_flag_misuse_is_a_usage_error_not_silently_ignored(self):
        cases = [
            ["--domain", "sales", "--doctype", "Sales Order", "--action", "create", "--kyc", "{}"],
            ["--domain", "procurement", "--doctype", "Supplier", "--action", "update", "--name", "S",
             "--kyc-waiver-confirmed"],
            ["--doctype", "Customer", "--action", "create", "--purchase-sourced-item"],
            ["--domain", "sales", "--doctype", "Sales Order", "--action", "submit", "--name", "SO",
             "--staged-fields", "[]"],
            ["--args", "{}"],
            [],
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                code, _, _ = run(BASE + CONTEXT + extra)
                self.assertEqual(code, 2)
        self.run_op.assert_not_called()

    def test_malformed_json_is_a_usage_error(self):
        code, _, err = run(BASE + ["--op", "sales.generic", "--args", "{nope"])
        self.assertEqual(code, 2)
        self.assertIn("--args must be valid JSON", err)


class AuditContextTests(unittest.TestCase):

    def setUp(self):
        p = patch.object(operations, "run_operation", return_value={"_audit_log_status": "ok"})
        self.run_op = p.start()
        self.addCleanup(p.stop)

    def test_gated_operations_require_full_context(self):
        for argv in (["--op", "system_admin.disable_user", "--args", '{"name": "u", "reason": "r"}'],
                     ["--domain", "system_admin", "--doctype", "Role", "--action", "create",
                      "--payload", '{"role_name": "R"}'],
                     ["--domain", "accounts", "--doctype", "Journal Entry", "--action", "submit",
                      "--name", "JE-1"],
                     ["--doctype", "Item", "--action", "create", "--payload", "{}"]):
            with self.subTest(argv=argv):
                code, _, err = run(BASE + ["--session-id", "S"] + argv)
                self.assertEqual(code, 2)
                self.assertIn("--channel-metadata", err)
                self.assertIn("--latest-prompt", err)
        self.run_op.assert_not_called()

    def test_ungated_draft_write_warns_but_proceeds(self):
        code, _, err = run(BASE + ["--domain", "sales", "--doctype", "Sales Order",
                                   "--action", "create", "--payload", "{}"])
        self.assertEqual(code, 0)
        for flag in ("--session-id not given", "--channel-metadata not given", "--latest-prompt not given"):
            self.assertIn(flag, err)

    def test_full_context_warns_nothing(self):
        code, _, err = run(BASE + CONTEXT + ["--domain", "sales", "--doctype", "Sales Order",
                                             "--action", "create", "--payload", "{}"])
        self.assertEqual(code, 0)
        self.assertNotIn("not given", err)


class ExitCodeTests(unittest.TestCase):
    ARGV = BASE + CONTEXT + ["--domain", "sales", "--doctype", "Sales Order", "--action", "create",
                             "--payload", "{}"]

    def _run_with(self, **kw):
        with patch.object(operations, "run_operation", **kw):
            return run(self.ARGV)

    def test_codes(self):
        cases = [
            (dict(return_value={"_audit_log_status": "ok"}), 0, ""),
            (dict(side_effect=core_client.DoctypeNotAllowedError("x")), 3, "refused, nothing was sent"),
            (dict(side_effect=core_client.TokenMismatchError("x")), 3, "refused"),
            (dict(side_effect=core_client.TransportTimeoutError("x")), 4, "outcome unknown"),
            (dict(side_effect=core_client.PartialOutcomeError("x")), 4, "partial"),
            (dict(side_effect=core_client.ConnectorError("ERPNext API error (417)")), 1, "ERROR"),
        ]
        for kw, code, text in cases:
            with self.subTest(code=code, text=text):
                got, _, err = self._run_with(**kw)
                self.assertEqual(got, code)
                self.assertIn(text, err)

    def test_degraded_audit_and_notes_are_surfaced(self):
        code, out, err = self._run_with(return_value={"_audit_log_status": "insert_failed",
                                                      "_notes": ["field(s) dropped: ['x']"]})
        self.assertEqual(code, 0)
        self.assertIn("NOT reliably in the audit trail", err)
        self.assertIn("WARN: field(s) dropped", err)
        self.assertEqual(json.loads(out)["_audit_log_status"], "insert_failed")


class RenderThenExecuteTests(unittest.TestCase):
    """Both CLIs, real pipeline, real token check — only the network is
    stubbed. The live schema renames a key and the purchase-sourced
    defaults add keys, so this fails if render and execute ever prepare
    different bytes (W05)."""

    LIVE_FIELDS = [{"fieldname": "item_code", "label": "Item Code"},
                   {"fieldname": "item_name", "label": "Item Name"},
                   {"fieldname": "is_purchase_item", "label": "Is Purchase Item"},
                   {"fieldname": "is_sales_item", "label": "Is Sales Item"}]

    @contextlib.contextmanager
    def _network(self):
        cfg = {"tag": "t", "base_url": "https://e.example", "api_key": "k", "api_secret": "s"}
        with patch.object(schema_mapping, "get_doctype_schema", return_value=(self.LIVE_FIELDS, None)), \
                patch.object(core_client, "get_env_config", return_value=cfg), \
                patch.object(core_client, "_validate_prod_requester"), \
                patch.object(core_client, "record_audit_log_start", return_value="LOG"), \
                patch.object(core_client, "record_audit_log_finish", return_value=True), \
                patch.object(core_client, "_do_mutate", return_value={"data": {"name": "X-1"}}) as do:
            yield do

    def _render(self, args):
        argv = ["render", "--op", "unscoped.generic", "--args", json.dumps(args), "--tag", "t",
                "--requested-by", testsupport.REQ]
        with patch.object(sys, "argv", ["confirm_token.py"] + argv):
            code, out, err = run(None, main=lambda _argv: confirm_token._cli())
        self.assertIn(code, (None, 0), err)
        return json.loads(out)

    def _execute(self, rendered, reply):
        return run(BASE + CONTEXT + ["--op", "unscoped.generic", "--args", json.dumps(rendered["args"]),
                                     "--confirmation-token", rendered["confirmation_token"],
                                     "--issued-at", str(rendered["issued_at"]),
                                     "--user-confirmation-text", reply])

    ARGS = {"doctype": "Item", "action": "create", "purchase_sourced_item": True,
            "payload": {"Item Code": "X-1", "Item Name": "Widget"}}

    def test_round_trip_succeeds(self):
        with self._network() as do:
            rendered = self._render(self.ARGS)
            self.assertEqual(rendered["request"]["body"],
                             {"item_code": "X-1", "item_name": "Widget",
                              "is_purchase_item": 1, "is_sales_item": 0})
            code, out, err = self._execute(rendered, f"yes {rendered['confirmation_code']}")
        self.assertEqual(code, 0, err)
        self.assertEqual(do.call_args.args[3], rendered["request"]["body"])
        self.assertEqual(json.loads(out)["_operation"], "unscoped.generic")

    def test_changed_args_after_render_are_refused(self):
        with self._network() as do:
            rendered = self._render(self.ARGS)
            tampered = copy.deepcopy(rendered)
            tampered["args"]["payload"]["Item Name"] = "Gadget"
            code, _, err = self._execute(tampered, f"yes {rendered['confirmation_code']}")
        self.assertEqual(code, 3)
        self.assertIn("does not match", err)
        do.assert_not_called()

    def test_reply_without_code_is_refused(self):
        with self._network() as do:
            rendered = self._render(self.ARGS)
            code, _, err = self._execute(rendered, "sure, go ahead")
        self.assertEqual(code, 3)
        do.assert_not_called()

    def test_render_refuses_early_for_owned_targets(self):
        args = {"doctype": "User", "action": "create", "payload": {"email": "a@b.c"}}
        argv = ["render", "--op", "unscoped.generic", "--args", json.dumps(args), "--tag", "t",
                "--requested-by", testsupport.REQ]
        with self._network(), patch.object(sys, "argv", ["confirm_token.py"] + argv):
            code, _, err = run(None, main=lambda _argv: confirm_token._cli())
        self.assertEqual(code, 3)
        self.assertIn("system_admin.create_user", err)


if __name__ == "__main__":
    unittest.main()
