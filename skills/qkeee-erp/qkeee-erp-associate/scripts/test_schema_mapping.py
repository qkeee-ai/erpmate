#!/usr/bin/env python3
"""Regression tests for schema_mapping.py (issue 01, .scratch/hermes-erp-
bot-reliability/issues/01-schema-first-attribute-mapping.md — folds in
F4). Bare `import schema_mapping` matches this repo's convention for a
top-level scripts/ module with no __init__.py.

Pure-function tests (match_fields/match_staged_report/
apply_confirmed_mappings) need no mocking at all. The I/O-layer tests
(get_doctype_schema/map_payload_for_write) mock discover.doctype_meta()
directly — never real network — same pattern test_discover.py already
uses for get_resource()."""

import unittest
from unittest.mock import patch

import schema_mapping
from core.client import ConnectorError

_SUPPLIER_FIELDS = [
    {"fieldname": "supplier_name", "label": "Supplier Name", "fieldtype": "Data"},
    {"fieldname": "supplier_type", "label": "Supplier Type", "fieldtype": "Select"},
]

_ADDRESS_FIELDS = [
    {"fieldname": "address_line1", "label": "Address Line 1", "fieldtype": "Data"},
    {"fieldname": "gstin", "label": "GSTIN/UIN", "fieldtype": "Data"},
]

_ITEM_FIELDS = [
    {"fieldname": "item_code", "label": "Item Code", "fieldtype": "Data"},
    {"fieldname": "gst_hsn_code", "label": "HSN/SAC", "fieldtype": "Data"},
]


class MatchFieldsTests(unittest.TestCase):
    def test_exact_fieldname_match_is_unaffected(self):
        result = schema_mapping.match_fields(_SUPPLIER_FIELDS, {"supplier_name": "Acme"})
        self.assertEqual(result["mapped"], {"supplier_name": "Acme"})
        self.assertEqual(result["matched_via"]["supplier_name"], "exact")
        self.assertEqual(result["suggested_mappings"], [])
        self.assertEqual(result["unmatched"], [])

    def test_normalized_fieldname_match(self):
        result = schema_mapping.match_fields(_ADDRESS_FIELDS, {"Address-Line1": "1 Main St"})
        self.assertEqual(result["mapped"], {"address_line1": "1 Main St"})
        self.assertEqual(result["matched_via"]["address_line1"], "normalized")

    def test_normalized_label_match(self):
        result = schema_mapping.match_fields(_ADDRESS_FIELDS, {"GSTIN/UIN": "27AAECG2483J1ZE"})
        self.assertEqual(result["mapped"], {"gstin": "27AAECG2483J1ZE"})
        self.assertEqual(result["matched_via"]["gstin"], "normalized")

    def test_synonym_hint_is_suggested_not_auto_applied(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {"HSN": "84713090"})
        self.assertEqual(result["mapped"], {})
        self.assertEqual(result["suggested_mappings"],
                          [{"candidate_key": "HSN", "suggested_fieldname": "gst_hsn_code",
                            "value": "84713090"}])
        self.assertEqual(result["unmatched"], [])

    def test_completely_unknown_key_is_unmatched(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {"warranty_period": "12 months"})
        self.assertEqual(result["mapped"], {})
        self.assertEqual(result["suggested_mappings"], [])
        self.assertEqual(result["unmatched"], ["warranty_period"])

    def test_mixed_payload_sorts_into_all_three_buckets(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {
            "item_code": "APPLE-IPAD", "HSN": "84713090", "warranty_period": "12 months",
        })
        self.assertEqual(result["mapped"], {"item_code": "APPLE-IPAD"})
        self.assertEqual(len(result["suggested_mappings"]), 1)
        self.assertEqual(result["unmatched"], ["warranty_period"])


class ApplyConfirmedMappingsTests(unittest.TestCase):
    def test_confirmed_suggestion_moves_into_mapped(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {"HSN": "84713090"})
        confirmed = schema_mapping.apply_confirmed_mappings(result, {"HSN": "gst_hsn_code"})
        self.assertEqual(confirmed["mapped"], {"gst_hsn_code": "84713090"})
        self.assertEqual(confirmed["suggested_mappings"], [])

    def test_mismatched_confirmation_target_is_refused_not_substituted(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {"HSN": "84713090"})
        confirmed = schema_mapping.apply_confirmed_mappings(result, {"HSN": "item_code"})
        self.assertEqual(confirmed["mapped"], {})
        self.assertEqual(len(confirmed["suggested_mappings"]), 1)

    def test_original_result_never_mutated(self):
        result = schema_mapping.match_fields(_ITEM_FIELDS, {"HSN": "84713090"})
        schema_mapping.apply_confirmed_mappings(result, {"HSN": "gst_hsn_code"})
        self.assertEqual(result["mapped"], {})
        self.assertEqual(len(result["suggested_mappings"]), 1)


class MatchStagedReportTests(unittest.TestCase):
    """F4 fold-in — doc-extraction's confidence-rated shape."""

    def test_missing_confidence_key_refuses(self):
        with self.assertRaises(schema_mapping.MalformedStagedReportError):
            schema_mapping.match_staged_report(_ITEM_FIELDS, [{"field": "item_code", "value": "X"}])

    def test_missing_value_key_refuses(self):
        with self.assertRaises(schema_mapping.MalformedStagedReportError):
            schema_mapping.match_staged_report(
                _ITEM_FIELDS, [{"field": "item_code", "confidence": "high"}])

    def test_null_value_is_not_a_missing_key(self):
        # value: null is doc-extraction's "field not in the document at all"
        # signal (doc-extraction.md step 5) -- a real, present key, just
        # excluded from the candidate dict since there's nothing to map.
        result = schema_mapping.match_staged_report(
            _ITEM_FIELDS, [{"field": "item_code", "value": None, "confidence": "low"}])
        self.assertEqual(result["mapped"], {})
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["high_risk"], [])

    def test_high_confidence_unmatched_field_is_unmatched_not_high_risk(self):
        result = schema_mapping.match_staged_report(
            _ITEM_FIELDS, [{"field": "warranty_period", "value": "12 months", "confidence": "high"}])
        self.assertEqual(result["unmatched"], ["warranty_period"])
        self.assertEqual(result["high_risk"], [])

    def test_low_confidence_matched_field_is_neither_unmatched_nor_high_risk(self):
        result = schema_mapping.match_staged_report(
            _ITEM_FIELDS, [{"field": "item_code", "value": "APPLE-IPAD", "confidence": "low"}])
        self.assertEqual(result["mapped"], {"item_code": "APPLE-IPAD"})
        self.assertEqual(result["high_risk"], [])

    def test_low_confidence_and_unmatched_is_high_risk_never_mapped_or_suggested(self):
        result = schema_mapping.match_staged_report(
            _ITEM_FIELDS, [{"field": "HSN", "value": "84713090", "confidence": "low"}])
        self.assertEqual(result["high_risk"], ["HSN"])
        self.assertEqual(result["mapped"], {})
        self.assertEqual(result["suggested_mappings"], [])  # not even offered as a suggestion
        self.assertEqual(result["unmatched"], [])  # not double-counted as plain "unmatched" either

    def test_row_and_confidence_carried_through(self):
        result = schema_mapping.match_staged_report(_ITEM_FIELDS, [
            {"field": "item_code", "value": "X", "confidence": "high", "row": "items[0]"},
        ])
        self.assertEqual(result["confidence_by_field"]["item_code"], "high")
        self.assertEqual(result["row_by_field"]["item_code"], "items[0]")


class GetDoctypeSchemaCacheTests(unittest.TestCase):
    def setUp(self):
        schema_mapping._SCHEMA_CACHE.clear()

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_meta_without_custom_fields_counts_as_unavailable(self, mock_meta):
        # W39: a base-DocType fallback hides Custom Fields; mapping against
        # it would drop e.g. gstin as "unmatched". Treat it as no schema
        # (payload passes through unmapped) instead.
        mock_meta.return_value = {"fields": _ITEM_FIELDS, "custom_fields_merged": False,
                                  "custom_fields_error": "ERPNext API error (403)"}
        fields, err = schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        self.assertIsNone(fields)
        self.assertIn("custom fields", err)

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_successful_fetch_is_cached_across_calls(self, mock_meta):
        mock_meta.return_value = {"fields": _ITEM_FIELDS}
        fields1, err1 = schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        fields2, err2 = schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        self.assertEqual(fields1, _ITEM_FIELDS)
        self.assertIsNone(err1)
        self.assertEqual(fields2, _ITEM_FIELDS)
        mock_meta.assert_called_once()

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_fetch_failure_is_cached_not_retried(self, mock_meta):
        mock_meta.side_effect = ConnectorError("ERPNext API error (403) on GET /api/resource/DocType/Item")
        fields1, err1 = schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        fields2, err2 = schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        self.assertIsNone(fields1)
        self.assertIn("403", err1)
        self.assertIsNone(fields2)
        self.assertIn("403", err2)
        mock_meta.assert_called_once()

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_cache_is_keyed_by_tag_and_doctype(self, mock_meta):
        mock_meta.return_value = {"fields": _ITEM_FIELDS}
        schema_mapping.get_doctype_schema("DEMO_ERP", "Item", requested_by="u@org.com")
        schema_mapping.get_doctype_schema("PROD_ERP", "Item", requested_by="u@org.com")
        schema_mapping.get_doctype_schema("DEMO_ERP", "Supplier", requested_by="u@org.com")
        self.assertEqual(mock_meta.call_count, 3)


class MapPayloadForWriteTests(unittest.TestCase):
    def setUp(self):
        schema_mapping._SCHEMA_CACHE.clear()

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_schema_fetch_failure_degrades_to_passthrough(self, mock_meta):
        mock_meta.side_effect = ConnectorError("ERPNext API error (403) on GET /api/resource/DocType/Item")
        result = schema_mapping.map_payload_for_write(
            "DEMO_ERP", "Item", {"item_code": "X", "HSN": "84713090"}, requested_by="u@org.com")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["payload"], {"item_code": "X", "HSN": "84713090"})
        self.assertIn("403", result["detail"])

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_plain_payload_matches_and_drops_unmatched(self, mock_meta):
        mock_meta.return_value = {"fields": _ITEM_FIELDS}
        result = schema_mapping.map_payload_for_write(
            "DEMO_ERP", "Item", {"item_code": "X", "warranty_period": "12 months"},
            requested_by="u@org.com")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["payload"], {"item_code": "X"})
        self.assertEqual(result["unmatched"], ["warranty_period"])

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_confirmed_mappings_fold_synonym_suggestion_into_payload(self, mock_meta):
        mock_meta.return_value = {"fields": _ITEM_FIELDS}
        result = schema_mapping.map_payload_for_write(
            "DEMO_ERP", "Item", {"item_code": "X", "HSN": "84713090"}, requested_by="u@org.com",
            confirmed_mappings={"HSN": "gst_hsn_code"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["payload"], {"item_code": "X", "gst_hsn_code": "84713090"})
        self.assertEqual(result["suggested_mappings"], [])

    @patch.object(schema_mapping.discover, "doctype_meta")
    def test_staged_fields_high_risk_status(self, mock_meta):
        mock_meta.return_value = {"fields": _ITEM_FIELDS}
        result = schema_mapping.map_payload_for_write(
            "DEMO_ERP", "Item", {}, requested_by="u@org.com",
            staged_fields=[{"field": "HSN", "value": "84713090", "confidence": "low"}])
        self.assertEqual(result["status"], "high_risk")
        self.assertEqual(result["high_risk"], ["HSN"])
        self.assertEqual(result["payload"], {})


if __name__ == "__main__":
    unittest.main()
