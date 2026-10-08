#!/usr/bin/env python3
"""
qkeee-erp-associate write CLI — the ONLY entry point for every write this
skill issues.

Every write is a named operation (core/operations.py) run through one
pipeline: mode -> requester -> allowlist -> ownership -> preconditions ->
confirmation token -> RBAC -> audit -> send. `--list-ops` prints every
operation with its arguments.

## Two ways to name the operation

- `--op <key> --args '<json>'` — any registered operation, e.g.
  `--op system_admin.disable_user --args '{"name": "a@b.c", "reason": "left"}'`.
- Shorthand for the generic operations: `--domain <slug> --doctype X
  --action Y [--payload ...] [--name ...]` runs `<slug>.generic`; without
  `--domain` it runs `unscoped.generic` (a doctype no domain owns, e.g.
  Item). The procurement/Item/doc-extraction flags (`--kyc`,
  `--kyc-waiver-confirmed`, `--purchase-sourced-item`, `--staged-fields`,
  `--confirmed-mappings`) and `--expected-modified` fold into the args.

## Gated operations: render first

When an operation needs confirmation (every bespoke operation; a generic
submit/cancel/delete; every unscoped action), run
`core/confirm_token.py render --op ... --args ...` first, show the user the
printed request and confirmation code, then call this with the printed
`args` UNCHANGED plus `--confirmation-token`, `--issued-at` and
`--user-confirmation-text` (the user's own reply, containing the code).
For those operations `--session-id`, `--channel-metadata` and
`--latest-prompt` are MANDATORY — a gated write is always fully
attributed. For ungated draft writes they are still warned about loudly.

## Failures and batches

When ERPNext rejects a create/update for a reason it names
(MandatoryError, LinkValidationError, ValidationError, DuplicateEntryError,
UniqueValidationError), stdout gets `{"write_failure": {error_class,
missing_fields, invalid_links, message}}`. Stop and ask the user; never
fill a value they did not give.

`--batch '<json list>'` runs several steps in order (each `{"op", "args"}`
plus its own confirmation fields) and stops at the first failure. stdout
gets the report: every step `succeeded`, `failed` or `not_attempted`.

## Exit codes

0 success · 1 ERPNext/other error · 2 usage error (incl. malformed --args) · 3 refused by a gate
(nothing was sent) · 4 outcome unknown or partial (timeout, partial post)
— re-read the record before any retry.
"""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Import every domain module for its side effect: registering its allowlist
# and its operations. manufacturing/doc_extraction have no write path.
from domains import (  # noqa: F401
    accounts,
    fixed_assets,
    hr_payroll,
    inventory,
    mis,
    procurement,
    sales,
    system_admin,
)
from core import operations
from core.client import (
    ConnectorError,
    GateRefusal,
    InvalidArgumentsError,
    PartialOutcomeError,
    TransportTimeoutError,
    WriteRejectedError,
    resolve_requested_by,
)
from core.client import _parse_json_arg  # noqa: F401 -- shared JSON-flag parsing

_DOMAIN_MODULES = {
    m.DOMAIN_NAME: m for m in (accounts, fixed_assets, hr_payroll, inventory, mis,
                               procurement, sales, system_admin)
}
_KNOWN_DOMAINS = set(_DOMAIN_MODULES)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REFUSED, EXIT_UNKNOWN_OUTCOME = 0, 1, 2, 3, 4

# Shorthand flags that become generic-operation args.
_SHORTHAND = ("doctype", "action", "payload", "name", "kyc", "kyc_waiver_confirmed",
              "purchase_sourced_item", "staged_fields", "confirmed_mappings", "expected_modified")


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


_CONTEXT_FLAGS = {
    "session_id": ("--session-id", "Qkeee Bot Audit Log will fall back to a synthetic "
                   "local-<timestamp> session, unrelated to the real platform thread. Resolve "
                   "the actual session id fresh for this logical session (00-conventions.md's "
                   "GRC baseline) and pass it explicitly."),
    "channel_metadata": ("--channel-metadata", "the audit row won't carry the platform space/"
                         "thread/channel id this write came from. Pass the channel's own "
                         "tracing detail as a JSON object, e.g. "
                         '\'{"space": "spaces/AAQ...", "thread": "threads/HzG..."}\'.'),
    "latest_prompt": ("--latest-prompt", "only --prompt-summary (a paraphrase) will be on the "
                      "audit row, not the user's verbatim request. Pass the literal most-recent "
                      "user message that drove this write."),
}


def _missing_context(args) -> list:
    return [k for k in _CONTEXT_FLAGS if not getattr(args, k)]


def _preflight_context_check(args) -> None:
    """Loud, not blocking — for ungated (draft) writes."""
    for key in _missing_context(args):
        flag, why = _CONTEXT_FLAGS[key]
        _warn(f"{flag} not given — {why}")


def _confirmation_label(op) -> str:
    """Which actions of this operation need a rendered confirmation —
    computed from the operation's own policy, never hand-maintained."""
    if not op.generic:
        return "none" if op.token_policy == operations.POLICY_NONE else "always"
    gated = [a for a in operations.RESOURCE_ACTIONS
             if operations.requires_confirmation(op.key, {"action": a})]
    if len(gated) == len(operations.RESOURCE_ACTIONS):
        return "always"
    return f"per action: {'/'.join(gated)}" if gated else "none"


def _list_ops() -> list:
    out = []
    for op in operations.list_operations():
        out.append({
            "op": op.key, "summary": op.summary, "credential": op.credential,
            "confirmation": _confirmation_label(op),
            "owns": sorted(f"{d} {a}" for d, a in op.owns),
            "args": op.args_help,
            "example_args": op.example_args,
        })
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="qkeee-erp-associate write CLI — every write goes through here. "
                    "See this file's module docstring.")
    p.add_argument("--list-ops", action="store_true", help="print every operation and exit")
    p.add_argument("--op", help="operation key (see --list-ops)")
    p.add_argument("--args", dest="op_args", help="JSON object of the operation's arguments")
    p.add_argument("--batch", help='JSON list of steps [{"op": ..., "args": {...}, '
                                   '"confirmation_token"?, "issued_at"?, "user_confirmation_text"?}]'
                                   " run in order; stops at the first failure")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--mode", choices=["read-only", "read-write"],
                   help="from qkeee_erp.mode — read-write required for any write to fire")
    p.add_argument("--requested-by",
                   help="ERPNext user id/email of the requester, resolved fresh from the live "
                        "inbound channel identity — no default, no fallback")
    # generic-operation shorthand
    p.add_argument("--domain", choices=sorted(_KNOWN_DOMAINS),
                   help="shorthand: run <domain>.generic; omit for unscoped.generic")
    p.add_argument("--doctype")
    p.add_argument("--action", choices=list(operations.RESOURCE_ACTIONS))
    p.add_argument("--payload", help="JSON object for create/update")
    p.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    p.add_argument("--expected-modified", help="submit/cancel/delete: as printed by render")
    p.add_argument("--kyc", help='procurement Supplier create: \'{"address": {...}, "contact": {...}}\'')
    p.add_argument("--kyc-waiver-confirmed", action="store_true",
                   help="procurement Supplier create: user explicitly waived KYC")
    p.add_argument("--purchase-sourced-item", action="store_true",
                   help="Item create: apply item_write_helpers.apply_purchase_sourced_item_defaults()")
    p.add_argument("--staged-fields", help="create/update: doc-extraction staged report (JSON array)")
    p.add_argument("--confirmed-mappings", help="create/update: JSON {candidate_key: live_fieldname}")
    # confirmation
    p.add_argument("--confirmation-token", help="from confirm_token.py render")
    p.add_argument("--issued-at", help="from confirm_token.py render")
    p.add_argument("--user-confirmation-text",
                   help="the user's own reply, containing the render's confirmation code")
    p.add_argument("--user-approved", action="store_true",
                   help="ungated writes: the confirm stage ran with the user (logged, not a gate)")
    p.add_argument("--approval-note", help="free text of what was confirmed")
    # audit context
    p.add_argument("--session-id", help="Qkeee Bot Audit Log session correlator")
    p.add_argument("--domain-code", default="qkeee-erp-associate", help="threaded into audit rows")
    p.add_argument("--channel", help="Google Chat/Discord/Telegram/WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help="JSON object of channel-specific tracing detail")
    p.add_argument("--prompt-summary", help="one-line summary of the user request")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt")
    return p


def _resolve_op_and_args(p, a):
    shorthand_given = [k for k in _SHORTHAND if getattr(a, k) not in (None, False)]
    if a.op:
        if shorthand_given or a.domain:
            p.error(f"--op cannot be combined with shorthand flags {shorthand_given or ['--domain']}; "
                    f"put them in --args.")
        return a.op, _parse_json_arg("--args", a.op_args, dict) or {}
    if a.op_args:
        p.error("--args needs --op.")
    if not (a.doctype and a.action):
        p.error("give --op, or the shorthand --doctype and --action (plus --domain).")
    op_args = {"doctype": a.doctype, "action": a.action}
    if a.name:
        op_args["name"] = a.name
    if a.expected_modified:
        op_args["expected_modified"] = a.expected_modified
    for flag, key, typ in (("--payload", "payload", dict), ("--kyc", "kyc", dict),
                           ("--staged-fields", "staged_fields", list),
                           ("--confirmed-mappings", "confirmed_mappings", dict)):
        value = _parse_json_arg(flag, getattr(a, key), typ)
        if value is not None:
            op_args[key] = value
    if a.kyc_waiver_confirmed:
        op_args["kyc_waiver_confirmed"] = True
    if a.purchase_sourced_item:
        op_args["purchase_sourced_item"] = True
    if (a.kyc or a.kyc_waiver_confirmed) and not (a.domain == "procurement" and a.doctype == "Supplier"
                                                 and a.action == "create"):
        p.error("--kyc/--kyc-waiver-confirmed only apply to --domain procurement --doctype Supplier "
                "--action create — they'd be silently ignored here otherwise.")
    if a.purchase_sourced_item and not (a.doctype == "Item" and a.action == "create"):
        p.error("--purchase-sourced-item only applies to --doctype Item --action create — "
                "it'd be silently ignored here otherwise.")
    if (a.staged_fields or a.confirmed_mappings) and a.action not in ("create", "update"):
        p.error("--staged-fields/--confirmed-mappings only apply to --action create/update — "
                "they'd be silently ignored here otherwise.")
    return (f"{a.domain}.generic" if a.domain else "unscoped.generic"), op_args


_STOP_AND_ASK = ("Stop. Show the user the missing or invalid fields, ask for the values, update "
                 "the spec and re-confirm. Never fill a value the user did not give.")


def _run_batch(steps: list, ctx) -> int:
    """Print run_batch()'s report; the exit code is the failing step's."""
    report = operations.run_batch(steps, ctx)
    print(json.dumps(report, indent=2, default=str))
    if report["stopped_at"] is None:
        return EXIT_OK
    failed = report["steps"][report["stopped_at"] - 1]
    print(f"ERROR: batch stopped at step {report['stopped_at']} of {len(steps)} "
          f"({report['counts']['succeeded']} succeeded, {report['counts']['not_attempted']} not "
          f"attempted): {failed['error']} — {_STOP_AND_ASK} Report created / failed / not "
          f"attempted as a table.", file=sys.stderr)
    if failed.get("refused"):
        return EXIT_REFUSED
    if failed.get("outcome_unknown"):
        return EXIT_UNKNOWN_OUTCOME
    return EXIT_ERROR


def main(argv=None) -> int:
    p = _build_parser()
    a = p.parse_args(argv)
    if a.list_ops:
        print(json.dumps(_list_ops(), indent=2))
        return EXIT_OK
    for flag, value in (("--tag", a.tag), ("--mode", a.mode), ("--requested-by", a.requested_by)):
        if not value:
            p.error(f"{flag} is required")
    batch = None
    try:
        if a.batch:
            single = [f for f in ("op", "op_args", "domain", "doctype", "action", "confirmation_token",
                                  "issued_at", "user_confirmation_text", "user_approved",
                                  "approval_note") if getattr(a, f)]
            if single:
                p.error(f"--batch carries each step's op/args/confirmation; it cannot be combined "
                        f"with {['--' + f.replace('_', '-') for f in single]}.")
            batch = _parse_json_arg("--batch", a.batch, list)
            if not batch or not all(isinstance(s, dict) for s in batch):
                raise ConnectorError("--batch must be a non-empty JSON list of step objects.")
            op_key = "batch"
            gated = any(operations.requires_confirmation(s.get("op") or "", s.get("args") or {})
                        for s in batch)
        else:
            op_key, op_args = _resolve_op_and_args(p, a)
            gated = operations.requires_confirmation(op_key, op_args)
        channel_metadata = _parse_json_arg("--channel-metadata", a.channel_metadata, dict)
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_USAGE

    if gated:
        missing = _missing_context(a)
        if missing:
            p.error(f"{op_key} needs a confirmation, so the audit context is mandatory: pass "
                    f"{', '.join(_CONTEXT_FLAGS[k][0] for k in missing)}.")
    else:
        _preflight_context_check(a)

    try:
        requested_by = resolve_requested_by(a.requested_by)
    except GateRefusal as e:  # --requested-by is not this session's authenticated sender
        print(f"ERROR: refused, nothing was sent: {e}", file=sys.stderr)
        return EXIT_REFUSED

    ctx = operations.WriteContext(
        tag=a.tag, mode=a.mode, requested_by=requested_by,
        session_id=a.session_id, domain_code=a.domain_code, channel=a.channel,
        channel_metadata=channel_metadata, prompt_summary=a.prompt_summary,
        latest_prompt=a.latest_prompt, confirmation_token=a.confirmation_token,
        issued_at=a.issued_at, user_confirmation_text=a.user_confirmation_text,
        user_approved=a.user_approved, approval_note=a.approval_note,
    )
    if batch is not None:
        return _run_batch(batch, ctx)
    try:
        result = operations.run_operation(op_key, op_args, ctx)
    except GateRefusal as e:
        print(f"ERROR: refused, nothing was sent: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except WriteRejectedError as e:
        print(json.dumps({"write_failure": e.failure}, indent=2))
        print(f"ERROR: ERPNext rejected the write: {e.failure['message']} — {_STOP_AND_ASK}",
              file=sys.stderr)
        return EXIT_ERROR
    except InvalidArgumentsError as e:
        print(f"ERROR: invalid operation arguments, nothing was sent: {e}", file=sys.stderr)
        return EXIT_USAGE
    except (TransportTimeoutError, PartialOutcomeError) as e:
        print(f"ERROR: outcome unknown or partial: {e}", file=sys.stderr)
        return EXIT_UNKNOWN_OUTCOME
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_ERROR

    for note in (result.get("_notes") or []) if isinstance(result, dict) else []:
        _warn(note)
    audit_status = result.get("_audit_log_status") if isinstance(result, dict) else None
    if audit_status not in ("ok", "exempt"):
        _warn(f"this write's Qkeee Bot Audit Log status is {audit_status!r}, not 'ok'/'exempt' — "
              f"the write itself succeeded, but it is NOT reliably in the audit trail. Surface "
              f"this to the user rather than reporting a plain success (00-conventions.md's GRC "
              f"baseline: \"A 'success' from a best-effort write is not proof it landed\").")
    print(json.dumps(result, indent=2, default=str))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
