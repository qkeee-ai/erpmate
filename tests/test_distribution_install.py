"""Install and update this distribution with a real Hermes (agents
.scratch/qkeee-erp-plugin-profile-split, issue 07: the Distribution job).

- Instance Notes (`skills/qkeee-erp-learned/`) survive `profile update`
  (issue 01).
- The old skill `scripts/` tree is gone; the plugin ships its setup.
- The plugin's Operator CLI loads under Hermes: `setup status` reports a
  fresh profile as not complete (no credentials yet).

Needs the `hermes` CLI on PATH (CI installs hermes-agent at a pinned ref);
skipped otherwise. Run with `python -m pytest tests` from the repo root.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HERMES = shutil.which("hermes")
pytestmark = pytest.mark.skipif(HERMES is None, reason="hermes CLI not installed")


def _hermes(home, *args, check=True):
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_NONINTERACTIVE="1")
    result = subprocess.run([HERMES, *args], env=env, capture_output=True, text=True,
                            timeout=300, stdin=subprocess.DEVNULL)
    if check:
        assert result.returncode == 0, f"hermes {' '.join(args)}:\n{result.stdout}\n{result.stderr}"
    return result


def test_install_then_update_keeps_instance_notes_and_ships_the_plugin(tmp_path):
    home = tmp_path
    _hermes(home, "profile", "install", str(REPO), "--name", "dist", "--yes")
    profile = home / "profiles" / "dist"
    assert (profile / "plugins" / "qkeee-erp" / "setup_steps.py").is_file()
    assert not (profile / "skills" / "qkeee-erp" / "qkeee-erp-associate" / "scripts").exists()

    notes = profile / "skills" / "qkeee-erp-learned" / "demo" / "SKILL.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("---\nname: demo\ndescription: Instance Notes\n---\n", encoding="utf-8")

    _hermes(home, "profile", "update", "dist", "--yes")
    assert notes.is_file(), "profile update deleted the Instance Notes"
    assert not (profile / "skills" / "qkeee-erp" / "qkeee-erp-associate" / "scripts").exists()

    _hermes(home, "-p", "dist", "plugins", "enable", "qkeee-erp", "--no-allow-tool-override")
    status = _hermes(home, "-p", "dist", "qkeee-erp", "setup", "status", check=False)
    assert status.returncode == 1, status.stdout + status.stderr
    assert "S1" in status.stdout and "credentials" in status.stdout
