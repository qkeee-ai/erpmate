#!/usr/bin/env python3
"""Regression tests for init_bot.py's log_role_provisioning() — the
manual Qkeee Bot Audit Log entries for Role provisioning, added because
`Role` sits in core.client.AUDIT_EXEMPT_DOCTYPES and is never
auto-logged by the normal write path (see that function's own docstring
for why Role must be created before the Audit Log DocType, and why this
deliberately bypasses the exemption via the raw _audit_insert()/
_audit_submit() primitives rather than _log_read()/
record_audit_log_start(), both of which honor it and would silently
no-op for "Role"). Bare `import init_bot` matches this repo's convention
for a top-level scripts/ module with no __init__.py.

Not re-testing _audit_insert()/_audit_submit() themselves — core/
test_client.py already covers those."""

import time
import unittest
from unittest.mock import patch

import init_bot


class LogRoleProvisioningTests(unittest.TestCase):
    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", return_value="AUDITLOG-READ-0001")
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_role_already_existed_logs_read_only(self, mocked_cfg, mocked_insert, mocked_submit):
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=False,
                                        approval_note="dry-run confirmed")
        mocked_insert.assert_called_once()
        fields = mocked_insert.call_args[0][1]
        self.assertEqual(fields["action"], "Read")
        self.assertEqual(fields["reference_doctype"], "Role")
        self.assertEqual(fields["reference_name"], init_bot.ROLE_NAME)
        self.assertEqual(fields["requested_by"], "admin@org.com")
        self.assertEqual(fields["status"], "Success")
        mocked_submit.assert_called_once_with({"tag": "qa"}, "AUDITLOG-READ-0001")

    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", side_effect=["AUDITLOG-READ-0002", "AUDITLOG-CREATE-0002"])
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_role_created_this_run_logs_read_then_create(self, mocked_cfg, mocked_insert, mocked_submit):
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=True,
                                        approval_note="dry-run confirmed, token abc123...")
        self.assertEqual(mocked_insert.call_count, 2)
        read_fields, create_fields = (c[0][1] for c in mocked_insert.call_args_list)
        self.assertEqual(read_fields["action"], "Read")
        self.assertEqual(create_fields["action"], "Create")
        self.assertEqual(create_fields["user_approved"], "Approved")
        self.assertEqual(create_fields["approval_note"], "dry-run confirmed, token abc123...")
        self.assertEqual(create_fields["reference_name"], init_bot.ROLE_NAME)

    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", return_value=None)
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_insert_failure_never_raises(self, mocked_cfg, mocked_insert, mocked_submit):
        # _audit_insert()'s own contract: None on failure, never raises —
        # this function must stay best-effort too, matching every other
        # audit-logging call site in this skill.
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=True,
                                        approval_note="note")  # must not raise


class RunRealCallsLogRoleProvisioningAfterDoctypesTests(unittest.TestCase):
    """Ordering matters: the Audit Log DocType must exist before a row
    can be written into it, so log_role_provisioning() must run AFTER
    the DocType-creation loop, not before/alongside Role creation."""

    @patch.object(init_bot, "log_role_provisioning")
    @patch.object(init_bot, "ensure_qkeee_env_file_skeleton", return_value=False)
    @patch.object(init_bot, "ensure_doctype", return_value=True)
    @patch.object(init_bot, "ensure_role", return_value=True)
    @patch.object(init_bot.core_client, "health_check", return_value={"status": "ok"})
    @patch.object(init_bot, "compute_plan", return_value={"role_needed": True, "doctypes_needed": ["Qkeee Bot Audit Log"],
                                                       "fields_to_migrate": []})
    def test_log_role_provisioning_called_after_doctype_loop(
            self, mocked_plan, mocked_health, mocked_role, mocked_doctype, mocked_env, mocked_log):
        calls = []
        mocked_doctype.side_effect = lambda *a, **k: calls.append("doctype") or True
        mocked_role.side_effect = lambda *a, **k: calls.append("role") or True
        mocked_log.side_effect = lambda *a, **k: calls.append("log")

        issued_at = int(time.time())
        token = init_bot._init_plan_token("qa", "admin@org.com", True,
                                           ["Qkeee Bot Audit Log"], issued_at=issued_at)
        init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=issued_at)

        self.assertEqual(calls, ["role", "doctype", "log"])
        mocked_log.assert_called_once_with("qa", "admin@org.com", True, unittest.mock.ANY)


class AuditFieldMigrationTests(unittest.TestCase):
    """reference_name moved from Dynamic Link to Data: as a Dynamic Link
    every audited record became undeletable (LinkExistsError, found live on
    DEMO_ERP 2026-10-06). init_bot detects and migrates an existing
    instance; the provisioning operation can only converge the doctype on
    doctype_defs.py."""

    AUDIT = "Qkeee Bot Audit Log"

    def _live(self, reference_type):
        from doctype_defs import ALL_DOCTYPES
        fields = [dict(f, name=f"row-{i}") for i, f in
                  enumerate(next(d for d in ALL_DOCTYPES if d["name"] == self.AUDIT)["fields"])]
        for f in fields:
            if f["fieldname"] == "reference_name":
                f["fieldtype"] = reference_type
        return {"name": self.AUDIT, "fields": fields}

    def test_definition_is_data_not_dynamic_link(self):
        from doctype_defs import ALL_DOCTYPES
        ref = next(f for f in next(d for d in ALL_DOCTYPES if d["name"] == self.AUDIT)["fields"]
                   if f["fieldname"] == "reference_name")
        self.assertEqual(ref["fieldtype"], "Data")

    def test_plan_detects_a_dynamic_link_instance(self):
        with patch.object(init_bot.core_client, "resource_exists", return_value=True),                 patch.object(init_bot, "_live_doctype", return_value=self._live("Dynamic Link")),                 patch.object(init_bot, "_bot_read_grants_needed", return_value=[]):
            plan = init_bot.compute_plan("qa")
        self.assertEqual(plan["fields_to_migrate"],
                         [f"{self.AUDIT}.reference_name: Dynamic Link -> Data"])
        with patch.object(init_bot.core_client, "resource_exists", return_value=True),                 patch.object(init_bot, "_live_doctype", return_value=self._live("Data")),                 patch.object(init_bot, "_bot_read_grants_needed", return_value=[]):
            self.assertEqual(init_bot.compute_plan("qa")["fields_to_migrate"], [])

    def test_plan_token_covers_the_migration(self):
        a = init_bot._init_plan_token("qa", "a@b.c", False, [], issued_at=1, fields_to_migrate=[])
        b = init_bot._init_plan_token("qa", "a@b.c", False, [], issued_at=1, fields_to_migrate=["x"])
        self.assertNotEqual(a, b)

    def test_migration_patches_only_the_migratable_field_through_the_pipeline(self):
        live = self._live("Dynamic Link")
        with patch.object(init_bot, "_live_doctype", return_value=live),                 patch.object(init_bot.operations, "run_operation") as run_op:
            migrated = init_bot.migrate_fields("qa", "admin@org.com", "confirmed")
        self.assertEqual(migrated, [f"{self.AUDIT}.reference_name"])
        key, args, _ctx = run_op.call_args.args
        self.assertEqual(key, "provisioning.migrate_fields")
        sent = {f["fieldname"]: f for f in args["fields"]}
        self.assertEqual(sent["reference_name"]["fieldtype"], "Data")
        self.assertEqual(sent["reference_name"]["name"], live["fields"][
            [f["fieldname"] for f in live["fields"]].index("reference_name")]["name"])
        untouched = [f for f in args["fields"] if f["fieldname"] != "reference_name"]
        self.assertEqual(untouched, [f for f in live["fields"] if f["fieldname"] != "reference_name"])

    def test_migrate_operation_refuses_anything_but_the_definition(self):
        from core import operations
        ctx = operations.WriteContext(tag="qa", mode="read-write", requested_by="a@b.c")
        op = operations.get_operation("provisioning.migrate_fields")
        good = self._live("Data")["fields"]
        op.prepare({"doctype_name": self.AUDIT, "fields": good}, ctx)  # accepted
        for label, fields in (
                ("other type", self._live("Long Text")["fields"]),
                ("missing field", good[1:])):
            with self.subTest(case=label):
                with self.assertRaises(init_bot.core_client.DoctypeNotAllowedError):
                    op.prepare({"doctype_name": self.AUDIT, "fields": fields}, ctx)
        with self.assertRaises(init_bot.core_client.DoctypeNotAllowedError):
            op.prepare({"doctype_name": "User", "fields": good}, ctx)

    def _run_gates(self, fields, live):
        from core import operations
        ctx = operations.WriteContext(tag="qa", mode="read-write", requested_by="a@b.c")
        op = operations.get_operation("provisioning.migrate_fields")
        args = {"doctype_name": self.AUDIT, "fields": fields}
        req = op.prepare(args, ctx)
        with patch.object(init_bot, "_live_doctype", return_value=live):
            for check in op.preconditions:
                check(req, args, ctx)

    def test_migration_keeps_a_live_only_legacy_field_unchanged(self):
        """Found live on DEMO_ERP 2026-10-06: the provisioned audit doctype
        carries a Frappe-added `amended_from` Link that doctype_defs.py
        doesn't define. migrate_fields() sends every live row back, so the
        guard must accept a live-only row sent unchanged — otherwise the
        migration can never run on such an instance."""
        legacy = {"fieldname": "amended_from", "fieldtype": "Link", "options": self.AUDIT,
                  "name": "row-legacy"}
        live = self._live("Dynamic Link")
        live["fields"].append(dict(legacy))
        with patch.object(init_bot, "_live_doctype", return_value=live), \
                patch.object(init_bot.operations, "run_operation") as run_op:
            init_bot.migrate_fields("qa", "admin@org.com", "confirmed")
        _key, args, _ctx = run_op.call_args.args
        self._run_gates(args["fields"], live)  # accepted
        for label, fields in (
                ("retyped legacy", [dict(f, fieldtype="Code") if f["fieldname"] == "amended_from"
                                    else f for f in args["fields"]]),
                ("new extra field", args["fields"] + [{"fieldname": "evil", "fieldtype": "Code"}]),
                ("dropped legacy", [f for f in args["fields"] if f["fieldname"] != "amended_from"])):
            with self.subTest(case=label):
                with self.assertRaises(init_bot.core_client.GateRefusal):
                    self._run_gates(fields, live)


class BotReadGrantTests(unittest.TestCase):
    """Issue 03: the Qkeee Bot role gets read (and only read), permlevel 0,
    on Module Def and Workflow — Environment Metadata the bot itself reads
    (ADR 0001) and the active-workflow check in `discover.py preflight`."""

    ROLE = init_bot.ROLE_NAME

    def _plan(self, perms_by_doctype):
        with patch.object(init_bot.core_client, "resource_exists", return_value=True), \
                patch.object(init_bot, "_live_doctype",
                             return_value=AuditFieldMigrationTests()._live("Data")), \
                patch.object(init_bot, "_live_permissions",
                             side_effect=lambda tag, dt: perms_by_doctype.get(dt, [])):
            return init_bot.compute_plan("qa")

    def test_definitions_are_read_only_on_module_def_and_workflow(self):
        from doctype_defs import BOT_READ_GRANTS
        self.assertEqual(BOT_READ_GRANTS, ("Module Def", "Workflow"))

    def test_plan_lists_both_rows_when_missing(self):
        plan = self._plan({"Module Def": [{"role": "System Manager", "permlevel": 0, "read": 1}]})
        self.assertEqual(plan["bot_read_grants"], ["Module Def: add", "Workflow: add"])

    def test_plan_skips_an_existing_read_row_and_fixes_a_row_without_read(self):
        plan = self._plan({
            "Module Def": [{"role": self.ROLE, "permlevel": 0, "read": 1}],
            "Workflow": [{"role": self.ROLE, "permlevel": 0, "read": 0}],
        })
        self.assertEqual(plan["bot_read_grants"], ["Workflow: set_read"])

    def test_plan_ignores_a_higher_permlevel_row(self):
        plan = self._plan({"Module Def": [{"role": self.ROLE, "permlevel": 1, "read": 1}],
                           "Workflow": [{"role": self.ROLE, "permlevel": 0, "read": 1}]})
        self.assertEqual(plan["bot_read_grants"], ["Module Def: add"])

    def test_dry_run_prints_both_rows(self):
        plan = {"role_needed": False, "doctypes_needed": [], "fields_to_migrate": [],
                "bot_read_grants": ["Module Def: add", "Workflow: add"]}
        with patch.object(init_bot.core_client, "health_check", return_value={}), \
                patch.object(init_bot, "compute_plan", return_value=plan), \
                patch("builtins.print") as out:
            result = init_bot.run_dry_run("qa", "admin@org.com")
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)
        self.assertIn("read on 'Module Def'", printed)
        self.assertIn("read on 'Workflow'", printed)
        self.assertIsNotNone(result["confirm_token"])

    def test_plan_token_covers_the_grants(self):
        a = init_bot._init_plan_token("qa", "a@b.c", False, [], issued_at=1, bot_read_grants=[])
        b = init_bot._init_plan_token("qa", "a@b.c", False, [], issued_at=1,
                                      bot_read_grants=["Workflow: add"])
        self.assertNotEqual(a, b)

    def test_grant_runs_the_permission_manager_add_rpc_with_read_only(self):
        from core import operations
        ctx = operations.WriteContext(tag="qa", mode="read-write", requested_by="a@b.c")
        req = operations.get_operation("provisioning.grant_bot_read").prepare(
            {"doctype": "Module Def", "step": "add"}, ctx)
        self.assertTrue(req.rpc_path.endswith("permission_manager.add"))
        self.assertEqual(req.body, {"parent": "Module Def", "role": self.ROLE, "permlevel": 0})
        req = operations.get_operation("provisioning.grant_bot_read").prepare(
            {"doctype": "Workflow", "step": "set_read"}, ctx)
        self.assertTrue(req.rpc_path.endswith("permission_manager.update"))
        self.assertEqual(req.body, {"doctype": "Workflow", "role": self.ROLE, "permlevel": 0,
                                    "ptype": "read", "value": 1, "if_owner": 0})

    def test_grant_refuses_any_other_doctype_or_step(self):
        from core import operations
        ctx = operations.WriteContext(tag="qa", mode="read-write", requested_by="a@b.c")
        op = operations.get_operation("provisioning.grant_bot_read")
        for args in ({"doctype": "Employee", "step": "add"},
                     {"doctype": "Module Def", "step": "write"},
                     {"doctype": "Module Def", "step": "add", "ptype": "write"}):
            with self.subTest(args=args):
                with self.assertRaises(init_bot.core_client.DoctypeNotAllowedError):
                    op.prepare(args, ctx)
        self.assertFalse(op.cli)

    def test_run_real_grants_after_the_doctypes_exist(self):
        plan = {"role_needed": False, "doctypes_needed": [], "fields_to_migrate": [],
                "bot_read_grants": ["Module Def: add", "Workflow: set_read"]}
        issued_at = int(time.time())
        token = init_bot._init_plan_token("qa", "admin@org.com", False, [], issued_at=issued_at,
                                          bot_read_grants=plan["bot_read_grants"])
        calls = []
        with patch.object(init_bot.core_client, "health_check", return_value={}), \
                patch.object(init_bot, "compute_plan", return_value=plan), \
                patch.object(init_bot, "ensure_role", return_value=False), \
                patch.object(init_bot, "ensure_doctype",
                             side_effect=lambda *a, **k: calls.append("doctype") or False), \
                patch.object(init_bot, "log_role_provisioning"), \
                patch.object(init_bot, "ensure_qkeee_env_file_skeleton", return_value=False), \
                patch.object(init_bot.operations, "run_operation",
                             side_effect=lambda key, args, ctx: calls.append((key, args)) or {}):
            summary = init_bot.run_real("qa", "admin@org.com", token, issued_at)
        self.assertEqual(calls, ["doctype",
                                 ("provisioning.grant_bot_read", {"doctype": "Module Def", "step": "add"}),
                                 ("provisioning.grant_bot_read", {"doctype": "Workflow", "step": "set_read"})])
        self.assertEqual(summary["bot_read_grants"], plan["bot_read_grants"])


if __name__ == "__main__":
    unittest.main()
