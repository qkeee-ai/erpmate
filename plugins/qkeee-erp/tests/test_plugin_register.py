"""register() against a fake Hermes ctx: what the plugin hands the gateway.

Hermes rolls back every registration of a plugin whose register() raises,
its CLI commands included (agents .scratch/qkeee-erp-plugin-profile-split,
issue 03). So register() must never raise; the identity guard must be
registered even when the ERP library cannot load."""

import sys
import types
import unittest
from unittest.mock import patch

import qkeee_erp_plugin
from qkeee_erp_plugin import erp_tools, identity_guard  # noqa: F401 — loaded before any test hides them
from qkeee_erp_plugin.qkeee_erp.core import client


class FakeCtx:
    def __init__(self):
        self.tools, self.hooks, self.cli, self.skills = [], [], [], {}

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills[name] = path

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli.append(name)

    def register_tool(self, name, toolset, schema, handler, **_):
        self.tools.append((name, toolset))

    def register_hook(self, hook, fn):
        self.hooks.append((hook, fn))

    def get_config(self, key, default=None):
        return default


def _hermes_modules():
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: "/nonexistent/hermes-home"
    session = types.ModuleType("gateway.session_context")
    session.get_session_env = lambda name, default="": default
    gateway = types.ModuleType("gateway")
    gateway.session_context = session
    return {"hermes_constants": constants, "gateway": gateway, "gateway.session_context": session}


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(client.set_session_env_reader, None)
        self.addCleanup(client.set_hermes_home_reader, None)
        modules = patch.dict(sys.modules, _hermes_modules())
        modules.start()
        self.addCleanup(modules.stop)
        config = patch.object(qkeee_erp_plugin, "_profile_config", return_value={})
        config.start()
        self.addCleanup(config.stop)

    def test_registers_the_erp_tools_and_hooks(self):
        ctx = FakeCtx()
        qkeee_erp_plugin.register(ctx)
        self.assertEqual(sorted(ctx.tools), sorted((n, "qkeee_erp") for n in erp_tools.SCHEMAS))
        self.assertIn(("pre_tool_call", identity_guard.pre_tool_call), ctx.hooks)
        self.assertEqual(sorted(h for h, _ in ctx.hooks),
                         ["post_tool_call", "pre_tool_call", "pre_tool_call"])

    def test_registers_the_usage_skill(self):
        ctx = FakeCtx()
        qkeee_erp_plugin.register(ctx)
        self.assertEqual(list(ctx.skills), ["usage"])
        text = ctx.skills["usage"].read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: usage\n"))
        for rule in ("prompt_summary", "latest_prompt", "gaps[].prompt", "user_confirmation_text"):
            self.assertIn(rule, text)

    def test_tools_read_the_plugin_settings_section(self):
        cfg = {"plugins": {"entries": {"qkeee-erp": {"settings": {"active_env": "demo",
                                                                   "mode": "read-write"}}}}}
        ctx = FakeCtx()
        with patch.object(qkeee_erp_plugin, "_profile_config", return_value=cfg), \
                patch.object(erp_tools, "ErpTools", wraps=erp_tools.ErpTools) as made:
            qkeee_erp_plugin.register(ctx)
            self.assertEqual(made.call_args.kwargs["settings"](), {"active_env": "demo", "mode": "read-write"})

    def test_legacy_skill_config_registers_no_tools_until_setup(self):
        cfg = {"skills": {"config": {"qkeee_erp": {"active_env": "demo", "mode": "read-write"}}}}
        ctx = FakeCtx()
        with patch.object(qkeee_erp_plugin, "_profile_config", return_value=cfg), \
                self.assertLogs("qkeee_erp_plugin", level="WARNING") as logs:
            qkeee_erp_plugin.register(ctx)
        self.assertEqual(ctx.tools, [])
        self.assertEqual(ctx.cli, ["qkeee-erp"])
        self.assertIn("hermes qkeee-erp setup", " ".join(logs.output))
        # Hooks protect; they grant nothing, so they register either way.
        self.assertEqual(sorted(h for h, _ in ctx.hooks),
                         ["post_tool_call", "pre_tool_call", "pre_tool_call"])

    def test_unreadable_config_registers_no_tools_and_does_not_raise(self):
        ctx = FakeCtx()
        with patch.object(qkeee_erp_plugin, "_profile_config", side_effect=OSError("boom")), \
                self.assertLogs("qkeee_erp_plugin", level="ERROR"):
            qkeee_erp_plugin.register(ctx)
        self.assertEqual(ctx.tools, [])

    def test_library_failure_keeps_the_guard_and_does_not_raise(self):
        ctx = FakeCtx()
        # `from . import erp_tools` raises once the module is neither a package
        # attribute nor importable.
        delattr(qkeee_erp_plugin, "erp_tools")
        sys.modules["qkeee_erp_plugin.erp_tools"] = None
        try:
            with self.assertLogs("qkeee_erp_plugin", level="ERROR"):
                qkeee_erp_plugin.register(ctx)
        finally:
            sys.modules["qkeee_erp_plugin.erp_tools"] = erp_tools
            qkeee_erp_plugin.erp_tools = erp_tools
        self.assertEqual(ctx.tools, [])
        self.assertEqual(ctx.hooks, [("pre_tool_call", identity_guard.pre_tool_call)])
        self.assertEqual(ctx.cli, ["qkeee-erp"])


if __name__ == "__main__":
    unittest.main()
