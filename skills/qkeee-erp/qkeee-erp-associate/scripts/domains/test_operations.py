#!/usr/bin/env python3
"""The operation pipeline, end to end (write-path hardening P2/P3/P4-18).

- Matrix: EVERY CLI operation in the live registry is driven through
  render -> execute and through each refusal case (read-only, no
  requester, no/stale/tampered token, missing/wrong user code, RBAC
  denial, transport failure). A new operation without an example below
  fails test_every_cli_operation_has_an_example.
- Pipeline order, registry rules, and the invariant that no domain module
  sends a write outside the pipeline.
- Each operation's own rules (ownership, unscoped deny, SSRF guard,
  concurrency, KYC lives in test_procurement.py, depreciation partial
  outcome, credential selection), gated reads, audit PII masking, and the
  transport hardening carried over from P0.
"""

import ast
import contextlib
import copy
import glob
import http.client
import io
import json
import os
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from core import client as core_client
from core import operations
from domains import fixed_assets, inventory, system_admin
import testsupport
from testsupport import REQ

_DOMAINS_DIR = os.path.dirname(os.path.abspath(__file__))

CFG = {"tag": "test", "base_url": "https://erp.example", "api_key": "k", "api_secret": "s",
       "credential": "bot"}

# One live record that satisfies every render/precondition read below.
LIVE = {"name": "X", "modified": "M1", "asset": "A-1",
        "roles": [{"role": "Accounts User"}],
        "finance_books": [{"value_after_depreciation": 500.0}],
        "depreciation_schedule": [
            {"schedule_date": "2026-01-31", "depreciation_amount": 10.0, "journal_entry": None},
            {"schedule_date": "2026-02-28", "depreciation_amount": 10.0, "journal_entry": None},
            {"schedule_date": "2099-01-31", "depreciation_amount": 10.0, "journal_entry": None}]}

MAPPED_INVOICE = {"doctype": "Sales Invoice", "company": "Acme", "customer": None,
                  "items": [{"item_code": "LAPTOP", "asset": "ACC-ASS-2026-00001", "is_fixed_asset": 1,
                             "qty": 1}]}

# How to tamper with each operation's declared example after render: any
# change to what is sent or confirmed must break the token. The examples
# themselves live on the operations (Operation.example_args) — the same
# ones `execute_write.py --list-ops` prints.
TAMPER = {
    "accounts.generic": "name", "sales.generic": "name", "hr_payroll.generic": "name",
    "inventory.generic": "name", "procurement.generic": "name", "fixed_assets.generic": "name",
    "system_admin.generic": "payload", "unscoped.generic": "payload",
    "fixed_assets.depreciation_run": "total_depreciation", "fixed_assets.scrap": "reason",
    "fixed_assets.restore": "reason", "fixed_assets.sell": "reason",
    "system_admin.create_user": "first_name", "system_admin.disable_user": "reason",
    "system_admin.set_user_roles": "reason", "system_admin.delete": "reason",
    "system_admin.create_webhook": "reason", "system_admin.toggle_workflow": "reason",
    "system_admin.permission_add": "reason", "system_admin.permission_update": "reason",
    "system_admin.permission_remove": "reason", "system_admin.permission_reset": "reason",
}
NO_EXAMPLE = {"mis.generic"}  # refuses every write — see OwnershipAndUnscopedTests


def _example(key):
    return copy.deepcopy(operations.get_operation(key).example_args)


def _tamper(args, field):
    args = copy.deepcopy(args)
    if field == "payload":
        args["payload"]["tampered"] = 1
    elif field == "total_depreciation":
        args["total_depreciation"] = (args.get("total_depreciation") or 0) + 1
    else:
        args[field] = f"{args[field]}-tampered"
    return args


@contextlib.contextmanager
def connector(send_error=None):
    """Every network-touching function stubbed. Yields the mocks."""
    m = {}
    with contextlib.ExitStack() as stack:
        for p in testsupport.offline_schema():
            stack.enter_context(p)

        def _p(target, name, **kw):
            m[name] = stack.enter_context(patch.object(target, name, **kw))

        _p(core_client, "get_env_config", side_effect=lambda tag, credential="bot": dict(CFG, credential=credential))
        _p(core_client, "_validate_prod_requester")
        _p(core_client, "record_audit_log_start", return_value="LOG-1")
        _p(core_client, "record_audit_log_finish", return_value=True)
        _p(core_client, "record_comment", return_value=True)
        _p(core_client, "get_resource", return_value={"data": copy.deepcopy(LIVE)})
        _p(core_client, "_do_mutate", return_value={"data": {"name": "NEW-1"}},
           **({"side_effect": send_error} if send_error else {}))
        _p(core_client, "_request", return_value={"message": {}},
           **({"side_effect": send_error} if send_error else {}))
        _p(system_admin, "_role_exists", return_value=True)
        _p(system_admin, "get_permissions",
           return_value=[{"role": "Accounts User", "permlevel": 0, "write": 0}])
        _p(fixed_assets, "map_sales_invoice", return_value=copy.deepcopy(MAPPED_INVOICE))
        yield m


def _sent(m) -> bool:
    return m["_do_mutate"].called or m["_request"].called


class RegistryTests(unittest.TestCase):

    def test_every_cli_operation_has_an_example_and_a_tamper_rule(self):
        for op in operations.list_operations():
            with self.subTest(op=op.key):
                if op.key in NO_EXAMPLE:
                    continue
                self.assertTrue(op.example_args, "declare example_args on the operation")
                self.assertIn(op.key, TAMPER, "add a TAMPER entry for the new operation")

    def test_provisioning_ops_are_not_cli(self):
        import init_bot  # noqa: F401 — registers provisioning.*
        self.assertIn("provisioning.create_role", operations.REGISTRY)
        self.assertNotIn("provisioning.create_role", {op.key for op in operations.list_operations()})

    def test_conflicting_ownership_claim_raises(self):
        op = operations.Operation(key="test.thief", domain=None, summary="", prepare=lambda a, c: None,
                                  owns=frozenset({("User", "create")}))
        with self.assertRaises(ValueError):
            operations.register_operation(op)
        self.assertNotIn("test.thief", operations.REGISTRY)

    def test_reregistering_same_key_is_idempotent(self):
        op = operations.get_operation("system_admin.create_user")
        operations.register_operation(op)
        self.assertEqual(operations.owned_by("User", "create"), "system_admin.create_user")

    def test_unknown_operation_is_refused(self):
        with self.assertRaises(core_client.DoctypeNotAllowedError):
            operations.get_operation("nope.generic")

    def test_every_admin_credential_write_needs_confirmation(self):
        """Nothing that sends with the admin key runs without the rendered
        token + the user's reply code — drafts included."""
        for op in operations.list_operations(cli_only=False):
            if op.credential != "admin" or not op.cli:
                continue
            for action in operations.RESOURCE_ACTIONS:
                with self.subTest(op=op.key, action=action):
                    self.assertTrue(operations.requires_confirmation(op.key, {"action": action}))
                    if op.generic:
                        probe = operations.PreparedRequest(transport="resource", doctype="Role",
                                                           action=action)
                        self.assertEqual(op.policy_for(probe), operations.POLICY_TOKEN_USER_CODE)

    def test_system_admin_generic_create_without_token_is_refused(self):
        with connector() as m:
            with self.assertRaises(core_client.ConfirmationRequiredError):
                operations.run_operation("system_admin.generic",
                                         {"doctype": "Role", "action": "create",
                                          "payload": {"role_name": "R"}}, testsupport.ctx())
            self.assertFalse(_sent(m))
            m["_validate_prod_requester"].assert_not_called()

    def test_admin_credential_only_for_system_admin(self):
        for op in operations.list_operations():
            with self.subTest(op=op.key):
                expected = "admin" if op.domain == "system_admin" else "bot"
                self.assertEqual(op.credential, expected)


class OperationMatrixTests(unittest.TestCase):
    """Ticket 18: every CLI operation x every gate."""

    def _ops(self):
        for op in operations.list_operations():
            if op.key not in NO_EXAMPLE:
                yield op.key, _example(op.key), TAMPER[op.key]

    def _render(self, key, args):
        with connector():
            return testsupport.render(key, copy.deepcopy(args), session_id="S-1", channel="CLI",
                                      channel_metadata={"thread": "T"}, latest_prompt="do it")

    def test_happy_path(self):
        for key, args, _ in self._ops():
            with self.subTest(op=key):
                full_args, ctx, out = self._render(key, args)
                with connector() as m:
                    result = operations.run_operation(key, full_args, ctx)
                self.assertEqual(result["_audit_log_status"], "ok")
                self.assertEqual(result["_operation"], key)
                start = m["record_audit_log_start"].call_args.kwargs
                self.assertIn(start["action"], operations.AUDIT_ACTIONS)
                self.assertEqual(start["session_id"], "S-1")
                self.assertEqual(start["channel_metadata"], {"thread": "T"})
                self.assertEqual(start["latest_prompt"], "do it")
                gated = out["policy"] != operations.POLICY_NONE
                self.assertEqual(start["user_approved"], gated)
                m["_validate_prod_requester"].assert_called_once()
                self.assertTrue(m["_validate_prod_requester"].call_args.kwargs["for_write"])
                op = operations.get_operation(key)
                send_creds = [c.kwargs.get("credential", "bot")
                              for c in m["get_env_config"].call_args_list]
                self.assertIn(op.credential, send_creds)

    def test_read_only_mode_is_refused_before_any_gate_or_send(self):
        for key, args, _ in self._ops():
            with self.subTest(op=key):
                full_args, ctx, _out = self._render(key, args)
                ctx.mode = "read-only"
                with connector() as m:
                    with self.assertRaises(core_client.ReadOnlyModeError):
                        operations.run_operation(key, full_args, ctx)
                    self.assertFalse(_sent(m))
                    m["_validate_prod_requester"].assert_not_called()

    def test_missing_requester_is_refused(self):
        for key, args, _ in self._ops():
            with self.subTest(op=key):
                full_args, ctx, _out = self._render(key, args)
                ctx.requested_by = None
                with connector() as m:
                    with self.assertRaises(core_client.MissingRequesterError):
                        operations.run_operation(key, full_args, ctx)
                    self.assertFalse(_sent(m))

    def test_token_gates(self):
        for key, args, tamper_field in self._ops():
            full_args, ctx, out = self._render(key, args)
            if out["policy"] == operations.POLICY_NONE:
                continue
            cases = {
                "no token": (dict(confirmation_token=None, issued_at=None),
                             core_client.ConfirmationRequiredError, full_args),
                "stale": (dict(issued_at=out["issued_at"] - 10_000),
                          core_client.StaleConfirmationError, full_args),
                "future": (dict(issued_at=out["issued_at"] + 10_000),
                           core_client.StaleConfirmationError, full_args),
                "malformed issued_at": (dict(issued_at="soon"),
                                        core_client.InvalidIssuedAtError, full_args),
                "tampered after render": ({}, core_client.TokenMismatchError,
                                          _tamper(full_args, tamper_field)),
                "no user reply": (dict(user_confirmation_text=None),
                                  core_client.UnconfirmedByUserError, full_args),
                "reply without code": (dict(user_confirmation_text="yes go ahead"),
                                       core_client.UnconfirmedByUserError, full_args),
            }
            for case, (overrides, exc, use_args) in cases.items():
                with self.subTest(op=key, case=case):
                    c = testsupport.ctx(**{**ctx.__dict__, **overrides})
                    with connector() as m:
                        with self.assertRaises(exc):
                            operations.run_operation(key, use_args, c)
                        self.assertFalse(_sent(m))
                        m["_validate_prod_requester"].assert_not_called()
                        m["record_audit_log_start"].assert_not_called()

    def test_token_for_one_requester_does_not_work_for_another(self):
        full_args, ctx, _ = self._render("system_admin.disable_user", _example("system_admin.disable_user"))
        ctx.requested_by = "someone.else@example.com"
        with connector() as m:
            with self.assertRaises(core_client.TokenMismatchError):
                operations.run_operation("system_admin.disable_user", full_args, ctx)
            self.assertFalse(_sent(m))

    def test_rbac_denial_blocks_before_audit_and_send(self):
        for key, args, _ in self._ops():
            with self.subTest(op=key):
                full_args, ctx, _out = self._render(key, args)
                with connector() as m:
                    m["_validate_prod_requester"].side_effect = core_client.UnvalidatedProdRequesterError("no")
                    with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                        operations.run_operation(key, full_args, ctx)
                    self.assertFalse(_sent(m))
                    m["record_audit_log_start"].assert_not_called()

    def test_transport_failure_closes_audit_row_as_failure(self):
        for key, args, _ in self._ops():
            with self.subTest(op=key):
                full_args, ctx, _out = self._render(key, args)
                with connector(send_error=core_client.TransportTimeoutError("t/o")) as m:
                    with self.assertRaises(core_client.ConnectorError):
                        operations.run_operation(key, full_args, ctx)
                    finish = m["record_audit_log_finish"].call_args
                self.assertEqual(finish.kwargs["status"], "Failure")


class PipelineOrderTests(unittest.TestCase):

    def setUp(self):
        self.calls = []
        rec = self.calls.append

        def prepare(args, ctx):
            rec("prepare")
            return operations.PreparedRequest(transport="resource", doctype="Item", action="create",
                                              body={"x": 1})
        self.op = operations.register_operation(operations.Operation(
            key="test.order", domain=None, summary="order probe", prepare=prepare,
            token_policy=operations.POLICY_NONE, allowlist_domain=None,
            preconditions=(lambda req, a, c: rec("precondition"),),
            post=lambda result, req, a, c, hooks: (rec("post"), result)[1], cli=False))
        self.addCleanup(operations.REGISTRY.pop, "test.order", None)

    def test_steps_run_in_the_documented_order(self):
        with connector() as m:
            m["_validate_prod_requester"].side_effect = lambda *a, **k: self.calls.append("rbac")
            m["record_audit_log_start"].side_effect = lambda *a, **k: (self.calls.append("audit_start"), "L")[1]
            m["_do_mutate"].side_effect = lambda *a, **k: (self.calls.append("send"), {"data": {"name": "N"}})[1]
            m["record_audit_log_finish"].side_effect = lambda *a, **k: (self.calls.append("audit_finish"), True)[1]
            result = operations.run_operation("test.order", {}, testsupport.ctx())
        self.assertEqual(self.calls, ["prepare", "precondition", "rbac", "audit_start", "send",
                                      "audit_finish", "post"])
        self.assertEqual(result["_audit_log_status"], "ok")

    def test_non_connector_exception_during_send_is_wrapped_and_audited(self):
        with connector(send_error=KeyError("boom")) as m:
            with self.assertRaises(core_client.ConnectorError):
                operations.run_operation("test.order", {}, testsupport.ctx())
        self.assertEqual(m["record_audit_log_finish"].call_args.kwargs["status"], "Failure")

    def test_render_and_execute_hash_identical_requests(self):
        """W05: mapping/defaults happen in prepare(), so a token rendered
        over the raw args matches at execute time even when mapping
        renames keys and defaults add keys."""
        import schema_mapping
        live_fields = [{"fieldname": "item_code", "label": "Item Code"},
                       {"fieldname": "item_name", "label": "Item Name"},
                       {"fieldname": "is_purchase_item", "label": "Is Purchase Item"},
                       {"fieldname": "is_sales_item", "label": "Is Sales Item"},
                       {"fieldname": "stock_uom", "label": "Default Unit of Measure"}]
        args = {"doctype": "Item", "action": "create", "purchase_sourced_item": True,
                "payload": {"Item Code": "X-1", "Item Name": "Widget", "stock_uom": "Nos"}}
        real_map = _REAL_MAP
        with connector() as m, \
                patch.object(schema_mapping, "map_payload_for_write", new=real_map), \
                patch.object(schema_mapping, "get_doctype_schema", return_value=(live_fields, None)):
            out = operations.prepare_only("unscoped.generic", args, testsupport.ctx())
            self.assertEqual(out["request"]["body"],
                             {"item_code": "X-1", "item_name": "Widget", "stock_uom": "Nos",
                              "is_purchase_item": 1, "is_sales_item": 0})
            ctx = testsupport.ctx(confirmation_token=out["confirmation_token"], issued_at=out["issued_at"],
                                  user_confirmation_text=f"confirm {out['confirmation_code']}")
            result = operations.run_operation("unscoped.generic", out["args"], ctx)
        self.assertEqual(result["_audit_log_status"], "ok")
        self.assertEqual(m["_do_mutate"].call_args.args[3]["item_code"], "X-1")


    def test_cheap_gates_run_before_any_live_read(self):
        """Mode/requester/allowlist/ownership refuse BEFORE enrich() does
        its schema read — a refused write leaves no read behind."""
        import schema_mapping
        cases = [("mis.generic", {"doctype": "GL Entry", "action": "create", "payload": {"a": 1}},
                  {}, core_client.DoctypeNotAllowedError),
                 ("sales.generic", {"doctype": "Sales Order", "action": "create", "payload": {"a": 1}},
                  {"mode": "read-only"}, core_client.ReadOnlyModeError),
                 ("system_admin.generic", {"doctype": "User", "action": "create", "payload": {"a": 1}},
                  {}, core_client.DoctypeNotAllowedError)]
        for key, args, ctx_kw, exc in cases:
            with self.subTest(op=key), connector() as m, \
                    patch.object(schema_mapping, "map_payload_for_write") as mapper:
                with self.assertRaises(exc):
                    operations.run_operation(key, args, testsupport.ctx(**ctx_kw))
                mapper.assert_not_called()
                m["get_resource"].assert_not_called()


class ProvisioningOperationTests(unittest.TestCase):
    """init_bot's two creates: exact records only, admin credential, not
    audited by the pipeline (log_role_provisioning() is the record)."""

    def setUp(self):
        import init_bot
        self.init_bot = init_bot

    def test_only_the_defined_records(self):
        with connector():
            for key, args in (("provisioning.create_role", {"role": "System Manager"}),
                              ("provisioning.create_role", {"role": self.init_bot.ROLE_NAME, "x": 1}),
                              ("provisioning.create_doctype", {"doctype_name": "Server Script"})):
                with self.subTest(key=key, args=args):
                    with self.assertRaises(core_client.DoctypeNotAllowedError):
                        operations.run_operation(key, args, testsupport.ctx())

    def test_create_role_uses_admin_credential_and_requester_gate(self):
        with connector() as m:
            result = operations.run_operation("provisioning.create_role",
                                              {"role": self.init_bot.ROLE_NAME}, testsupport.ctx())
        self.assertEqual(m["_do_mutate"].call_args.args[0]["credential"], "admin")
        m["_validate_prod_requester"].assert_called_once()
        m["record_audit_log_start"].assert_not_called()
        self.assertEqual(result["_audit_log_status"], "exempt")


import schema_mapping as _sm  # noqa: E402
_REAL_MAP = _sm.map_payload_for_write


class InvariantTests(unittest.TestCase):
    """No domain module sends a write outside the pipeline."""

    # Domain modules never touch the transport at all: writes go through
    # operations, reads through client.read_rpc/query_resource/get_resource.
    FORBIDDEN_CALLS = {"_do_mutate", "_request", "mutate_resource", "gated_mutate_resource"}

    def test_domain_modules_send_no_write_outside_the_pipeline(self):
        for path in sorted(glob.glob(os.path.join(_DOMAINS_DIR, "*.py"))):
            if os.path.basename(path).startswith("test_"):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                with self.subTest(file=os.path.basename(path), line=node.lineno):
                    self.assertNotIn(name, self.FORBIDDEN_CALLS)

    # Every public function a domain module exposes, by kind. A new public
    # function must be added here deliberately — and is then exercised by
    # the gated-read tests (reads) or the operation matrix (writes go
    # through operations, never a public function of their own).
    PUBLIC_CALLABLES = {
        "accounts": {},
        "sales": {},
        "hr_payroll": {},
        "mis": {},
        "procurement": {"prepare": "operation hook",
                        "enrich": "operation hook", "check_kyc": "operation hook",
                        "create_linked_kyc": "operation hook"},
        "inventory": {"get_stock_reconciliation_items": "gated read",
                      "get_bin_qty": "gated read", "bin_rows_to_actual_source_qty": "pure"},
        "fixed_assets": {"map_sales_invoice": "gated read"},
        "system_admin": {"get_roles_and_doctypes": "gated read",
                         "get_permissions": "gated read", "get_scheduler_status": "gated read"},
    }

    def test_every_public_callable_is_accounted_for(self):
        import importlib
        import inspect
        for module_name, expected in self.PUBLIC_CALLABLES.items():
            module = importlib.import_module(f"domains.{module_name}")
            public = {n for n, obj in vars(module).items()
                      if inspect.isfunction(obj) and obj.__module__ == module.__name__
                      and not n.startswith("_")}
            with self.subTest(module=module_name):
                self.assertEqual(public, set(expected))


    def test_list_ops_matches_registry(self):
        import execute_write
        listed = {o["op"] for o in execute_write._list_ops()}
        self.assertEqual(listed, {op.key for op in operations.list_operations()})


class OwnershipAndUnscopedTests(unittest.TestCase):

    def _run(self, key, args):
        with connector() as m:
            with self.assertRaises(core_client.DoctypeNotAllowedError) as ctx:
                operations.run_operation(key, args, testsupport.ctx())
            self.assertFalse(_sent(m))
        return str(ctx.exception)

    def test_system_admin_generic_refuses_every_owned_pair(self):
        for (doctype, action), owner in sorted(operations._OWNERS.items()):
            with self.subTest(doctype=doctype, action=action):
                args = {"doctype": doctype, "action": action, "name": "N", "payload": {"x": 1}}
                if action in operations.FINALIZING_ACTIONS:
                    args.pop("payload")
                self.assertIn(owner, self._run("system_admin.generic", args))

    def test_system_admin_generic_refuses_unsupported_pairs(self):
        self.assertIn("UI-level guidance", self._run(
            "system_admin.generic", {"doctype": "Webhook", "action": "update", "name": "W", "payload": {}}))
        self.assertIn("UI-level guidance", self._run(
            "system_admin.generic", {"doctype": "Workflow", "action": "create", "payload": {}}))

    def test_unscoped_refuses_domain_doctypes(self):
        msg = self._run("unscoped.generic", {"doctype": "Supplier", "action": "create",
                                             "payload": {"supplier_name": "A"}})
        self.assertIn("procurement.generic", msg)  # the KYC gate can't be bypassed

    def test_unscoped_refuses_privilege_and_code_doctypes(self):
        for doctype in ("Server Script", "Custom DocPerm", "Has Role", "System Settings"):
            with self.subTest(doctype=doctype):
                self.assertIn("privilege/system", self._run(
                    "unscoped.generic", {"doctype": doctype, "action": "create", "payload": {}}))

    def test_unscoped_refuses_gated_operations_targets(self):
        self.assertIn("system_admin.create_user", self._run(
            "unscoped.generic", {"doctype": "User", "action": "create", "payload": {}}))

    def test_delete_without_token_is_refused_in_every_writer_domain(self):
        for domain, doctype in (("accounts", "Journal Entry"), ("sales", "Sales Order"),
                                ("hr_payroll", "Leave Application"), ("inventory", "Stock Entry"),
                                ("procurement", "Purchase Order"), ("fixed_assets", "Asset")):
            # (system_admin.generic refuses deletes outright — system_admin.delete owns them.)
            with self.subTest(domain=domain), connector() as m:
                with self.assertRaises(core_client.ConfirmationRequiredError):
                    operations.run_operation(f"{domain}.generic",
                                             {"doctype": doctype, "action": "delete", "name": "X",
                                              "expected_modified": "M1"}, testsupport.ctx())
                self.assertFalse(_sent(m))

    def test_mis_refuses_everything(self):
        with connector():
            with self.assertRaises(core_client.DoctypeNotAllowedError):
                operations.run_operation("mis.generic", {"doctype": "GL Entry", "action": "create",
                                                         "payload": {}}, testsupport.ctx())

    def test_client_exposes_no_write_entry_point(self):
        """Every write goes through core/operations.py. client.py is the
        lower layer: no mutate_resource()/gated_mutate_resource(), and it
        never imports operations (no dependency cycle)."""
        for name in ("mutate_resource", "gated_mutate_resource", "_operations"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(core_client, name))
        tree = ast.parse(open(core_client.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {f"{node.module}.{a.name}" for a in node.names} | {node.module or ""}
        self.assertFalse({i for i in imported if "operations" in i}, "client.py must not import operations")

    def test_domain_modules_expose_no_mutate_wrapper(self):
        import importlib
        for name in ("accounts", "sales", "hr_payroll", "inventory", "mis", "procurement",
                     "fixed_assets", "system_admin"):
            with self.subTest(module=name):
                self.assertFalse(hasattr(importlib.import_module(f"domains.{name}"), "mutate"))


class SystemAdminRuleTests(unittest.TestCase):

    def test_webhook_ssrf_guard(self):
        for url in ("http://hooks.example.com/x", "https://localhost/x", "https://10.0.0.5/x",
                    "https://169.254.169.254/latest", "https://intranet/x", "https://erp.local/x",
                    "https://[::1]/x", ""):
            with self.subTest(url=url):
                with self.assertRaises(core_client.PreconditionFailedError):
                    operations.check_public_https_url(url)
        operations.check_public_https_url("https://hooks.example.com/x")

    def test_create_webhook_requires_a_name(self):
        """Frappe v16 Webhook is prompt-named: a create without `name` fails
        live with "Please set the document name" (DEMO_ERP 2026-10-06)."""
        op = operations.get_operation("system_admin.create_webhook")
        args = _example("system_admin.create_webhook")
        req = op.prepare(args, testsupport.ctx())
        self.assertEqual(req.body["name"], args["name"])
        args.pop("name")
        with self.assertRaises(core_client.ConnectorError):
            op.prepare(args, testsupport.ctx())

    def test_create_user_refuses_unknown_role(self):
        args, ctx, _ = testsupport.render("system_admin.create_user",
                                          {"email": "n@x.com", "first_name": "N", "roles": ["Nope"]})
        with connector() as m:
            m["_role_exists"] = None
            with patch.object(system_admin, "_role_exists", return_value=False):
                with self.assertRaises(core_client.PreconditionFailedError):
                    operations.run_operation("system_admin.create_user", args, ctx)

    def test_create_user_render_flags_elevated_roles(self):
        with connector():
            out = operations.prepare_only("system_admin.create_user",
                                          {"email": "n@x.com", "first_name": "N",
                                           "roles": ["System Manager", "Accounts User"]},
                                          testsupport.ctx())
        self.assertEqual(out["request"]["confirmed_facts"]["elevated_roles"], ["System Manager"])

    def test_set_user_roles_refused_if_roles_changed_since_render(self):
        with connector():
            args, ctx, _ = testsupport.render("system_admin.set_user_roles",
                                              _example("system_admin.set_user_roles"))
        self.assertEqual(args["roles_before"], ["Accounts User"])
        changed = dict(LIVE, roles=[{"role": "Accounts User"}, {"role": "System Manager"}])
        with connector() as m:
            m["get_resource"].return_value = {"data": changed}
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.run_operation("system_admin.set_user_roles", args, ctx)
            self.assertFalse(_sent(m))

    def test_disable_user_sends_exactly_enabled_0(self):
        with connector():
            args, ctx, _ = testsupport.render("system_admin.disable_user",
                                              _example("system_admin.disable_user"))
        with connector() as m:
            operations.run_operation("system_admin.disable_user", args, ctx)
        self.assertEqual(m["_do_mutate"].call_args.args[3], {"enabled": 0})

    def test_delete_posts_no_comment_and_refuses_other_doctypes(self):
        with connector():
            args, ctx, _ = testsupport.render("system_admin.delete", _example("system_admin.delete"))
        with connector() as m:
            operations.run_operation("system_admin.delete", args, ctx)
        self.assertTrue(m["_do_mutate"].call_args.kwargs["skip_comment"])
        with connector():
            with self.assertRaises(core_client.DoctypeNotAllowedError):
                operations.prepare_only("system_admin.delete",
                                        {"doctype": "Sales Invoice", "name": "S", "reason": "r"},
                                        testsupport.ctx())

    def test_permission_ops_audit_against_the_target_doctype(self):
        for action, audit_action in (("add", "Create"), ("update", "Update"),
                                     ("remove", "Delete"), ("reset", "Delete")):
            key = f"system_admin.permission_{action}"
            with self.subTest(op=key):
                with connector():
                    args, ctx, _ = testsupport.render(key, _example(key))
                with connector() as m:
                    operations.run_operation(key, args, ctx)
                start = m["record_audit_log_start"].call_args.kwargs
                self.assertEqual((start["action"], start["doctype"], start["name"]),
                                 (audit_action, "DocType", "Supplier"))
                m["record_comment"].assert_not_called()

    def test_permission_update_render_records_current_value(self):
        with connector():
            out = operations.prepare_only("system_admin.permission_update",
                                          _example("system_admin.permission_update"),
                                          testsupport.ctx())
        self.assertEqual(out["args"]["current_value"], 0)

    def test_admin_send_but_bot_audit(self):
        with connector():
            args, ctx, _ = testsupport.render("system_admin.toggle_workflow",
                                              _example("system_admin.toggle_workflow"))
        with connector() as m:
            operations.run_operation("system_admin.toggle_workflow", args, ctx)
        self.assertEqual(m["_do_mutate"].call_args.args[0]["credential"], "admin")
        self.assertEqual(m["record_audit_log_start"].call_args.args[0]["credential"], "bot")


class FixedAssetRuleTests(unittest.TestCase):

    def test_depreciation_render_shows_due_rows_only(self):
        with connector():
            out = operations.prepare_only("fixed_assets.depreciation_run",
                                          {"depr_schedule_name": "ADS-1", "date": "2026-03-01"},
                                          testsupport.ctx())
        self.assertEqual((out["args"]["pending_rows"], out["args"]["total_depreciation"]), (2, 20.0))
        self.assertEqual(out["request"]["body"], {"depr_schedule_name": "ADS-1", "date": "2026-03-01"})

    def test_rpc_audit_rows_reference_the_asset(self):
        for key in ("fixed_assets.depreciation_run", "fixed_assets.scrap", "fixed_assets.restore"):
            with self.subTest(op=key):
                with connector():
                    args, ctx, _ = testsupport.render(key, _example(key))
                with connector() as m:
                    operations.run_operation(key, args, ctx)
                start = m["record_audit_log_start"].call_args.kwargs
                self.assertEqual((start["doctype"], start["name"]), ("Asset", "A-1" if key.endswith("run")
                                                                     else _example(key)["asset"]))
                self.assertEqual(m["record_audit_log_finish"].call_args.kwargs["reference_name"],
                                 start["name"])

    def test_finalizing_action_on_a_missing_record_is_a_gate_refusal(self):
        """Live-found (DEMO_ERP smoke): a 404 in the unchanged-since-render
        read must surface as a refusal (exit 3), not an ERPNext error."""
        with connector() as m:
            m["get_resource"].side_effect = core_client.ConnectorError(
                "ERPNext API error (404) on GET /api/resource/Journal Entry/X: not found")
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.run_operation("accounts.generic",
                                         {"doctype": "Journal Entry", "action": "delete", "name": "X",
                                          "expected_modified": "M1"}, testsupport.ctx())
            self.assertFalse(_sent(m))

    def test_depreciation_slice_indices_are_refused(self):
        with connector():
            with self.assertRaises(core_client.ConnectorError):
                operations.prepare_only("fixed_assets.depreciation_run",
                                        {"depr_schedule_name": "ADS-1", "sch_start_idx": 0,
                                         "sch_end_idx": 3}, testsupport.ctx())

    def test_depreciation_partial_failure_reports_posted_rows(self):
        with connector():
            args, ctx, _ = testsupport.render("fixed_assets.depreciation_run",
                                              {"depr_schedule_name": "ADS-1", "date": "2026-03-01"})
        posted = copy.deepcopy(LIVE)
        posted["depreciation_schedule"][0]["journal_entry"] = "JE-DEP-1"
        with connector(send_error=core_client.ConnectorError("ERPNext API error (417): row 2")) as m:
            m["get_resource"].return_value = {"data": posted}
            with self.assertRaises(core_client.PartialOutcomeError) as cm:
                operations.run_operation("fixed_assets.depreciation_run", args, ctx)
        self.assertIn("JE-DEP-1", str(cm.exception))
        self.assertIn("PARTIALLY", m["record_audit_log_finish"].call_args.kwargs["error_detail"])

    def test_depreciation_refused_if_schedule_belongs_to_another_asset(self):
        with connector():
            args, ctx, _ = testsupport.render("fixed_assets.depreciation_run",
                                              {"depr_schedule_name": "ADS-1"})
        with connector() as m:
            m["get_resource"].return_value = {"data": dict(LIVE, asset="A-2")}
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.run_operation("fixed_assets.depreciation_run", args, ctx)
            self.assertFalse(_sent(m))

    def test_scrap_requires_explicit_date_and_sends_it(self):
        with connector():
            with self.assertRaises(core_client.ConnectorError):
                operations.prepare_only("fixed_assets.scrap", {"asset": "A-1", "reason": "x"},
                                        testsupport.ctx())
            out = operations.prepare_only("fixed_assets.scrap", _example("fixed_assets.scrap"),
                                          testsupport.ctx())
        example = _example("fixed_assets.scrap")
        self.assertEqual(out["request"]["body"], {"asset_name": example["asset"],
                                                  "scrap_date": example["scrap_date"]})
        self.assertEqual(out["request"]["confirmed_facts"]["book_value"], 500.0)

    def test_sell_creates_a_draft_invoice_checked_against_accounts(self):
        with connector():
            args, ctx, _ = testsupport.render("fixed_assets.sell", _example("fixed_assets.sell"))
        self.assertEqual(args["invoice"]["items"][0]["asset"], "ACC-ASS-2026-00001")
        with connector() as m:
            operations.run_operation("fixed_assets.sell", args, ctx)
        self.assertEqual(m["_do_mutate"].call_args.args[1:3], ("Sales Invoice", "create"))
        bad = dict(args, invoice={"items": [{"item_code": "LAPTOP", "asset": "A-9", "is_fixed_asset": 1}]})
        with connector():
            with self.assertRaises(core_client.ConnectorError):
                operations.run_operation("fixed_assets.sell", bad, ctx)

    def test_sell_sets_customer_and_applies_sale_proceeds(self):
        """ERPNext's mapper leaves `customer` blank and the asset row at
        rate 0: live, every sell failed (MandatoryError: customer), and the
        confirmed `sale_proceeds` never reached the invoice (DEMO_ERP
        2026-10-06)."""
        op = operations.get_operation("fixed_assets.sell")
        example = _example("fixed_assets.sell")
        args = dict(example, sell_qty=2, sale_proceeds=30000,
                    invoice={"doctype": "Sales Invoice", "items": [
                        {"item_code": "LAPTOP", "asset": example["asset"], "is_fixed_asset": 1,
                         "qty": 2, "rate": 0}]})
        req = op.prepare(args, testsupport.ctx())
        self.assertEqual(req.body["customer"], example["customer"])
        self.assertEqual(req.body["items"][0]["rate"], 15000)
        for missing in ("customer", "sale_proceeds"):
            with self.subTest(missing=missing):
                bad = dict(args)
                bad.pop(missing)
                with self.assertRaises(core_client.ConnectorError):
                    op.prepare(bad, testsupport.ctx())

    def test_map_sales_invoice_is_a_gated_read_and_strips_server_keys(self):
        mapped = {"doctype": "Sales Invoice", "name": "new-sales-invoice-1", "__islocal": 1,
                  "docstatus": 0, "items": [{"name": "row-1", "parent": "x", "asset": "A-1"}]}
        with patch.object(core_client, "read_rpc", return_value={"message": mapped}) as rr:
            out = fixed_assets.map_sales_invoice("t", "A-1", "LAPTOP", "Acme", 1, requested_by=REQ)
        self.assertEqual(rr.call_args.kwargs["gate_doctype"], "Sales Invoice")
        self.assertEqual(out, {"doctype": "Sales Invoice", "items": [{"asset": "A-1"}]})


class GatedReadTests(unittest.TestCase):

    def test_read_rpc_gate_denial_sends_nothing(self):
        with patch.object(core_client, "_validate_prod_requester",
                          side_effect=core_client.UnvalidatedProdRequesterError("no")), \
                patch.object(core_client, "_request") as req:
            with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                core_client.read_rpc("t", "GET", "/x", gate_doctype="Stock Reconciliation",
                                     requested_by=REQ)
            req.assert_not_called()

    def test_read_rpc_logs_and_can_use_admin_credential(self):
        with patch.object(core_client, "_validate_prod_requester"), \
                patch.object(core_client, "get_env_config",
                             side_effect=lambda tag, credential="bot": dict(CFG, credential=credential)), \
                patch.object(core_client, "_request", return_value={"message": [1]}) as req, \
                patch.object(core_client, "_log_read") as log:
            core_client.read_rpc("t", "GET", "/x", gate_doctype="Custom DocPerm",
                                 requested_by=REQ, credential="admin")
        self.assertEqual(req.call_args.args[0]["credential"], "admin")
        self.assertEqual(log.call_args.args[0]["credential"], "bot")

    def test_inventory_reads_are_gated(self):
        with patch.object(core_client, "query_resource",
                          return_value={"data": [{"item_code": "I", "warehouse": "W", "actual_qty": 3}],
                                        "has_more": False}) as q:
            out = inventory.get_bin_qty("t", "I", "W", requested_by=REQ, session_id="S")
        self.assertEqual(q.call_args.kwargs["requested_by"], REQ)
        self.assertEqual(q.call_args.kwargs["session_id"], "S")
        self.assertEqual(inventory.bin_rows_to_actual_source_qty(out["data"]), {("I", "W"): 3})
        with patch.object(core_client, "read_rpc", return_value={"message": [{"batch_no": "B1"}]}) as rr:
            res = inventory.get_stock_reconciliation_items("t", "W", "Acme", "2026-10-01",
                                                           requested_by=REQ)
        self.assertEqual(rr.call_args.kwargs["gate_doctype"], "Stock Reconciliation")
        self.assertTrue(res["batch_tracked"])

    def test_system_admin_reads_are_gated_with_admin_credential(self):
        for fn, args in ((system_admin.get_roles_and_doctypes, ()),
                         (system_admin.get_permissions, ("Supplier",)),
                         (system_admin.get_scheduler_status, ())):
            with self.subTest(fn=fn.__name__), \
                    patch.object(core_client, "read_rpc", return_value={"message": {}}) as rr:
                fn("t", *args, requested_by=REQ)
                self.assertEqual(rr.call_args.kwargs["credential"], "admin")
                self.assertEqual(rr.call_args.kwargs["requested_by"], REQ)


class AuditRedactionTests(unittest.TestCase):

    def test_payloads_and_diff_are_masked(self):
        before = {"employee_name": "A", "bank_ac_no": "123456789", "notes": "ssn 123-45-6789"}
        after = {"employee_name": "A", "bank_ac_no": "999999999", "notes": "ok"}
        with patch.object(core_client, "_audit_insert", return_value="LOG") as ins:
            core_client.record_audit_log_start({"tag": "t"}, action="Update", doctype="Employee",
                                               name="E-1", requested_by=REQ, payload_before=before)
        stored = json.loads(ins.call_args.args[1]["payload_before"])
        self.assertEqual(stored["bank_ac_no"], "***masked***")
        self.assertNotIn("123-45-6789", ins.call_args.args[1]["payload_before"])
        with patch.object(core_client, "_audit_update", return_value=True) as upd, \
                patch.object(core_client, "_audit_submit"):
            core_client.record_audit_log_finish({"tag": "t"}, "LOG", status="Success",
                                                payload_before=before, payload_after=after)
        fields = upd.call_args.args[2]
        self.assertNotIn("999999999", fields["payload_after"])
        diff = json.loads(fields["field_diff"])
        bank = [d for d in diff if d["fieldname"] == "bank_ac_no"]
        self.assertEqual(bank, [{"fieldname": "bank_ac_no", "old": "***changed***", "new": "***changed***"}])

    def test_mask_list_extends_from_env(self):
        with patch.object(core_client, "_qkeee_env", return_value={"QKEEE_ERP_AUDIT_MASK_FIELDS": "salary"}):
            self.assertEqual(core_client._redact_audit_payload({"salary": 10})["salary"], "***masked***")


# ---------------------------------------------------------------------------
# Carried over from P0: requester gate for writes, transport hardening
# ---------------------------------------------------------------------------

class WriteGateExemptionTests(unittest.TestCase):

    def test_user_role_doctype_writes_are_not_exempt(self):
        for doctype in ("User", "Role", "DocType"):
            with self.subTest(doctype=doctype), \
                    patch.object(core_client, "_log_gate_decision"), \
                    patch.object(core_client, "resource_exists", return_value=False):
                with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                    core_client._validate_prod_requester("test", "not-a-user@x", doctype, "create",
                                                          for_write=True)

    def test_internal_reads_of_user_stay_exempt_for_recursion(self):
        # Only the gate's own plumbing (internal=True) skips the gate for
        # User/Role — that is what breaks the resource_exists() recursion.
        for doctype in ("User", "Role"):
            with self.subTest(doctype=doctype), patch.object(core_client, "resource_exists") as exists:
                core_client._validate_prod_requester("test", "anyone", doctype, "read", internal=True)
                exists.assert_not_called()

    def test_business_reads_of_user_and_role_are_gated(self):
        # Regression (dev-erp, 2026-10-07): a plain User-list read skipped
        # the gate entirely and leaked the user directory.
        for doctype in ("User", "Role"):
            with self.subTest(doctype=doctype),                     patch.object(core_client, "resource_exists", return_value=False):
                with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                    core_client._validate_prod_requester("test", "not-a-user@x", doctype, "read")
                with self.assertRaises(core_client.UnvalidatedProdRequesterError):
                    core_client._validate_prod_requester("test", None, doctype, "read")

    def test_bookkeeping_writes_stay_exempt(self):
        for doctype in (core_client.AUDIT_LOG_DOCTYPE, "Comment"):
            with self.subTest(doctype=doctype), patch.object(core_client, "resource_exists") as exists:
                core_client._validate_prod_requester("test", "anyone", doctype, "create", for_write=True)
                exists.assert_not_called()


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RequestTransportHardeningTests(unittest.TestCase):

    def test_read_timeout_on_write_is_transport_timeout_with_unknown_outcome(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(core_client.TransportTimeoutError) as ctx:
                core_client._request(CFG, "POST", "/api/resource/Item", payload={})
        self.assertIn("UNKNOWN", str(ctx.exception))

    def test_connect_timeout_wrapped_in_urlerror(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(TimeoutError())):
            with self.assertRaises(core_client.TransportTimeoutError):
                core_client._request(CFG, "GET", "/api/resource/Item")

    def test_html_body_is_malformed_response(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(b"<html>challenge</html>")):
            with self.assertRaises(core_client.MalformedResponseError):
                core_client._request(CFG, "GET", "/api/resource/Item")

    def test_remote_disconnect_is_connector_error(self):
        with patch("urllib.request.urlopen", side_effect=http.client.RemoteDisconnected("closed")):
            with self.assertRaises(core_client.ConnectorError):
                core_client._request(CFG, "POST", "/api/resource/Item", payload={})

    def test_valid_json_still_parses(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(b'{"data": 1}')):
            self.assertEqual(core_client._request(CFG, "GET", "/x"), {"data": 1})

    def test_timeout_during_write_marks_audit_outcome_unknown(self):
        with connector(send_error=core_client.TransportTimeoutError("t/o")) as m:
            with self.assertRaises(core_client.TransportTimeoutError):
                operations.run_operation("sales.generic",
                                         {"doctype": "Sales Order", "action": "create", "payload": {"customer": "C"}},
                                         testsupport.ctx())
        self.assertIn("outcome_unknown=True", m["record_audit_log_finish"].call_args.kwargs["error_detail"])

    def test_timeout_during_depreciation_stays_a_timeout_with_partial_detail(self):
        with connector():
            args, ctx, _ = testsupport.render("fixed_assets.depreciation_run",
                                              {"depr_schedule_name": "ADS-1", "date": "2026-03-01"})
        with connector(send_error=core_client.TransportTimeoutError("t/o")) as m:
            with self.assertRaises(core_client.TransportTimeoutError) as cm:
                operations.run_operation("fixed_assets.depreciation_run", args, ctx)
        self.assertIn("PARTIALLY", str(cm.exception))
        self.assertIn("outcome_unknown=True", m["record_audit_log_finish"].call_args.kwargs["error_detail"])

    def test_coerce_issued_at(self):
        self.assertEqual(core_client.coerce_issued_at("123"), 123)
        for bad in ("abc", None, True, [1]):
            with self.subTest(bad=bad), self.assertRaises(core_client.InvalidIssuedAtError):
                core_client.coerce_issued_at(bad)

    def test_admin_credential_env_vars(self):
        env = {"QKEEE_ERP_T_BASE_URL": "https://e.example", "QKEEE_ERP_T_API_KEY": "bk",
               "QKEEE_ERP_T_API_SECRET": "bs", "QKEEE_ERP_T_ADMIN_API_KEY": "ak",
               "QKEEE_ERP_T_ADMIN_API_SECRET": "as"}
        with patch.object(core_client, "_qkeee_env", return_value=env):
            self.assertEqual(core_client.get_env_config("t")["api_key"], "bk")
            self.assertEqual(core_client.get_env_config("t", credential="admin")["api_key"], "ak")
        with patch.object(core_client, "_qkeee_env", return_value={k: v for k, v in env.items() if "ADMIN" not in k}):
            with self.assertRaises(core_client.ConnectorError) as ctx:
                core_client.get_env_config("t", credential="admin")
        self.assertIn("QKEEE_ERP_T_ADMIN_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
