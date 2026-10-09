"""The plugin's own settings (agents .scratch/qkeee-erp-plugin-profile-split,
issue 11): the plugin reads `plugins.entries.qkeee-erp.settings` only. A
profile config that still holds the legacy `skills.config.qkeee_erp`
section is "setup pending" until setup step S2 moves it."""

import copy
import unittest

from qkeee_erp_plugin import settings

LEGACY = {"skills": {"config": {"qkeee_erp": {"active_env": "demo", "mode": "read-write"}}}}


def plugin_section(**values):
    return {"plugins": {"entries": {"qkeee-erp": {"settings": dict(values)}}}}


class ReadTests(unittest.TestCase):
    def test_reads_the_plugin_section(self):
        cfg = plugin_section(active_env="demo", mode="read-write")
        self.assertEqual(settings.plugin_settings(cfg), {"active_env": "demo", "mode": "read-write"})

    def test_ignores_the_legacy_skill_section(self):
        self.assertEqual(settings.plugin_settings(LEGACY), {})

    def test_missing_or_malformed_sections_read_as_empty(self):
        for cfg in ({}, None, {"plugins": None}, {"plugins": {"entries": {"qkeee-erp": "x"}}},
                    {"plugins": {"entries": {"qkeee-erp": {"settings": []}}}}):
            with self.subTest(cfg=cfg):
                self.assertEqual(settings.plugin_settings(cfg), {})


class SetupPendingTests(unittest.TestCase):
    def test_legacy_section_is_pending_and_names_setup(self):
        reason = settings.setup_pending(LEGACY)
        self.assertIn("skills.config.qkeee_erp", reason)
        self.assertIn("hermes qkeee-erp setup", reason)

    def test_plugin_section_only_is_not_pending(self):
        self.assertIsNone(settings.setup_pending(plugin_section(active_env="demo")))

    def test_no_settings_at_all_is_not_pending(self):
        self.assertIsNone(settings.setup_pending({}))

    def test_both_sections_is_still_pending(self):
        cfg = copy.deepcopy(LEGACY)
        cfg.update(plugin_section(active_env="demo"))
        self.assertIsNotNone(settings.setup_pending(cfg))


class MigrateTests(unittest.TestCase):
    """Setup step S2: copy the legacy values once, drop scripts_dir."""

    def test_copies_the_legacy_values_and_removes_the_legacy_section(self):
        cfg = copy.deepcopy(LEGACY)
        changes = settings.migrate_legacy_config(cfg)
        self.assertTrue(changes)
        self.assertEqual(settings.plugin_settings(cfg), {"active_env": "demo", "mode": "read-write"})
        self.assertNotIn("qkeee_erp", cfg["skills"]["config"])
        self.assertIsNone(settings.setup_pending(cfg))

    def test_is_idempotent(self):
        cfg = copy.deepcopy(LEGACY)
        settings.migrate_legacy_config(cfg)
        once = copy.deepcopy(cfg)
        self.assertEqual(settings.migrate_legacy_config(cfg), [])
        self.assertEqual(cfg, once)

    def test_never_overwrites_a_plugin_value(self):
        cfg = copy.deepcopy(LEGACY)
        cfg.update(plugin_section(mode="read-only"))
        settings.migrate_legacy_config(cfg)
        self.assertEqual(settings.plugin_settings(cfg), {"active_env": "demo", "mode": "read-only"})

    def test_drops_scripts_dir(self):
        cfg = plugin_section(active_env="demo", scripts_dir="/opt/x/scripts")
        self.assertTrue(settings.migrate_legacy_config(cfg))
        self.assertEqual(settings.plugin_settings(cfg), {"active_env": "demo"})

    def test_keeps_other_skill_config_and_plugin_entries(self):
        cfg = copy.deepcopy(LEGACY)
        cfg["skills"]["config"]["other"] = {"x": 1}
        cfg["plugins"] = {"enabled": ["qkeee-erp"], "entries": {"qkeee-erp": {"allow_tool_override": False}}}
        settings.migrate_legacy_config(cfg)
        self.assertEqual(cfg["skills"]["config"], {"other": {"x": 1}})
        self.assertEqual(cfg["plugins"]["enabled"], ["qkeee-erp"])
        self.assertFalse(cfg["plugins"]["entries"]["qkeee-erp"]["allow_tool_override"])

    def test_fresh_config_needs_no_change(self):
        cfg = {"model": {"default": "x"}}
        self.assertEqual(settings.migrate_legacy_config(cfg), [])
        self.assertEqual(cfg, {"model": {"default": "x"}})


if __name__ == "__main__":
    unittest.main()
