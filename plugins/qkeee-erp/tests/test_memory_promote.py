#!/usr/bin/env python3
"""Tests for memory_promote.py. No network, no hermes-agent
import — this module never imports hermes-agent internals (see its module
docstring for why it can't), so these tests only exercise pure string/
dict formatting and the redaction pass."""

import json
import unittest

from qkeee_erp_plugin.qkeee_erp.core import memory_promote as mp


class SkillNameSanitizationTests(unittest.TestCase):
    def test_lowercases_and_strips_invalid_chars(self):
        # Underscore is itself allowed by skill_manage's VALID_NAME_RE
        # (^[a-z0-9][a-z0-9._-]*$), so it's preserved, not converted.
        self.assertEqual(mp.sanitize_env_tag_for_skill_name("PROD_ERP"), "prod_erp")
        self.assertEqual(mp.sanitize_env_tag_for_skill_name("Demo IN!"), "demo-in")

    def test_slash_is_never_in_the_manage_name(self):
        args = mp.learned_skill_manage_args("example-env")
        self.assertNotIn("/", args["name"])
        self.assertEqual(args["category"], "qkeee-erp-learned")
        # skill_manager_tool.py's VALID_NAME_RE: ^[a-z0-9][a-z0-9._-]*$
        import re
        self.assertRegex(args["name"], r"^[a-z0-9][a-z0-9._-]*$")

    def test_qualified_name_is_prose_only(self):
        self.assertEqual(mp.learned_skill_name("example-env"), "qkeee-erp-learned/example-env")


class RedactionTests(unittest.TestCase):
    def test_redact_findings_scrubs_nested_strings(self):
        findings = {"notes": "SSN 123-45-6789", "nested": {"x": "card 4111 1111 1111 1111"}}
        redacted = mp.redact_findings(findings)
        self.assertIn("[REDACTED-SSN]", redacted["notes"])
        self.assertIn("[REDACTED-CARD]", redacted["nested"]["x"])

    def test_breadcrumb_is_redacted(self):
        line = mp.format_memory_breadcrumb("example-env", "contact is 123-45-6789")
        self.assertIn("[REDACTED-SSN]", line)
        self.assertNotIn("123-45-6789", line)


class FormatterTests(unittest.TestCase):
    def test_learned_skill_md_has_valid_frontmatter_shape(self):
        content = mp.format_learned_skill_md("example-env")
        self.assertTrue(content.startswith("---\n"))
        self.assertIn("name: qkeee-erp-learned/example-env", content)
        self.assertIn("description:", content)
        # Frontmatter closed + non-empty body, mirroring
        # skill_manager_tool.py's _validate_frontmatter() shape checks.
        self.assertIn("\n---\n", content[3:])

    def test_environment_md_includes_learned_heading(self):
        content = mp.format_environment_md("example-env", {"frappe_version": "15.4"})
        self.assertIn("## Learned", content)
        self.assertIn("Frappe: 15.4", content)

    def test_environment_md_handles_empty_findings(self):
        content = mp.format_environment_md("example-env", {})
        self.assertIn("no findings supplied yet", content)

    def test_doctypes_catalog_lists_each_entry(self):
        content = mp.format_doctypes_catalog_md("example-env", [
            {"name": "Qkeee Widget", "module": "Custom", "app": None, "custom": True},
        ])
        self.assertIn("Qkeee Widget", content)
        self.assertIn("custom", content)


class BuildPromotionPlanTests(unittest.TestCase):
    def test_plan_order_and_shape(self):
        plan = mp.build_promotion_plan(
            "example-env",
            {"frappe_version": "15.4"},
            doctypes=[{"name": "X", "module": "Custom", "app": None, "custom": True}],
            custom_apps={"crm": {"summary": "Frappe CRM"}},
            non_erpnext_systems={"tally-prime": {"docs_source": "user-provided PDF"}},
            one_line_summary="test summary",
        )
        tools = [(s["tool"], s.get("action"), s.get("file_path")) for s in plan]
        self.assertEqual(tools[0], ("skill_manage", "create", None))
        self.assertEqual(tools[1], ("skill_manage", "write_file", "references/environment.md"))
        self.assertEqual(tools[2], ("skill_manage", "write_file", "references/doctypes-catalog.md"))
        self.assertIn(("skill_manage", "write_file", "references/custom-apps/crm.md"), tools)
        self.assertIn(("skill_manage", "write_file", "references/non-erpnext/tally-prime.md"), tools)
        self.assertEqual(tools[-1], ("memory", "add", None))

    def test_create_call_carries_category_not_slash_name(self):
        plan = mp.build_promotion_plan("example-env", {})
        create_step = plan[0]
        self.assertEqual(create_step["name"], "example-env")
        self.assertEqual(create_step["category"], "qkeee-erp-learned")

    def test_skill_already_exists_uses_edit_not_create(self):
        plan = mp.build_promotion_plan("example-env", {}, skill_already_exists=True)
        self.assertEqual(plan[0]["action"], "edit")
        self.assertNotIn("category", plan[0])

    def test_plan_is_json_serializable(self):
        plan = mp.build_promotion_plan("example-env", {"notes": "SSN 123-45-6789"})
        dumped = json.dumps(plan)
        self.assertNotIn("123-45-6789", dumped)


class PartialCatalogTests(unittest.TestCase):
    """Issue 09: pending skill e701c144 promoted a catalog with no app list
    (modules/apps had failed) and nothing said so; activation step 2 then
    latches it and skips assessment. A catalog built with gaps says
    `catalog_complete: false` and lists them."""

    GAP = {"capability": "module_def_read", "who": "bot", "user": "bot@org.com",
           "role": "Qkeee Bot", "doctype": "Module Def", "perm": "read",
           "effect": "discover.py modules cannot map a DocType to its app",
           "grant_steps": "...", "prompt": "..."}

    def _environment_md(self, plan):
        return next(s["file_content"] for s in plan
                    if s.get("file_path") == "references/environment.md")

    def test_findings_with_a_gap_are_marked_incomplete_and_list_it(self):
        content = self._environment_md(mp.build_promotion_plan("demo_erp", {}, gaps=[self.GAP]))
        self.assertIn("catalog_complete: false", content)
        self.assertIn("module_def_read", content)
        self.assertIn('read on "Module Def" for bot bot@org.com (role "Qkeee Bot")', content)

    def test_findings_without_gaps_are_complete(self):
        content = self._environment_md(mp.build_promotion_plan("demo_erp", {"frappe_version": "16"},
                                                               gaps=[]))
        self.assertIn("catalog_complete: true", content)
        self.assertNotIn("Gaps", content)

    def test_gaps_default_to_none_given(self):
        content = self._environment_md(mp.build_promotion_plan("demo_erp", {}))
        self.assertIn("catalog_complete: true", content)

    def test_breadcrumb_says_partial(self):
        plan = mp.build_promotion_plan("demo_erp", {}, gaps=[self.GAP], one_line_summary="v16")
        self.assertIn("PARTIAL catalog", plan[-1]["content"])


if __name__ == "__main__":
    unittest.main()
