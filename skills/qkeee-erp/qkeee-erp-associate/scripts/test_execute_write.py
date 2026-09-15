#!/usr/bin/env python3
"""Regression tests for execute_write.py (F1, .scratch/hermes-erp-bot-
reliability/spec.md). Bare `import execute_write` matches this repo's
convention for a top-level scripts/ module — pytest inserts the file's
own directory into sys.path when collecting it (no __init__.py there).

Not re-testing mutate_resource()/gated_mutate_resource() themselves —
core/test_client.py covers those. These tests cover what this script adds:
every domain's allowlist actually being registered (the reason this file
exists over the bare `core/client.py mutate --domain` CLI), the loud
missing-context warnings, and the --domain-vs-gated dispatch."""

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import execute_write
from core.client import DOMAIN_WRITE_ALLOWLISTS


def _passthrough_schema_mapping(tag, doctype, payload, **kwargs):
    """Stand-in for schema_mapping.map_payload_for_write() in tests that
    aren't exercising issue 01's mapping behavior itself (see
    SchemaMappingDispatchTests below for those) — returns the payload
    unchanged with status "ok", so tests written before issue 01 landed
    don't need a live/mocked ERPNext schema fetch just to keep dispatching."""
    return {"payload": dict(payload or {}), "status": "ok", "detail": None,
            "suggested_mappings": [], "unmatched": [], "high_risk": []}


def _run_cli(argv):
    """Shared by DispatchTests and PurchaseSourcedItemFlagTests — runs
    execute_write._cli() against a given argv, capturing stdout/stderr
    and the effective exit code (0 if it returned normally)."""
    with patch.object(sys, "argv", ["execute_write.py"] + argv):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                execute_write._cli()
                code = 0
            except SystemExit as e:
                code = e.code
    return code, buf_out.getvalue(), buf_err.getvalue()


class DomainAllowlistsRegisteredOnImportTests(unittest.TestCase):
    """The entire reason this file exists: core/client.py's own bare
    `mutate --domain <slug>` CLI subcommand 404s standalone because no
    domains/*.py module has been imported in that process. Importing
    execute_write must register every one of them."""

    def test_every_known_domain_has_a_registered_allowlist(self):
        for domain in execute_write._KNOWN_DOMAINS:
            with self.subTest(domain=domain):
                self.assertIn(domain, DOMAIN_WRITE_ALLOWLISTS)
                self.assertTrue(DOMAIN_WRITE_ALLOWLISTS[domain] is not None)

    def test_known_domains_match_the_fixed_enum(self):
        # 00-conventions.md's fixed domain-slug enum, minus manufacturing/
        # doc-extraction (no write path — see domains/__init__.py).
        expected = {"accounts", "fixed_assets", "hr_payroll", "inventory",
                    "mis", "procurement", "sales", "system_admin"}
        self.assertEqual(execute_write._KNOWN_DOMAINS, expected)


class PreflightContextWarningTests(unittest.TestCase):
    """Loud, not blocking — see execute_write.py's module docstring. Must
    name exactly what's missing, matching the F1 failure mode (hardcoded
    SID="", no channel_metadata, no latest_prompt)."""

    class _Args:
        def __init__(self, session_id=None, channel_metadata=None, latest_prompt=None):
            self.session_id = session_id
            self.channel_metadata = channel_metadata
            self.latest_prompt = latest_prompt

    def _stderr_from(self, args) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            execute_write._preflight_context_check(args)
        return buf.getvalue()

    def test_all_missing_warns_all_three(self):
        out = self._stderr_from(self._Args())
        self.assertIn("--session-id not given", out)
        self.assertIn("--channel-metadata not given", out)
        self.assertIn("--latest-prompt not given", out)

    def test_all_present_warns_nothing(self):
        out = self._stderr_from(self._Args(
            session_id="sess-1", channel_metadata='{"space": "spaces/x"}',
            latest_prompt="create the supplier from this invoice",
        ))
        self.assertEqual(out, "")

    def test_partial_only_warns_the_missing_ones(self):
        out = self._stderr_from(self._Args(session_id="sess-1"))
        self.assertNotIn("--session-id not given", out)
        self.assertIn("--channel-metadata not given", out)
        self.assertIn("--latest-prompt not given", out)


class DispatchTests(unittest.TestCase):
    """--domain given -> that domain module's OWN mutate() (never
    core.client.mutate_resource() directly — see module docstring, F2's
    Supplier-KYC gate is why); --domain omitted -> gated_mutate_resource(),
    refusing cleanly (not a traceback) without a token."""

    _run = staticmethod(_run_cli)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write.sales, "mutate")
    def test_domain_given_routes_to_that_domains_own_mutate(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "SO-0001"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Quotation", "--action", "create", "--domain", "sales",
            "--payload", '{"customer": "Acme"}',
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this quotation",
        ])
        self.assertEqual(code, 0)
        mock_mutate.assert_called_once()
        self.assertNotIn("domain", mock_mutate.call_args.kwargs)  # sales.mutate() bakes its own domain in
        self.assertIn("SO-0001", out)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write.procurement, "mutate")
    def test_supplier_create_forwards_kyc_flags_to_procurement_mutate(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "Acme Supplies"},
                                     "_audit_log_status": "ok", "_kyc": {"waived": False}}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
            "--payload", '{"supplier_name": "Acme Supplies", "supplier_type": "Company"}',
            "--kyc", '{"address": {"address_line1": "1 Main St", "gstin": "27AAECG2483J1ZE"}}',
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this supplier",
        ])
        self.assertEqual(code, 0)
        mock_mutate.assert_called_once()
        self.assertEqual(mock_mutate.call_args.kwargs.get("kyc"), {"address": {
            "address_line1": "1 Main St", "gstin": "27AAECG2483J1ZE"}})
        self.assertEqual(mock_mutate.call_args.kwargs.get("kyc_waiver_confirmed"), False)

    def test_kyc_flag_rejected_when_not_supplier_create(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Purchase Order", "--action", "create", "--domain", "procurement",
            "--payload", '{}', "--kyc-waiver-confirmed",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("only apply to --domain procurement --doctype Supplier --action create", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    def test_domain_omitted_without_token_refuses_cleanly(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X"}',
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--confirmation-token and --issued-at are required", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    def test_domain_omitted_with_token_but_no_user_confirmation_text_refuses_cleanly(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--user-confirmation-text is required", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write, "gated_mutate_resource")
    def test_domain_omitted_with_token_and_confirmation_text_routes_to_gated_mutate(self, mock_gated):
        mock_gated.return_value = {"data": {"name": "APPLE-IPAD"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "APPLE-IPAD"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this item",
        ])
        self.assertEqual(code, 0)
        mock_gated.assert_called_once()
        self.assertEqual(mock_gated.call_args.kwargs.get("confirmation_token"), "abc123")
        self.assertEqual(mock_gated.call_args.kwargs.get("user_confirmation_text"), "yes ABC123")


class PurchaseSourcedItemFlagTests(unittest.TestCase):
    """--purchase-sourced-item (F6, F9): applies item_write_helpers.
    apply_purchase_sourced_item_defaults() to --payload before the write
    fires, only for --doctype Item --action create."""

    _run = staticmethod(_run_cli)

    def test_flag_rejected_for_non_item_create(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Purchase Order", "--action", "create", "--domain", "procurement",
            "--payload", '{}', "--purchase-sourced-item",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--purchase-sourced-item only applies to --doctype Item --action create", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write, "gated_mutate_resource")
    def test_flag_applies_defaults_before_dispatch(self, mock_gated):
        mock_gated.return_value = {"data": {"name": "APPLE-IPAD-AIR11-128GB"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create", "--purchase-sourced-item",
            "--payload", '{"item_code": "APPLE-IPAD-AIR11-128GB", "item_name": "iPad Air"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
        ])
        self.assertEqual(code, 0)
        mock_gated.assert_called_once()
        sent_payload = mock_gated.call_args.kwargs.get("payload")
        self.assertEqual(sent_payload["is_purchase_item"], 1)
        self.assertEqual(sent_payload["is_sales_item"], 0)
        self.assertEqual(sent_payload["item_name"], "iPad Air")

    def test_bare_standard_rate_refused_before_dispatch(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create", "--purchase-sourced-item",
            "--payload", '{"item_code": "X", "standard_rate": 40677.98}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("Standard SELLING", err)
        self.assertIn("build_standard_buying_item_price_payload", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    def test_flag_omitted_leaves_payload_untouched_standard_rate_and_all(self):
        with patch.object(execute_write, "gated_mutate_resource") as mock_gated:
            mock_gated.return_value = {"data": {"name": "X"}, "_audit_log_status": "ok"}
            self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Item", "--action", "create",
                "--payload", '{"item_code": "X", "standard_rate": 1.0}',
                "--confirmation-token", "abc123", "--issued-at", "1700000000",
                "--user-confirmation-text", "yes ABC123",
                "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
            ])
        sent_payload = mock_gated.call_args.kwargs.get("payload")
        self.assertNotIn("is_purchase_item", sent_payload)
        self.assertEqual(sent_payload["standard_rate"], 1.0)


class AuditStatusSurfacedTests(unittest.TestCase):
    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write.procurement, "mutate")
    def test_non_ok_audit_status_warns(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "X"}, "_audit_log_status": "insert_failed"}
        with patch.object(sys, "argv", ["execute_write.py",
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
                "--payload", '{"supplier_name": "X", "supplier_type": "Company"}',
                "--kyc-waiver-confirmed",
                "--session-id", "s", "--channel-metadata", "{}", "--latest-prompt", "p",
        ]):
            buf_err = io.StringIO()
            with contextlib.redirect_stderr(buf_err), contextlib.redirect_stdout(io.StringIO()):
                execute_write._cli()
        self.assertIn("insert_failed", buf_err.getvalue())
        self.assertIn("NOT reliably in the audit trail", buf_err.getvalue())


class SchemaMappingDispatchTests(unittest.TestCase):
    """issue 01 wiring itself: _apply_schema_mapping() runs for every
    create/update, before dispatch — mocked at schema_mapping.
    map_payload_for_write() (the one function _apply_schema_mapping()
    calls), never at the HTTP layer, matching this file's own convention
    of not re-testing what core/test_client.py or test_schema_mapping.py
    already cover."""

    _run = staticmethod(_run_cli)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write")
    @patch.object(execute_write, "gated_mutate_resource")
    def test_unavailable_status_warns_and_sends_original_payload(self, mock_gated, mock_map):
        mock_map.return_value = {"payload": {"item_code": "X"}, "status": "unavailable",
                                  "detail": "ERPNext API error (403) on GET /api/resource/DocType/Item",
                                  "suggested_mappings": [], "unmatched": [], "high_risk": []}
        mock_gated.return_value = {"data": {"name": "X"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
        ])
        self.assertEqual(code, 0)
        self.assertIn("schema-first field mapping unavailable", err)
        self.assertEqual(mock_gated.call_args.kwargs.get("payload"), {"item_code": "X"})

    @patch.object(execute_write.schema_mapping, "map_payload_for_write")
    @patch.object(execute_write, "gated_mutate_resource")
    def test_suggested_and_unmatched_fields_warn_but_still_dispatch(self, mock_gated, mock_map):
        mock_map.return_value = {
            "payload": {"item_code": "X"}, "status": "ok", "detail": None,
            "suggested_mappings": [{"candidate_key": "HSN", "suggested_fieldname": "gst_hsn_code",
                                     "value": "84713090"}],
            "unmatched": ["warranty_period"], "high_risk": [],
        }
        mock_gated.return_value = {"data": {"name": "X"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X", "HSN": "84713090", "warranty_period": "12 months"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
        ])
        self.assertEqual(code, 0)
        self.assertIn("matched a live schema field only via a synonym hint", err)
        self.assertIn("warranty_period", err)
        mock_gated.assert_called_once()
        self.assertEqual(mock_gated.call_args.kwargs.get("payload"), {"item_code": "X"})

    @patch.object(execute_write.schema_mapping, "map_payload_for_write")
    def test_high_risk_status_refuses_before_dispatch(self, mock_map):
        mock_map.return_value = {"payload": {}, "status": "high_risk", "detail": None,
                                  "suggested_mappings": [], "unmatched": [], "high_risk": ["HSN"]}
        with patch.object(execute_write, "gated_mutate_resource") as mock_gated:
            code, out, err = self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Item", "--action", "create",
                "--staged-fields", '[{"field": "HSN", "value": "84713090", "confidence": "low"}]',
                "--confirmation-token", "abc123", "--issued-at", "1700000000",
                "--user-confirmation-text", "yes ABC123",
                "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
            ])
            mock_gated.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertIn("BOTH low-confidence AND unmatched", err)

    def test_staged_fields_rejected_for_non_create_update_action(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "submit", "--name", "X",
            "--staged-fields", '[{"field": "HSN", "value": "1", "confidence": "high"}]',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("only apply to --action create/update", err)

    @patch.object(execute_write.schema_mapping, "map_payload_for_write")
    def test_staged_fields_and_confirmed_mappings_forwarded(self, mock_map):
        mock_map.return_value = {"payload": {"item_code": "X"}, "status": "ok", "detail": None,
                                  "suggested_mappings": [], "unmatched": [], "high_risk": []}
        with patch.object(execute_write, "gated_mutate_resource") as mock_gated:
            mock_gated.return_value = {"data": {"name": "X"}, "_audit_log_status": "ok"}
            self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Item", "--action", "create",
                "--payload", '{"item_code": "X"}',
                "--staged-fields", '[{"field": "item_code", "value": "X", "confidence": "high"}]',
                "--confirmed-mappings", '{"HSN": "gst_hsn_code"}',
                "--confirmation-token", "abc123", "--issued-at", "1700000000",
                "--user-confirmation-text", "yes ABC123",
                "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
            ])
        self.assertEqual(mock_map.call_args.kwargs.get("staged_fields"),
                          [{"field": "item_code", "value": "X", "confidence": "high"}])
        self.assertEqual(mock_map.call_args.kwargs.get("confirmed_mappings"), {"HSN": "gst_hsn_code"})


class KycSchemaMappingDispatchTests(unittest.TestCase):
    """Issue 02 (.scratch/hermes-erp-bot-reliability/issues/
    02-kyc-subpayload-schema-mapping-gap.md): --kyc's own address/contact
    sub-payloads must get schema-mapped against THEIR live schema
    (Address/Contact), separately from the top-level --payload's Supplier
    mapping — this domain's linked-record write
    (procurement._create_linked_kyc_record()) never gets a second mapping
    pass itself, so whatever execute_write.py hands to procurement.mutate()
    as `kyc` here is exactly what ends up written. Mocked at schema_mapping.
    map_payload_for_write(), same convention as SchemaMappingDispatchTests
    above."""

    _run = staticmethod(_run_cli)

    @patch.object(execute_write.procurement, "mutate")
    def test_kyc_address_and_contact_each_mapped_against_their_own_doctype(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "Acme Supplies"},
                                     "_audit_log_status": "ok", "_kyc": {"waived": False}}
        seen_doctypes = []

        def _fake_map(tag, doctype, payload, **kwargs):
            seen_doctypes.append(doctype)
            mapped = dict(payload)
            unmatched = []
            if doctype == "Address" and "addr_typo" in mapped:
                del mapped["addr_typo"]
                unmatched = ["addr_typo"]
            return {"payload": mapped, "status": "ok", "detail": None,
                    "suggested_mappings": [], "unmatched": unmatched, "high_risk": []}

        with patch.object(execute_write.schema_mapping, "map_payload_for_write", side_effect=_fake_map):
            code, out, err = self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
                "--payload", '{"supplier_name": "Acme Supplies", "supplier_type": "Company"}',
                "--kyc", '{"address": {"address_line1": "1 Main St", "gstin": "27AAECG2483J1ZE", '
                         '"addr_typo": "x"}, "contact": {"first_name": "Jane"}}',
                "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
                "--latest-prompt", "create this supplier",
            ])

        self.assertEqual(code, 0)
        # Supplier's own --payload, plus Address and Contact each mapped
        # separately — not folded into the Supplier call, not skipped.
        self.assertEqual(sorted(seen_doctypes), ["Address", "Contact", "Supplier"])
        sent_kyc = mock_mutate.call_args.kwargs.get("kyc")
        self.assertNotIn("addr_typo", sent_kyc["address"])
        self.assertEqual(sent_kyc["address"]["gstin"], "27AAECG2483J1ZE")
        self.assertEqual(sent_kyc["contact"], {"first_name": "Jane"})
        self.assertIn("addr_typo", err)  # unmatched-field warning fired for Address specifically

    @patch.object(execute_write.schema_mapping, "map_payload_for_write", new=_passthrough_schema_mapping)
    @patch.object(execute_write.procurement, "mutate")
    def test_kyc_waiver_confirmed_skips_kyc_mapping_entirely(self, mock_mutate):
        """No --kyc given (waiver path, F2) — _apply_kyc_schema_mapping()
        must not run at all; there's nothing to map."""
        mock_mutate.return_value = {"data": {"name": "Acme Supplies"},
                                     "_audit_log_status": "ok", "_kyc": {"waived": True}}
        with patch.object(execute_write, "_apply_kyc_schema_mapping") as mock_kyc_map:
            code, out, err = self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
                "--payload", '{"supplier_name": "Acme Supplies", "supplier_type": "Company"}',
                "--kyc-waiver-confirmed",
                "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
                "--latest-prompt", "create this supplier",
            ])
        self.assertEqual(code, 0)
        mock_kyc_map.assert_not_called()

    @patch.object(execute_write.procurement, "mutate")
    def test_kyc_contact_only_never_maps_address(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "Acme Supplies"},
                                     "_audit_log_status": "ok", "_kyc": {"waived": False}}
        calls = []

        def _fake_map(tag, doctype, payload, **kwargs):
            calls.append(doctype)
            return {"payload": dict(payload), "status": "ok", "detail": None,
                    "suggested_mappings": [], "unmatched": [], "high_risk": []}

        with patch.object(execute_write.schema_mapping, "map_payload_for_write", side_effect=_fake_map):
            code, out, err = self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
                "--payload", '{"supplier_name": "Acme Supplies", "supplier_type": "Company"}',
                "--kyc", '{"contact": {"first_name": "Jane"}}',
                "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
                "--latest-prompt", "create this supplier",
            ])
        self.assertEqual(code, 0)
        self.assertNotIn("Address", calls)
        self.assertIn("Contact", calls)


if __name__ == "__main__":
    unittest.main()
