"""register() against a fake Hermes ctx: what the plugin hands the gateway.

Hermes rolls back every registration of a plugin whose register() raises,
its CLI commands included (agents .scratch/qkeee-erp-plugin-profile-split,
issue 03). So register() must never raise; the identity guard must be
registered even when the ERP library cannot load."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
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
        unmet = patch.object(qkeee_erp_plugin, "_setup_unmet", return_value=[])
        unmet.start()
        self.addCleanup(unmet.stop)

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

    def test_unmet_setup_registers_no_tools_until_setup(self):
        reasons = ["S2 plugin config: the profile config still holds skills.config.qkeee_erp",
                   "P2 terminal backend: terminal.backend is 'local'"]
        ctx = FakeCtx()
        with patch.object(qkeee_erp_plugin, "_setup_unmet", return_value=reasons), \
                self.assertLogs("qkeee_erp_plugin", level="WARNING") as logs:
            qkeee_erp_plugin.register(ctx)
        self.assertEqual(ctx.tools, [])
        self.assertEqual(ctx.cli, ["qkeee-erp"])
        log = " ".join(logs.output)
        self.assertIn("qkeee-erp setup status", log)
        self.assertIn("terminal.backend", log)
        # Hooks protect; they grant nothing, so they register either way.
        self.assertEqual(sorted(h for h, _ in ctx.hooks),
                         ["post_tool_call", "pre_tool_call", "pre_tool_call"])

    def test_a_failing_tool_or_hook_registration_does_not_raise(self):
        for method in ("register_tool", "register_hook"):
            with self.subTest(method=method):
                ctx = FakeCtx()
                original = getattr(ctx, method)

                def flaky(*a, _orig=original, **kw):
                    if a[:1] != ("pre_tool_call",) or kw or len(ctx.hooks) > 0:
                        raise RuntimeError("hermes api drift")
                    return _orig(*a, **kw)
                setattr(ctx, method, flaky)
                with self.assertLogs("qkeee_erp_plugin", level="ERROR"):
                    qkeee_erp_plugin.register(ctx)
                self.assertEqual(ctx.cli, ["qkeee-erp"])

    def test_a_setup_check_error_registers_no_tools_and_does_not_raise(self):
        ctx = FakeCtx()
        with patch.object(qkeee_erp_plugin, "_setup_unmet", side_effect=OSError("boom")), \
                self.assertLogs("qkeee_erp_plugin", level="ERROR"):
            qkeee_erp_plugin.register(ctx)
        self.assertEqual(ctx.tools, [])

    def test_trace_file_records_the_unmet_reasons(self):
        # Live finding, 2026-10-26 (agents repo, issue 11): register()'s own
        # logger.warning/error never showed up in a kanban worker's captured
        # log (quiet-mode spawn). _trace() writes straight to a file instead,
        # so the real reason is visible without depending on hermes-agent's
        # own logging configuration for a worker process.
        reasons = ["P2 terminal backend: terminal.backend is 'local'"]
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(sys.modules["hermes_constants"].__dict__,
                             {"get_hermes_home": lambda: home}), \
                    patch.object(qkeee_erp_plugin, "_setup_unmet", return_value=reasons), \
                    self.assertLogs("qkeee_erp_plugin", level="WARNING"):
                qkeee_erp_plugin.register(FakeCtx())
            trace = (Path(home) / "plugin-data" / "qkeee-erp" / "register_trace.log").read_text()
        self.assertIn("REFUSED (unmet setup)", trace)
        self.assertIn("terminal.backend", trace)

    def test_trace_file_records_success(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(sys.modules["hermes_constants"].__dict__, {"get_hermes_home": lambda: home}):
                qkeee_erp_plugin.register(FakeCtx())
            trace = (Path(home) / "plugin-data" / "qkeee-erp" / "register_trace.log").read_text()
        self.assertIn(f"OK: {len(erp_tools.SCHEMAS)} erp_* tools registered", trace)

    def test_trace_skips_silently_on_no_known_home(self):
        # Empty-string home must not fall back to Path("") (= cwd) and write
        # into wherever the process happens to be running — caught live: an
        # earlier draft of this guard did exactly that during this test run,
        # writing a stray plugin-data/ dir into the repo's own source tree.
        cwd_before = sorted(Path(".").iterdir())
        qkeee_erp_plugin._trace("", "should not raise")
        qkeee_erp_plugin._trace(None, "should not raise either")
        self.assertEqual(sorted(Path(".").iterdir()), cwd_before)

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
