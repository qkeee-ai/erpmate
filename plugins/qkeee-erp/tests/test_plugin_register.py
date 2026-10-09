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
        self.tools, self.hooks = [], []

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

    def test_registers_the_erp_tools_and_hooks(self):
        ctx = FakeCtx()
        qkeee_erp_plugin.register(ctx)
        self.assertEqual(sorted(ctx.tools), sorted((n, "qkeee_erp") for n in erp_tools.SCHEMAS))
        self.assertIn(("pre_tool_call", identity_guard.pre_tool_call), ctx.hooks)
        self.assertEqual(sorted(h for h, _ in ctx.hooks),
                         ["post_tool_call", "pre_tool_call", "pre_tool_call"])

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


if __name__ == "__main__":
    unittest.main()
