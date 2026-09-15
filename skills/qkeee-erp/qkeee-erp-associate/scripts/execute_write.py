#!/usr/bin/env python3
"""
qkeee-erp-associate write CLI — the one entry point for every create/
update/submit/cancel/delete this skill issues, domain-scoped or not.

## Why this file exists

`core/client.py`'s own bare `mutate --domain <slug>` CLI subcommand
never imports any `domains/*.py` module, so run standalone it 404s with
"domain has no registered ALLOWED_WRITE_DOCTYPES" even when that domain
module genuinely declares one — `register_domain_allowlist()` only runs
at import time. This file imports every `domains/*.py` module up front
(so any `--domain` value's allowlist is registered before the write
fires, regardless of which one), and requires the audit-context flags
loudly rather than letting them go quietly missing — see `_cli()`'s
pre-flight warnings below. It does not change `core/client.py` itself;
it's a caller, same tier as `discover.py`.

## Two write shapes, one flag decides which

- `--domain <slug>` given → dispatches to that `domains/<slug>.py`
  module's own `mutate()`, NOT `core.client.mutate_resource()` directly.
  This matters: a domain module's `mutate()` is where that domain layers
  its own doctype-specific rules on top of the generic allowlist gate —
  procurement's Supplier-KYC-completeness check is one example. Calling
  `mutate_resource(domain=...)` directly would skip that rule entirely;
  going through the domain module's `mutate()` is what makes it
  unbypassable regardless of which caller fires the write. `<slug>` must
  match one of `scripts/domains/*.py`'s `DOMAIN_NAME` (accounts,
  fixed_assets, hr_payroll, inventory, mis, procurement, sales,
  system_admin).
- `--domain` omitted → `core.client.gated_mutate_resource(...)` — the
  advisory-token path for a doctype no domain owns (Item today).
  `--confirmation-token`/`--issued-at` are required in this shape;
  `gated_mutate_resource()` itself refuses to proceed without them. So is
  `--user-confirmation-text` — the literal text of the user's own reply,
  which must contain the confirmation code `confirm_token.py`'s CLI
  prints alongside the token. This is what actually ties execution to a
  human having seen the rendered draft; the token match alone only
  proves the payload wasn't altered since render — see
  `confirm_token.confirmation_code()`'s own docstring for the honest
  limits of what this does and doesn't prove.

## Audit context is loud here, not silent

`core/client.py`'s own `mutate` CLI subcommand silently substitutes a
synthetic `local-<timestamp>` session_id when `--session-id` is omitted
(see `_cli()`'s `if ... not args.session_id: args.session_id =
_session_or_fallback(None)`). This script does NOT hide that
substitution: omitting `--session-id`, `--channel-metadata`, or
`--latest-prompt` prints a WARN to stderr naming exactly what's missing,
before the write fires. Passing them is still not code-enforced — the
loud warning is the middle ground: an agent can still choose to proceed,
but never by accident or unnoticed.

Every result also gets its `_audit_log_status` checked here and, when
it's anything other than "ok"/"exempt", printed as a second, separate
warning — `00-conventions.md`'s GRC baseline already says to do this;
this script does it uniformly so no caller has to remember to.

## `--purchase-sourced-item`

A thin, Item-specific conditional, not generic write-path logic: when
`--doctype Item --action create` and this flag is set, the payload is run
through `item_write_helpers.apply_purchase_sourced_item_defaults()` before
the write fires — defaults `is_purchase_item=1, is_sales_item=0` (nothing
in a purchase document supports "the org resells this"), and refuses a
bare `standard_rate` key outright (setting `standard_rate` bare on Item
create auto-creates a Standard SELLING Item Price in ERPNext — wrong for
a purchase-sourced item; see `item_write_helpers.py`'s own docstring for
the fix). See that file rather than this one for the actual logic.

## Schema-first attribute mapping

Every `--action create`/`update`, for every `--doctype`, unconditionally
(not opt-in the way `--purchase-sourced-item` is): before dispatch,
`_apply_schema_mapping()` runs the payload (or `--staged-fields`, when
doc-extraction's confidence-rated output is being written directly)
through `schema_mapping.map_payload_for_write()` against that doctype's
live field schema, so a field only gets left out because it genuinely
isn't on the instance — never because a domain doc's curated field list
didn't happen to mention it. Degrades to the payload as given (warn, not
refuse) if the schema fetch itself fails; hard-refuses only when a
`--staged-fields` entry is both low-confidence and unmatched against the
live schema. See `schema_mapping.py`'s own module docstring for the full
design rationale (why this lives here and not inside
`mutate_resource()`/`gated_mutate_resource()`, the fuzzy-match
confirmation story).

`--kyc`'s own `address`/`contact` sub-payloads get this too, mapped
separately against Address's/Contact's own live schema before dispatch —
the top-level `_apply_schema_mapping()` call only ever covers the
Supplier payload itself, and `--kyc` is forwarded to
`procurement.mutate()` as its own argument, not folded into `--payload`.
No `--staged-fields`/`--confirmed-mappings` support for the KYC
sub-payload; see `_apply_kyc_schema_mapping()`.
"""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Import every domain module for its side effect (register_domain_allowlist()/
# register_domain_token_gate() at import time) — this is what lets --domain
# work standalone, in a single process, regardless of which domain is named.
# domains/__init__.py deliberately does NOT do this itself (see its own
# docstring) — manufacturing and doc_extraction have no write path at all,
# so they're not here either.
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
from core.client import (
    ConnectorError,
    gated_mutate_resource,
    resolve_requested_by,
)
from core.client import _parse_json_arg  # noqa: F401 -- shared JSON-flag parsing, same errors as core/client.py's own CLI
from item_write_helpers import (
    BareStandardRateOnPurchaseSourcedItemError,
    apply_purchase_sourced_item_defaults,
)
import schema_mapping

# name -> module, so --domain dispatches to that domain's OWN mutate() —
# never core.client.mutate_resource() directly — see module docstring.
_DOMAIN_MODULES = {
    accounts.DOMAIN_NAME: accounts,
    fixed_assets.DOMAIN_NAME: fixed_assets,
    hr_payroll.DOMAIN_NAME: hr_payroll,
    inventory.DOMAIN_NAME: inventory,
    mis.DOMAIN_NAME: mis,
    procurement.DOMAIN_NAME: procurement,
    sales.DOMAIN_NAME: sales,
    system_admin.DOMAIN_NAME: system_admin,
}
_KNOWN_DOMAINS = set(_DOMAIN_MODULES)


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _apply_schema_mapping(args, payload: dict, effective_requested_by: str, channel_metadata: dict,
                           staged_fields: list, confirmed_mappings: dict, *, doctype: str = None) -> dict:
    """Runs schema-first attribute mapping before every create/update
    dispatch below. See schema_mapping.py's own module docstring for why
    this lives here rather than inside mutate_resource()/
    gated_mutate_resource(). Loud-not-blocking for a schema-fetch failure
    or a suggested/unmatched field (mirrors
    `_preflight_context_check()`'s posture); hard-refuses only on a
    `high_risk` field — a field that's both low-confidence and unmatched
    against the live schema — via ConnectorError, caught by this file's
    existing top-level ConnectorError handler.

    `doctype` defaults to `args.doctype` — the top-level `--payload`'s own
    target doctype. `_apply_kyc_schema_mapping()` below passes "Address"/
    "Contact" explicitly instead, to map the `--kyc` sub-payload's own
    fields against THEIR live schema rather than Supplier's."""
    doctype = doctype or args.doctype
    mapping = schema_mapping.map_payload_for_write(
        args.tag, doctype, payload, requested_by=effective_requested_by,
        staged_fields=staged_fields, confirmed_mappings=confirmed_mappings,
        session_id=args.session_id, domain_code=args.domain_code,
        channel=args.channel, channel_metadata=channel_metadata,
        prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt,
    )
    if mapping["status"] == "unavailable":
        _warn(f"schema-first field mapping unavailable for '{doctype}' on tag '{args.tag}' "
              f"({mapping['detail']}) — proceeding with the payload as given, unmapped.")
        return payload
    if mapping["suggested_mappings"]:
        _warn(f"{len(mapping['suggested_mappings'])} field(s) on '{doctype}' matched a live schema "
              f"field only via a synonym hint, NOT applied without confirmation: "
              f"{mapping['suggested_mappings']!r} — re-run with --confirmed-mappings including the "
              f"ones the user confirms.")
    if mapping["unmatched"]:
        _warn(f"field(s) with no matching live schema field on '{doctype}', dropped from "
              f"the payload actually sent: {mapping['unmatched']!r}")
    if mapping["status"] == "high_risk":
        raise ConnectorError(
            f"Refusing {args.action} on '{doctype}': staged field(s) "
            f"{mapping['high_risk']!r} are BOTH low-confidence AND unmatched against the live "
            f"schema — the highest-risk combination. Resolve manually with the user (correct "
            f"the source value, or supply the right live fieldname via --confirmed-mappings) "
            f"before retrying."
        )
    return mapping["payload"]


def _apply_kyc_schema_mapping(args, kyc: dict, effective_requested_by: str, channel_metadata: dict) -> dict:
    """The Supplier-KYC flow builds an Address/Contact sub-payload
    (`--kyc`) that procurement.py's `_create_linked_kyc_record()` writes
    via `core_client.mutate_resource()` directly — never through this
    file's own top-level `--payload` path, so it never gets
    `_apply_schema_mapping()`'s schema-first field mapping otherwise (the
    call above only ever covers the Supplier's own fields; `kyc` is
    forwarded separately as `domain_kwargs`, never through
    `schema_mapping.map_payload_for_write()` at all). Maps
    `kyc["address"]`/`kyc["contact"]` against Address's/Contact's own
    live schema BEFORE dispatch, same exact/normalized-match behavior as
    `_apply_schema_mapping()` above — deliberately no
    `--staged-fields`/`--confirmed-mappings` support here: the KYC
    sub-payload has no staged-report shape, and folding a fuzzy-match
    confirmation for two different doctypes through one shared top-level
    CLI flag risks confirming the wrong mapping against the wrong
    doctype. Because `staged_fields` is never given here, `map_payload_
    for_write()` can never return `high_risk` for this call (that status
    is staged_fields-only, see schema_mapping.py) — only the unavailable/
    ok/suggested/unmatched outcomes apply, all warn-not-refuse."""
    result = dict(kyc)
    for key, sub_doctype in (("address", "Address"), ("contact", "Contact")):
        if not result.get(key):
            continue
        result[key] = _apply_schema_mapping(
            args, result[key], effective_requested_by, channel_metadata,
            staged_fields=None, confirmed_mappings=None, doctype=sub_doctype,
        )
    return result


def _preflight_context_check(args) -> None:
    """Loud, not blocking — see module docstring. Names exactly what's
    missing so the caller can't miss it."""
    if not args.session_id:
        _warn("--session-id not given — Qkeee Bot Audit Log will fall back to a synthetic "
              "local-<timestamp> session, unrelated to the real platform thread. Resolve the "
              "actual session id fresh for this logical session (00-conventions.md's GRC "
              "baseline) and pass it explicitly.")
    if not args.channel_metadata:
        _warn("--channel-metadata not given — the audit row won't carry the platform space/"
              "thread/channel id this write came from. Pass the channel's own tracing detail "
              "as a JSON object, e.g. "
              '\'{"space": "spaces/AAQ...", "thread": "threads/HzG..."}\'.')
    if not args.latest_prompt:
        _warn("--latest-prompt not given — only --prompt-summary (a paraphrase) will be on the "
              "audit row, not the user's verbatim request. Pass the literal most-recent user "
              "message that drove this write.")


def _cli():
    p = argparse.ArgumentParser(
        description="qkeee-erp-associate write CLI — the one entry point for every "
                    "create/update/submit/cancel/delete, domain-scoped or not. See this "
                    "file's own module docstring for why it exists."
    )
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                   help="from qkeee_erp.mode — read-write required for any write to actually fire")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email of the requester, resolved fresh from the live "
                        "inbound channel identity — no default, no fallback")
    p.add_argument("--doctype", required=True)
    p.add_argument("--action", required=True, choices=["create", "update", "submit", "cancel", "delete"])
    p.add_argument("--payload", help="JSON object for create/update")
    p.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    p.add_argument("--domain", choices=sorted(_KNOWN_DOMAINS),
                   help="registered domain name — routes through mutate_resource()'s reviewed "
                        "allowlist. Omit for a doctype no domain owns (e.g. Item) to route "
                        "through gated_mutate_resource() instead (requires "
                        "--confirmation-token/--issued-at).")
    p.add_argument("--confirmation-token",
                   help="required when --domain is omitted; also required when the named "
                        "domain has registered this --action via register_domain_token_gate() "
                        "(normally submit/cancel/delete)")
    p.add_argument("--issued-at", type=int, help="epoch seconds the confirmation token was computed at")
    p.add_argument("--user-confirmation-text",
                   help="required when --domain is omitted (gated_mutate_resource path): literal "
                        "text of the user's own reply, must contain confirm_token."
                        "confirmation_code(confirmation_token). Not applicable to a domain-scoped "
                        "write's own submit/cancel/delete token gate.")
    p.add_argument("--user-approved", action="store_true",
                   help="pass only when this write's confirm stage genuinely ran with the user "
                        "first — logged for later scanning, not enforced as a gate")
    p.add_argument("--approval-note", help="free text of what was confirmed")
    p.add_argument("--session-id", help="plain string correlator for Qkeee Bot Audit Log rows — "
                        "resolve fresh per logical session, see 00-conventions.md's GRC baseline")
    p.add_argument("--domain-code", default="qkeee-erp-associate",
                   help="threaded into audit rows (defaults to the skill name; a domain write "
                        "auto-labels as qkeee-erp-associate/<domain> regardless)")
    p.add_argument("--channel", help="conversation surface, e.g. Google Chat/Discord/Telegram/"
                        "WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help="JSON object of channel-specific tracing detail "
                        "(the chat space/thread id, etc.)")
    p.add_argument("--prompt-summary", help="one-line summary of the user request driving this write")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt from the driving chat")
    p.add_argument("--kyc", help='procurement.py-only: JSON object, e.g. \'{"address": {...}, '
                        '"contact": {...}}\' — required (or --kyc-waiver-confirmed) for a '
                        "Supplier create; see procurement.md's KYC write order")
    p.add_argument("--kyc-waiver-confirmed", action="store_true",
                   help="procurement.py-only: pass only when the user has explicitly confirmed "
                        "proceeding with a Supplier create without KYC")
    p.add_argument("--purchase-sourced-item", action="store_true",
                   help="Item-only, --action create: apply item_write_helpers."
                        "apply_purchase_sourced_item_defaults() to --payload before writing — "
                        "defaults is_purchase_item=1/is_sales_item=0 and refuses a bare "
                        "standard_rate key. See item_write_helpers.py.")
    p.add_argument("--staged-fields",
                   help="schema-first attribute mapping, --action create/update only: "
                        "JSON array of doc-extraction's staged-report entries, e.g. "
                        '\'[{"field": "HSN", "value": "84713090", "confidence": "high"}]\' — '
                        "matched against the live doctype schema instead of --payload's own keys "
                        "when given. See schema_mapping.py.")
    p.add_argument("--confirmed-mappings",
                   help="JSON object {candidate_key: live_fieldname} confirming one or "
                        "more schema_mapping.py suggested_mappings entries the user has explicitly "
                        "signed off on — the only way a fuzzy/synonym-matched field ever reaches "
                        "the payload actually sent.")

    args = p.parse_args()

    kyc_applicable = (args.domain == "procurement" and args.doctype == "Supplier"
                       and args.action == "create")
    if (args.kyc or args.kyc_waiver_confirmed) and not kyc_applicable:
        p.error("--kyc/--kyc-waiver-confirmed only apply to --domain procurement --doctype "
                "Supplier --action create — they'd be silently ignored here otherwise.")

    if args.purchase_sourced_item and not (args.doctype == "Item" and args.action == "create"):
        p.error("--purchase-sourced-item only applies to --doctype Item --action create — "
                "it'd be silently ignored here otherwise.")

    try:
        payload = _parse_json_arg("--payload", args.payload, dict)
        channel_metadata = _parse_json_arg("--channel-metadata", args.channel_metadata, dict)
        kyc = _parse_json_arg("--kyc", args.kyc, dict)
        staged_fields = _parse_json_arg("--staged-fields", args.staged_fields, list)
        confirmed_mappings = _parse_json_arg("--confirmed-mappings", args.confirmed_mappings, dict)
        if args.purchase_sourced_item:
            payload = apply_purchase_sourced_item_defaults(payload or {})
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except BareStandardRateOnPurchaseSourcedItemError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    effective_requested_by = resolve_requested_by(args.requested_by)
    _preflight_context_check(args)

    if args.action in ("create", "update"):
        try:
            payload = _apply_schema_mapping(args, payload, effective_requested_by, channel_metadata,
                                             staged_fields, confirmed_mappings)
            if kyc_applicable and kyc:
                kyc = _apply_kyc_schema_mapping(args, kyc, effective_requested_by, channel_metadata)
        except ConnectorError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.staged_fields or args.confirmed_mappings:
        p.error("--staged-fields/--confirmed-mappings only apply to --action create/update — "
                "they'd be silently ignored here otherwise.")

    common = dict(
        payload=payload, name=args.name, mode=args.mode, requested_by=effective_requested_by,
        session_id=args.session_id, domain_code=args.domain_code,
        channel=args.channel, channel_metadata=channel_metadata,
        approval_note=args.approval_note,
        prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt,
    )

    try:
        if args.domain:
            domain_kwargs = {}
            if kyc_applicable:
                domain_kwargs.update(kyc=kyc, kyc_waiver_confirmed=args.kyc_waiver_confirmed)
            result = _DOMAIN_MODULES[args.domain].mutate(
                args.tag, args.doctype, args.action,
                user_approved=args.user_approved,
                confirmation_token=args.confirmation_token, issued_at=args.issued_at,
                **domain_kwargs, **common,
            )
        else:
            if not args.confirmation_token or args.issued_at is None:
                p.error("--confirmation-token and --issued-at are required when --domain is "
                        "omitted (gated_mutate_resource path) — render the draft first via "
                        "confirm_token.py's advisory-token CLI over the exact facts shown to "
                        "and confirmed by the user, then pass its output here unchanged.")
            if not args.user_confirmation_text:
                p.error("--user-confirmation-text is required when --domain is omitted "
                        "(gated_mutate_resource path) — the literal text of the user's own "
                        "reply, containing the confirmation_code shown in the rendered draft.")
            result = gated_mutate_resource(
                args.tag, args.doctype, args.action,
                confirmation_token=args.confirmation_token, issued_at=args.issued_at,
                user_confirmation_text=args.user_confirmation_text,
                **common,
            )
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    audit_status = result.get("_audit_log_status") if isinstance(result, dict) else None
    if audit_status not in ("ok", "exempt"):
        _warn(f"this write's Qkeee Bot Audit Log status is {audit_status!r}, not 'ok'/'exempt' — "
              f"the write itself succeeded, but it is NOT reliably in the audit trail. Surface "
              f"this to the user rather than reporting a plain success (00-conventions.md's GRC "
              f"baseline: \"A 'success' from a best-effort write is not proof it landed\").")

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _cli()
