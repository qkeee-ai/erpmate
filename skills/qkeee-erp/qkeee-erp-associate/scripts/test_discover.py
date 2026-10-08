#!/usr/bin/env python3
"""Regression tests for discover.py — the resurrected schema-discovery
script (see discover.py's own module docstring and
.scratch/hermes-erp-bot-reliability/spec.md finding F8 for why it exists
again). Bare `import discover` matches this repo's convention for a
top-level scripts/ module with no __init__.py — pytest inserts the file's
own directory (scripts/) into sys.path when collecting it.

Not re-testing get_resource()/query_resource() themselves — core/
test_client.py already covers those. These tests cover the logic
discover.py adds on top: DocField-avoidance (meta goes through
get_resource(DocType, ...), never a DocField query), the _META_FIELD_KEYS
filter, and resolve_doctype()'s null-vs-error distinction for `app`.
"""

import unittest
from unittest.mock import patch

import discover
from core import client as _client



class DoctypeMetaUsesDocTypeGetResourceTests(unittest.TestCase):
    """The whole point of this script's resurrection: never query DocField
    directly (see discover.py module docstring's "why this file didn't
    exist until now"). meta must always go through get_resource() against
    DocType — the single-resource GET whose docstring in core/client.py
    says it's "the only way to get child-table rows.\""""

    def setUp(self):
        # These cover the base-DocType fallback; the merged-meta RPC is
        # covered by MergedMetaTests.
        p = patch.object(discover, "read_rpc",
                         side_effect=discover.ConnectorError("ERPNext API error (403) on GET getdoctype"))
        p.start()
        self.addCleanup(p.stop)

    @patch.object(discover, "get_resource")
    def test_meta_calls_get_resource_against_doctype_not_docfield(self, mock_get):
        mock_get.return_value = {"data": {
            "name": "Supplier", "module": "Buying", "custom": 0, "istable": 0,
            "issubmittable": 0, "description": None,
            "fields": [
                {"fieldname": "supplier_name", "label": "Supplier Name", "fieldtype": "Data",
                 "reqd": 1, "options": None, "owner": "Administrator", "idx": 1},
                {"fieldname": "gstin", "label": "GSTIN", "fieldtype": "Data", "reqd": 0,
                 "options": None, "owner": "Administrator", "idx": 7},
            ],
        }}
        result = discover.doctype_meta("DEMO_ERP", "Supplier", requested_by="user@org.com")

        called_doctype = mock_get.call_args[0][1]
        called_name = mock_get.call_args[0][2]
        self.assertEqual(called_doctype, "DocType")
        self.assertEqual(called_name, "Supplier")
        self.assertEqual(result["module"], "Buying")
        self.assertEqual(len(result["fields"]), 2)

    @patch.object(discover, "get_resource")
    def test_meta_field_filter_drops_noise_keys(self, mock_get):
        mock_get.return_value = {"data": {
            "name": "Item", "module": "Stock", "custom": 0, "istable": 0,
            "issubmittable": 0, "description": None,
            "fields": [{
                "fieldname": "gst_hsn_code", "label": "HSN/SAC", "fieldtype": "Data",
                "reqd": 0, "options": None, "owner": "Administrator", "creation": "2020-01-01",
                "modified_by": "Administrator", "idx": 42, "doctype": "DocField",
                "parentfield": "fields", "parenttype": "DocType",
            }],
        }}
        result = discover.doctype_meta("DEMO_ERP", "Item", requested_by="user@org.com")
        field = result["fields"][0]
        self.assertEqual(field, {"fieldname": "gst_hsn_code", "label": "HSN/SAC",
                                  "fieldtype": "Data", "reqd": 0, "options": None})
        self.assertNotIn("owner", field)
        self.assertNotIn("idx", field)
        self.assertNotIn("parenttype", field)


class ResolveDoctypeAppLookupTests(unittest.TestCase):
    """`app: null` must mean two different things distinguishably — see
    resolve_doctype()'s own docstring."""

    def setUp(self):
        # These cover the base-DocType fallback; the merged-meta RPC is
        # covered by MergedMetaTests.
        p = patch.object(discover, "read_rpc",
                         side_effect=discover.ConnectorError("ERPNext API error (403) on GET getdoctype"))
        p.start()
        self.addCleanup(p.stop)

    @patch.object(discover, "get_resource")
    def test_no_module_means_no_lookup_attempted(self, mock_get):
        mock_get.return_value = {"data": {"name": "X", "module": None, "custom": 1,
                                            "istable": 0, "issubmittable": 0, "fields": []}}
        result = discover.resolve_doctype("DEMO_ERP", "X", requested_by="user@org.com")
        self.assertIsNone(result["app"])
        self.assertIsNone(result["app_lookup_error"])
        mock_get.assert_called_once()  # only the DocType fetch, no Module Def fetch

    @patch.object(discover, "get_resource")
    def test_module_lookup_failure_is_distinguished_from_no_module(self, mock_get):
        def side_effect(tag, doctype, name, **kwargs):
            if doctype == "DocType":
                return {"data": {"name": "Supplier", "module": "Buying", "custom": 0,
                                  "istable": 0, "issubmittable": 0, "fields": []}}
            raise discover.ConnectorError("ERPNext API error (403) on GET /api/resource/Module Def/Buying")
        mock_get.side_effect = side_effect
        result = discover.resolve_doctype("DEMO_ERP", "Supplier", requested_by="user@org.com")
        self.assertIsNone(result["app"])
        self.assertIsNotNone(result["app_lookup_error"])
        self.assertIn("403", result["app_lookup_error"])

    @patch.object(discover, "get_resource")
    def test_successful_module_lookup_sets_app(self, mock_get):
        def side_effect(tag, doctype, name, **kwargs):
            if doctype == "DocType":
                return {"data": {"name": "Supplier", "module": "Buying", "custom": 0,
                                  "istable": 0, "issubmittable": 0, "fields": []}}
            return {"data": {"name": "Buying", "app_name": "erpnext"}}
        mock_get.side_effect = side_effect
        result = discover.resolve_doctype("DEMO_ERP", "Supplier", requested_by="user@org.com")
        self.assertEqual(result["app"], "erpnext")
        self.assertIsNone(result["app_lookup_error"])


class ListInstalledAppsFallbackTests(unittest.TestCase):
    """A blocked/whitelist-denied RPC must degrade to a named fallback
    hint, never raise past this function (the 'apps' subcommand needs a
    clean result to print either way)."""

    @patch.object(discover, "_log_read")
    @patch.object(discover, "_request")
    @patch.object(discover, "get_env_config", return_value={"tag": "DEMO_ERP"})
    @patch.object(discover, "validate_environment_metadata_requester")
    def test_blocked_rpc_returns_fallback_hint_not_raise(self, mock_validate, mock_cfg,
                                                          mock_request, mock_log):
        mock_request.side_effect = discover.ConnectorError(
            "ERPNext API error (403) ... PermissionError: get_versions is not whitelisted"
        )
        result = discover.list_installed_apps("DEMO_ERP", requested_by="user@org.com")
        self.assertIn("error", result)
        self.assertIn("modules", result["fallback"])
        mock_log.assert_called_once()


class EnvironmentMetadataExemptTests(unittest.TestCase):
    """Issue 02 / ADR 0001: Environment Metadata (DocType, Module Def,
    get_versions) is exempt from the requester's per-doctype permission
    check. The requester must still be present and bound to the session,
    and every read still writes one audit row under that requester."""

    REQ = "hr.user@org.com"

    def setUp(self):
        env = patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        import os
        for var in (_client._SESSION_SENDER_ENV, _client._SESSION_PLATFORM_ENV):
            os.environ.pop(var, None)
        # Any permission lookup at all is a failure: the requester holds no
        # Module Def read, and the gate must not ask.
        for name in ("resource_exists", "check_user_permission", "verify_rbac_precheck_reliable",
                     "_requester_has_role_permission"):
            p = patch.object(_client, name,
                             side_effect=AssertionError(f"{name} must not run for metadata"))
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("get_env_config", {"tag": "DEMO_ERP"}),):
            p = patch.object(_client, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.gate_log = patch.object(_client, "_log_gate_decision").start()
        self.addCleanup(patch.stopall)

    def test_modules_succeeds_without_module_def_permission_and_logs_one_row(self):
        with patch.object(_client, "_request",
                          return_value={"data": [{"name": "HR", "app_name": "hrms"}]}), \
                patch.object(_client, "_log_read") as log_read:
            result = discover.list_modules("DEMO_ERP", requested_by=self.REQ)
        self.assertEqual(result["apps_seen_via_modules"], ["hrms"])
        log_read.assert_called_once()
        self.assertEqual(log_read.call_args.args[1], "Module Def")
        self.assertEqual(log_read.call_args.args[3], self.REQ)
        self.gate_log.assert_not_called()

    def test_apps_succeeds_without_module_def_permission_and_logs_one_row(self):
        with patch.object(discover, "_request", return_value={"message": {"frappe": "16.0.0"}}), \
                patch.object(discover, "get_env_config", return_value={"tag": "DEMO_ERP"}), \
                patch.object(discover, "_log_read") as log_read:
            result = discover.list_installed_apps("DEMO_ERP", requested_by=self.REQ)
        self.assertEqual(result["apps"], {"frappe": "16.0.0"})
        log_read.assert_called_once()
        self.assertEqual(log_read.call_args.args[3], self.REQ)

    def test_resolve_reads_module_def_without_requester_permission(self):
        def fake_request(cfg, method, path, params=None, payload=None):
            if "getdoctype" in path:
                return {"docs": [{"name": "Employee", "module": "Setup", "fields": []}]}
            return {"data": {"name": "Setup", "app_name": "erpnext"}}
        with patch.object(_client, "_request", side_effect=fake_request), \
                patch.object(_client, "_log_read") as log_read:
            result = discover.resolve_doctype("DEMO_ERP", "Employee", requested_by=self.REQ)
        self.assertEqual(result["app"], "erpnext")
        self.assertIsNone(result["app_lookup_error"])
        self.assertEqual([c.args[1] for c in log_read.call_args_list], ["DocType", "Module Def"])
        self.assertTrue(all(c.args[3] == self.REQ for c in log_read.call_args_list))

    def test_missing_requester_is_still_refused(self):
        with patch.object(_client, "_request") as req:
            for call in (lambda: discover.list_modules("DEMO_ERP", requested_by=None),
                         lambda: discover.list_installed_apps("DEMO_ERP", requested_by=""),
                         lambda: discover.doctype_meta("DEMO_ERP", "Employee", requested_by=None)):
                with self.subTest(call=call):
                    with self.assertRaises(_client.UnvalidatedProdRequesterError):
                        call()
        req.assert_not_called()

    def test_requester_must_match_session_sender(self):
        import os
        os.environ[_client._SESSION_SENDER_ENV] = "someone.else@org.com"
        with patch.object(_client, "_request") as req:
            with self.assertRaises(_client.UnvalidatedProdRequesterError):
                discover.list_modules("DEMO_ERP", requested_by=self.REQ)
            with self.assertRaises(_client.UnvalidatedProdRequesterError):
                discover.list_installed_apps("DEMO_ERP", requested_by=self.REQ)
        req.assert_not_called()

    def test_module_def_write_is_still_gated(self):
        self.assertNotIn("Module Def", _client.WRITE_GATE_EXEMPT_DOCTYPES)
        with patch.object(_client, "resource_exists", return_value=False):
            with self.assertRaises(_client.UnvalidatedProdRequesterError):
                _client._validate_prod_requester(
                    "DEMO_ERP", self.REQ, "Module Def", "write", for_write=True)

    def test_exemption_is_the_closed_adr_0001_list(self):
        self.assertEqual(_client.ENVIRONMENT_METADATA_DOCTYPES, {"DocType", "Module Def"})
        self.assertEqual(_client.ENVIRONMENT_METADATA_RPCS,
                         {"frappe.utils.change_log.get_versions"})
        with self.assertRaises(ValueError):
            _client.validate_environment_metadata_requester(
                "DEMO_ERP", self.REQ, "frappe.client.get_list")


class ListModulesLimitTests(unittest.TestCase):
    @patch.object(discover, "query_resource")
    def test_uses_large_explicit_limit_and_surfaces_has_more(self, mock_query):
        mock_query.return_value = {
            "data": [{"name": "Buying", "app_name": "erpnext"}],
            "has_more": True, "limit": 1000,
        }
        result = discover.list_modules("DEMO_ERP", requested_by="user@org.com")
        _, kwargs = mock_query.call_args
        self.assertEqual(kwargs.get("limit"), 1000)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["apps_seen_via_modules"], ["erpnext"])


if __name__ == "__main__":
    unittest.main()


class MergedMetaTests(unittest.TestCase):
    """W39 (DEMO_ERP 2026-10-06, India Compliance installed): the base
    DocType record carries no Custom Fields or Property Setters, so the
    IC fields (gstin, pan, mandatory gst_category) were invisible and
    schema mapping dropped them. doctype_meta() reads Frappe's merged meta
    (frappe.desk.form.load.getdoctype) instead."""

    MERGED = {"docs": [{"name": "Address", "module": "Contacts", "custom": 0, "istable": 0,
                        "issubmittable": 0, "description": None,
                        "fields": [{"fieldname": "address_line1", "fieldtype": "Data", "reqd": 1},
                                   {"fieldname": "gstin", "fieldtype": "Data", "reqd": 0},
                                   {"fieldname": "gst_category", "fieldtype": "Select", "reqd": 1}]},
                       {"name": "Dynamic Link", "istable": 1, "fields": []}]}

    def test_merged_meta_includes_custom_fields(self):
        with patch.object(discover, "read_rpc", return_value=self.MERGED) as rr,                 patch.object(discover, "get_resource") as base:
            meta = discover.doctype_meta("DEMO_ERP", "Address", requested_by="u@org.com")
        self.assertIn("getdoctype", rr.call_args.args[2])
        self.assertEqual(rr.call_args.kwargs["params"], {"doctype": "Address"})
        base.assert_not_called()
        self.assertTrue(meta["custom_fields_merged"])
        self.assertEqual({f["fieldname"]: f["reqd"] for f in meta["fields"]},
                         {"address_line1": 1, "gstin": 0, "gst_category": 1})
        self.assertEqual(meta["module"], "Contacts")

    def test_rpc_failure_falls_back_to_base_doctype_and_says_so(self):
        with patch.object(discover, "read_rpc",
                          side_effect=discover.ConnectorError("ERPNext API error (403)")),                 patch.object(discover, "get_resource", return_value={"data": {
                    "name": "Address", "module": "Contacts", "fields": []}}):
            meta = discover.doctype_meta("DEMO_ERP", "Address", requested_by="u@org.com")
        self.assertFalse(meta["custom_fields_merged"])
        self.assertIn("403", meta["custom_fields_error"])

    def test_missing_doctype_is_not_masked_by_the_fallback(self):
        with patch.object(discover, "read_rpc",
                          side_effect=discover.ConnectorError("ERPNext API error (404) on GET")),                 patch.object(discover, "get_resource") as base:
            with self.assertRaises(discover.ConnectorError):
                discover.doctype_meta("DEMO_ERP", "Nope", requested_by="u@org.com")
        base.assert_not_called()


class ConditionalAndNamingMetaTests(unittest.TestCase):
    """Issue 05: a field made mandatory by `mandatory_depends_on` was
    invisible (only `reqd` was kept), and the naming rule wasn't returned,
    so a create failed with MandatoryError on a customised instance."""

    MERGED = {"docs": [{
        "name": "Employee", "module": "Setup", "autoname": "naming_series:",
        "naming_rule": "By \"Naming Series\" field", "title_field": "employee_name",
        "fields": [
            {"fieldname": "naming_series", "fieldtype": "Select", "reqd": 1,
             "options": "HR-EMP-\nEMP-.YYYY.-"},
            {"fieldname": "first_name", "fieldtype": "Data", "reqd": 1, "length": 140,
             "permlevel": 0},
            {"fieldname": "relieving_date", "fieldtype": "Date", "reqd": 0,
             "mandatory_depends_on": "eval:doc.status=='Left'",
             "depends_on": "eval:doc.status!='Active'",
             "read_only_depends_on": "eval:doc.docstatus==1"},
            {"fieldname": "department_name", "fieldtype": "Data",
             "fetch_from": "department.department_name", "permlevel": 1},
            {"fieldname": "ctc", "fieldtype": "Currency", "non_negative": 1},
        ]}]}

    def _meta(self, merged=None):
        with patch.object(discover, "read_rpc", return_value=merged or self.MERGED):
            return discover.doctype_meta("DEMO_ERP", "Employee", requested_by="u@org.com")

    def test_conditional_mandatory_field_is_listed_with_its_expression(self):
        meta = self._meta()
        self.assertEqual(meta["conditional_mandatory"],
                         [{"fieldname": "relieving_date", "expression": "eval:doc.status=='Left'"}])

    def test_naming_keys_are_returned(self):
        meta = self._meta()
        self.assertEqual(meta["autoname"], "naming_series:")
        self.assertEqual(meta["naming_rule"], "By \"Naming Series\" field")
        self.assertEqual(meta["title_field"], "employee_name")
        self.assertEqual(meta["naming_series_options"], ["HR-EMP-", "EMP-.YYYY.-"])

    def test_no_naming_series_field_means_no_options(self):
        merged = {"docs": [{"name": "Employee", "autoname": "hash", "fields": []}]}
        meta = self._meta(merged)
        self.assertIsNone(meta["naming_series_options"])
        self.assertEqual(meta["conditional_mandatory"], [])

    def test_field_level_conditional_keys_are_kept(self):
        fields = {f["fieldname"]: f for f in self._meta()["fields"]}
        self.assertEqual(fields["relieving_date"]["depends_on"], "eval:doc.status!='Active'")
        self.assertEqual(fields["relieving_date"]["read_only_depends_on"], "eval:doc.docstatus==1")
        self.assertEqual(fields["department_name"]["fetch_from"], "department.department_name")
        self.assertEqual(fields["department_name"]["permlevel"], 1)
        self.assertEqual(fields["first_name"]["length"], 140)
        self.assertEqual(fields["ctc"]["non_negative"], 1)

    def test_custom_fields_merged_keys_unchanged(self):
        meta = self._meta()
        self.assertTrue(meta["custom_fields_merged"])
        self.assertIsNone(meta["custom_fields_error"])


class SubmittableFlagTests(unittest.TestCase):
    """W40: Frappe's DocType field is `is_submittable`; the meta used to
    read a non-existent `issubmittable`, so every doctype (Purchase Order
    included) was reported non-submittable (DEMO_ERP 2026-10-06)."""

    def test_reads_is_submittable(self):
        merged = {"docs": [{"name": "Purchase Order", "module": "Buying", "is_submittable": 1,
                            "istable": 0, "fields": []}]}
        with patch.object(discover, "read_rpc", return_value=merged):
            meta = discover.doctype_meta("DEMO_ERP", "Purchase Order", requested_by="u@org.com")
        self.assertTrue(meta["issubmittable"])
