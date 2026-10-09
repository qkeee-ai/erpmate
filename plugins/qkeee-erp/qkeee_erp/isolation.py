"""Isolation: proof that nothing the agent runs can read the gateway's
secrets (agents ADR 0006; .scratch/qkeee-erp-plugin-profile-split, issue
10). setup_steps runs it as items I2 and I1; register() trusts only a
stored proof whose fingerprint equals the live terminal config.

- `live_terminal(cfg, environ)`: the fingerprint of the terminal the
  agent's tools will use (backend, ssh host, user, port, key as written,
  plus `terminal.env_passthrough` and `terminal.credential_files`). Same
  precedence as Hermes' `apply_terminal_config_to_env`: a key set in
  config.yaml `terminal.*` wins, a TERMINAL_* env var fills a key the
  config leaves out, else the Hermes default. Reads only; never bridges
  into os.environ. The key stays unexpanded: the boot hook and the gateway
  run with different HOMEs.
- `leaks(fp, secret_paths)`: what a real session would hand the terminal
  that the probe cannot see (the probe-only connection forwards no env and
  uploads no files): a passthrough of a `QKEEE_ERP_*` variable, or a
  credential file naming one of the secret files. Skill-registered
  passthroughs and credential files are not covered (the Profile's skills
  carry no code).
- `probe(fp, paths, run)`: through the configured backend, try to `cat`
  each gateway path by its absolute gateway path. Isolated only when every
  read fails. The `local` terminal is the Operator's trusted shell: not
  Isolated, and nothing runs. A backend error, or output without one
  marker per path, is "not proven", never "read failed".
- `isolation.json` in plugin-data/qkeee-erp: result, reason, fingerprint,
  terminal target, probed paths, time. `check()` asks for a new probe when
  a secret file appeared that the last probe did not cover.
- `claims(root, own_home, target)`: other Profiles under the Hermes root
  whose record proves Isolation for the same terminal target
  (`ssh_user@ssh_host`). A stale record in a Profile that no longer uses
  the plugin still claims: that fails closed, and the refusal names the
  file to remove. One
  ERP-enabled Profile per terminal target, so two Profiles never share a
  terminal home.

No Hermes import at load time; `hermes_probe` imports the terminal
backend factory when it runs.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import re
import secrets
import shlex
from typing import Callable, Mapping

RECORD = "isolation.json"
PLUGIN_ID = "qkeee-erp"  # = settings.PLUGIN_ID; copied so this module stays stdlib only
SECRET_ENV_PREFIX = "QKEEE_ERP_"  # the variables in qkeee-erp.env
PROBE_TIMEOUT = 30

# terminal.<key> -> the env var the terminal tool reads (hermes_cli.config
# TERMINAL_CONFIG_ENV_MAP), and the Hermes default.
_KEYS = (("backend", "TERMINAL_ENV", "local"), ("ssh_host", "TERMINAL_SSH_HOST", ""),
         ("ssh_user", "TERMINAL_SSH_USER", ""), ("ssh_port", "TERMINAL_SSH_PORT", 22),
         ("ssh_key", "TERMINAL_SSH_KEY", ""))
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def live_terminal(cfg, environ: Mapping[str, str]) -> dict:
    """The fingerprint of the terminal the agent will use."""
    term = cfg.get("terminal") if isinstance(cfg, dict) else None
    term = term if isinstance(term, dict) else {}
    fp = {}
    for key, var, default in _KEYS:
        value = term[key] if term.get(key) not in (None, "") else environ.get(var) or default
        fp[key] = value
    try:
        fp["ssh_port"] = int(fp["ssh_port"])
    except (TypeError, ValueError):
        fp["ssh_port"] = str(fp["ssh_port"])
    fp["backend"] = str(fp["backend"]).strip().lower()
    fp["ssh_host"], fp["ssh_user"], fp["ssh_key"] = (str(fp["ssh_host"]), str(fp["ssh_user"]),
                                                     str(fp["ssh_key"]))
    fp["env_passthrough"] = sorted(str(v) for v in _as_list(term.get("env_passthrough")))
    fp["credential_files"] = sorted((json.dumps(v, sort_keys=True) if not isinstance(v, str) else v)
                                    for v in _as_list(term.get("credential_files")))
    return fp


def _as_list(value) -> list:
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def leaks(fp, secret_paths: list[str]) -> list[str]:
    """Why a real session would hand the terminal a secret; [] if none."""
    out = [f"terminal.env_passthrough forwards {name}" for name in fp.get("env_passthrough", [])
           if name.upper().startswith(SECRET_ENV_PREFIX)]
    names = {os.path.basename(p) for p in secret_paths}
    for entry in fp.get("credential_files", []):
        # An entry is a path or a JSON mapping; any path part named like a
        # secret file counts (fails closed on a same-named file elsewhere).
        if names & set(re.split(r"[\\/\"'\s,:{}\[\]]+", entry)):
            out.append(f"terminal.credential_files uploads {entry}")
    return out


def target(fp) -> str | None:
    """`ssh_user@ssh_host` of an ssh terminal (loopback names folded to
    localhost), else None."""
    if fp.get("backend") != "ssh" or not fp.get("ssh_host") or not fp.get("ssh_user"):
        return None
    host = str(fp["ssh_host"]).strip().lower()
    return f"{fp['ssh_user']}@{'localhost' if host in _LOOPBACK else host}"


def probe_command(paths: list[str], nonce: str) -> str:
    """One POSIX shell line: per path, `<nonce> <index> readable|denied`."""
    return "; ".join(
        f"if cat -- {shlex.quote(p)} >/dev/null 2>&1; then echo '{nonce} {i} readable'; "
        f"else echo '{nonce} {i} denied'; fi" for i, p in enumerate(paths))


def probe(fp, paths: list[str], run: Callable[[dict, str], dict]) -> tuple[bool, str]:
    """(isolated, reason). `run(fp, command)` executes through the backend
    and returns {"output", "returncode"}; it may raise."""
    backend = fp.get("backend")
    if backend == "local":
        return False, ("the local terminal is the Operator's trusted shell and can read "
                       "plugin-data (ADR 0006); nothing probed")
    if backend != "ssh":
        return False, f"terminal backend {backend!r} is not supported for Isolation (ssh only)"
    if not paths:
        return False, "no secret file to probe: not proven"
    nonce = "qkeee-probe-" + secrets.token_hex(8)
    try:
        result = run(fp, probe_command(paths, nonce))
    except Exception as e:  # unreachable target, auth failure, timeout: not proven
        return False, f"probe could not run through {target(fp)}: {type(e).__name__}: {e}"
    output = str((result or {}).get("output") or "")
    answers = dict(re.findall(rf"^{re.escape(nonce)} (\d+) (readable|denied)\s*$", output, re.M))
    missing = [p for i, p in enumerate(paths) if str(i) not in answers]
    if missing:
        return False, (f"probe output lacks a marker for {', '.join(missing)} (rc "
                       f"{(result or {}).get('returncode')}): not proven")
    readable = [p for i, p in enumerate(paths) if answers[str(i)] == "readable"]
    if readable:
        return False, f"the terminal {target(fp)} can read {', '.join(readable)}"
    return True, f"the terminal {target(fp)} cannot read {', '.join(paths)}"


# -- record ------------------------------------------------------------------

def record_path(home: str) -> str:
    return os.path.join(home, "plugin-data", PLUGIN_ID, RECORD)


def read_record(home: str) -> dict | None:
    try:
        with open(record_path(home), encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_record(home: str, fp: dict, isolated: bool, reason: str, now: _dt.datetime,
                 paths: list[str]) -> dict:
    record = {"isolated": isolated, "reason": reason, "fingerprint": fp, "target": target(fp),
              "paths": list(paths), "time": now.isoformat(timespec="seconds")}
    path = record_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    os.replace(tmp, path)
    return record


def check(record: dict | None, fp: dict, paths: list[str]) -> str | None:
    """None when the stored proof covers the live terminal and every secret
    file in `paths`, else why not."""
    if record is None:
        return "no Isolation probe recorded yet; run `hermes qkeee-erp setup`"
    stored = record.get("fingerprint")
    if stored != fp:
        changed = sorted(k for k in set(fp) | set(stored if isinstance(stored, dict) else {})
                         if not isinstance(stored, dict) or stored.get(k) != fp.get(k))
        return (f"terminal config changed since the probe ({', '.join(changed)}): Isolation "
                f"unknown until setup runs again")
    if record.get("isolated") is not True:
        return f"not Isolated: {record.get('reason')}"
    unprobed = [p for p in paths if p not in (record.get("paths") or [])]
    if unprobed:
        return (f"{', '.join(unprobed)} appeared after the probe: Isolation unknown until setup "
                f"runs again")
    return None


# -- exclusive target --------------------------------------------------------

def _profile_homes(root: str) -> list[str]:
    """The default profile (the root) and every named profile under it."""
    return [root] + sorted(p for p in glob.glob(os.path.join(root, "profiles", "*"))
                           if os.path.isdir(p))


def claims(root: str, own_home: str, own_target: str) -> list[str]:
    """Profile homes, other than own_home, whose record proves Isolation
    for own_target. Unreadable records claim nothing."""
    own = os.path.normcase(os.path.realpath(own_home))
    out = []
    for home in _profile_homes(root):
        if os.path.normcase(os.path.realpath(home)) == own:
            continue
        record = read_record(home)
        if not record or record.get("isolated") is not True:
            continue
        fp = record.get("fingerprint")
        theirs = target(fp) if isinstance(fp, dict) else None
        if (theirs or record.get("target")) == own_target:
            out.append(home)
    return out


# -- production backend ------------------------------------------------------

def hermes_probe(fp: dict, command: str) -> dict:
    """Run `command` once through the Hermes terminal backend for `fp`, as
    a probe: a throwaway ssh connection with no remote setup, no skills
    sync and no sync-back (spike, issue 03). Raises when it cannot connect."""
    from tools.terminal_tool_backends import _create_environment
    env = _create_environment(
        fp["backend"], image="", cwd="~", timeout=PROBE_TIMEOUT,
        ssh_config={"host": fp["ssh_host"], "user": fp["ssh_user"], "port": fp["ssh_port"],
                    "key": fp["ssh_key"]},
        task_id="qkeee-erp-isolation-probe", probe_only=True)
    try:
        return env.execute(command, timeout=PROBE_TIMEOUT)
    finally:
        env.cleanup()
