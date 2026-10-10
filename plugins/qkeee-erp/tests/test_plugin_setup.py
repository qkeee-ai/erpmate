"""`hermes qkeee-erp setup` (agents .scratch/qkeee-erp-plugin-profile-split,
issue 07): steps S1-S3 and prerequisites P1-P2 against config fixtures in a
temporary HERMES_HOME. No Hermes: the test env reads and writes config.yaml
with PyYAML, and a fake keygen writes the key file."""

import json
import os
import shutil
import tempfile
import unittest

import yaml

from qkeee_erp_plugin import setup_steps as setup
from testsupport import FakeBackend

VERSION = "0.2.0"

FRESH = """\
# erpmate shipped config (abridged)
platform_toolsets:
  cli: [clarify, code_execution, qkeee_erp, terminal]
  discord: [clarify, code_execution, terminal]
  google_chat: [clarify, file, qkeee_erp, terminal]
known_plugin_toolsets:
  cli: [qkeee_erp, spotify]
  discord: [qkeee_erp, spotify]
  google_chat: [qkeee_erp]
terminal:
  backend: ssh
  cwd: /opt/cwd
  ssh_host: terminal
  ssh_user: hermes
  ssh_port: 22
  ssh_key: {key}
"""

DEV_ERP = """\
platform_toolsets:
  cli: [clarify, code_execution, qkeee_erp, terminal]
  google_chat: [clarify, code_execution, file, terminal]
known_plugin_toolsets:
  cli: [spotify]
skills:
  config:
    qkeee_erp:
      active_env: dev
      mode: read-write
terminal:
  backend: local
  cwd: /opt/cwd
"""

HALF_DONE = """\
platform_toolsets:
  google_chat: [clarify, file, qkeee_erp, terminal]
known_plugin_toolsets:
  cli: [qkeee_erp]
  discord: [qkeee_erp]
  google_chat: [qkeee_erp]
plugins:
  entries:
    qkeee-erp:
      settings:
        active_env: dev
terminal:
  backend: local
"""

COMMENT_HEAVY = """\
# Heavily commented config: nothing here needs a change,
# so setup must not rewrite the file.
platform_toolsets:
  # google chat serves the Requesters
  google_chat: [clarify, qkeee_erp]   # trailing comment
known_plugin_toolsets:
  cli: [qkeee_erp]
  discord: [qkeee_erp]
  google_chat: [qkeee_erp]
terminal:
  backend: ssh   # sidecar
  ssh_host: terminal
  ssh_user: hermes
  ssh_key: {key}
"""

NON_ERPMATE = """\
model:
  default: some/model
"""


class SetupTestCase(unittest.TestCase):
    in_container = True

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.key = os.path.join(self.home, ".terminal-ssh", "id_ed25519").replace("\\", "/")
        self.docker_key = os.path.join(self.home, "docker-terminal-ssh", "id_ed25519")
        self.keygen_calls = []
        self.write_calls = 0

    # -- fixture helpers -----------------------------------------------------

    def config(self, text):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(text.format(key=self.key))

    @property
    def config_path(self):
        return os.path.join(self.home, "config.yaml")

    def read(self):
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def raw(self):
        with open(self.config_path, encoding="utf-8") as f:
            return f.read()

    def credentials(self, legacy=False):
        path = (os.path.join(self.home, "qkeee-erp.env") if legacy
                else os.path.join(self.home, "plugin-data", "qkeee-erp", "qkeee-erp.env"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("QKEEE_ERP_DEV_BASE_URL=https://e\n")
        return path

    def make_key(self):
        os.makedirs(os.path.dirname(self.key), exist_ok=True)
        open(self.key, "w").close()

    def env(self, **kw):
        def write(cfg):
            self.write_calls += 1
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f)

        def keygen(path):
            self.keygen_calls.append(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
            open(path + ".pub", "w").close()

        values = dict(hermes_home=self.home, read_config=self.read, write_config=write,
                      version=VERSION, in_container=self.in_container, keygen=keygen,
                      docker_terminal=dict(setup.DOCKER_TERMINAL, ssh_key=self.docker_key),
                      environ={}, run_probe=FakeBackend())  # every probe read fails: Isolated
        values.update(kw)
        return setup.SetupEnv(**values)

    def backups(self):
        return [n for n in os.listdir(self.home) if n.startswith("config.yaml.bak-")]

    @staticmethod
    def statuses(report):
        return {item["key"]: item["status"] for item in report["items"]}


class FreshInstallTests(SetupTestCase):
    def test_complete_install_needs_no_change(self):
        self.config(FRESH)
        self.credentials()
        self.make_key()
        before = self.raw()
        report = setup.run(self.env())
        self.assertEqual(set(self.statuses(report).values()), {setup.DONE})
        self.assertEqual([c for c in report["changes"] if not c.startswith("I1")], [])
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.backups(), [])

    def test_without_credentials_s1_is_pending_and_the_run_continues(self):
        self.config(FRESH)
        self.make_key()
        report = setup.run(self.env())
        status = self.statuses(report)
        self.assertEqual(status["S1"], setup.PENDING)
        self.assertEqual(status["P2"], setup.DONE)
        reason = next(i["reason"] for i in report["items"] if i["key"] == "S1")
        self.assertIn("plugin-data", reason)

    def test_missing_ssh_key_is_created_and_its_public_key_named(self):
        self.config(FRESH)
        self.credentials()
        report = setup.run(self.env())
        self.assertEqual(self.keygen_calls, [self.key])
        self.assertEqual(self.statuses(report)["S3"], setup.DONE)
        self.assertTrue(any(self.key + ".pub" in c for c in report["changes"]))


class DevErpLikeTests(SetupTestCase):
    def setUp(self):
        super().setUp()
        self.config(DEV_ERP)
        self.credentials(legacy=True)

    def test_without_the_flag_only_plugin_steps_write(self):
        report = setup.run(self.env())
        cfg = self.read()
        status = self.statuses(report)
        self.assertEqual((status["S1"], status["S2"]), (setup.DONE, setup.DONE))
        self.assertEqual((status["P1"], status["P2"]), (setup.PENDING, setup.PENDING))
        self.assertEqual(status["S3"], setup.PENDING)  # backend is not ssh yet
        # S1 moved the legacy credentials.
        self.assertFalse(os.path.exists(os.path.join(self.home, "qkeee-erp.env")))
        self.assertTrue(os.path.exists(os.path.join(self.home, "plugin-data", "qkeee-erp", "qkeee-erp.env")))
        # S2 moved the settings; the Profile settings stayed as they were.
        self.assertNotIn("qkeee_erp", cfg.get("skills", {}).get("config", {}))
        self.assertEqual(cfg["plugins"]["entries"]["qkeee-erp"]["settings"],
                         {"active_env": "dev", "mode": "read-write"})
        self.assertIn("code_execution", cfg["platform_toolsets"]["google_chat"])
        self.assertEqual(cfg["terminal"]["backend"], "local")
        self.assertEqual(len(self.backups()), 1)

    def test_prerequisites_report_their_reasons(self):
        report = setup.run(self.env(), dry_run=True)
        reasons = {i["key"]: i["reason"] for i in report["items"]}
        self.assertIn("code_execution", reasons["P1"])
        self.assertIn("qkeee_erp", reasons["P1"])
        self.assertIn("ssh", reasons["P2"])
        self.assertIn("--apply-profile-fixes", reasons["P1"])

    def test_with_the_flag_profile_settings_get_the_erpmate_values(self):
        report = setup.run(self.env(), apply_profile_fixes=True)
        cfg = self.read()
        self.assertEqual(set(self.statuses(report).values()), {setup.DONE})
        chat = cfg["platform_toolsets"]["google_chat"]
        self.assertIn("qkeee_erp", chat)
        self.assertNotIn("code_execution", chat)
        self.assertIn("clarify", chat)  # other tools kept
        for platform in ("cli", "discord", "google_chat"):
            self.assertIn("qkeee_erp", cfg["known_plugin_toolsets"][platform])
        self.assertIn("spotify", cfg["known_plugin_toolsets"]["cli"])
        term = cfg["terminal"]
        self.assertEqual((term["backend"], term["ssh_host"], term["ssh_user"], term["ssh_port"]),
                         ("ssh", "terminal", "hermes", 22))
        self.assertEqual(term["cwd"], "/opt/cwd")
        self.assertEqual(self.keygen_calls, [term["ssh_key"]])
        self.assertEqual(len(self.backups()), 1)

    def test_second_run_changes_nothing_and_makes_no_backup(self):
        setup.run(self.env(), apply_profile_fixes=True)
        before, writes = self.raw(), self.write_calls
        report = setup.run(self.env(), apply_profile_fixes=True)
        self.assertEqual(report["changes"], [])
        self.assertEqual((self.raw(), self.write_calls), (before, writes))
        self.assertEqual(len(self.backups()), 1)

    def test_dry_run_writes_nothing(self):
        before = self.raw()
        report = setup.run(self.env(), dry_run=True, apply_profile_fixes=True)
        self.assertEqual(self.raw(), before)
        self.assertEqual((self.write_calls, self.backups(), self.keygen_calls), (0, [], []))
        self.assertTrue(os.path.exists(os.path.join(self.home, "qkeee-erp.env")))
        self.assertTrue(report["changes"])  # the plan
        self.assertFalse(os.path.exists(setup.state_path(self.home)))

    def test_a_failing_step_stops_the_run_and_does_not_advance_the_state(self):
        def broken(cfg):
            raise OSError("disk full")
        report = setup.run(self.env(write_config=broken), apply_profile_fixes=True)
        status = self.statuses(report)
        self.assertEqual(status["S2"], setup.FAILED)
        self.assertEqual(report["stopped_at"], "S2")
        self.assertEqual((status["P1"], status["P2"], status["S3"]),
                         (setup.PENDING, setup.PENDING, setup.PENDING))
        self.assertEqual(self.keygen_calls, [])
        self.assertIn("qkeee_erp", self.read()["skills"]["config"])
        self.assertTrue(setup.unmet(self.env()))
        with open(setup.state_path(self.home), encoding="utf-8") as f:
            self.assertNotEqual(json.load(f).get("version"), VERSION)


class HalfDoneTests(SetupTestCase):
    def test_only_the_missing_items_change(self):
        self.config(HALF_DONE)
        self.credentials()
        report = setup.run(self.env(), apply_profile_fixes=True)
        self.assertEqual(set(self.statuses(report).values()), {setup.DONE})
        self.assertTrue(all(c.startswith(("P2", "S3", "I1")) for c in report["changes"]), report["changes"])


class CommentHeavyTests(SetupTestCase):
    def test_a_config_that_needs_nothing_keeps_its_comments(self):
        self.config(COMMENT_HEAVY)
        self.credentials()
        self.make_key()
        before = self.raw()
        report = setup.run(self.env(), apply_profile_fixes=True)
        self.assertEqual(set(self.statuses(report).values()), {setup.DONE})
        self.assertEqual(self.raw(), before)


class NonErpmateTests(SetupTestCase):
    def test_without_the_flag_nothing_is_written(self):
        self.config(NON_ERPMATE)
        self.credentials()
        before = self.raw()
        report = setup.run(self.env())
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.write_calls, 0)
        status = self.statuses(report)
        self.assertEqual((status["P1"], status["P2"]), (setup.PENDING, setup.PENDING))


class HostInstallTests(SetupTestCase):
    in_container = False

    def test_host_defaults_point_at_a_local_os_user(self):
        self.config(NON_ERPMATE)
        self.credentials()
        setup.run(self.env(), apply_profile_fixes=True)
        term = self.read()["terminal"]
        self.assertEqual((term["backend"], term["ssh_user"]), ("ssh", "hermes-terminal"))
        self.assertIn(term["ssh_host"], ("localhost", "127.0.0.1"))
        self.assertTrue(term["ssh_key"].replace("\\", "/").startswith(self.home.replace("\\", "/")))
        self.assertEqual(self.keygen_calls, [term["ssh_key"]])

    def copy_shipped_config(self):
        repo = os.path.join(os.path.dirname(__file__), "..", "..", "..")
        shutil.copy(os.path.join(repo, "config.yaml"), self.config_path)

    def test_shipped_sidecar_values_are_pending_on_a_host(self):
        """`profile install` copies the shipped config, whose terminal points
        at the Docker sidecar; on a host that target does not exist."""
        self.copy_shipped_config()
        self.credentials()
        report = setup.run(self.env())
        p2 = next(i for i in report["items"] if i["key"] == "P2")
        self.assertEqual(p2["status"], setup.PENDING)
        self.assertIn("sidecar", p2["reason"])

    def test_with_the_flag_sidecar_values_become_host_values(self):
        self.copy_shipped_config()
        self.credentials()
        report = setup.run(self.env(), apply_profile_fixes=True)
        term = self.read()["terminal"]
        self.assertEqual((term["ssh_host"], term["ssh_user"], term["cwd"]),
                         ("localhost", "hermes-terminal", "~"))
        self.assertTrue(term["ssh_key"].replace("\\", "/").startswith(self.home.replace("\\", "/")))
        self.assertEqual(self.keygen_calls, [term["ssh_key"]])
        self.assertEqual(self.statuses(report)["P2"], setup.DONE)

    def test_host_values_set_by_hand_are_kept(self):
        self.config(FRESH.replace("ssh_host: terminal", "ssh_host: 10.0.0.5")
                    .replace("cwd: /opt/cwd", "cwd: /srv/work"))
        self.credentials()
        self.make_key()
        report = setup.run(self.env(), apply_profile_fixes=True)
        self.assertEqual(self.statuses(report)["P2"], setup.DONE)
        term = self.read()["terminal"]
        self.assertEqual((term["ssh_host"], term["cwd"]), ("10.0.0.5", "/srv/work"))

    def test_in_a_container_the_sidecar_values_are_right(self):
        self.copy_shipped_config()
        self.credentials()
        report = setup.run(self.env(in_container=True))
        self.assertEqual(self.statuses(report)["P2"], setup.DONE)


class UnmetTests(SetupTestCase):
    """What register() checks: every item done and setup run for this
    plugin version."""

    def setUp(self):
        super().setUp()
        self.config(FRESH)
        self.credentials()
        self.make_key()

    def test_setup_never_run_is_unmet(self):
        unmet = setup.unmet(self.env())
        self.assertEqual(len(unmet), 1)
        self.assertIn("hermes qkeee-erp setup", unmet[0])

    def test_after_a_complete_run_nothing_is_unmet(self):
        setup.run(self.env())
        self.assertEqual(setup.unmet(self.env()), [])

    def test_a_new_plugin_version_needs_setup_again(self):
        setup.run(self.env())
        self.assertTrue(setup.unmet(self.env(version="0.3.0")))

    def test_a_config_drift_after_setup_is_unmet(self):
        setup.run(self.env())
        cfg = self.read()
        cfg["terminal"]["backend"] = "local"
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        unmet = setup.unmet(self.env())
        self.assertTrue(any(u.startswith("P2") for u in unmet), unmet)

    def test_unmet_writes_nothing(self):
        self.config(DEV_ERP)
        before = self.raw()
        setup.unmet(self.env())
        self.assertEqual((self.raw(), self.write_calls), (before, 0))


class ReviewFixTests(SetupTestCase):
    def test_old_and_new_credentials_both_present_is_pending(self):
        self.config(FRESH)
        self.make_key()
        self.credentials()
        old = self.credentials(legacy=True)
        report = setup.run(self.env())
        row = next(i for i in report["items"] if i["key"] == "S1")
        self.assertEqual(row["status"], setup.PENDING)
        self.assertIn("delete", row["reason"])
        self.assertTrue(os.path.exists(old))  # never deleted by setup

    def test_two_backups_in_the_same_second_do_not_overwrite(self):
        import datetime
        fixed = datetime.datetime(2026, 10, 9, 10, 0, 0, tzinfo=datetime.timezone.utc)
        self.config(DEV_ERP)
        setup.run(self.env(now=lambda: fixed))
        self.config(DEV_ERP)  # the legacy section again
        setup.run(self.env(now=lambda: fixed))
        self.assertEqual(len(self.backups()), 2)

    def test_a_home_relative_key_path_is_expanded(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}):
            self.config(FRESH.replace("ssh_key: {key}", "ssh_key: ~/terminal-key"))
            self.credentials()
            setup.run(self.env())
            self.assertEqual(self.keygen_calls, [os.path.expanduser("~/terminal-key")])
        self.assertTrue(self.keygen_calls[0].startswith(self.home))

    def test_last_complete_version_survives_two_incomplete_runs(self):
        self.config(FRESH)
        self.credentials()
        self.make_key()
        setup.run(self.env())
        self.config(DEV_ERP)
        setup.run(self.env(version="0.3.0"))
        setup.run(self.env(version="0.3.0"))
        with open(setup.state_path(self.home), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_complete_version"], VERSION)

    def test_version_gap_is_its_own_value(self):
        self.config(FRESH)
        self.credentials()
        self.make_key()
        self.assertIn("never", setup.version_gap(self.env()))
        setup.run(self.env())
        self.assertIsNone(setup.version_gap(self.env()))


    def test_status_shows_a_failure_from_the_last_run(self):
        self.config(DEV_ERP)

        def broken(cfg):
            raise OSError("disk full")
        setup.run(self.env(write_config=broken))
        row = next(r for r in setup.status(self.env()) if r["key"] == "S2")
        self.assertEqual(row["status"], setup.FAILED)
        self.assertIn("disk full", row["reason"])

    def test_scripts_dir_alone_is_dropped(self):
        self.config(FRESH)
        self.credentials()
        self.make_key()
        cfg = self.read()
        cfg["plugins"] = {"entries": {"qkeee-erp": {"settings": {"scripts_dir": "/x"}}}}
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        self.assertEqual(self.statuses({"items": setup.status(self.env())})["S2"], setup.PENDING)
        setup.run(self.env())
        self.assertNotIn("scripts_dir", self.read()["plugins"]["entries"]["qkeee-erp"]["settings"])

    def test_any_erp_chat_platform_loses_code_execution_but_cli_keeps_it(self):
        self.config(FRESH)
        cfg = self.read()
        cfg["platform_toolsets"]["email"] = ["code_execution", "qkeee_erp"]
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        report = setup.run(self.env(), dry_run=True)
        self.assertIn("email", next(i["reason"] for i in report["items"] if i["key"] == "P1"))
        setup.run(self.env(), apply_profile_fixes=True)
        toolsets = self.read()["platform_toolsets"]
        self.assertNotIn("code_execution", toolsets["email"])
        self.assertIn("code_execution", toolsets["cli"])
        self.assertIn("code_execution", toolsets["discord"])  # serves no ERP

    def test_dry_run_plans_the_key_after_planning_the_ssh_terminal(self):
        self.config(DEV_ERP)
        report = setup.run(self.env(), dry_run=True, apply_profile_fixes=True)
        self.assertTrue(any(c.startswith("S3: would created ssh key") for c in report["changes"]),
                        report["changes"])
        self.assertEqual(self.keygen_calls, [])


class RegisterContractTests(SetupTestCase):
    """register() through the real setup checks (no patched _setup_unmet)."""

    def register(self):
        import sys
        from unittest.mock import patch
        import qkeee_erp_plugin
        import test_plugin_register as reg
        from qkeee_erp_plugin.qkeee_erp.core import client
        self.addCleanup(client.set_session_env_reader, None)
        self.addCleanup(client.set_hermes_home_reader, None)
        ctx = reg.FakeCtx()
        with patch.dict(sys.modules, reg._hermes_modules()), \
                patch.object(setup, "production_env", self.env), \
                patch.object(qkeee_erp_plugin, "_profile_config", return_value={}):
            qkeee_erp_plugin.register(ctx)
        return ctx

    def test_tools_only_after_a_complete_setup_hooks_always(self):
        self.config(DEV_ERP)
        self.credentials(legacy=True)
        before = self.register()
        self.assertEqual(before.tools, [])
        setup.run(self.env(), apply_profile_fixes=True)
        after = self.register()
        self.assertEqual(len(after.tools), 5)
        for ctx in (before, after):
            self.assertEqual(sorted(h for h, _ in ctx.hooks),
                             ["post_tool_call", "pre_tool_call", "pre_tool_call"])


class ShippedValuesTests(unittest.TestCase):
    """The values P1/P2 write with the flag are the shipped config.yaml's."""

    def test_match_the_shipped_config(self):
        repo = os.path.join(os.path.dirname(__file__), "..", "..", "..")
        with open(os.path.join(repo, "config.yaml"), encoding="utf-8") as f:
            shipped = yaml.safe_load(f)
        self.assertEqual(sorted(shipped["platform_toolsets"]["google_chat"]),
                         sorted(setup.SHIPPED_GOOGLE_CHAT))
        for key, value in setup.DOCKER_TERMINAL.items():
            self.assertEqual(shipped["terminal"][key], value, key)
        self.assertEqual(shipped["terminal"]["cwd"], setup.DOCKER_CWD)


class OperatorCliTests(SetupTestCase):
    """`hermes qkeee-erp setup [status] [--dry-run] [--apply-profile-fixes]`."""

    def cli(self, argv):
        import testsupport
        return testsupport.run_operator_cli(argv, setup_env=self.env)

    def test_setup_status_lists_each_item_and_writes_nothing(self):
        self.config(DEV_ERP)
        before = self.raw()
        code, out, _ = self.cli(["setup", "status"])
        self.assertEqual(code, 1)
        for key in ("S1", "S2", "P1", "P2", "S3", "I2", "I1"):
            self.assertIn(key, out)
        self.assertIn("pending", out)
        self.assertEqual(self.raw(), before)

    def test_setup_status_is_0_when_complete(self):
        self.config(FRESH)
        self.credentials()
        self.make_key()
        self.cli(["setup"])
        code, out, _ = self.cli(["setup", "status"])
        self.assertEqual(code, 0, out)
        self.assertIn("complete", out)

    def test_setup_with_flags_applies_and_reports_changes_and_backup(self):
        self.config(DEV_ERP)
        self.credentials(legacy=True)
        code, out, _ = self.cli(["setup", "--apply-profile-fixes"])
        self.assertEqual(code, 0, out)
        self.assertIn("P2: terminal.backend = 'ssh'", out)
        self.assertIn("config.yaml.bak-", out)
        self.assertEqual(self.read()["terminal"]["backend"], "ssh")

    def test_setup_dry_run_writes_nothing(self):
        self.config(DEV_ERP)
        before = self.raw()
        code, out, _ = self.cli(["setup", "--dry-run", "--apply-profile-fixes"])
        self.assertEqual(code, 0)
        self.assertIn("would", out)
        self.assertEqual(self.raw(), before)

    def test_setup_failure_exits_1(self):
        self.config(DEV_ERP)

        def broken(cfg):
            raise OSError("disk full")
        import testsupport
        code, out, err = testsupport.run_operator_cli(
            ["setup"], setup_env=lambda: self.env(write_config=broken))
        self.assertEqual(code, 1)
        self.assertIn("disk full", out + err)

    def test_setup_needs_no_requester_and_runs_while_setup_is_pending(self):
        self.config(DEV_ERP)  # legacy section: ERP commands refuse, setup must not
        code, _, err = self.cli(["setup", "status"])
        self.assertNotIn("refused", err)


if __name__ == "__main__":
    unittest.main()
