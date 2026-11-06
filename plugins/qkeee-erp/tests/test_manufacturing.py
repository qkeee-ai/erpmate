#!/usr/bin/env python3
"""Regression tests for manufacturing.py's two enrich hooks (agents
.scratch/qkeee-erp-manufacturing-domain/issues/03, 04): the BOM
default-marking ambiguity refusal and the Work Order stock-availability
note. Run alongside test_allowlist_gates.py (same convention)."""

import unittest
from unittest.mock import patch

from qkeee_erp_plugin.qkeee_erp.core import client as core_client
from qkeee_erp_plugin.qkeee_erp.core import operations

from qkeee_erp_plugin.qkeee_erp.domains import manufacturing
import testsupport

_OFFLINE = testsupport.offline_schema()


def setUpModule():
    for p in _OFFLINE:
        p.start()


def tearDownModule():
    for p in _OFFLINE:
        p.stop()


def _patched_connector(query_result=None):
    """Mirrors test_procurement.py's _patched_connector: allowlist/RBAC/
    audit-log machinery no-op'd, _do_mutate stubbed to return a created
    record. query_resource stubbed separately per test since each one
    needs a different canned result for the BOM-conflict / Bin-qty read."""
    return (
        patch.object(core_client, "get_env_config", return_value={"tag": "test"}),
        patch.object(core_client, "_validate_prod_requester"),
        patch.object(core_client, "record_audit_log_start", return_value="AUDITLOG-TEST"),
        patch.object(core_client, "record_audit_log_finish"),
        patch.object(core_client, "record_comment", return_value=True),
        patch.object(core_client, "query_resource", return_value=query_result or {"data": []}),
        patch.object(core_client, "_do_mutate", return_value={"data": {"name": "TEST-0001"}}),
    )


class BomDefaultAmbiguityTests(unittest.TestCase):
    def test_refuses_when_another_active_default_exists_for_the_item(self):
        patches = _patched_connector(query_result={"data": [{"name": "BOM-DEMO-FG-001-001", "is_default": 1}]})
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6] as mocked_do_mutate:
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.call_generic(
                    f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'BOM', 'create',
                    payload={'item': 'DEMO-FG-001', 'is_default': 1, 'items': []},
                    mode='read-write', requested_by='tester@example.com')
            mocked_do_mutate.assert_not_called()

    def test_proceeds_when_no_conflicting_default_exists(self):
        patches = _patched_connector(query_result={"data": []})
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6] as mocked_do_mutate:
            operations.call_generic(
                f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'BOM', 'create',
                payload={'item': 'DEMO-FG-001', 'is_default': 1, 'items': []},
                mode='read-write', requested_by='tester@example.com')
        mocked_do_mutate.assert_called_once()

    def test_non_default_bom_never_triggers_the_check(self):
        # is_default omitted entirely: the enrich hook must no-op without
        # even querying, same as every other doctype/action it ignores.
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "query_resource") as mocked_query, \
             patches[6] as mocked_do_mutate:
            operations.call_generic(
                f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'BOM', 'create',
                payload={'item': 'DEMO-FG-001', 'items': []},
                mode='read-write', requested_by='tester@example.com')
        mocked_query.assert_not_called()
        mocked_do_mutate.assert_called_once()


class WorkOrderStockAvailabilityTests(unittest.TestCase):
    def test_notes_a_shortfall_without_refusing(self):
        # Bin query returns less than the required_items qty.
        patches = _patched_connector(query_result={"data": [{"item_code": "DEMO-RM-001",
                                                              "warehouse": "Stores - DL",
                                                              "actual_qty": 2}]})
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6] as mocked_do_mutate:
            result = operations.call_generic(
                f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'Work Order', 'create',
                payload={'production_item': 'DEMO-FG-001', 'source_warehouse': 'Stores - DL',
                         'required_items': [{'item_code': 'DEMO-RM-001', 'required_qty': 20}]},
                mode='read-write', requested_by='tester@example.com')
        mocked_do_mutate.assert_called_once()
        self.assertTrue(any('DEMO-RM-001' in n and 'Stores - DL' in n for n in result["_notes"]))

    def test_no_note_when_stock_is_sufficient(self):
        patches = _patched_connector(query_result={"data": [{"item_code": "DEMO-RM-001",
                                                              "warehouse": "Stores - DL",
                                                              "actual_qty": 100}]})
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6] as mocked_do_mutate:
            result = operations.call_generic(
                f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'Work Order', 'create',
                payload={'production_item': 'DEMO-FG-001', 'source_warehouse': 'Stores - DL',
                         'required_items': [{'item_code': 'DEMO-RM-001', 'required_qty': 20}]},
                mode='read-write', requested_by='tester@example.com')
        mocked_do_mutate.assert_called_once()
        self.assertFalse(any('shortfall' in n.lower() for n in result.get("_notes", [])))

    def test_submit_never_triggers_the_check(self):
        # enrich() runs before the finalizing-action preconditions (which
        # refuse first here, missing expected_modified) -- the point of
        # this test is that query_resource is never reached either way,
        # since the hook is scoped to doctype == "Work Order" AND
        # action == "create".
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "query_resource") as mocked_query, \
             patches[6]:
            with self.assertRaises(core_client.PreconditionFailedError):
                operations.call_generic(
                    f'{manufacturing.DOMAIN_NAME}.generic', 'test', 'Work Order', 'submit',
                    name='MFG-WO-2026-00001', mode='read-write', requested_by='tester@example.com')
        mocked_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
