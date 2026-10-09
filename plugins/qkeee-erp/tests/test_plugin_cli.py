"""Operator CLI `hermes -p <profile> qkeee-erp <command>` (agents
.scratch/qkeee-erp-plugin-profile-split, issue 05).

A CLI run has no gateway sender, so every command that reads or writes
ERPNext names its Requester with --requested-by. Each command runs the
matching erp_* tool handler (one code path): the tests patch the same
library functions the tool tests patch. Audit rows record channel `cli`."""

import contextlib
import copy
import json
import os
import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp import discover, init_bot, schema_mapping
from qkeee_erp_plugin.qkeee_erp.core import client, operations
import testsupport

REQ = testsupport.REQ
SETTINGS = {"plugins": {"entries": {"qkeee-erp": {"settings": {"active_env": "dev",
                                                                "mode": "read-write"}}}}}


class CliTestCase(unittest.TestCase):
    config = SETTINGS

    def setUp(self):
        # No gateway session in a CLI run.
        client.set_session_env_reader(lambda name, default="": default)
        self.addCleanup(client.set_session_env_reader, None)
        env = patch.dict(os.environ, {"HERMES_KANBAN_TASK": "", "HERMES_SESSION_USER_ID": ""})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, argv):
        return testsupport.run_operator_cli(argv, self.config)


class RequesterTests(CliTestCase):
    ERP_COMMANDS = (["query", "Customer"], ["get", "Customer", "C-1"], ["report", "General Ledger"],
                    ["roles"], ["discover", "meta", "Item"],
                    ["render", "--op", "sales.generic", "--args", "{}"],
                    ["execute-write", "--op", "sales.generic", "--args", "{}"],
                    ["init-bot", "--dry-run"])

    def test_every_erp_command_without_requested_by_is_refused(self):
        with patch.object(client, "_request") as network:
            for argv in self.ERP_COMMANDS:
                with self.subTest(argv=argv):
                    code, _, err = self.run_cli(argv)
                    self.assertEqual(code, 2)
                    self.assertIn("--requested-by", err)
        network.assert_not_called()

    def test_blank_requested_by_is_refused(self):
        with patch.object(client, "query_resource") as mocked:
            code, _, err = self.run_cli(["query", "Customer", "--requested-by", " "])
        self.assertEqual(code, 3)
        self.assertIn("--requested-by", err)
        mocked.assert_not_called()

    def test_setup_and_diagnostic_commands_need_no_requester(self):
        with patch.object(client, "health_check", return_value={"tag": "dev"}) as health, \
                patch.object(client, "list_configured_tags", return_value=["DEV"]):
            self.assertEqual(self.run_cli(["health"])[0], 0)
            self.assertEqual(self.run_cli(["list-envs"])[0], 0)
            self.assertEqual(self.run_cli(["list-ops"])[0], 0)
        health.assert_called_once_with("dev")


class SameCodePathTests(CliTestCase):
    """Each command calls the library function its erp_* tool calls, with
    the CLI requester, the plugin settings and the `cli` audit channel."""

    def test_query(self):
        with patch.object(client, "query_resource", return_value=[{"name": "C-1"}]) as mocked:
            code, out, _ = self.run_cli(["query", "Customer", "--filters", '[["name","=","C-1"]]',
                                         "--fields", '["name"]', "--limit", "5",
                                         "--requested-by", REQ, "--prompt-summary", "audit check"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["result"], [{"name": "C-1"}])
        args, kw = mocked.call_args
        self.assertEqual(args, ("dev", "Customer", [["name", "=", "C-1"]], ["name"], 5))
        self.assertEqual((kw["requested_by"], kw["channel"], kw["prompt_summary"]),
                         (REQ, "cli", "audit check"))
        self.assertTrue(kw["session_id"])

    def test_get_and_tag_override(self):
        with patch.object(client, "get_resource", return_value={}) as mocked:
            self.run_cli(["get", "Item", "I-1", "--no-strip", "--tag", "qa", "--requested-by", REQ])
        self.assertEqual(mocked.call_args.args, ("qa", "Item", "I-1", False))

    def test_report(self):
        with patch.object(client, "run_query_report", return_value={}) as mocked:
            self.run_cli(["report", "General Ledger", "--filters", '{"company": "A"}',
                          "--requested-by", REQ])
        self.assertEqual(mocked.call_args.args, ("dev", "General Ledger", {"company": "A"}))

    def test_roles(self):
        with patch.object(client, "get_user_roles", return_value=[]) as mocked:
            self.run_cli(["roles", "--user", "u@x.com", "--requested-by", REQ])
        self.assertEqual(mocked.call_args.args, ("dev", "u@x.com"))
        self.assertEqual(mocked.call_args.kwargs["requested_by"], REQ)

    def test_discover(self):
        with patch.object(discover, "preflight", return_value={}) as mocked:
            self.run_cli(["discover", "preflight", "Item", "--payload", '{"item_code": "X"}',
                          "--requested-by", REQ])
        self.assertEqual(mocked.call_args.args, ("dev", "Item"))
        self.assertEqual(mocked.call_args.kwargs["payload"], {"item_code": "X"})

    def test_render(self):
        with patch.object(operations, "prepare_only", return_value={"policy": "always"}) as mocked:
            self.run_cli(["render", "--op", "sales.generic", "--args", '{"doctype": "Sales Order"}',
                          "--requested-by", REQ])
        key, args, ctx = mocked.call_args.args
        self.assertEqual((key, args), ("sales.generic", {"doctype": "Sales Order"}))
        self.assertEqual((ctx.requested_by, ctx.channel, ctx.tag), (REQ, "cli", "dev"))

    def test_execute_write_takes_mode_from_settings(self):
        self.config = {"plugins": {"entries": {"qkeee-erp": {"settings": {"mode": "read-only"}}}}}
        with patch.object(operations, "run_operation", return_value={"_audit_log_status": "ok"}) as mocked:
            self.run_cli(["execute-write", "--op", "sales.generic", "--args", "{}",
                          "--confirmation-token", "tok", "--issued-at", "123",
                          "--user-confirmation-text", "yes AB12", "--latest-prompt", "yes AB12",
                          "--requested-by", REQ])
        ctx = mocked.call_args.args[2]
        self.assertEqual((ctx.mode, ctx.tag, ctx.confirmation_token, ctx.issued_at,
                          ctx.user_confirmation_text), ("read-only", "default", "tok", 123, "yes AB12"))

    def test_execute_write_batch(self):
        steps = [{"op": "sales.generic", "args": {}}]
        with patch.object(operations, "run_batch", return_value={"stopped_at": None, "steps": []}) as mocked:
            code, _, err = self.run_cli(["execute-write", "--batch", json.dumps(steps),
                                          "--latest-prompt", "go", "--requested-by", REQ])
        self.assertEqual(code, 0, err)
        self.assertEqual(mocked.call_args.args[0], steps)

    def test_init_bot(self):
        with patch.object(init_bot, "run_dry_run", return_value={}) as dry, \
                patch.object(init_bot, "run_real", return_value={}) as real:
            self.run_cli(["init-bot", "--dry-run", "--requested-by", REQ])
            self.run_cli(["init-bot", "--confirm-token", "t", "--issued-at", "9", "--requested-by", REQ])
        dry.assert_called_once_with("dev", REQ)
        real.assert_called_once_with("dev", REQ, "t", 9)


class SetupPendingTests(CliTestCase):
    config = {"skills": {"config": {"qkeee_erp": {"active_env": "dev", "mode": "read-write"}}}}

    def test_erp_commands_refuse_until_setup(self):
        with patch.object(client, "query_resource") as mocked, patch.object(client, "_request") as network:
            for argv in (["query", "Customer", "--requested-by", REQ], ["health"]):
                with self.subTest(argv=argv):
                    code, _, err = self.run_cli(argv)
                    self.assertEqual(code, 3)
                    self.assertIn("hermes qkeee-erp setup", err)
        mocked.assert_not_called()
        network.assert_not_called()


class ExitCodeTests(CliTestCase):
    ARGV = ["execute-write", "--op", "sales.generic", "--requested-by", REQ,
            "--args", '{"doctype": "Sales Order", "action": "create", "payload": {}}']

    def test_codes(self):
        cases = [
            (dict(return_value={"_audit_log_status": "ok"}), 0, ""),
            (dict(side_effect=client.DoctypeNotAllowedError("x")), 3, "x"),
            (dict(side_effect=client.TokenMismatchError("x")), 3, "x"),
            (dict(side_effect=client.InvalidArgumentsError("x")), 2, "invalid operation arguments"),
            (dict(side_effect=client.TransportTimeoutError("x")), 4, "outcome unknown"),
            (dict(side_effect=client.PartialOutcomeError("x")), 4, "partial"),
            (dict(side_effect=client.ConnectorError("ERPNext API error (417)")), 1, "417"),
        ]
        for kw, code, text in cases:
            with self.subTest(code=code, text=text):
                with patch.object(operations, "run_operation", **kw):
                    got, _, err = self.run_cli(self.ARGV)
                self.assertEqual(got, code)
                self.assertIn(text, err)

    def test_degraded_audit_is_a_warning(self):
        with patch.object(operations, "run_operation", return_value={"_audit_log_status": "insert_failed"}):
            code, out, err = self.run_cli(self.ARGV)
        self.assertEqual(code, 0)
        self.assertIn("NOT reliably in the audit trail", err)
        self.assertEqual(json.loads(out)["result"]["_audit_log_status"], "insert_failed")

    def test_batch_exit_code_is_the_failing_steps(self):
        report = {"stopped_at": 1, "steps": [{"refused": True, "error": "no"}], "counts": {}}
        with patch.object(operations, "run_batch", return_value=report):
            code, _, _ = self.run_cli(["execute-write", "--batch", '[{"op": "x"}]', "--requested-by", REQ])
        self.assertEqual(code, 3)

    def test_malformed_json_is_a_usage_error(self):
        code, _, err = self.run_cli(["execute-write", "--op", "sales.generic", "--args", "{nope",
                                     "--requested-by", REQ])
        self.assertEqual(code, 2)
        self.assertIn("--args", err)

    def test_missing_operation_arg_is_a_usage_error(self):
        code, _, err = self.run_cli(["render", "--op", "system_admin.create_webhook",
                                     "--args", '{"reason": "r"}', "--requested-by", REQ])
        self.assertEqual(code, 2)
        self.assertIn("missing required argument", err)


class RenderThenExecuteTests(CliTestCase):
    """Real pipeline, real token check; only the network is stubbed. The
    live schema renames keys and the purchase-sourced defaults add keys, so
    this fails if render and execute ever prepare different bytes (W05)."""

    LIVE_FIELDS = [{"fieldname": "item_code", "label": "Item Code"},
                   {"fieldname": "item_name", "label": "Item Name"},
                   {"fieldname": "is_purchase_item", "label": "Is Purchase Item"},
                   {"fieldname": "is_sales_item", "label": "Is Sales Item"}]
    ARGS = {"doctype": "Item", "action": "create", "purchase_sourced_item": True,
            "payload": {"Item Code": "X-1", "Item Name": "Widget"}}

    @contextlib.contextmanager
    def _network(self):
        cfg = {"tag": "dev", "base_url": "https://e.example", "api_key": "k", "api_secret": "s"}
        with patch.object(schema_mapping, "get_doctype_schema", return_value=(self.LIVE_FIELDS, None)), \
                patch.object(client, "get_env_config", return_value=cfg), \
                patch.object(client, "_validate_prod_requester"), \
                patch.object(client, "record_audit_log_start", return_value="LOG"), \
                patch.object(client, "record_audit_log_finish", return_value=True), \
                patch.object(client, "_do_mutate", return_value={"data": {"name": "X-1"}}) as do:
            yield do

    def _render(self, args):
        code, out, err = self.run_cli(["render", "--op", "unscoped.generic", "--args", json.dumps(args),
                                       "--requested-by", REQ])
        self.assertEqual(code, 0, err)
        return json.loads(out)["result"]

    def _execute(self, rendered, reply):
        return self.run_cli(["execute-write", "--op", "unscoped.generic",
                             "--args", json.dumps(rendered["args"]),
                             "--confirmation-token", rendered["confirmation_token"],
                             "--issued-at", str(rendered["issued_at"]),
                             "--user-confirmation-text", reply, "--latest-prompt", reply,
                             "--requested-by", REQ])

    def test_round_trip_succeeds(self):
        with self._network() as do:
            rendered = self._render(self.ARGS)
            self.assertEqual(rendered["request"]["body"],
                             {"item_code": "X-1", "item_name": "Widget",
                              "is_purchase_item": 1, "is_sales_item": 0})
            code, out, err = self._execute(rendered, f"yes {rendered['confirmation_code']}")
        self.assertEqual(code, 0, err)
        self.assertEqual(do.call_args.args[3], rendered["request"]["body"])
        self.assertEqual(json.loads(out)["result"]["_operation"], "unscoped.generic")

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
            code, _, _ = self._execute(rendered, "sure, go ahead")
        self.assertEqual(code, 3)
        do.assert_not_called()

    def test_gated_write_needs_latest_prompt(self):
        with self._network() as do:
            rendered = self._render(self.ARGS)
            code, _, err = self.run_cli(["execute-write", "--op", "unscoped.generic",
                                         "--args", json.dumps(rendered["args"]),
                                         "--confirmation-token", rendered["confirmation_token"],
                                         "--issued-at", str(rendered["issued_at"]),
                                         "--user-confirmation-text", "yes", "--requested-by", REQ])
        self.assertNotEqual(code, 0)
        self.assertIn("latest_prompt", err)
        do.assert_not_called()

    def test_render_refuses_early_for_owned_targets(self):
        args = {"doctype": "User", "action": "create", "payload": {"email": "a@b.c"}}
        with self._network():
            code, _, err = self.run_cli(["render", "--op", "unscoped.generic", "--args", json.dumps(args),
                                         "--requested-by", REQ])
        self.assertEqual(code, 3)
        self.assertIn("system_admin.create_user", err)


if __name__ == "__main__":
    unittest.main()
