"""`hermes qkeee-erp setup` (agents .scratch/qkeee-erp-plugin-profile-split,
issue 07; spec D5-D7, D13).

One idempotent run brings a Docker install, a host install, an update and
an existing install to the same state. Each item checks whether its result
is already present; only a missing result is applied.

| Item | Kind | Done when |
|---|---|---|
| S1 credentials | step | `plugin-data/qkeee-erp/qkeee-erp.env` exists and no legacy `<profile>/qkeee-erp.env` remains (a lone legacy file is moved, mode 600) |
| S2 config | step | no legacy `skills.config.qkeee_erp` section and no `scripts_dir` setting (settings.migrate_legacy_config) |
| P1 toolsets | prerequisite | google_chat has `qkeee_erp`; no ERP chat platform (google_chat, or any but cli listing `qkeee_erp`) has `code_execution`; `known_plugin_toolsets` lists `qkeee_erp` for cli, discord and google_chat |
| P2 terminal | prerequisite | `terminal.backend` is `ssh` with host, user and key set |
| S3 terminal key | step | the `terminal.ssh_key` file exists (created if missing) |

S3 runs after P2 so that `--apply-profile-fixes` can switch the backend
and then create its key in one run. Issue 10 appends the Isolation items
(I1 probe, I2 exclusive target).

- Steps (S*) write without a flag. Prerequisites (P*) change Profile
  settings: they only report, unless `apply_profile_fixes`.
- Before the first config write of a run: `config.yaml.bak-<UTC>` (D13:
  no down-steps; restore the backup). Never overwrites an earlier backup.
- After each config write, the run reads config.yaml back and checks the
  item against what was written, not against what it meant to write.
- A step that raises is `failed` and stops the run; the next run starts
  over and skips what is done. A `pending` item (an Operator must act, or a
  prerequisite without the flag) does not stop the run.
- State: `plugin-data/qkeee-erp/setup.json` (items, time, plugin version).
  The version is recorded only by a run that ends with every item done.
  `status()` shows a failure recorded there while the item is still not
  done.
- `unmet()` is what register() checks (D7): every item done now, and setup
  run for this plugin version (so a Profile update that brings a new
  plugin version needs setup again). It never writes.

No Hermes or ERP library import at load time: the Operator CLI reaches
this module even when the library cannot load.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from . import settings

DONE, PENDING, FAILED = "done", "pending", "failed"
STEP, PREREQUISITE = "step", "prerequisite"
PLUGIN_ID = settings.PLUGIN_ID
SETUP_COMMAND = "hermes qkeee-erp setup"

# Values from the shipped Profile config.yaml that P1/P2 write with the flag
# (tests/test_plugin_setup.py ShippedValuesTests keeps them equal).
CHAT_PLATFORM = "google_chat"
SHIPPED_GOOGLE_CHAT = ["clarify", "cronjob", "delegation", "file", "memory", "qkeee_erp",
                       "session_search", "skills", "terminal", "todo", "vision", "web"]
KNOWN_TOOLSET_PLATFORMS = ("cli", "discord", "google_chat")
OPERATOR_PLATFORM = "cli"  # Kanban workers and the Operator: may keep code_execution
TOOLSET = "qkeee_erp"
DOCKER_TERMINAL = {"ssh_host": "terminal", "ssh_user": "hermes", "ssh_port": 22,
                   "ssh_key": "/opt/data/.terminal-ssh/id_ed25519"}
HOST_TERMINAL_USER = "hermes-terminal"
CONTAINER_ENV = "QKEEE_ERP_IN_CONTAINER"  # set to 1 by the Dockerfile


@dataclass
class SetupEnv:
    """Everything setup touches outside its own files, injected for tests.

    - `read_config()`: the profile's config.yaml as written (no defaults).
    - `write_config(cfg)`: write it back (production: hermes_cli.config).
    - `keygen(path)`: create an ssh key pair at path / path + ".pub".
    """
    hermes_home: str
    read_config: Callable[[], dict]
    write_config: Callable[[dict], None]
    version: str
    in_container: bool = False
    keygen: Callable[[str], None] = None
    docker_terminal: dict = field(default_factory=lambda: dict(DOCKER_TERMINAL))
    now: Callable[[], _dt.datetime] = field(
        default=lambda: _dt.datetime.now(_dt.timezone.utc))

    @property
    def config_path(self) -> str:
        return os.path.join(self.hermes_home, "config.yaml")

    @property
    def data_dir(self) -> str:
        return os.path.join(self.hermes_home, "plugin-data", PLUGIN_ID)

    @property
    def credentials_path(self) -> str:
        return os.path.join(self.data_dir, "qkeee-erp.env")

    @property
    def legacy_credentials_path(self) -> str:
        return os.path.join(self.hermes_home, "qkeee-erp.env")

    def terminal_defaults(self) -> dict:
        if self.in_container:
            return dict(self.docker_terminal)
        return {"ssh_host": "localhost", "ssh_user": HOST_TERMINAL_USER, "ssh_port": 22,
                "ssh_key": os.path.join(self.hermes_home, ".terminal-ssh", "id_ed25519")}


def state_path(hermes_home: str) -> str:
    return os.path.join(hermes_home, "plugin-data", PLUGIN_ID, "setup.json")


# -- helpers -----------------------------------------------------------------

def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _child(parent: dict, key: str) -> dict:
    """parent[key] as a dict, replacing a missing or non-dict value."""
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def _terminal(cfg) -> dict:
    return _mapping(_mapping(cfg).get("terminal"))


def _ssh_key_path(cfg) -> str | None:
    """The ssh terminal's key path (~ expanded), or None when the terminal
    is not an ssh terminal with a key."""
    term = _terminal(cfg)
    if term.get("backend") != "ssh" or not term.get("ssh_key"):
        return None
    return os.path.expanduser(str(term["ssh_key"]))


class _Item:
    key = label = kind = ""
    writes_config = False

    def check(self, cfg, env) -> str | None:
        """None when done, else why not."""
        raise NotImplementedError

    def apply(self, cfg, env, dry: bool = False) -> list[str]:
        """Bring the result about; returns one line per change. Config items
        change `cfg` in place; the runner writes it. `dry`: change `cfg`
        only, touch no file."""
        raise NotImplementedError


class _Credentials(_Item):
    key, label, kind = "S1", "credentials", STEP

    def check(self, cfg, env):
        new, old = env.credentials_path, env.legacy_credentials_path
        if os.path.exists(new) and os.path.exists(old):
            return (f"both {new} and the legacy {old} exist; only the first is read. An Operator "
                    f"checks that it is current, then deletes {old}")
        if os.path.exists(new):
            return None
        if os.path.exists(old):
            return f"legacy {old} not yet moved to {new}"
        return (f"no ERPNext credentials: an Operator creates {new} (mode 600; template "
                f"qkeee-erp-associate.env.example), then runs setup again")

    def apply(self, cfg, env, dry=False):
        new, old = env.credentials_path, env.legacy_credentials_path
        if not os.path.exists(old) or os.path.exists(new):
            return []  # the Operator must act; check() says how
        if not dry:
            os.makedirs(env.data_dir, exist_ok=True)
            shutil.move(old, new)
            os.chmod(new, 0o600)
        return [f"moved {old} to {new} (mode 600)"]


class _Config(_Item):
    key, label, kind = "S2", "plugin config", STEP
    writes_config = True

    def check(self, cfg, env):
        legacy = settings.setup_pending(cfg)
        if legacy:
            return legacy
        dropped = [k for k in settings.DROPPED if k in settings.plugin_settings(cfg)]
        if dropped:
            return f"plugins.entries.{PLUGIN_ID}.settings still holds {', '.join(dropped)}"
        return None

    def apply(self, cfg, env, dry=False):
        return settings.migrate_legacy_config(cfg)


def _erp_chat_platforms(toolsets: dict) -> list[str]:
    """Chat platforms that serve ERP (google_chat, or any listing qkeee_erp)
    yet keep code_execution, which could read the credentials. cli is the
    Operator's and the Kanban workers' platform and keeps it."""
    return [p for p, tools in sorted(toolsets.items())
            if p != OPERATOR_PLATFORM and isinstance(tools, list) and "code_execution" in tools
            and (p == CHAT_PLATFORM or TOOLSET in tools)]


class _Toolsets(_Item):
    key, label, kind = "P1", "toolsets", PREREQUISITE
    writes_config = True

    def check(self, cfg, env):
        problems = []
        toolsets = _mapping(cfg.get("platform_toolsets"))
        chat = toolsets.get(CHAT_PLATFORM)
        if not isinstance(chat, list):
            problems.append(f"platform_toolsets.{CHAT_PLATFORM} is not set (the stock toolset "
                            f"includes code_execution)")
        elif TOOLSET not in chat:
            problems.append(f"platform_toolsets.{CHAT_PLATFORM} lacks {TOOLSET}")
        for platform in _erp_chat_platforms(toolsets):
            problems.append(f"platform_toolsets.{platform} has code_execution next to {TOOLSET}")
        known = _mapping(cfg.get("known_plugin_toolsets"))
        missing = [p for p in KNOWN_TOOLSET_PLATFORMS
                   if TOOLSET not in (known.get(p) if isinstance(known.get(p), list) else [])]
        if missing:
            problems.append(f"known_plugin_toolsets lacks {TOOLSET} for {', '.join(missing)}")
        return "; ".join(problems) or None

    def apply(self, cfg, env, dry=False):
        changes = []
        toolsets = _child(cfg, "platform_toolsets")
        if not isinstance(toolsets.get(CHAT_PLATFORM), list):
            toolsets[CHAT_PLATFORM] = list(SHIPPED_GOOGLE_CHAT)
            changes.append(f"platform_toolsets.{CHAT_PLATFORM} = the shipped list")
        elif TOOLSET not in toolsets[CHAT_PLATFORM]:
            toolsets[CHAT_PLATFORM].append(TOOLSET)
            changes.append(f"platform_toolsets.{CHAT_PLATFORM} += {TOOLSET}")
        for platform in _erp_chat_platforms(toolsets):
            toolsets[platform].remove("code_execution")
            changes.append(f"platform_toolsets.{platform} -= code_execution")
        known = _child(cfg, "known_plugin_toolsets")
        for platform in KNOWN_TOOLSET_PLATFORMS:
            if not isinstance(known.get(platform), list):
                known[platform] = []
            if TOOLSET not in known[platform]:
                known[platform].append(TOOLSET)
                changes.append(f"known_plugin_toolsets.{platform} += {TOOLSET}")
        return changes


class _Terminal(_Item):
    key, label, kind = "P2", "terminal backend", PREREQUISITE
    writes_config = True

    def check(self, cfg, env):
        term = _terminal(cfg)
        if term.get("backend") != "ssh":
            return (f"terminal.backend is {term.get('backend') or 'unset'!r}, not 'ssh': the agent's "
                    f"terminal could read the gateway's secrets (agents ADR 0006)")
        missing = [k for k in ("ssh_host", "ssh_user", "ssh_key") if not term.get(k)]
        if missing:
            return f"terminal.{', terminal.'.join(missing)} not set"
        return None

    def apply(self, cfg, env, dry=False):
        term = _child(cfg, "terminal")
        changes = []
        if term.get("backend") != "ssh":
            term["backend"] = "ssh"
            changes.append("terminal.backend = 'ssh'")
        for k, v in env.terminal_defaults().items():
            if not term.get(k):
                term[k] = v
                changes.append(f"terminal.{k} = {v!r}")
        return changes


class _TerminalKey(_Item):
    key, label, kind = "S3", "terminal key", STEP

    def check(self, cfg, env):
        path = _ssh_key_path(cfg)
        if path is None:
            return "waits for P2 (an ssh terminal with a key path)"
        if not os.path.exists(path):
            return f"no ssh key at {path}"
        return None

    def apply(self, cfg, env, dry=False):
        path = _ssh_key_path(cfg)
        if path is None:
            return []
        if not dry:
            env.keygen(path)
        term = _terminal(cfg)
        return [f"created ssh key {path}; add {path}.pub to {term.get('ssh_user')}@"
                f"{term.get('ssh_host')}'s authorized_keys (root step)"]


ITEMS = (_Credentials(), _Config(), _Toolsets(), _Terminal(), _TerminalKey())


# -- state -------------------------------------------------------------------

def _read_state(hermes_home: str) -> dict:
    try:
        with open(state_path(hermes_home), encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(env, items: list, complete: bool) -> None:
    previous = _read_state(env.hermes_home)
    state = {"items": {i["key"]: {"status": i["status"], "reason": i["reason"]} for i in items},
             "time": env.now().isoformat(timespec="seconds")}
    if complete:
        state["version"] = env.version
    else:
        last = previous.get("version") or previous.get("last_complete_version")
        if last:
            state["last_complete_version"] = last
    os.makedirs(env.data_dir, exist_ok=True)
    tmp = state_path(env.hermes_home) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, state_path(env.hermes_home))


def _backup(env) -> str | None:
    if not os.path.exists(env.config_path):
        return None
    base = f"{env.config_path}.bak-{env.now().strftime('%Y%m%dT%H%M%SZ')}"
    path, n = base, 1
    while os.path.exists(path):  # never overwrite an earlier backup
        path, n = f"{base}-{n}", n + 1
    shutil.copy2(env.config_path, path)
    return path


# -- public API --------------------------------------------------------------

def _row(item, status, reason):
    return {"key": item.key, "label": item.label, "kind": item.kind, "status": status,
            "reason": reason}


def status(env) -> list[dict]:
    """Each item: done, pending, or failed (the last run failed on it and it
    is still not done), with the reason. Writes nothing."""
    cfg = env.read_config() or {}
    recorded = _mapping(_read_state(env.hermes_home).get("items"))
    rows = []
    for item in ITEMS:
        reason = item.check(cfg, env)
        last = _mapping(recorded.get(item.key))
        if reason is None:
            rows.append(_row(item, DONE, None))
        elif last.get("status") == FAILED:
            rows.append(_row(item, FAILED, f"last run: {last.get('reason')}; now: {reason}"))
        else:
            rows.append(_row(item, PENDING, reason))
    return rows


def version_gap(env) -> str | None:
    """Why setup must run again for this plugin version, or None."""
    recorded = _read_state(env.hermes_home).get("version")
    if recorded == env.version:
        return None
    return (f"setup has not run for plugin version {env.version} (last: {recorded or 'never'}); "
            f"run `{SETUP_COMMAND}`")


def unmet(env) -> list[str]:
    """Why the plugin must not serve ERP calls; [] when setup is complete."""
    out = [f"{r['key']} {r['label']}: {r['reason']}" for r in status(env) if r["status"] != DONE]
    gap = version_gap(env)
    if not out and gap:
        out.append(gap)
    return out


def run(env, *, dry_run: bool = False, apply_profile_fixes: bool = False) -> dict:
    """Run every item in order. Returns {items, changes, backup, stopped_at}.
    A dry run carries each planned config change forward, so later items
    plan against it."""
    cfg = copy.deepcopy(env.read_config() or {})
    rows, changes = [], []
    backup, stopped_at = None, None
    for item in ITEMS:
        if stopped_at:
            rows.append(_row(item, PENDING, f"not run: {stopped_at} failed"))
            continue
        reason = item.check(cfg, env)
        if reason is None:
            rows.append(_row(item, DONE, None))
            continue
        if item.kind == PREREQUISITE and not apply_profile_fixes:
            rows.append(_row(item, PENDING, f"{reason} (Profile setting: fix it by hand, or run "
                                            f"setup --apply-profile-fixes)"))
            continue
        if dry_run:
            changes += [f"{item.key}: would {c}" for c in item.apply(cfg, env, dry=True)]
            rows.append(_row(item, PENDING, reason))
            continue
        try:
            if item.writes_config:
                working = copy.deepcopy(cfg)
                done = item.apply(working, env)
                if done:
                    if backup is None:
                        backup = _backup(env)
                    env.write_config(working)
                    cfg = copy.deepcopy(env.read_config() or {})  # what was really written
            else:
                done = item.apply(cfg, env)
        except Exception as e:  # a failed step stops the run (spec "setup")
            rows.append(_row(item, FAILED, f"{type(e).__name__}: {e}"))
            stopped_at = item.key
            continue
        changes += [f"{item.key}: {c}" for c in done]
        reason = item.check(cfg, env)
        rows.append(_row(item, DONE if reason is None else PENDING, reason))
    if not dry_run:
        _write_state(env, rows, complete=all(r["status"] == DONE for r in rows))
    return {"items": rows, "changes": changes, "backup": backup, "stopped_at": stopped_at}


# -- production wiring -------------------------------------------------------

def plugin_version() -> str:
    """`version:` from this plugin's plugin.yaml (no YAML import needed)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return "unknown"


def _ssh_keygen(path: str) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "qkeee-erp-terminal",
                    "-f", path], check=True, capture_output=True, text=True)


def in_container() -> bool:
    """The Docker image sets QKEEE_ERP_IN_CONTAINER=1; /.dockerenv covers an
    image built before that."""
    return os.environ.get(CONTAINER_ENV) == "1" or os.path.exists("/.dockerenv")


def production_env() -> SetupEnv:
    """The env for the running profile: Hermes reads and writes config.yaml."""
    from hermes_cli.config import read_user_config_raw, save_config
    from hermes_constants import get_hermes_home
    return SetupEnv(hermes_home=str(get_hermes_home()), read_config=read_user_config_raw,
                    write_config=save_config, version=plugin_version(),
                    in_container=in_container(), keygen=_ssh_keygen)
