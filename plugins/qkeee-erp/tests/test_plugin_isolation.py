"""Isolation (agents .scratch/qkeee-erp-plugin-profile-split, issue 10;
ADR 0006): setup items I2 exclusive target and I1 probe, through
setup_steps with a fake terminal backend. No Hermes and no ssh: the fake
probe answers each `cat` as the test says, or raises."""

import json
import os

import yaml

import test_plugin_setup as base
from testsupport import FakeBackend
from qkeee_erp_plugin import setup_steps as setup
from qkeee_erp_plugin.qkeee_erp import isolation


class IsolationTestCase(base.SetupTestCase):
    def setUp(self):
        super().setUp()
        self.backend = FakeBackend()

    def env(self, **kw):
        kw.setdefault("run_probe", self.backend)
        return super().env(**kw)

    def ready(self, text=base.FRESH):
        """A profile where every item before I2/I1 is done."""
        self.config(text)
        self.credentials()
        self.make_key()

    def dotenv(self):
        path = os.path.join(self.home, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("OPENROUTER_API_KEY=x\n")
        return path

    def record(self, home=None):
        path = os.path.join(home or self.home, "plugin-data", "qkeee-erp", "isolation.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def item(self, report, key):
        return next(i for i in report["items"] if i["key"] == key)


class ProbeTests(IsolationTestCase):
    def test_both_reads_fail_is_isolated(self):
        self.ready()
        env_path = self.dotenv()
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I1")["status"], setup.DONE)
        record = self.record()
        self.assertTrue(record["isolated"])
        _, command = self.backend.calls[0]
        self.assertIn(self.env().credentials_path, command)
        self.assertIn(env_path, command)
        self.assertEqual(setup.unmet(self.env()), [])

    def test_credentials_readable_is_not_isolated(self):
        self.ready()
        self.backend.readable = {self.env().credentials_path}
        report = setup.run(self.env())
        i1 = self.item(report, "I1")
        self.assertEqual(i1["status"], setup.PENDING)
        self.assertIn("not Isolated", i1["reason"])
        self.assertFalse(self.record()["isolated"])
        self.assertTrue(any(u.startswith("I1") for u in setup.unmet(self.env())))

    def test_dotenv_readable_is_not_isolated(self):
        self.ready()
        self.backend.readable = {self.dotenv()}
        setup.run(self.env())
        self.assertFalse(self.record()["isolated"])

    def test_probe_error_is_not_isolated(self):
        self.ready()
        self.backend.error = ConnectionError("Connection refused")
        report = setup.run(self.env())
        i1 = self.item(report, "I1")
        self.assertEqual(i1["status"], setup.PENDING)
        self.assertIn("Connection refused", i1["reason"])
        self.assertFalse(self.record()["isolated"])

    def test_output_without_every_marker_is_not_isolated(self):
        """A command that never ran (or ran in another shell) prints no
        markers: a missing answer is not a failed read."""
        self.ready()
        self.backend.output = "bash: cat: command not found\n"
        setup.run(self.env())
        record = self.record()
        self.assertFalse(record["isolated"])
        self.assertIn("marker", record["reason"])

    def test_without_credentials_no_probe_runs(self):
        self.config(base.FRESH)
        self.make_key()
        report = setup.run(self.env())
        self.assertEqual(self.backend.calls, [])
        self.assertIn("S1", self.item(report, "I1")["reason"])

    def test_local_backend_is_not_isolated_and_runs_no_probe(self):
        self.config(base.DEV_ERP)
        self.credentials(legacy=True)
        report = setup.run(self.env())
        self.assertEqual(self.backend.calls, [])
        i1 = self.item(report, "I1")
        self.assertEqual(i1["status"], setup.PENDING)
        self.assertIn("local", i1["reason"])

    def test_record_holds_fingerprint_and_time(self):
        self.ready()
        setup.run(self.env())
        record = self.record()
        self.assertEqual(record["fingerprint"],
                         {"backend": "ssh", "ssh_host": "terminal", "ssh_user": "hermes",
                          "ssh_port": 22, "ssh_key": self.key, "env_passthrough": [],
                          "credential_files": []})
        self.assertEqual(record["target"], "hermes@terminal")
        self.assertIn("time", record)

    def test_second_run_does_not_probe_again(self):
        self.ready()
        setup.run(self.env())
        setup.run(self.env())
        self.assertEqual(len(self.backend.calls), 1)

    def test_status_and_unmet_never_probe(self):
        self.ready()
        setup.status(self.env())
        setup.unmet(self.env())
        self.assertEqual(self.backend.calls, [])

    def test_dry_run_does_not_probe(self):
        self.ready()
        report = setup.run(self.env(), dry_run=True)
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(any(c.startswith("I1: would") for c in report["changes"]))


class LeakTests(IsolationTestCase):
    """A real session (not the probe) forwards terminal.env_passthrough and
    uploads terminal.credential_files: those must not carry the secrets."""

    def edit_terminal(self, **values):
        cfg = self.read()
        cfg["terminal"].update(values)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)

    def test_plugin_env_var_in_passthrough_is_not_isolated_and_not_probed(self):
        self.ready()
        self.edit_terminal(env_passthrough=["GITHUB_TOKEN", "QKEEE_ERP_DEV_API_SECRET"])
        report = setup.run(self.env())
        self.assertEqual(self.backend.calls, [])
        i1 = self.item(report, "I1")
        self.assertEqual(i1["status"], setup.PENDING)
        self.assertIn("QKEEE_ERP_DEV_API_SECRET", i1["reason"])

    def test_credentials_or_dotenv_as_credential_file_is_not_isolated(self):
        for value in (self.env().credentials_path, "qkeee-erp.env",
                      [{"path": os.path.join(self.home, ".env")}]):
            with self.subTest(value=value):
                self.ready()
                self.dotenv()
                self.edit_terminal(credential_files=value if isinstance(value, list) else [value])
                report = setup.run(self.env())
                self.assertEqual(self.item(report, "I1")["status"], setup.PENDING)

    def test_changing_passthrough_after_setup_needs_setup_again(self):
        self.ready()
        setup.run(self.env())
        self.edit_terminal(env_passthrough=["GITHUB_TOKEN"])
        self.assertTrue(any(u.startswith("I1") and "changed" in u for u in setup.unmet(self.env())))
        setup.run(self.env())
        self.assertEqual(setup.unmet(self.env()), [])

    def test_dotenv_created_after_the_probe_needs_setup_again(self):
        self.ready()
        setup.run(self.env())
        self.dotenv()
        self.assertTrue(any(u.startswith("I1") and ".env" in u for u in setup.unmet(self.env())))
        setup.run(self.env())
        self.assertIn(os.path.join(self.home, ".env"), self.backend.calls[-1][1])
        self.assertEqual(setup.unmet(self.env()), [])

    def test_nothing_to_probe_is_not_isolated(self):
        ok, reason = isolation.probe({"backend": "ssh", "ssh_host": "h", "ssh_user": "u"}, [],
                                     self.backend)
        self.assertFalse(ok)
        self.assertEqual(self.backend.calls, [])

    def test_home_relative_key_is_stored_as_written(self):
        """The gateway and the boot hook run with different HOMEs: expanding
        ~ would make their fingerprints differ."""
        fp = isolation.live_terminal({"terminal": {"backend": "ssh", "ssh_key": "~/.ssh/k"}}, {})
        self.assertEqual(fp["ssh_key"], "~/.ssh/k")


class FingerprintTests(IsolationTestCase):
    def setUp(self):
        super().setUp()
        self.ready()
        setup.run(self.env())
        self.assertEqual(setup.unmet(self.env()), [])

    def edit_terminal(self, **values):
        cfg = self.read()
        cfg["terminal"].update(values)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)

    def test_changed_user_is_not_isolated_until_setup_runs(self):
        self.edit_terminal(ssh_user="other")
        unmet = setup.unmet(self.env())
        self.assertTrue(any(u.startswith("I1") and "changed" in u for u in unmet), unmet)
        setup.run(self.env())
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(setup.unmet(self.env()), [])

    def test_changed_port_or_key_is_not_isolated(self):
        for change in ({"ssh_port": 2222}, {"ssh_key": self.key + "2"}):
            with self.subTest(change=change):
                self.edit_terminal(**change)
                if "ssh_key" in change:
                    open(change["ssh_key"], "w").close()
                self.assertTrue(any(u.startswith("I1") for u in setup.unmet(self.env())))

    def test_terminal_env_var_counts_where_config_leaves_the_key_out(self):
        """The terminal tool reads TERMINAL_* env vars; config.yaml keys win
        over them, env fills keys the config leaves out (hermes_cli.config
        apply_terminal_config_to_env)."""
        self.assertEqual(setup.unmet(self.env(environ={"TERMINAL_SSH_HOST": "elsewhere"})), [])
        self.assertEqual(setup.unmet(self.env(environ={"TERMINAL_SSH_PORT": "2222"})), [])
        cfg = self.read()
        del cfg["terminal"]["ssh_port"]
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        self.assertEqual(setup.unmet(self.env()), [])
        self.assertTrue(any(u.startswith("I1") for u in
                            setup.unmet(self.env(environ={"TERMINAL_SSH_PORT": "2222"}))))


class LiveTerminalTests(IsolationTestCase):
    def test_defaults_without_config_or_env(self):
        self.assertEqual(isolation.live_terminal({}, {}),
                         {"backend": "local", "ssh_host": "", "ssh_user": "", "ssh_port": 22,
                          "ssh_key": "", "env_passthrough": [], "credential_files": []})

    def test_env_backend_when_config_has_no_backend(self):
        fp = isolation.live_terminal({"terminal": {"ssh_host": "h"}},
                                     {"TERMINAL_ENV": "ssh", "TERMINAL_SSH_HOST": "x",
                                      "TERMINAL_SSH_USER": "u"})
        self.assertEqual((fp["backend"], fp["ssh_host"], fp["ssh_user"]), ("ssh", "h", "u"))

    def test_target_normalises_loopback(self):
        for host in ("localhost", "127.0.0.1", "::1", "LocalHost"):
            self.assertEqual(isolation.target({"backend": "ssh", "ssh_host": host,
                                               "ssh_user": "hermes-terminal"}),
                             "hermes-terminal@localhost")
        self.assertIsNone(isolation.target({"backend": "local"}))


class ExclusiveTargetTests(IsolationTestCase):
    """I2: one ERP-enabled Profile per terminal target."""

    def setUp(self):
        super().setUp()
        self.root = self.home
        self.home = os.path.join(self.root, "profiles", "b")
        os.makedirs(self.home)
        self.key = os.path.join(self.home, ".terminal-ssh", "id_ed25519").replace("\\", "/")

    def claim(self, home, target_user="hermes", host="terminal", isolated=True):
        data = os.path.join(home, "plugin-data", "qkeee-erp")
        os.makedirs(data, exist_ok=True)
        with open(os.path.join(data, "isolation.json"), "w", encoding="utf-8") as f:
            json.dump({"isolated": isolated, "target": f"{target_user}@{host}",
                       "fingerprint": {"backend": "ssh", "ssh_host": host,
                                       "ssh_user": target_user}}, f)

    def test_same_target_in_a_second_profile_is_refused(self):
        self.claim(os.path.join(self.root, "profiles", "a"))
        self.ready()
        report = setup.run(self.env())
        i2 = self.item(report, "I2")
        self.assertEqual(i2["status"], setup.PENDING)
        self.assertIn("hermes@terminal", i2["reason"])
        self.assertIn(os.path.join("profiles", "a"), i2["reason"])
        self.assertIn("I2", self.item(report, "I1")["reason"])
        self.assertEqual(self.backend.calls, [])  # no probe of a shared target
        self.assertTrue(any(u.startswith("I2") for u in setup.unmet(self.env())))

    def test_the_default_profile_counts_too(self):
        self.claim(self.root)
        self.ready()
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I2")["status"], setup.PENDING)

    def test_a_different_target_is_fine(self):
        self.claim(os.path.join(self.root, "profiles", "a"), target_user="other")
        self.ready()
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I2")["status"], setup.DONE)
        self.assertEqual(self.item(report, "I1")["status"], setup.DONE)

    def test_a_profile_that_is_not_isolated_does_not_claim(self):
        self.claim(os.path.join(self.root, "profiles", "a"), isolated=False)
        self.ready()
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I2")["status"], setup.DONE)

    def test_its_own_record_does_not_count(self):
        self.ready()
        setup.run(self.env())
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I2")["status"], setup.DONE)

    def test_a_broken_record_elsewhere_is_ignored(self):
        data = os.path.join(self.root, "profiles", "a", "plugin-data", "qkeee-erp")
        os.makedirs(data)
        with open(os.path.join(data, "isolation.json"), "w") as f:
            f.write("{not json")
        self.ready()
        report = setup.run(self.env())
        self.assertEqual(self.item(report, "I2")["status"], setup.DONE)


class RegisterTests(IsolationTestCase):
    """register() registers erp_* tools only with a matching Isolation record."""

    def register(self):
        return base.RegisterContractTests.register(self)

    def test_no_tools_when_not_isolated(self):
        self.ready()
        self.backend.readable = {self.env().credentials_path}
        setup.run(self.env())
        self.assertEqual(self.register().tools, [])

    def test_tools_when_isolated(self):
        self.ready()
        setup.run(self.env())
        self.assertEqual(len(self.register().tools), 5)

    def test_no_tools_after_a_terminal_change(self):
        self.ready()
        setup.run(self.env())
        cfg = self.read()
        cfg["terminal"]["ssh_host"] = "elsewhere"
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        self.assertEqual(self.register().tools, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
