#!/usr/bin/env python3
"""Regression tests for env_defs.py — the machine-readable manifest of
qkeee-erp.env's required/optional keys, and the twin of
skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example.
Keeps the two from drifting apart the way doctype_defs.py and a live
instance's schema did (the bug that motivated this whole module)."""

import re
import unittest
from pathlib import Path

from qkeee_erp_plugin.qkeee_erp import env_defs

ENV_EXAMPLE = (Path(__file__).resolve().parents[3] / "skills" / "qkeee-erp" /
              "qkeee-erp-associate" / "qkeee-erp-associate.env.example")


class ManifestShapeTests(unittest.TestCase):
    def test_every_manifest_value_is_a_non_empty_one_line_string(self):
        for manifest in (env_defs.REQUIRED_PER_TAG, env_defs.OPTIONAL_PER_TAG,
                        env_defs.OPTIONAL_GLOBAL):
            for suffix, purpose in manifest.items():
                with self.subTest(suffix=suffix):
                    self.assertIsInstance(purpose, str)
                    self.assertTrue(purpose.strip())
                    self.assertNotIn("\n", purpose)

    def test_required_and_optional_per_tag_are_disjoint(self):
        self.assertFalse(set(env_defs.REQUIRED_PER_TAG) & set(env_defs.OPTIONAL_PER_TAG))


class MissingRequiredKeysTests(unittest.TestCase):
    def test_nothing_present_reports_every_required_key(self):
        self.assertEqual(env_defs.missing_required_keys(set()),
                         sorted(env_defs.REQUIRED_PER_TAG))

    def test_everything_present_reports_nothing(self):
        self.assertEqual(env_defs.missing_required_keys(set(env_defs.REQUIRED_PER_TAG)), [])

    def test_partial_presence_reports_only_the_gap(self):
        present = set(env_defs.REQUIRED_PER_TAG) - {"ADMIN_API_SECRET"}
        self.assertEqual(env_defs.missing_required_keys(present), ["ADMIN_API_SECRET"])

    def test_an_optional_or_unknown_key_present_changes_nothing(self):
        present = set(env_defs.REQUIRED_PER_TAG) | {"ADMIN_EMAIL", "SOME_UNKNOWN_KEY"}
        self.assertEqual(env_defs.missing_required_keys(present), [])


class EnvExampleRoundTripTests(unittest.TestCase):
    """Every suffix this manifest names (required or optional, per-tag or
    global) must appear somewhere in the human-readable .env.example, and
    vice versa for the per-tag QKEEE_ERP_DEFAULT_<SUFFIX> lines — so a new
    var added to one is never forgotten in the other."""

    def test_env_example_exists(self):
        self.assertTrue(ENV_EXAMPLE.exists(), f"{ENV_EXAMPLE} not found")

    def test_every_manifest_suffix_appears_in_the_env_example(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        for suffix in (set(env_defs.REQUIRED_PER_TAG) | set(env_defs.OPTIONAL_PER_TAG)
                      | set(env_defs.OPTIONAL_GLOBAL)):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, text, f"{suffix} is in env_defs.py but not documented "
                                           f"in {ENV_EXAMPLE.name}")

    def test_every_default_tag_suffix_in_the_env_example_is_in_the_manifest(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        documented = set(re.findall(r"QKEEE_ERP_DEFAULT_([A-Z_]+)=", text))
        known = set(env_defs.REQUIRED_PER_TAG) | set(env_defs.OPTIONAL_PER_TAG)
        undocumented_in_manifest = documented - known
        self.assertEqual(undocumented_in_manifest, set(),
                         f"{ENV_EXAMPLE.name} documents {undocumented_in_manifest} but "
                         f"env_defs.py doesn't know it")


if __name__ == "__main__":
    unittest.main()
