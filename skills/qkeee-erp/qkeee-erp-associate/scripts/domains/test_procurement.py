#!/usr/bin/env python3
"""Regression tests for procurement.py's Supplier-KYC gate (F2, .scratch/
hermes-erp-bot-reliability/spec.md). Run alongside test_allowlist_gates.py
(same directory/import convention: `cd scripts/domains && python -m
pytest -q`, or the whole suite via `python -m pytest scripts` from the
skill root).

Live-observed failure this gate exists to close: a Supplier was created
with GSTIN retried as a Supplier field (wrong doctype — belongs on
Address, linked via Dynamic Link) and no Address/Contact created at all,
despite procurement.md's own non-negotiable that this domain's KYC bar
is stricter than ERPNext's own. That was prompt discipline alone; this
file tests the code-level backstop."""

import os
import sys
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_THIS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core import client as core_client  # noqa: E402
from core import operations  # noqa: E402

import procurement  # noqa: E402
import schema_mapping  # noqa: E402
import testsupport  # noqa: E402

_OFFLINE = testsupport.offline_schema()


def setUpModule():
    for p in _OFFLINE:
        p.start()


def tearDownModule():
    for p in _OFFLINE:
        p.stop()


def _patched_connector(created_name="GNR Solution Private Limited"):
    """Common patch set: allowlist/RBAC/audit-log machinery no-op'd,
    _do_mutate stubbed to return a Supplier-shaped create response. Mirrors
    test_allowlist_gates.py's AllowedDoctypeClearsTheGateTests pattern."""
    return (
        patch.object(core_client, "get_env_config", return_value={"tag": "test"}),
        patch.object(core_client, "_validate_prod_requester"),
        patch.object(core_client, "record_audit_log_start", return_value="AUDITLOG-TEST"),
        patch.object(core_client, "record_audit_log_finish"),
        patch.object(core_client, "record_comment", return_value=True),
        patch.object(core_client, "_do_mutate", return_value={"data": {"name": created_name}}),
    )


class SupplierCreateRequiresKycOrWaiverTests(unittest.TestCase):
    def test_neither_kyc_nor_waiver_refuses_before_any_write(self):
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mocked_do_mutate:
            with self.assertRaises(procurement.IncompleteSupplierKYCError):
                operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com')
            mocked_do_mutate.assert_not_called()

    def test_kyc_without_address_key_still_refuses(self):
        # A kyc dict with only e.g. {"contact": {...}} and no "address" is
        # still incomplete per this domain's own bar (tax ID lives on
        # Address) — must not be treated as satisfying the gate.
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mocked_do_mutate:
            with self.assertRaises(procurement.IncompleteSupplierKYCError):
                operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': {'contact': {'first_name': 'Priya'}}})
            mocked_do_mutate.assert_not_called()


class SupplierCreateWithWaiverTests(unittest.TestCase):
    def test_waiver_proceeds_and_is_marked_on_the_result(self):
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mocked_do_mutate:
            result = operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc_waiver_confirmed': True})
        mocked_do_mutate.assert_called_once()
        self.assertTrue(result["_kyc"]["waived"])
        self.assertIsNone(result["_kyc"]["address"])


class SupplierCreateWithKycTests(unittest.TestCase):
    def test_kyc_address_creates_linked_address_with_dynamic_link(self):
        patches = _patched_connector(created_name="Acme")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate") as mocked_do_mutate:
            mocked_do_mutate.side_effect = [
                {"data": {"name": "Acme"}},  # Supplier create
                {"data": {"name": "ADDR-0001"}},  # Address create
            ]
            result = operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': {'address': {'address_line1': '1 Main St', 'gstin': '27AAECG2483J1ZE'}}})
        self.assertEqual(mocked_do_mutate.call_count, 2)
        address_payload = mocked_do_mutate.call_args_list[1].kwargs.get("payload") \
            or mocked_do_mutate.call_args_list[1].args[3]
        self.assertEqual(address_payload["gstin"], "27AAECG2483J1ZE")
        self.assertIn({"link_doctype": "Supplier", "link_name": "Acme"}, address_payload["links"])
        self.assertFalse(result["_kyc"]["waived"])
        self.assertEqual(result["_kyc"]["address"]["data"]["name"], "ADDR-0001")

    def test_kyc_address_and_contact_creates_both(self):
        patches = _patched_connector(created_name="Acme")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate") as mocked_do_mutate:
            mocked_do_mutate.side_effect = [
                {"data": {"name": "Acme"}},
                {"data": {"name": "ADDR-0001"}},
                {"data": {"name": "CONT-0001"}},
            ]
            result = operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': {'address': {'gstin': '27AAECG2483J1ZE'}, 'contact': {'first_name': 'Priya'}}})
        self.assertEqual(mocked_do_mutate.call_count, 3)
        self.assertEqual(result["_kyc"]["contact"]["data"]["name"], "CONT-0001")

    def test_existing_links_on_kyc_payload_are_preserved_not_overwritten(self):
        patches = _patched_connector(created_name="Acme")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate") as mocked_do_mutate:
            mocked_do_mutate.side_effect = [
                {"data": {"name": "Acme"}},
                {"data": {"name": "ADDR-0001"}},
            ]
            operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': {'address': {'gstin': 'X', 'links': [{'link_doctype': 'Company', 'link_name': 'DEMO LLP'}]}}})
        address_payload = mocked_do_mutate.call_args_list[1].kwargs.get("payload") \
            or mocked_do_mutate.call_args_list[1].args[3]
        self.assertEqual(len(address_payload["links"]), 2)
        self.assertIn({"link_doctype": "Company", "link_name": "DEMO LLP"}, address_payload["links"])
        self.assertIn({"link_doctype": "Supplier", "link_name": "Acme"}, address_payload["links"])


class KycTaxIdAndPrevalidationTests(unittest.TestCase):
    """Ticket 13 / D2 (C): refused BEFORE the Supplier create when the
    address carries no tax ID, or when Address/Contact miss a mandatory
    field per live meta."""

    def _create(self, kyc, waiver=False):
        return operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': kyc, 'kyc_waiver_confirmed': waiver})

    def test_address_without_tax_id_is_refused(self):
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as do_mutate:
            with self.assertRaises(procurement.IncompleteSupplierKYCError) as ctx:
                self._create({"address": {"address_line1": "1 Main St"}})
            do_mutate.assert_not_called()
        self.assertIn("tax ID", str(ctx.exception))

    def test_each_default_tax_id_field_satisfies_it(self):
        for field in procurement.KYC_TAX_ID_FIELDS:
            with self.subTest(field=field):
                patches = _patched_connector(created_name="Acme")
                with patches[0], patches[1], patches[2], patches[3], patches[4], \
                     patch.object(core_client, "_do_mutate",
                                  side_effect=[{"data": {"name": "Acme"}},
                                               {"data": {"name": "ADDR-1"}}]) as do_mutate:
                    self._create({"address": {field: "X123"}})
                self.assertEqual(do_mutate.call_count, 2)

    def test_env_override_names_the_tax_id_field(self):
        env = {"QKEEE_ERP_TEST_KYC_TAX_ID_FIELD": "vat_no"}
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as do_mutate, \
             patch.object(core_client, "_qkeee_env", return_value=env):
            with self.assertRaises(procurement.IncompleteSupplierKYCError):
                self._create({"address": {"gstin": "27AAECG2483J1ZE"}})
            do_mutate.assert_not_called()

    # Live Address meta of a core ERPNext instance (no India Compliance):
    # no tax-ID field at all (DEMO_ERP, 2026-10-06).
    _CORE_ADDRESS_META = [{"fieldname": f, "fieldtype": "Data", "reqd": 0}
                          for f in ("address_title", "address_line1", "city", "country")]

    def _create_on_core_instance(self, supplier_payload, kyc, side_effect):
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(operations, "_schema_map", side_effect=lambda ctx, dt, p, notes, **kw: dict(p)), \
             patch("schema_mapping.get_doctype_schema", return_value=(self._CORE_ADDRESS_META, None)), \
             patch.object(core_client, "_do_mutate", side_effect=side_effect) as do_mutate:
            try:
                return operations.call_generic(
                    f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create',
                    payload=supplier_payload, mode='read-write', requested_by='tester@example.com',
                    extra_args={'kyc': kyc}), do_mutate
            except procurement.IncompleteSupplierKYCError as e:
                return e, do_mutate

    def test_core_instance_without_address_tax_field_takes_supplier_tax_id(self):
        """Without India Compliance, Address has no tax-ID field, so the
        rule "tax ID on the Address" could never be met and every KYC
        Supplier create was refused (DEMO_ERP 2026-10-06). There, the
        Supplier's own core `tax_id` field carries it."""
        result, do_mutate = self._create_on_core_instance(
            {"supplier_name": "Acme", "supplier_type": "Company", "tax_id": "27AAPFU0939F1ZV"},
            {"address": {"address_line1": "1 Main St", "city": "Pune", "country": "India"}},
            [{"data": {"name": "Acme"}}, {"data": {"name": "ADDR-1"}}])
        self.assertEqual(do_mutate.call_count, 2)
        self.assertEqual(result["_kyc"]["address"]["data"]["name"], "ADDR-1")

    def test_core_instance_refuses_without_supplier_tax_id_and_says_where(self):
        err, do_mutate = self._create_on_core_instance(
            {"supplier_name": "Acme", "supplier_type": "Company"},
            {"address": {"address_line1": "1 Main St", "gstin": "27AAPFU0939F1ZV"}},
            [{"data": {"name": "Acme"}}])
        self.assertIsInstance(err, procurement.IncompleteSupplierKYCError)
        self.assertIn("tax_id", str(err))
        self.assertIn("Supplier", str(err))
        do_mutate.assert_not_called()

    def test_waiver_allows_address_without_tax_id(self):
        patches = _patched_connector(created_name="Acme")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate",
                          side_effect=[{"data": {"name": "Acme"}},
                                       {"data": {"name": "ADDR-1"}}]):
            result = self._create({"address": {"address_line1": "1 Main St"}}, waiver=True)
        self.assertFalse(result["_kyc"]["waived"])  # an address WAS captured
        self.assertEqual(result["_kyc"]["address"]["data"]["name"], "ADDR-1")

    def test_missing_mandatory_address_field_is_refused_before_any_write(self):
        meta = [{"fieldname": "city", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "gstin", "fieldtype": "Data", "reqd": 0},
                {"fieldname": "address_line1", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "links", "fieldtype": "Table", "reqd": 1},
                {"fieldname": "country", "fieldtype": "Link", "reqd": 1, "default": "India"}]
        patches = _patched_connector()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as do_mutate, \
             patch.object(schema_mapping, "get_doctype_schema", return_value=(meta, None)):
            with self.assertRaises(procurement.IncompleteSupplierKYCError) as ctx:
                self._create({"address": {"gstin": "X", "address_line1": "1 Main St"}})
            do_mutate.assert_not_called()
        self.assertIn("['city']", str(ctx.exception))


class KycRollbackTests(unittest.TestCase):
    """Ticket 13 / D2 (A): a server-side failure of the linked create
    deletes the just-created Supplier again; if that fails too, the error
    names what was left behind."""

    def _create(self):
        return operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'create', payload={'supplier_name': 'Acme', 'supplier_type': 'Company'}, mode='read-write', requested_by='tester@example.com', extra_args={'kyc': {'address': {'gstin': 'X'}, 'contact': {'first_name': 'Priya'}}})

    def test_address_failure_deletes_the_supplier_again(self):
        patches = _patched_connector()
        effects = [{"data": {"name": "Acme"}},
                   core_client.ConnectorError("ERPNext API error (417): city is mandatory"),
                   {}]
        with patches[0], patches[1], patches[2] as audit_start, patches[3] as audit_finish, patches[4], \
             patch.object(core_client, "_do_mutate", side_effect=effects) as do_mutate:
            with self.assertRaises(procurement.KycLinkFailedError) as ctx:
                self._create()
        # Three audited writes: Supplier create, failed Address create, compensating delete.
        actions = [(c.kwargs["action"], c.kwargs["doctype"]) for c in audit_start.call_args_list]
        self.assertEqual(actions, [("Create", "Supplier"), ("Create", "Address"), ("Delete", "Supplier")])
        self.assertIn("compensating delete", audit_start.call_args_list[2].kwargs["approval_note"])
        statuses = [c.kwargs["status"] for c in audit_finish.call_args_list]
        self.assertEqual(statuses, ["Success", "Failure", "Success"])
        delete_call = do_mutate.call_args_list[2]
        self.assertEqual(delete_call.args[1:3], ("Supplier", "delete"))
        self.assertEqual(delete_call.args[4], "Acme")
        self.assertIn("nothing is left behind", str(ctx.exception))

    def test_contact_failure_after_address_still_rolls_back(self):
        patches = _patched_connector()
        effects = [{"data": {"name": "Acme"}}, {"data": {"name": "ADDR-1"}},
                   core_client.ConnectorError("contact invalid"), {}]
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate", side_effect=effects) as do_mutate:
            with self.assertRaises(procurement.KycLinkFailedError):
                self._create()
        self.assertEqual(do_mutate.call_args_list[3].args[1:3], ("Supplier", "delete"))

    def test_rollback_failure_names_what_was_left_behind(self):
        patches = _patched_connector()
        effects = [{"data": {"name": "Acme"}},
                   core_client.ConnectorError("address invalid"),
                   core_client.ConnectorError("LinkExistsError")]
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(core_client, "_do_mutate", side_effect=effects):
            with self.assertRaises(procurement.KycPartialFailureError) as ctx:
                self._create()
        self.assertIn("'Acme'", str(ctx.exception))
        self.assertIsInstance(ctx.exception, core_client.PartialOutcomeError)


class KycMappingRunsAtRenderAndExecuteTests(unittest.TestCase):
    """The KYC Address is mapped against Address's live schema in
    enrich(), so render shows (and the token covers) the mapped fields."""

    def test_render_shows_mapped_kyc(self):
        from core import operations

        def fake_map(tag, doctype, payload, **kw):
            mapped = {("gstin" if k == "GSTIN" else k): v for k, v in (payload or {}).items()}
            return {"payload": mapped, "status": "ok", "detail": None,
                    "suggested_mappings": [], "unmatched": [], "high_risk": []}
        with patch.object(schema_mapping, "map_payload_for_write", new=fake_map):
            out = operations.prepare_only(
                "procurement.generic",
                {"doctype": "Supplier", "action": "create", "payload": {"supplier_name": "Acme"},
                 "kyc": {"address": {"GSTIN": "27AAECG2483J1ZE"}}},
                operations.WriteContext(tag="test", requested_by="tester@example.com"))
        self.assertEqual(out["request"]["confirmed_facts"]["kyc"]["address"], {"gstin": "27AAECG2483J1ZE"})


class SupplierUpdateIsNotGatedTests(unittest.TestCase):
    """Onboarding-time bar only — see mutate()'s own docstring for why
    'update' is deliberately excluded."""

    def test_update_proceeds_without_kyc(self):
        patches = _patched_connector(created_name="Acme")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mocked_do_mutate, \
             patch.object(core_client, "get_resource", return_value={"data": None}):
            operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Supplier', 'update', name='Acme', payload={'email_id': 'accounts@acme.example'}, mode='read-write', requested_by='tester@example.com')
        mocked_do_mutate.assert_called_once()


class AddressAndContactAreAllowlistedTests(unittest.TestCase):
    def test_address_and_contact_in_allowed_write_doctypes(self):
        self.assertIn("Address", procurement.ALLOWED_WRITE_DOCTYPES)
        self.assertIn("Contact", procurement.ALLOWED_WRITE_DOCTYPES)

    def test_address_create_works_standalone_not_only_via_supplier_kyc(self):
        # Backfilling KYC onto an already-onboarded supplier — see
        # mutate()'s docstring's "update is not gated" note.
        patches = _patched_connector(created_name="ADDR-0002")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as mocked_do_mutate:
            operations.call_generic(f'{procurement.DOMAIN_NAME}.generic', 'test', 'Address', 'create', payload={'gstin': '27AAECG2483J1ZE', 'links': [{'link_doctype': 'Supplier', 'link_name': 'Acme'}]}, mode='read-write', requested_by='tester@example.com')
        mocked_do_mutate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
