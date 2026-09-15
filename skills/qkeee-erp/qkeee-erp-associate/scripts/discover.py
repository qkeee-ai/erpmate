#!/usr/bin/env python3
"""
qkeee-erp-associate discovery helper — resolves what's actually installed on
a target ERPNext instance (apps + versions, DocType-to-module-to-app
mapping, live field schema) so this skill can reason from real metadata
instead of guessing or trusting static docs or memory of a prior instance.

Every read goes through `core.client.get_resource()`/`query_resource()`,
so every discovery read carries a validated `requested_by` and gets the
same unconditional `Qkeee Bot Audit Log` row every other read in this
skill gets.

`apps` (`frappe.utils.change_log.get_versions`) has no DocType behind
it — same situation `core.client.run_query_report()` is in for a Report
run. It's a direct `_request()` call, routed through
`_validate_prod_requester()` and a manual `_log_read()` call first,
mirroring `run_query_report()`'s own pattern.

`modules` uses an explicit `limit=1000` instead of Frappe's
`limit_page_length: 0` ("no limit" sentinel): `query_resource()` doesn't
expose that sentinel — it always fetches `limit + 1` and reports
`has_more`. 1000 is comfortably above any real org's `Module Def` count;
`has_more` is still surfaced so a caller isn't silently handed a
truncated list.

Requires System Manager–level read access to `DocType` on the target
instance for `meta`/`resolve` — a correctly least-privileged bot account
can 403 here, and that is a real permission gap to report to the user,
not a bug in this script (see `doctype_meta()`'s docstring). `modules`/
`apps` read `Module Def`/a whitelisted RPC instead and are commonly
available even when `meta`/`resolve` aren't — try them first
(`02-environment-assessment.md` step 2 already sequences it this way).

Non-negotiable this script exists to serve: never propose a field/doctype
that isn't confirmed live on the target instance (Non-negotiable 4,
`00-conventions.md`). GitHub docs and docs.frappe.io describe the general
shape of an app; only this script's output (backed by
`/api/resource/DocType/<name>` and friends) confirms what a specific
org's instance actually has.
"""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.client import (
    ConnectorError,
    _log_read,
    _request,
    _validate_prod_requester,
    get_env_config,
    get_resource,
    query_resource,
)

# Fields kept from a DocType meta doc — everything else (permissions,
# print settings, form layout hints, etc.) is noise for the "what does
# this doctype actually look like" question this script answers.
_META_FIELD_KEYS = {
    "fieldname", "label", "fieldtype", "reqd", "options", "read_only",
    "hidden", "default", "unique", "in_list_view",
}


def list_installed_apps(tag: str, *, requested_by: str = None, session_id: str = None,
                         domain_code: str = None, channel: str = None,
                         channel_metadata: dict = None, prompt_summary: str = None,
                         latest_prompt: str = None) -> dict:
    """Installed-apps + version list — the same data the ERPNext desk's
    Help > About dialog shows. Frappe exposes this via a whitelisted RPC
    method, not a REST resource, so it can't route through
    get_resource()/query_resource() directly. Validated against
    'Module Def' read permission as the closest real doctype-shaped proxy
    for "can this requester see what's installed" — the same
    close-enough-proxy pattern core.client.run_query_report() already
    uses (validates against doctype='Report' for a report run that isn't
    itself a DocType record).

    Not stable across every Frappe/ERPNext version — if this method name
    has moved, or is blocked by the instance's whitelist policy entirely
    (`PermissionError: ... is not whitelisted`), `_request` raises a
    normal ConnectorError, and the `modules` subcommand (a plain REST
    read that works even when this RPC is blocked) is the expected next
    step — not a rare fallback.
    """
    _validate_prod_requester(tag, requested_by, "Module Def", "read")
    cfg = get_env_config(tag)
    try:
        result = _request(cfg, "GET", "/api/method/frappe.utils.change_log.get_versions")
        payload = {"source": "frappe.utils.change_log.get_versions", "apps": result.get("message", result)}
    except ConnectorError as e:
        payload = {
            "source": "frappe.utils.change_log.get_versions", "error": str(e),
            "fallback": "use 'modules' subcommand to enumerate apps indirectly via Module Def, "
                        "or ask the user to paste the Help > About dialog contents",
        }
    _log_read(cfg, "Module Def", "installed-apps", requested_by, session_id, domain_code,
              channel, channel_metadata, response_payload=payload,
              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    return payload


def list_modules(tag: str, *, requested_by: str = None, session_id: str = None,
                  domain_code: str = None, channel: str = None,
                  channel_metadata: dict = None, prompt_summary: str = None,
                  latest_prompt: str = None) -> dict:
    """Module Def rows — every module/app pairing the instance knows
    about. This is the primary app-discovery path (see
    list_installed_apps()'s docstring for why `apps` is opportunistic,
    not primary), and is also how a resolved DocType's `module` field
    gets traced to an owning app (see resolve_doctype()).

    `limit=1000`: comfortably above any real org's Module Def count.
    `has_more` is surfaced in the return value in the rare case it isn't.
    """
    result = query_resource(tag, "Module Def", fields=["name", "app_name"], limit=1000,
                             session_id=session_id, domain_code=domain_code,
                             requested_by=requested_by, channel=channel,
                             channel_metadata=channel_metadata,
                             prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    rows = result.get("data", [])
    apps = sorted({r.get("app_name") for r in rows if r.get("app_name")})
    return {"modules": rows, "apps_seen_via_modules": apps, "has_more": result.get("has_more", False)}


def doctype_meta(tag: str, doctype: str, *, requested_by: str = None, session_id: str = None,
                  domain_code: str = None, channel: str = None, channel_metadata: dict = None,
                  prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """Live field schema for one DocType — fieldname/label/fieldtype/reqd
    (mandatory flag)/options (Link target or Select choices) for every
    field actually on this instance, plus module/istable/issubmittable/
    custom flags. Authoritative over any GitHub README or docs.frappe.io
    page, which describe the general shape but not this org's
    customizations (custom fields, altered mandatory flags, etc.).

    Uses get_resource() — a single-resource GET on the DocType record
    itself (`/api/resource/DocType/<name>`), which returns its `fields`
    child table inline. Deliberately NOT a query against the standalone
    `DocField` doctype: DocField is a child-table doctype (`istable=1`)
    and Frappe grants no role standalone List permission on a table-only
    doctype — that call 403s regardless of how privileged the caller is,
    it's not a permission tier this script should route around by
    escalating the bot account (see this file's module docstring).

    Requires System Manager-level read access to the DocType doctype
    itself — can 403 under a correctly least-privileged shared bot
    account. A caller hitting that should surface it as a permissions gap
    to fix, not treat it as "doctype doesn't exist" or silently fall back
    to guessing field names.
    """
    result = get_resource(tag, "DocType", doctype,
                           session_id=session_id, domain_code=domain_code,
                           requested_by=requested_by, channel=channel,
                           channel_metadata=channel_metadata,
                           prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    doc = result.get("data") or {}
    fields = [
        {k: f.get(k) for k in _META_FIELD_KEYS if k in f}
        for f in doc.get("fields", [])
    ]
    return {
        "doctype": doc.get("name"),
        "module": doc.get("module"),
        "custom": bool(doc.get("custom")),
        "istable": bool(doc.get("istable")),
        "issubmittable": bool(doc.get("issubmittable")),
        "description": doc.get("description"),
        "fields": fields,
    }


def resolve_doctype(tag: str, doctype: str, *, requested_by: str = None, session_id: str = None,
                     domain_code: str = None, channel: str = None, channel_metadata: dict = None,
                     prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """DocType -> module -> app, in one call. This is the core "which app
    owns this doctype" lookup — run it before assuming a doctype belongs
    to any specific domain or to an unmapped custom app.

    `app` is `None` in two distinguishable cases, surfaced separately so
    a caller can't conflate them: a doctype with no `module` at all
    reports `app: null, app_lookup_error: null` (nothing to look up —
    genuinely no module recorded); a doctype whose Module Def lookup
    itself failed (permission denied, network error, a module name that
    doesn't resolve) reports `app: null, app_lookup_error: "<the
    ConnectorError message>"` — a fetch that failed, not a confirmed
    absence. Collapsing both cases to a bare `app: null` risks a false
    "this is custom, no owning app" claim when the lookup had actually
    just errored.
    """
    meta = doctype_meta(tag, doctype, requested_by=requested_by, session_id=session_id,
                         domain_code=domain_code, channel=channel,
                         channel_metadata=channel_metadata, prompt_summary=prompt_summary,
                         latest_prompt=latest_prompt)
    module = meta.get("module")
    app_name = None
    app_lookup_error = None
    if module:
        try:
            mod_result = get_resource(tag, "Module Def", module,
                                       session_id=session_id, domain_code=domain_code,
                                       requested_by=requested_by, channel=channel,
                                       channel_metadata=channel_metadata,
                                       prompt_summary=prompt_summary, latest_prompt=latest_prompt)
            app_name = (mod_result.get("data") or {}).get("app_name")
        except ConnectorError as e:
            app_lookup_error = str(e)
    return {
        "doctype": meta.get("doctype"),
        "module": module,
        "app": app_name,
        "app_lookup_error": app_lookup_error,
        "custom": meta.get("custom"),
        "istable": meta.get("istable"),
        "issubmittable": meta.get("issubmittable"),
        "field_count": len(meta.get("fields", [])),
    }


def _cli():
    p = argparse.ArgumentParser(
        description="qkeee-erp-associate discovery CLI — live DocType/app metadata, "
                    "never guessed (Non-negotiable 4, 00-conventions.md)."
    )
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email of the requester — every read here is gated + "
                        "audited exactly like core/client.py's own reads, no exceptions")
    p.add_argument("--session-id", help="plain string correlator threaded into Qkeee Bot Audit Log rows")
    p.add_argument("--domain-code", default="qkeee-erp-associate/discover",
                   help="threaded into audit rows")
    p.add_argument("--channel", help="conversation surface, e.g. Google Chat/Discord/Telegram/"
                        "WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help="JSON object of channel-specific tracing detail "
                        "(e.g. the chat space/thread id) — threaded into the audit row so it "
                        "isn't left blank")
    p.add_argument("--prompt-summary", help="one-line summary of the user request driving this lookup")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt from the driving chat, if any")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("apps", help="installed apps + versions (About-dialog data) — "
                                 "opportunistic, try 'modules' first")
    sub.add_parser("modules", help="Module Def rows + apps seen through them (primary app discovery)")

    m = sub.add_parser("meta", help="live field schema for one DocType")
    m.add_argument("doctype")

    r = sub.add_parser("resolve", help="doctype -> module -> app in one call")
    r.add_argument("doctype")

    args = p.parse_args()
    try:
        channel_metadata = json.loads(args.channel_metadata) if args.channel_metadata else None
    except json.JSONDecodeError as e:
        raise SystemExit(f"--channel-metadata must be valid JSON: {e}")

    kw = dict(
        requested_by=args.requested_by, session_id=args.session_id, domain_code=args.domain_code,
        channel=args.channel, channel_metadata=channel_metadata,
        prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt,
    )

    try:
        if args.command == "apps":
            print(json.dumps(list_installed_apps(args.tag, **kw), indent=2))
        elif args.command == "modules":
            print(json.dumps(list_modules(args.tag, **kw), indent=2))
        elif args.command == "meta":
            print(json.dumps(doctype_meta(args.tag, args.doctype, **kw), indent=2))
        elif args.command == "resolve":
            print(json.dumps(resolve_doctype(args.tag, args.doctype, **kw), indent=2))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
