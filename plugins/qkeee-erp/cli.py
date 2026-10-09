"""Operator CLI: `hermes -p <profile> qkeee-erp <command>` (agents
.scratch/qkeee-erp-plugin-profile-split, issue 05; spec D4).

For the Operator, not the agent. The agent uses the erp_* tools.

- Each ERP command builds the matching erp_* tool's arguments and runs
  that tool's handler (erp_tools.ErpTools): one code path for gates,
  confirmation, audit and error handling.
- A CLI run has no gateway sender, so every command that reads or writes
  ERPNext names its Requester with --requested-by. The Requester Gate
  checks it as it checks a chat sender. Audit rows record channel `cli`
  and the OS user that ran the command.
- `health`, `whoami`, `list-envs` and `list-ops` need no Requester:
  `health` checks connectivity as the bot account, the others make no
  ERPNext call.
- Mode and the default Instance tag come from the plugin settings
  (settings.py); there is no --mode flag. While setup is pending, ERP
  commands refuse.

Exit codes: 0 ok, 1 error, 2 usage or invalid operation arguments (nothing
sent), 3 refused by a gate (nothing sent), 4 outcome unknown or partial.

This module imports nothing from the ERP library at load time: register()
adds the command even when the library cannot load.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REFUSED, EXIT_UNKNOWN = 0, 1, 2, 3, 4

COMMAND = "qkeee-erp"
HELP = "ERPNext Operator commands for the qkeee-erp plugin"

_DISCOVER_ACTIONS = ("apps", "modules", "meta", "resolve", "preflight")
# execute-write confirmation flags: (argparse dest, flag) -> tool argument of the same name.
_CONFIRMATION_FLAGS = (("confirmation_token", "--confirmation-token"), ("issued_at", "--issued-at"),
                       ("user_confirmation_text", "--user-confirmation-text"),
                       ("user_approved", "--user-approved"), ("approval_note", "--approval-note"))


class _Usage(Exception):
    """A malformed flag value: nothing was sent."""


def _json(flag, raw, expected_type):
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise _Usage(f"{flag} must be valid JSON ({e}): {raw!r}") from None
    if not isinstance(value, expected_type):
        raise _Usage(f"{flag} must be a JSON {expected_type.__name__}: {raw!r}")
    return value


def _common(p, requester: bool):
    p.add_argument("--tag", help="Instance tag; default: the plugin setting active_env")
    if requester:
        p.add_argument("--requested-by", required=True,
                       help="ERPNext user (email) this command acts for; the Requester Gate checks it")
        p.add_argument("--prompt-summary", help="one-line reason, written to the audit row")
        p.add_argument("--latest-prompt", help="the request this command carries out, verbatim; "
                                               "mandatory for a write that needs confirmation")


def setup_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="qkeee_erp_command", metavar="<command>", required=True)

    p = sub.add_parser("setup", help="bring this profile to the state the plugin needs (idempotent); "
                                     "`setup status` only reports")
    p.add_argument("action", nargs="?", choices=["status"], help="report each item, change nothing")
    p.add_argument("--dry-run", action="store_true", help="print the planned changes, write nothing")
    p.add_argument("--apply-profile-fixes", action="store_true",
                   help="also write the Profile settings the prerequisites need (toolsets, terminal)")

    _common(sub.add_parser("health", help="connectivity, auth and role gaps (as the bot account)"), False)
    sub.add_parser("whoami", help="the gateway session identity this process sees (no network)")
    sub.add_parser("list-envs", help="Instance tags with a full credential set (no network)")
    sub.add_parser("list-ops", help="every registered write operation and its arguments")

    p = sub.add_parser("query", help="list records of one DocType")
    p.add_argument("doctype")
    p.add_argument("--filters", help='JSON list, e.g. \'[["status","=","Open"]]\'')
    p.add_argument("--fields", help='JSON list, e.g. \'["name","status"]\'')
    p.add_argument("--limit", type=int, default=20)
    _common(p, True)

    p = sub.add_parser("get", help="one record with its child tables")
    p.add_argument("doctype")
    p.add_argument("name")
    p.add_argument("--no-strip", action="store_true", help="return the raw doc")
    _common(p, True)

    p = sub.add_parser("report", help="run a built-in query report")
    p.add_argument("report_name")
    p.add_argument("--filters", help='JSON object, e.g. \'{"company": "Acme"}\'')
    _common(p, True)

    p = sub.add_parser("roles", help="a user's roles")
    p.add_argument("--user", default="", help="default: the bot account")
    _common(p, True)

    p = sub.add_parser("discover", help="installed apps and live DocType metadata")
    p.add_argument("action", choices=_DISCOVER_ACTIONS)
    p.add_argument("doctype", nargs="?")
    p.add_argument("--payload", help="preflight: JSON object of the planned create")
    _common(p, True)

    for name, help_text in (("render", "prepare a write; prints the request and confirmation code"),
                            ("execute-write", "run a write (the only write path)")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--op", help="operation key, see list-ops")
        p.add_argument("--args", dest="op_args", help="JSON object of the operation's arguments")
        if name == "execute-write":
            p.add_argument("--batch", help='JSON list of steps [{"op", "args", "confirmation_token"?, '
                                           '"issued_at"?, "user_confirmation_text"?}]; stops at the '
                                           'first failure')
            p.add_argument("--confirmation-token")
            p.add_argument("--issued-at", type=int)
            p.add_argument("--user-confirmation-text", help="the Requester's reply with the code")
            p.add_argument("--user-approved", action="store_true")
            p.add_argument("--approval-note")
        _common(p, True)

    p = sub.add_parser("init-bot", help="provision the bot Role and audit DocTypes on an Instance "
                                        "(elevated credentials)")
    p.add_argument("--dry-run", action="store_true", help="report the plan and print a confirm token")
    p.add_argument("--confirm-token", help="from a prior --dry-run")
    p.add_argument("--issued-at", type=int, help="from a prior --dry-run")
    _common(p, True)


class OperatorCli:
    """`read_config()` returns the profile config dict (read-only);
    `setup_env()` builds the setup_steps.SetupEnv for this profile."""

    def __init__(self, read_config, setup_env):
        self._read_config = read_config
        self._setup_env = setup_env

    def run(self, args) -> int:
        try:
            return self._run(args)
        except _Usage as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_USAGE

    def _run(self, args) -> int:
        from . import settings

        cmd = args.qkeee_erp_command
        if cmd == "setup":  # needs no Requester and runs exactly when ERP commands refuse
            return self._setup(args)
        if cmd == "whoami":
            from .qkeee_erp.core import client
            return _print_ok(client.session_identity())
        if cmd == "list-envs":
            from .qkeee_erp.core import client
            return _print_ok({"configured_tags": client.list_configured_tags()})

        cfg = self._read_config() or {}
        pending = settings.setup_pending(cfg)
        if pending:
            print(f"ERROR: refused, nothing was sent: {pending}", file=sys.stderr)
            return EXIT_REFUSED
        values = settings.plugin_settings(cfg)

        from . import erp_tools
        requester = None
        if hasattr(args, "requested_by"):
            requester = (args.requested_by or "").strip()
            if not requester:
                print("ERROR: refused, nothing was sent: --requested-by is blank", file=sys.stderr)
                return EXIT_REFUSED
        tools = erp_tools.ErpTools(settings=lambda: values,
                                   requester=lambda: _cli_requester(requester),
                                   audit=lambda tool_args: _cli_audit(cmd, tool_args))
        if cmd == "init-bot":
            return _init_bot(args, tools.tag({"tag": args.tag}), requester)
        name, tool_args = _tool_call(args)
        return _report(json.loads(tools.handler(name)(tool_args)))


    def _setup(self, args) -> int:
        from . import setup_steps
        env = self._setup_env()
        if args.action == "status":
            _print_items(setup_steps.status(env))
            if setup_steps.unmet(env):
                gap = setup_steps.version_gap(env)
                print("setup: not complete." + (f" {gap}" if gap else ""))
                return EXIT_ERROR
            print(f"setup: complete for plugin version {env.version}")
            return EXIT_OK
        report = setup_steps.run(env, dry_run=args.dry_run, apply_profile_fixes=args.apply_profile_fixes)
        _print_items(report["items"])
        for change in report["changes"]:
            print(f"  {change}")
        if report["backup"]:
            print(f"config backup: {report['backup']}")
        if report["stopped_at"]:
            print(f"ERROR: setup stopped at {report['stopped_at']}; fix it and run setup again",
                  file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK


def _print_items(rows) -> None:
    for r in rows:
        line = f"{r['key']:<3} {r['label']:<17} {r['status']:<8}"
        print(f"{line} {r['reason']}" if r["reason"] else line.rstrip())


def _cli_requester(requester: str) -> str:
    from .qkeee_erp.core import client
    # Refuses a --requested-by that names someone other than a gateway
    # sender, should this ever run inside a session.
    return client.resolve_requested_by(requester)


def _cli_audit(cmd: str, tool_args: dict) -> dict:
    from . import erp_tools
    from .qkeee_erp.core import client
    return dict(session_id=client._session_or_fallback(None), domain_code=erp_tools.DOMAIN_CODE,
                channel="cli", channel_metadata={"operator": getpass.getuser(), "command": cmd},
                prompt_summary=tool_args.get("prompt_summary"),
                latest_prompt=tool_args.get("latest_prompt"))


def _tool_call(a) -> tuple[str, dict]:
    """The erp_* tool and its arguments for one parsed command."""
    cmd = a.qkeee_erp_command
    out = {k: v for k, v in (("tag", getattr(a, "tag", None)),
                             ("prompt_summary", getattr(a, "prompt_summary", None)),
                             ("latest_prompt", getattr(a, "latest_prompt", None))) if v}
    if cmd == "health":
        return "erp_discover", {**out, "action": "health"}
    if cmd == "list-ops":
        return "erp_execute_write", {"phase": "list_ops"}
    if cmd == "query":
        return "erp_query", {**out, "doctype": a.doctype, "filters": _json("--filters", a.filters, list),
                             "fields": _json("--fields", a.fields, list), "limit": a.limit}
    if cmd == "get":
        return "erp_get", {**out, "doctype": a.doctype, "name": a.name, "no_strip": a.no_strip}
    if cmd == "report":
        return "erp_report", {**out, "report_name": a.report_name,
                              "filters": _json("--filters", a.filters, dict)}
    if cmd == "roles":
        return "erp_discover", {**out, "action": "roles", "user": a.user}
    if cmd == "discover":
        return "erp_discover", {**out, "action": a.action, "doctype": a.doctype,
                                "payload": _json("--payload", a.payload, dict)}
    phase = "render" if cmd == "render" else "execute"
    out.update(phase=phase, op=a.op, args=_json("--args", a.op_args, dict) or {})
    if cmd == "execute-write":
        batch = _json("--batch", a.batch, list)
        if batch is not None:
            single = [flag for key, flag in (("op", "--op"), ("op_args", "--args"), *_CONFIRMATION_FLAGS)
                      if getattr(a, key)]
            if single:
                raise _Usage(f"--batch carries each step's op, args and confirmation; do not combine "
                             f"it with {', '.join(single)}")
            del out["op"]
            del out["args"]
            out["batch"] = batch
        out.update({key: getattr(a, key) for key, _ in _CONFIRMATION_FLAGS if getattr(a, key)})
    return "erp_execute_write", out


def _print_ok(result) -> int:
    print(json.dumps({"ok": True, "result": result}, indent=2, default=str))
    return EXIT_OK


def _report(out: dict) -> int:
    """Print a tool envelope; map it to an exit code."""
    print(json.dumps(out, indent=2, default=str))
    for warning in out.get("warnings") or []:
        print(f"WARN: {warning}", file=sys.stderr)
    result = out.get("result")
    if out.get("ok"):
        return EXIT_OK
    if isinstance(result, dict) and result.get("stopped_at"):  # a batch stopped at a failed step
        failed = result["steps"][result["stopped_at"] - 1]
        out = {**failed, "error": f"batch stopped at step {result['stopped_at']}: {failed.get('error')}"}
    error = out.get("error") or "failed"
    if out.get("refused"):
        print(f"ERROR: refused, nothing was sent: {error}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"ERROR: {error}", file=sys.stderr)
    if out.get("invalid_arguments"):
        return EXIT_USAGE
    if out.get("outcome_unknown"):
        return EXIT_UNKNOWN
    return EXIT_ERROR


def _init_bot(a, tag: str, requester: str) -> int:
    """No erp_* tool provisions an Instance: this calls init_bot directly."""
    from .qkeee_erp import init_bot
    from .qkeee_erp.core import client
    try:
        requester = _cli_requester(requester)
        if a.dry_run:
            init_bot.run_dry_run(tag, requester)
        else:
            init_bot.run_real(tag, requester, a.confirm_token, a.issued_at)
    except client.GateRefusal as e:
        print(f"ERROR: refused, nothing was sent: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except client.ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK
