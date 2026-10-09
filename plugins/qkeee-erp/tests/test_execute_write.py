#!/usr/bin/env python3
"""Tests for execute_write.py: importing it registers every domain's
allowlist and operations; list_ops() describes them.

The pipeline is covered by test_operations.py; the Operator CLI (exit
codes, render -> execute round trip with the real token check) by
test_plugin_cli.py."""

import unittest

from qkeee_erp_plugin.qkeee_erp import execute_write
from qkeee_erp_plugin.qkeee_erp.core import operations
from qkeee_erp_plugin.qkeee_erp.core.client import DOMAIN_WRITE_ALLOWLISTS


class ImportRegistersEverythingTests(unittest.TestCase):

    def test_every_known_domain_has_a_registered_allowlist_and_generic_op(self):
        expected = {"accounts", "fixed_assets", "hr_payroll", "inventory", "mis",
                    "procurement", "sales", "system_admin"}
        self.assertEqual(execute_write._KNOWN_DOMAINS, expected)
        for domain in expected:
            with self.subTest(domain=domain):
                self.assertIn(domain, DOMAIN_WRITE_ALLOWLISTS)
                self.assertIn(f"{domain}.generic", operations.REGISTRY)

    def test_list_ops(self):
        ops = {o["op"]: o for o in execute_write.list_ops()}
        keys = set(ops)
        self.assertEqual(ops["system_admin.generic"]["confirmation"], "always")
        self.assertEqual(ops["sales.generic"]["confirmation"], "per action: submit/cancel/delete")
        self.assertEqual(ops["system_admin.disable_user"]["confirmation"], "always")
        self.assertIn("system_admin.create_user", keys)
        self.assertIn("unscoped.generic", keys)
        self.assertFalse(any(k.startswith("provisioning.") for k in keys))


class DocsNameEveryOperationTests(unittest.TestCase):
    """Ticket 17: every CLI operation is named in the references, and the
    cookbook points at list_ops for each operation's worked example."""

    def test_every_operation_key_is_documented(self):
        import glob
        import os
        repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
        refs = os.path.join(repo, "skills", "qkeee-erp", "qkeee-erp-associate", "references")
        text = "".join(open(p, encoding="utf-8").read()
                       for p in glob.glob(os.path.join(refs, "**", "*.md"), recursive=True))
        for op in operations.list_operations():
            with self.subTest(op=op.key):
                self.assertIn(op.key, text)

    def test_list_ops_prints_an_example_for_every_writer_operation(self):
        for o in execute_write.list_ops():
            with self.subTest(op=o["op"]):
                if o["op"] != "mis.generic":
                    self.assertTrue(o["example_args"])


if __name__ == "__main__":
    unittest.main()
