"""Render checks the requester's right for the action (HRMS demo run review,
issue 09).

The requester had no submit right on Exit Interview; the gate refused it
only at execute, after the user had confirmed a code. Render now runs the
same requester check, as a dry run (no gate audit row), and shows no code
for a step that cannot run. Execute keeps its own check."""

import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations
import testsupport
from test_operations import connector

SUBMIT = {"doctype": "Exit Interview", "action": "submit", "name": "HR-EXIT-INT-00001"}
GAP = {"capability": "requester:submit", "prompt": "Ask an admin to grant submit."}


def _deny(tag, requested_by, doctype, perm_type, *a, **kw):
    raise core_client.UnvalidatedProdRequesterError(
        f"Refused on '{tag}': {requested_by} holds no role with '{perm_type}' on '{doctype}'.",
        gaps=[GAP])


class RenderRequesterTests(unittest.TestCase):

    def test_submit_render_refused_for_requester_without_submit(self):
        with connector() as m:
            m["_validate_prod_requester"].side_effect = _deny
            with self.assertRaises(core_client.UnvalidatedProdRequesterError) as cm:
                operations.prepare_only("unscoped.generic", dict(SUBMIT), testsupport.ctx())
        self.assertEqual(cm.exception.gaps, [GAP])
        self.assertIn("'submit'", str(cm.exception))
        call = m["_validate_prod_requester"].call_args
        self.assertEqual(call.args[2:4], ("Exit Interview", "submit"))
        self.assertTrue(call.kwargs["dry"])
        self.assertTrue(call.kwargs["for_write"])

    def test_render_succeeds_with_the_right(self):
        with connector() as m:
            out = operations.prepare_only("unscoped.generic", dict(SUBMIT), testsupport.ctx())
        self.assertIn("confirmation_code", out)
        m["_validate_prod_requester"].assert_called_once()

    def test_execute_runs_its_own_check(self):
        with connector() as m:
            full, exec_ctx, _ = testsupport.render("unscoped.generic", dict(SUBMIT))
            m["_validate_prod_requester"].reset_mock()
            operations.run_operation("unscoped.generic", full, exec_ctx)
        calls = m["_validate_prod_requester"].call_args_list
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].kwargs.get("dry", False))


class DryGateTests(unittest.TestCase):

    def test_dry_run_writes_no_gate_audit_row(self):
        with patch.object(core_client, "_log_gate_decision") as log:
            with self.assertRaises(core_client.GateRefusal):
                core_client._validate_prod_requester("test", None, "Exit Interview", "submit",
                                                     for_write=True, dry=True)
            self.assertFalse(log.called)
            with self.assertRaises(core_client.GateRefusal):
                core_client._validate_prod_requester("test", None, "Exit Interview", "submit",
                                                     for_write=True)
            self.assertTrue(log.called)


if __name__ == "__main__":
    unittest.main()
