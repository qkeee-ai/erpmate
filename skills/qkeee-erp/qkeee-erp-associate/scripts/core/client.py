#!/usr/bin/env python3
"""
qkeee-erp-associate core connector — the single, consolidated ERPNext
(Frappe REST API) client for every domain of the qkeee-erp-associate skill.

This is the single connector every domain module uses; there is no
per-domain copy. It carries the gated write path, PII redaction, and
requester validation shared by every domain.

Self-contained: stdlib only (urllib), no third-party deps.

Env/credential model (tagged, not fixed dev/test/qa/prod):
  QKEEE_ERP_<TAG>_BASE_URL
  QKEEE_ERP_<TAG>_API_KEY
  QKEEE_ERP_<TAG>_API_SECRET

<TAG> defaults to "DEFAULT" if the user didn't name one at install.
Active tag + read-only/read-write mode stay non-secret and live in
metadata.hermes.config (qkeee_erp.active_env, qkeee_erp.mode) — those two
are deliberately still global: an environment switch should never silently
also change write access.

There is deliberately no env-var or config default for `requested_by`
(no `QKEEE_ERP_<TAG>_REQUESTED_BY`, no metadata.hermes.config key). A
standing default is a stale-identity trap — it silently attributes every
call to whoever configured it, long after they've stopped being the
person actually asking. `requested_by` is resolved fresh, every call,
from the live inbound channel identity instead — see "Requester identity
comes from the channel, never from config" below.

Non-negotiable: never issue a write call while mode == "read-only". This is
enforced in mutate_resource() below, not just in the calling domain's
prompt/reference doc.

Bot account + requester attribution: the API key/secret above must belong
to a dedicated ERPNext integration/bot user, never an individual's personal
login. Every write additionally requires `requested_by` (the ERPNext user
id/email of the human who asked for the change) and, on success, posts a
best-effort audit Comment on the affected record naming that requester — so
ERPNext's own audit trail shows who asked, not just that the bot acted. See
record_comment()/mutate_resource() below.

Requester identity comes from the channel, never from config: the CALLER
(the Hermes agent driving this CLI, not this module) is responsible for
resolving `requested_by` on every single call from the inbound message's
own identity — the Google Chat/Teams/Slack sender's work email, the
email channel's From address, whatever the platform actually hands over
for "who sent this." That resolved identity is passed explicitly via
`--requested-by` / `requested_by=`. This module never stores, caches, or
falls back to a previous call's value across calls — resolve_requested_by()
below is a thin pass-through, not a lookup. `_validate_prod_requester()`
independently re-validates whatever's passed as a real ERPNext `User` with
the right permission before any read or write proceeds — see below.

Every write is additionally logged to the `Qkeee Bot Audit Log` doctype
(two-phase: an `Attempted` row inserted before the real write, updated to
`Success`/`Failure` after), and every read is logged there too,
unconditionally — no debug flag gates it. Audit Log's `session` field is
a plain string correlator, with no doctype of its own behind it. All of
this is best-effort — see "Audit logging is best-effort, not a gate"
below.

RBAC pre-check: every read and write with a `requested_by` resolves that
identity as a real ERPNext `User` and confirms via ERPNext's own
`frappe.client.has_permission` that they actually hold the permission the
call needs — on every environment tag, not PROD only. See
`_validate_prod_requester()` below (the name reflects a narrower PROD-only
origin; the check itself is now universal).

Known limitation, structural rather than instance-specific:
`frappe.client.has_permission` has no `user=` parameter in stock Frappe at
all — it always answers for the calling session, never a named other user
— so per-requester permission can't actually be verified via that RPC.

When `verify_rbac_precheck_reliable()` detects this, `_validate_prod_requester()`
does not lean on a `domain=` allowlist or a verified advisory-draft token to
let the write through — `_requester_has_role_permission()` asks the same
question a different way instead: locally, from the requester's own live
role list and the doctype's own live DocPerm rows, never through the broken
RPC. Only a positively-confirmed grant proceeds; an inconclusive or negative
local verdict refuses outright (`UnvalidatedProdRequesterError`), regardless
of `domain`/`advisory_token_verified` status — those attest a write's
*shape* was reviewed ahead of time, but neither is treated as proof
`requested_by` specifically can do it once the RPC that would otherwise
confirm that is unreliable. See `_requester_has_role_permission()`'s own
docstring for what this local check can and can't see (User Permissions and
`if_owner` scoping are invisible to it), and `_validate_prod_requester()`'s
docstring for the full decision tree.

Write-allowlist gate: domain modules under `scripts/domains/*.py` each
declare an ALLOWED_WRITE_DOCTYPES tuple and register it via
register_domain_allowlist() at import time. mutate_resource(...,
domain="<name>") then refuses any create/update/submit/cancel/delete whose
doctype isn't in that domain's allowlist, raising DoctypeNotAllowedError.
`mis.py` registers an EMPTY allowlist, so every doctype is refused there.
`domain=None` (the default) applies no allowlist restriction at all — used
by gated_mutate_resource() below (an advisory-first write path for
doctypes that aren't known in advance, so no capability review /
allowlist can be drawn up ahead of time) and by any core-level/admin
script (e.g. init_bot.py) that intentionally operates outside domain
scope.

Advisory-first write gate: this file also ships
`gated_mutate_resource()`, the associate's OWN write entry point for
whatever doesn't fit a named domain — it requires a confirmation_token +
issued_at from a render_*.py draft script, enforcing "never write without
an advisory-first draft" in code, not just prompt discipline. Domain
modules' own `mutate()` wrappers call plain `mutate_resource(...,
domain=<name>)` directly (their capability tables are reviewed at design
time via ALLOWED_WRITE_DOCTYPES); nothing in gated_mutate_resource()'s
remit has had that review, so it stays allowlist-free and
confirmation-token-gated instead. See confirm_token.py's
`advisory_write_token()`.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Dual-mode import: works whether this file is run directly as a script
# (`python core/client.py ...` — Python puts `core/` itself on sys.path,
# so a plain `import confirm_token` resolves) or imported as `core.client`
# from a sibling package (a domains/*.py module that has put `scripts/` —
# core's PARENT — on sys.path first; see domains/*.py's own import
# preamble). Avoids hardcoding either sys.path shape.
try:
    from confirm_token import advisory_write_token, confirmation_code, is_fresh
except ImportError:
    from core.confirm_token import advisory_write_token, confirmation_code, is_fresh

# Default attribution label for audit Comments when no domain-specific
# label is supplied — see mutate_resource()'s `domain`/`skill_label` params.
SKILL_LABEL = "qkeee-erp-associate"

# Qkeee Bot audit-trail doctype (see scripts/init_bot.py). A target
# instance may not have it provisioned yet — every call into it below is
# best-effort and never blocks or fails the caller's actual ERPNext
# read/write.
AUDIT_LOG_DOCTYPE = "Qkeee Bot Audit Log"

# Doctypes exempt from audit-wrapping on the WRITE path
# (record_audit_log_start()/record_audit_log_finish(), consulted by
# mutate_resource()). Mandatory, not optional: without this, logging a
# write to Qkeee Bot Audit Log would itself be logged, recursing forever.
# "Comment" is exempt for a related reason — the best-effort audit-comment
# post (record_comment(), below) is itself a write; without this
# exemption every audited write would double-log itself (once for the
# record, once for the Comment documenting it). "User"/"DocType"/"Role"
# are exempt for the same reason they're in PROD_GATE_EXEMPT_DOCTYPES
# below (core-infra doctypes managed by qkeee-erp-bot-init / system-admin,
# not written by a business requester) — kept in sync with that set
# deliberately: init_bot.py's Role/DocType bootstrap writes rely on this
# exemption to stay silent so its own log_role_provisioning() can be the
# ONE place that logs them (manually, bypassing this exemption on purpose
# — see that function's docstring). Removing "User"/"DocType"/"Role" here
# would double-log that bootstrap sequence for no benefit — this set is
# scoped to the write path only.
#
# The READ path (_log_read(), consulted by query_resource()/get_resource()/
# run_query_report()/get_user_roles()) does NOT use this set — see
# _LOG_READ_RECURSION_EXEMPT_DOCTYPES below: a doctype-keyed exemption is
# the wrong axis there, since it would also silently swallow a genuine
# business-intent read of User/Role (e.g. `query User`, `roles <user>`),
# not just the internal plumbing call it exists to stop from recursing.
# That plumbing call (resource_exists(), and
# _fetch_doctype_role_permissions()'s own get_resource(tag, "DocType", ...))
# is exempted directly via an `internal=True` kwarg instead.
AUDIT_EXEMPT_DOCTYPES = {
    AUDIT_LOG_DOCTYPE,
    "Comment",
    "User",
    "DocType",
    "Role",
}

# Read-path recursion exemption — deliberately NARROWER than
# AUDIT_EXEMPT_DOCTYPES above. Only the two doctypes that would actually
# recurse (logging a read of the audit doctype itself, or of a Comment)
# stay exempt by doctype. Everything else that needs to skip logging for
# plumbing reasons (resource_exists(), _fetch_doctype_role_permissions(),
# _bot_identity(), _requester_has_role_permission()'s own role lookup)
# does so via get_resource()'s / get_user_roles()'s own `internal=True`
# kwarg instead — purpose-keyed, not doctype-keyed, so a real
# business-intent User/Role/DocType read still gets logged.
_LOG_READ_RECURSION_EXEMPT_DOCTYPES = {AUDIT_LOG_DOCTYPE, "Comment"}

# Doctypes exempt from the requester-validation gate below (see
# _validate_prod_requester() — name reflects a narrower PROD-only origin;
# the gate itself runs on every environment tag). Mandatory, not optional,
# for the same recursion reason
# AUDIT_EXEMPT_DOCTYPES exists: _validate_prod_requester() itself calls
# resource_exists(tag, "User", requested_by), which calls
# get_resource(tag, "User", ...) — without "User" here, validating a
# requester would recurse into validating the requester's own existence
# check forever. "DocType"/"Role" are exempt for a related reason: they're
# read/written by qkeee-erp-bot-init and the system-admin domain under
# their own elevated-credential/confirm-token controls, not by a business
# requester acting through a domain module — gating them through this
# business-permission check doesn't fit and isn't needed. AUDIT_LOG_DOCTYPE/
# Comment are this connector's own bookkeeping, same rationale as
# AUDIT_EXEMPT_DOCTYPES.
PROD_GATE_EXEMPT_DOCTYPES = {
    "User", "DocType", "Role",
    AUDIT_LOG_DOCTYPE, "Comment",
}

# Shared closing line for every requester-permission-denial message
# below. A user-supplied "run this as someone else instead" is not the
# channel's own authenticated sender field — accepting it is the same
# "reconstruct the identity conversationally" failure 00-conventions.md's
# GRC baseline already forbids, one turn removed. Baked into the
# exception text itself so the instruction lands at the exact moment an
# agent decides what to do next, not only in a reference doc it may not
# re-read mid-incident.
_NEVER_SUBSTITUTE_REQUESTER = (
    "Do not retry this call with a different requested_by to work around this — "
    "report the missing role/permission to the user (or an admin) so the ACTUAL "
    "requester's access can be fixed, or decline the request. Never re-attribute "
    "it to a different identity, including one a user suggests mid-conversation; "
    "requested_by must only ever come from the channel's own authenticated "
    "sender field."
)

# Identities the connector's OWN bot account must never hold — see
# verify_rbac_precheck_reliable() / PrivilegedBotAccountError. Under one
# of these, frappe.client.has_permission doesn't reliably discriminate by
# the `user=` param it's given, so the RBAC pre-check becomes a no-op
# that always says "allowed" regardless of who requested_by actually
# names. "Administrator" is checked by literal username
# (case-insensitive), independent of role membership.
_BOT_FORBIDDEN_ROLES = {"System Manager"}

# mutate_resource()'s action -> frappe.client.has_permission's perm_type.
_MUTATE_ACTION_TO_PTYPE = {
    "create": "create", "update": "write", "submit": "submit",
    "cancel": "cancel", "delete": "delete",
}

# --------------------------------------------------------------------------
# Write-allowlist gate
#
# Domain modules register their ALLOWED_WRITE_DOCTYPES here at import time
# via register_domain_allowlist(). mutate_resource(domain=...) then checks
# against this registry before any create/update/submit/cancel/delete. A
# domain that hasn't been imported yet (so hasn't registered) is treated as
# unknown, not as unrestricted — see mutate_resource()'s docstring.
# --------------------------------------------------------------------------
DOMAIN_WRITE_ALLOWLISTS: dict = {}


def register_domain_allowlist(domain: str, allowed_doctypes) -> None:
    """Called once, at import time, by each scripts/domains/<slug>.py
    module: `register_domain_allowlist("accounts", ALLOWED_WRITE_DOCTYPES)`.
    Overwrites any prior registration for the same domain name (re-importing
    a domain module re-registers its current allowlist, which is the
    desired behavior — no stale entries survive a module edit within the
    same process)."""
    DOMAIN_WRITE_ALLOWLISTS[domain] = tuple(allowed_doctypes)


# --------------------------------------------------------------------------
# Generic advisory-first confirmation-token gate for a domain's plain
# mutate() wrapper.
#
# fixed_assets.py / system_admin.py already carry their OWN bespoke
# double-confirm token schemes (depreciation_run_token(), disposal_token(),
# destructive_action_token(), permission_change_token(), ...) for their
# highest-blast-radius single actions — those stay exactly as they are and
# do NOT register here; this registry exists for domains that have no such
# bespoke scheme (accounts, hr_payroll, sales, procurement, inventory) so
# their plain create->update->submit/cancel/delete path — described in
# 00-conventions.md's Non-negotiable 5 as "three distinct steps, never
# chained" — gets a real code-level backstop for the submit/cancel/delete
# step, instead of relying on prompt discipline alone to keep create/update
# and submit separate turns. A domain registers the specific actions it
# wants gated this way; omitting a domain here (or omitting an action)
# means mutate_resource() applies no token check for it — either because
# that domain gates it its own way (fixed_assets, system_admin) or because
# nothing in it warrants one (mis's empty write allowlist, doc-extraction's
# lack of a connector).
DOMAIN_TOKEN_GATED_ACTIONS: dict = {}


def register_domain_token_gate(domain: str, actions) -> None:
    """Opt a domain's plain mutate_resource(domain=...) calls into the
    shared advisory_write_token gate for the given actions (normally
    {"submit", "cancel", "delete"} — create/update are the draft steps and
    stay ungated here, they're what gets reviewed before this gate ever
    triggers). Called once, at import time, alongside
    register_domain_allowlist()."""
    DOMAIN_TOKEN_GATED_ACTIONS[domain] = set(actions)


def _require_advisory_token(action: str, doctype: str, name: str, payload: dict,
                             requested_by: str, confirmation_token: str, issued_at) -> None:
    """Shared verification logic behind the generic domain token gate above
    — same freshness + exact-match-over-the-real-facts mechanics as
    gated_mutate_resource() and each domain's own bespoke token check, just
    factored out so mutate_resource() can apply it without duplicating the
    three checks inline."""
    if not confirmation_token or issued_at is None:
        raise ConnectorError(
            f"Refusing {action} on '{doctype}': this step requires a fresh "
            f"confirmation_token + issued_at. Show the reviewed draft/impact to the user, "
            f"get an explicit confirmation, compute the token via "
            f"confirm_token.py's advisory-token CLI (or advisory_write_token()) over these "
            f"exact (action, doctype, name, payload, requested_by, issued_at) facts, and "
            f"pass it here — never hand-construct one."
        )
    if not is_fresh(int(issued_at)):
        raise StaleConfirmationError(
            f"This confirmation for {action} on '{doctype}' has expired or its issued_at is "
            f"implausible — re-show the current draft/impact to the user, reconfirm, and get "
            f"a fresh token before retrying."
        )
    expected = advisory_write_token(action, doctype, name, payload or {}, requested_by, int(issued_at))
    if confirmation_token != expected:
        raise ConnectorError(
            f"confirmation_token does not match the (action, doctype, name, payload, "
            f"requested_by, issued_at) facts actually being submitted for {action} on "
            f"'{doctype}' — recompute it over exactly what was shown to and confirmed by the "
            f"user, don't hand-construct one."
        )


class ConnectorError(Exception):
    """Raised for missing config / auth / HTTP failures with a specific, actionable message."""


class ReadOnlyModeError(ConnectorError):
    """Raised when a write call is attempted while qkeee_erp.mode == read-only."""


class MissingRequesterError(ConnectorError):
    """Raised when a write call is attempted without a requested_by identity."""


class UnvalidatedProdRequesterError(ConnectorError):
    """Raised, on every tag (name reflects a narrower PROD-only origin —
    the gate is universal, see _validate_prod_requester()), when
    requested_by is missing, isn't a real ERPNext User, or lacks the
    permission this call needs per ERPNext's own
    frappe.client.has_permission check."""


class StaleConfirmationError(ConnectorError):
    """Raised when a confirmation_token's issued_at is too old (or implausibly
    future) — re-render the draft against current data and reconfirm."""


class UnconfirmedByUserError(ConnectorError):
    """Raised by gated_mutate_resource() when user_confirmation_text is
    missing, or doesn't contain the confirmation_token's derived
    confirmation_code — see confirm_token.confirmation_code()'s own
    docstring for what this does and doesn't prove. A matching
    confirmation_token alone proves the payload wasn't tampered with
    since render; it does NOT prove the render was ever shown to the
    actual requester — the same process can compute and verify that
    token in one turn. This is the additional check for gated_mutate_
    resource()'s domain-less write path specifically (a doctype no
    named domain's own mutate() has a chance to layer a stricter rule
    onto, unlike e.g. procurement's Supplier-KYC gate)."""


class DoctypeNotAllowedError(ConnectorError):
    """Raised when mutate_resource(domain=...) targets a doctype outside
    that domain's registered ALLOWED_WRITE_DOCTYPES (or the domain itself
    is unknown/unregistered) — see the write-allowlist gate section above."""


class PrivilegedBotAccountError(ConnectorError):
    """Not raised anywhere in this module — kept defined only for any
    external caller that still catches it specifically.

    Describes the condition it used to signal: this connector's OWN
    authenticated bot identity (not requested_by) is Administrator or
    holds a role in _BOT_FORBIDDEN_ROLES, or a live probe shows ERPNext's
    frappe.client.has_permission doesn't actually discriminate by the
    `user=` param on this instance — see verify_rbac_precheck_reliable()
    below, which still detects and warns on exactly this condition.
    _validate_prod_requester() now refuses via UnvalidatedProdRequesterError
    in this situation instead (whether the local role/DocPerm fallback
    check — see _requester_has_role_permission() — comes back with a
    confirmed non-grant or couldn't be completed at all): the underlying
    problem is the same either way ("requested_by's real permission can't
    be trusted") and gets one exception type, not two. Provision a
    genuinely narrow-role dedicated bot account instead (see init_bot.py
    / 00-conventions.md's bot-account requirement)."""


def _tag_env_var(tag: str, suffix: str) -> str:
    sanitized = "".join(c if c.isalnum() else "_" for c in tag.upper()) or "DEFAULT"
    return f"QKEEE_ERP_{sanitized}_{suffix}"


def resolve_requested_by(cli_value: str) -> str:
    """CLI-level requested_by resolution, called from `_cli()`.

    Thin pass-through, deliberately: `cli_value` (--requested-by on THIS
    call) is the only source. There is no tag-level or config default to
    fall back to — every prior fallback (QKEEE_ERP_<TAG>_REQUESTED_BY,
    any metadata.hermes.config key) has been removed. The caller (the
    Hermes agent driving this CLI) must resolve the real requester fresh
    from the inbound channel identity before calling — see the module
    docstring's "Requester identity comes from the channel, never from
    config". An absent value here is returned as "" and caught downstream
    by `_validate_prod_requester()` / `mutate_resource()`, which fail
    closed rather than silently proceeding unattributed."""
    return cli_value or ""


# SSN-shaped (###-##-####) and Luhn-valid 13-19 digit runs (spaces/dashes
# tolerated, e.g. a pasted "4111 1111 1111 1111"). Deliberately narrow —
# SSN/credit-card only, not a general DLP engine — see redact_pii().
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_CANDIDATE_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> str:
    """Best-effort redaction of SSN-shaped and Luhn-valid credit-card-
    shaped digit runs from free text before it's posted as an ERPNext
    Comment or stored in an audit-log free-text field (`approval_note`,
    `channel_metadata`). NOT a substitute for not typing sensitive values
    into these fields in the first place; this is a defensive backstop for
    text copied verbatim from chat/email that the calling code didn't
    itself catch. Pattern-based, narrow by design: SSN + credit card only,
    not general PII/DLP coverage — a business phone/account/PO number that
    happens to Luhn-validate is an accepted rare false positive, redaction
    erring toward over-redaction being the safer failure mode here.
    `None`/empty input passes through unchanged."""
    if not text:
        return text

    def _cc_sub(m):
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return "[REDACTED-CARD]"
        return m.group(0)

    text = _CC_CANDIDATE_RE.sub(_cc_sub, text)
    text = _SSN_RE.sub("[REDACTED-SSN]", text)
    return text


def _redact_pii_deep(obj):
    """Recursive redact_pii() over a JSON-shaped structure (dict/list/str)
    — used for channel_metadata, which is caller-supplied free-form JSON
    and may itself contain a pasted SSN/card number in one of its values."""
    if isinstance(obj, str):
        return redact_pii(obj)
    if isinstance(obj, dict):
        return {k: _redact_pii_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_pii_deep(x) for x in obj]
    return obj


def check_user_permission(tag: str, doctype: str, perm_type: str, requested_by: str,
                           docname: str = None) -> bool:
    """Asks ERPNext itself (frappe.client.has_permission) whether
    `requested_by` — NOT the bot account this connector authenticates
    as — holds `perm_type` ("read"/"write"/"create"/"submit"/"cancel"/
    "delete") on `doctype` (and on the specific `docname`, if given, for
    a record-level check; doctype-level only if omitted).

    KNOWN GAP: stock Frappe's `frappe.client.has_permission` whitelisted
    method checks the CURRENTLY AUTHENTICATED user's own permission by
    default — passing a `user=` query param to check permission "as" a
    different user is only honored on some Frappe versions/configurations.
    This connector always sends `user=<requested_by>` and trusts whatever
    ERPNext returns, but that does not confirm the target Frappe version
    actually evaluates permission for `requested_by` rather than silently
    evaluating for the bot account instead. Confirm this against each
    target instance before relying on it as an actual per-requester gate
    rather than the role-membership heuristic get_user_roles() already
    provides."""
    result = check_user_permission_raw(tag, doctype, perm_type, requested_by, docname)
    return bool(result.get("message"))


def check_user_permission_raw(tag: str, doctype: str, perm_type: str, requested_by: str,
                               docname: str = None) -> dict:
    """Raw response from frappe.client.has_permission, for a caller that
    wants to inspect more than the boolean. See check_user_permission()'s
    docstring for the per-instance verification caveat this shares.

    Some Frappe builds' `frappe.client.has_permission` has no default for
    `docname` — omitting the query param entirely 500s with `TypeError:
    has_permission() missing 1 required positional argument: 'docname'`
    for every doctype-level check (every `create`, and any `query_resource`
    list read). Sending `docname=""` satisfies the positional requirement
    and falls back to a doctype-level check, returning the same
    `has_permission` result as a record-level check (a non-empty
    placeholder like the literal string `"None"` 404s instead). Always
    send the param, empty string when no real docname exists yet.

    KNOWN GAP: at least one tested Frappe build's `has_permission` returns
    `true` for every `user=` value under a privileged (Administrator or
    System-Manager-holding) caller identity, including a deliberately
    nonexistent user, against a System-Manager-only doctype. See
    `verify_rbac_precheck_reliable()` below — it probes this exact
    failure mode per target instance and `_validate_prod_requester()`
    refuses to trust this function's result when the probe says the
    check doesn't discriminate."""
    cfg = get_env_config(tag)
    params = {"doctype": doctype, "perm_type": perm_type, "user": requested_by,
              "docname": docname or ""}
    return _request(cfg, "GET", "/api/method/frappe.client.has_permission", params=params)


# Per-tag caches for verify_rbac_precheck_reliable() — the bot's own
# identity and the live discrimination probe don't change mid-process, so
# each is resolved once per tag rather than on every read/write.
_BOT_IDENTITY_CACHE: dict = {}
_RBAC_PRECHECK_TRUST_CACHE: dict = {}
_PRECHECK_WARNED_TAGS: set = set()

# Deliberately never a real user — the probe's whole point is asking about
# an identity ERPNext cannot possibly grant real permissions to.
_RBAC_PROBE_BOGUS_USER = "qkeee-erp-rbac-probe-nonexistent-user@invalid.example"


def _bot_identity(tag: str) -> dict:
    """Resolve + cache the CONNECTOR'S OWN authenticated identity for this
    tag (username + roles) — not requested_by. One extra HTTP round trip
    per tag per process. A lookup failure is cached as an unknown identity
    (empty user, empty roles) rather than retried every call — treated as
    privileged/untrusted by verify_rbac_precheck_reliable() below, since an
    identity this connector can't even resolve can't be confirmed safe."""
    if tag not in _BOT_IDENTITY_CACHE:
        try:
            _BOT_IDENTITY_CACHE[tag] = get_user_roles(tag, internal=True)
        except ConnectorError:
            _BOT_IDENTITY_CACHE[tag] = {"user": "", "roles": []}
    return _BOT_IDENTITY_CACHE[tag]


def _probe_rbac_precheck_discriminates(tag: str) -> bool:
    """Live, per-tag probe (cached after first call): asks
    frappe.client.has_permission whether a deliberately bogus,
    guaranteed-nonexistent user holds 'write' on 'Role' (a System-Manager-
    only doctype in stock ERPNext). A trustworthy pre-check must answer
    False. Catches the failure mode live, per target instance, rather than
    trusting the static identity check alone — a future Frappe patch, or
    an instance-specific customization, could change this behavior in
    either direction, and the identity check alone can't see that.
    Anything that keeps this from getting a clean answer (a ConnectorError
    reaching the endpoint) is treated as "does not discriminate" — fail
    closed, never assume the pre-check works when it couldn't be probed."""
    if tag not in _RBAC_PRECHECK_TRUST_CACHE:
        try:
            bogus_allowed = check_user_permission(tag, "Role", "write", _RBAC_PROBE_BOGUS_USER)
        except ConnectorError:
            _RBAC_PRECHECK_TRUST_CACHE[tag] = False
        else:
            _RBAC_PRECHECK_TRUST_CACHE[tag] = not bogus_allowed
    return _RBAC_PRECHECK_TRUST_CACHE[tag]


def verify_rbac_precheck_reliable(tag: str) -> dict:
    """Whether this tag's RBAC pre-check (_validate_prod_requester() /
    check_user_permission()) can actually be trusted right now — combines
    the static identity check (bot account isn't Administrator or
    System Manager) with the live discrimination probe above. Never raises
    on its own; callers decide what to do with an unreliable result —
    _validate_prod_requester() falls back to a local, RPC-independent
    role/DocPerm check instead and requires a positively-confirmed
    grant to proceed, regardless of `domain`/advisory-token status (see
    that function's own docstring); health_check() just surfaces it as a
    warning. Called from `hermes qkeee-erp health`
    and internally before every write — safe to call repeatedly, both
    underlying checks are cached per tag."""
    identity = _bot_identity(tag)
    bot_user = (identity.get("user") or "").strip()
    bot_roles = set(identity.get("roles") or [])
    privileged_identity = (not bot_user) or bot_user.lower() == "administrator" or bool(bot_roles & _BOT_FORBIDDEN_ROLES)
    precheck_discriminates = _probe_rbac_precheck_discriminates(tag)
    return {
        "reliable": (not privileged_identity) and precheck_discriminates,
        "bot_user": bot_user,
        "bot_roles": sorted(bot_roles),
        "privileged_identity": privileged_identity,
        "precheck_discriminates": precheck_discriminates,
    }


# Per-(tag, doctype) cache for _fetch_doctype_role_permissions() below —
# mirrors _BOT_IDENTITY_CACHE's shape: a cached failure (tuple sentinel)
# is stored too, so a doctype this bot can't read DocType-level metadata
# for doesn't retry that fetch on every subsequent read/write in the same
# process. DocType metadata doesn't change mid-process.
_DOCTYPE_PERMISSIONS_CACHE: dict = {}

# DocPerm child-table keys kept — mirrors discover.py's _META_FIELD_KEYS
# filter, just for the `permissions` table instead of `fields`.
_PERM_FIELD_KEYS = {"role", "permlevel", "read", "write", "create",
                     "submit", "cancel", "delete", "if_owner"}


def _fetch_doctype_role_permissions(tag: str, doctype: str):
    """Live DocPerm rows (permlevel 0 only — see _requester_has_role_
    permission()'s docstring for why) for `doctype`, fetched directly via
    get_resource() rather than through discover.py: discover.py already
    imports FROM this module, so this module importing discover.py back
    would be circular. Returns `(rows_or_None, error_message_or_None)`,
    cached per (tag, doctype) including a cached failure — same shape and
    reasoning as schema_mapping.py's get_doctype_schema()."""
    cache_key = (tag, doctype)
    if cache_key not in _DOCTYPE_PERMISSIONS_CACHE:
        try:
            result = get_resource(tag, "DocType", doctype, requested_by="", internal=True)
            doc = result.get("data") or {}
            rows = [
                {k: p.get(k) for k in _PERM_FIELD_KEYS if k in p}
                for p in doc.get("permissions", [])
                if not p.get("permlevel")
            ]
            _DOCTYPE_PERMISSIONS_CACHE[cache_key] = rows
        except ConnectorError as e:
            _DOCTYPE_PERMISSIONS_CACHE[cache_key] = ("__error__", str(e))
    cached = _DOCTYPE_PERMISSIONS_CACHE[cache_key]
    if isinstance(cached, tuple) and cached and cached[0] == "__error__":
        return None, cached[1]
    return cached, None


def _requester_has_role_permission(tag: str, doctype: str, perm_type: str, requested_by: str):
    """Independent corroborating signal for whether `requested_by` holds
    `perm_type` on `doctype`, computed LOCALLY from two plain doc reads —
    the requester's own assigned roles (`get_user_roles()`) and the
    doctype's own live DocPerm rows (`_fetch_doctype_role_permissions()`
    above) — instead of trusting `frappe.client.has_permission`'s `user=`
    param, which `verify_rbac_precheck_reliable()` has already confirmed
    doesn't reliably discriminate under a privileged bot identity.
    Only ever consulted from `_validate_prod_requester()` when that RPC
    is already known unreliable — see the call site for why this isn't
    run unconditionally (a reliable `has_permission` already gives the
    authoritative, fully-Frappe-native answer; this is a narrower
    approximation, not a strictly better check).

    Returns `True`/`False` when both reads succeed and a verdict can
    actually be reached; `None` when either read fails — e.g. this bot
    also lacks the System-Manager-level DocType read, a common gap on a
    correctly least-privileged bot — or when
    `get_user_roles()`'s own result is empty (its own docstring: an empty
    roles list is ambiguous, never a confirmed "holds nothing"). Callers
    must treat `None` as "couldn't check," never as either verdict.

    Known, deliberate limitations — a corroborating signal, not a
    reimplementation of Frappe's permission engine:
    - **User Permissions** (a per-record Link-based restriction, e.g.
      "only this user's own territory/company") are invisible here — a
      role can look unconditionally permitted at the DocPerm level while
      a User Permission still narrows it further, server-side, in ways
      this function can't see.
    - **`if_owner`-scoped DocPerm rows are never counted toward `True`.**
      Ownership can't be cheaply verified without an extra fetch of the
      specific record (and is meaningless for `create`, which has no
      owner yet) — an if_owner-only match is treated as inconclusive
      (falls through to `False` here only if no OTHER, unconditional row
      matches; never silently upgraded to a confirmed grant)."""
    try:
        roles_result = get_user_roles(tag, requested_by, internal=True)
    except ConnectorError:
        return None
    requester_roles = set(roles_result.get("roles") or [])
    if not requester_roles:
        return None

    perm_rows, _fetch_error = _fetch_doctype_role_permissions(tag, doctype)
    if perm_rows is None:
        return None

    for row in perm_rows:
        if row.get("role") not in requester_roles:
            continue
        if row.get("if_owner"):
            continue
        if row.get(perm_type):
            return True
    return False


def _validate_prod_requester(tag: str, requested_by: str, doctype: str, perm_type: str,
                              docname: str = None, *, domain: str = None,
                              advisory_token_verified: bool = False,
                              session_id: str = None, domain_code: str = None,
                              channel: str = None, channel_metadata: dict = None,
                              prompt_summary: str = None, latest_prompt: str = None) -> None:
    """The requester-validation gate — RBAC pre-check, every environment.
    (Name reflects a narrower PROD-only origin; the check itself is
    universal.)

    `domain`/`advisory_token_verified` are still accepted
    (mutate_resource()/gated_mutate_resource() thread them through — the
    allowlist and advisory-token-confirm checks that produce them remain
    real, valuable, INDEPENDENT controls at their own gates: allowlist
    scopes which doctypes a domain may touch at all, advisory-token-
    confirm proves a payload matches what was actually shown to and
    confirmed by a human) but NEITHER rescues an unreliable-RBAC-precheck
    write on its own — see the "not reliable" branch below. They attest a
    write's SHAPE was reviewed ahead of time; neither one confirms
    `requested_by` specifically can do it, and once
    `frappe.client.has_permission` itself can't be trusted, that
    distinction isn't good enough to rescue this gate by itself.

    `session_id`/`domain_code`/`channel`/`channel_metadata`/
    `prompt_summary`/`latest_prompt`: carried through purely so the
    gate-decision Audit Log row this function writes for every branch
    (see _log_gate_decision()) has the same session/channel/prompt
    context every other audit row gets — no effect on the actual
    decision.

    No-op for any doctype in PROD_GATE_EXEMPT_DOCTYPES, on every tag.
    Otherwise:

    - Presence of `requested_by` is mandatory on EVERY tag, no exceptions.
      There is no env-var or config default to fall back to (see the
      module docstring); a caller must resolve the live inbound channel
      identity and pass it explicitly on every call.
    - Whenever `requested_by` is present it is validated as a real
      ERPNext User (resource_exists check), then checked for `perm_type`
      on `doctype`/`docname` — via ERPNext's own `has_permission` RPC
      when `verify_rbac_precheck_reliable()` says that RPC can be
      trusted; otherwise via `_requester_has_role_permission()`'s local,
      RPC-independent role/DocPerm check instead — see that function's
      own docstring, and the "not reliable" branch below, for exactly
      what that check can and can't see. Every supplied requester gets
      checked, on every tag, one way or the other — a bogus/unauthorized/
      unverifiable requester is never silently accepted.

    Raises UnvalidatedProdRequesterError on any failure — fails closed,
    never proceeds unverified. Called from query_resource()/get_resource()/
    run_query_report()/mutate_resource() — every read and write. Every
    raise, and the final allow, is also logged to Qkeee Bot Audit Log as
    one gate-decision row — a denial otherwise leaves zero trace."""
    if doctype in PROD_GATE_EXEMPT_DOCTYPES:
        return

    def _log(allowed: bool, detail: dict) -> None:
        _log_gate_decision(
            tag, perm_type=perm_type, doctype=doctype, docname=docname, requested_by=requested_by,
            allowed=allowed, detail=detail, session_id=session_id, domain_code=domain_code,
            channel=channel, channel_metadata=channel_metadata,
            prompt_summary=prompt_summary, latest_prompt=latest_prompt,
        )

    if not requested_by:
        _log(False, {"reason": "no_requester_given"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{doctype}' on tag '{tag}': no requester was "
            f"given. A validated, explicit requester is mandatory on every call, on "
            f"every environment — there is no env-var or config default to fall back "
            f"to. Resolve the inbound channel identity (the Google Chat/Teams/Slack "
            f"sender's own work email, the email channel's From address, etc.) as a "
            f"real ERPNext User first, then pass it explicitly via --requested-by / "
            f"requested_by= on this call."
        )
    if not resource_exists(tag, "User", requested_by):
        _log(False, {"reason": "requester_not_a_known_user"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{doctype}' on tag '{tag}': requester "
            f"'{requested_by}' is not a known ERPNext User. Never proceed with an "
            f"unvalidated channel identity — confirm the real ERPNext user id/email "
            f"before retrying."
        )
    trust = verify_rbac_precheck_reliable(tag)
    if not trust["reliable"]:
        if tag not in _PRECHECK_WARNED_TAGS:
            _PRECHECK_WARNED_TAGS.add(tag)
            print(
                f"WARN: RBAC pre-check is NOT reliable on tag '{tag}' — bot identity "
                f"{trust['bot_user']!r} is privileged ({trust['privileged_identity']}) "
                f"and/or the live has_permission probe didn't discriminate a bogus user "
                f"({not trust['precheck_discriminates']}). Per-requester permission is "
                f"verified locally instead (role list + doctype DocPerm rows) — a "
                f"confirmed grant is required to proceed; a `domain` allowlist or a verified "
                f"advisory token does not rescue this on their own (see "
                f"_requester_has_role_permission()'s docstring). Provision a narrow-role "
                f"dedicated bot account and re-run health() to restore the has_permission RPC "
                f"itself instead of depending on this fallback.",
                file=sys.stderr,
            )
        # has_permission's user= param is known unreliable here, so ask
        # the same question a different way — locally, from the
        # requester's own live role list and the doctype's own live
        # DocPerm rows (never through the broken RPC). Only a
        # locally-CONFIRMED grant (True) proceeds. A `domain` allowlist or
        # a verified advisory token attests this write's shape was
        # reviewed ahead of time — neither confirms requested_by
        # specifically can do it, so neither rescues an inconclusive
        # (None) or negative (False) local verdict; both fail closed the
        # same way. Applies uniformly to read and write — see
        # _requester_has_role_permission()'s own docstring for exactly
        # what this can and can't see.
        role_verdict = _requester_has_role_permission(tag, doctype, perm_type, requested_by)
        if role_verdict is True:
            _log(True, {"path": "local_role_docperm_fallback", "reason": "rbac_precheck_unreliable"})
            return
        if role_verdict is False:
            _log(False, {"path": "local_role_docperm_fallback", "reason": "no_granting_role"})
            raise UnvalidatedProdRequesterError(
                f"Refusing this call on tag '{tag}': requester '{requested_by}' holds no role "
                f"with '{perm_type}' permission on '{doctype}' per that doctype's own live "
                f"DocPerm rows — computed locally (role list + DocPerm rows) since "
                f"frappe.client.has_permission's user= param is known unreliable on this tag. "
                f"This is independent, corroborating evidence, not a full reimplementation of "
                f"Frappe's permission engine (User Permissions / if_owner scoping aren't "
                f"checked — see _requester_has_role_permission()'s docstring) — but a positive "
                f"'no role grants this' verdict overrides even a `domain` allowlist or a "
                f"verified advisory token, since those exist to cover 'can't verify,' not "
                f"'verified, and it's a no.' {_NEVER_SUBSTITUTE_REQUESTER}"
            )
        # role_verdict is None: the local check itself couldn't complete
        # (failed to resolve requester roles, or this doctype's live
        # DocPerm rows — commonly this bot also lacking System-Manager-
        # level DocType read, a common gap on a correctly least-privileged
        # bot). An unverifiable requester permission is refused outright
        # here too — a `domain` allowlist or a verified advisory token
        # does not rescue this either, since neither confirms
        # requested_by's own permission, only that the write's shape was
        # reviewed ahead of time. Trade-off, stated plainly: this makes
        # System-Manager-level DocType read a hard requirement for ANY
        # write once has_permission is unreliable, even a domain-scoped
        # one that could otherwise proceed on the allowlist alone —
        # availability loss, in exchange for never proceeding without
        # positive, locally-confirmed evidence.
        _log(False, {"path": "local_role_docperm_fallback", "reason": "inconclusive"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call on tag '{tag}': requester '{requested_by}''s '{perm_type}' "
            f"permission on '{doctype}' could not be verified at all — this connector's own "
            f"bot identity ({trust['bot_user']!r}) makes frappe.client.has_permission's user= "
            f"param unreliable on this tag, AND the local role/DocPerm fallback check couldn't "
            f"complete (failed to resolve requester '{requested_by}''s live roles, or this "
            f"doctype's live DocPerm rows). A `domain` allowlist or a verified advisory token "
            f"attests this write's shape was reviewed ahead of time, never that "
            f"'{requested_by}' can actually do it — neither is trusted to rescue an "
            f"unverifiable requester permission by itself. Provision a bot account that can "
            f"read User/DocType metadata (see init_bot.py / 00-conventions.md), or restore a "
            f"working has_permission RPC, to unblock writes on this tag. {_NEVER_SUBSTITUTE_REQUESTER}"
        )
    allowed = check_user_permission(tag, doctype, perm_type, requested_by, docname)
    if not allowed:
        _log(False, {"path": "has_permission_rpc"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call on tag '{tag}': requester '{requested_by}' does not "
            f"have '{perm_type}' permission on '{doctype}'"
            f"{f' (record {docname!r})' if docname else ''} per ERPNext's own permission "
            f"check (frappe.client.has_permission). Refusing rather than proceeding on "
            f"an unauthorized request. {_NEVER_SUBSTITUTE_REQUESTER}"
        )
    _log(True, {"path": "has_permission_rpc"})


def _qkeee_env_file_path() -> str:
    """Path to the isolated ERPNext-credentials file, deliberately separate
    from Hermes' own profile .env. execute_code/terminal strip ALL env vars
    from the sandbox by default; a var only survives if a loaded skill's
    frontmatter `required_environment_variables` names it exactly — but a
    user-chosen --tag can never be declared ahead of time in static
    frontmatter, so QKEEE_ERP_<TAG>_* for any tag other than the one named
    at install time gets silently stripped from the sandbox even when it's
    sitting correctly in the profile's real .env. Reading a dedicated file
    directly (bypassing os.environ/the passthrough registry entirely)
    sidesteps that mismatch, and keeps these credentials physically
    separate from any LLM-provider secret that might live in the main
    .env. HERMES_HOME is unconditionally forwarded into every sandbox
    child regardless of skill declarations, so it's a reliable anchor even
    when the tag-specific vars themselves aren't. Falls back to CWD for a
    bare non-Hermes shell running this script directly."""
    base = os.environ.get("HERMES_HOME") or os.getcwd()
    return os.path.join(base, "qkeee-erp.env")


def _load_qkeee_env_file() -> dict:
    """Hand-rolled KEY=VALUE parser for qkeee-erp.env (no python-dotenv —
    this module is stdlib-only by design, see module docstring). Comments
    (#) and blank lines skipped; a single layer of surrounding quotes is
    stripped, matching common .env convention. A missing file is not an
    error — callers fall back to os.environ for back-compat with a
    manually-exported shell."""
    path = _qkeee_env_file_path()
    result = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key:
                    result[key] = value
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"WARN: failed to read {path} (non-fatal, falling back to os.environ): {e}", file=sys.stderr)
    return result


_QKEEE_ENV_FILE_CACHE = None


def _qkeee_env() -> dict:
    """Merged config view: qkeee-erp.env file values take precedence over
    os.environ (the file is the source of truth once it exists), os.environ
    remains the fallback for manual/CI runs that still export vars
    directly. Cached per-process — the file doesn't change mid-invocation."""
    global _QKEEE_ENV_FILE_CACHE
    if _QKEEE_ENV_FILE_CACHE is None:
        _QKEEE_ENV_FILE_CACHE = _load_qkeee_env_file()
    merged = dict(os.environ)
    merged.update(_QKEEE_ENV_FILE_CACHE)
    return merged


def get_env_config(tag: str = "default") -> dict:
    """Resolve base_url/api_key/api_secret for a given environment tag.

    Fails with a specific "missing QKEEE_ERP_<TAG>_API_KEY" style error,
    never a generic auth failure.

    Refuses a non-https base_url by default — _request() sends the bot
    account's api_key/api_secret in a plain Authorization header on every
    call, so a plaintext http:// target means those credentials cross the
    wire in the clear. Set QKEEE_ERP_<TAG>_ALLOW_INSECURE=1 to override
    for a genuine local/dev http instance.

    Does NOT resolve a requester default — there is no
    QKEEE_ERP_<TAG>_REQUESTED_BY (removed). `requested_by` is always
    supplied by the caller, resolved fresh from the inbound channel
    identity; see resolve_requested_by() / the module docstring.
    """
    env = _qkeee_env()
    base_url = env.get(_tag_env_var(tag, "BASE_URL"))
    api_key = env.get(_tag_env_var(tag, "API_KEY"))
    api_secret = env.get(_tag_env_var(tag, "API_SECRET"))

    missing = [
        name
        for name, val in (
            (_tag_env_var(tag, "BASE_URL"), base_url),
            (_tag_env_var(tag, "API_KEY"), api_key),
            (_tag_env_var(tag, "API_SECRET"), api_secret),
        )
        if not val
    ]
    if missing:
        raise ConnectorError(
            f"Missing environment variable(s) for tag '{tag}': {', '.join(missing)}. "
            f"Set them in {_qkeee_env_file_path()} (create it if missing — KEY=VALUE per line), "
            f"or export them directly, then retry."
        )

    base_url = base_url.rstrip("/")
    if not base_url.startswith("https://") and not env.get(_tag_env_var(tag, "ALLOW_INSECURE")):
        raise ConnectorError(
            f"'{_tag_env_var(tag, 'BASE_URL')}' ({base_url}) is not https — refusing to send "
            f"credentials over plaintext transport by default. Set "
            f"{_tag_env_var(tag, 'ALLOW_INSECURE')}=1 to override for a genuine local/dev "
            f"http instance."
        )

    return {
        "tag": tag,
        "base_url": base_url,
        "api_key": api_key,
        "api_secret": api_secret,
    }


def _request(cfg: dict, method: str, path: str, params: dict = None, payload: dict = None) -> dict:
    url = cfg["base_url"] + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {cfg['api_key']}:{cfg['api_secret']}")
    req.add_header("Content-Type", "application/json")
    # Python's default urllib UA ("Python-urllib/x.y") is blocked by common
    # WAF/bot-protection (e.g. Cloudflare) fronting production ERPNext
    # instances, returning a 403 that looks like an auth failure but isn't.
    # Always send an explicit UA.
    req.add_header("User-Agent", "qkeee-erp-associate/1.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ConnectorError(
            f"ERPNext API error ({e.code}) on {method} {path} against '{cfg['tag']}' "
            f"({cfg['base_url']}): {body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise ConnectorError(
            f"Could not reach '{cfg['tag']}' ({cfg['base_url']}): {e.reason}. "
            f"Check the base URL and network connectivity."
        ) from e


def health_check(tag: str = "default") -> dict:
    """Verify active environment is reachable and authenticated.

    Confirms connectivity + valid credentials only — not query/write-time
    permission on any specific DocType (e.g. a role-restricted bot account
    may health-check fine yet still 403 on a later read/write against a
    doctype it lacks access to). Report a later permission error as its
    own distinct failure mode, not folded into "connectivity is broken".

    Also runs verify_rbac_precheck_reliable() (cached after the first call
    per tag) and surfaces it as `rbac_precheck_reliable` — never fails this
    health check on its own; a caller should read the reliability flag and
    warn/act on it, matching how a doctype-specific permission gap is its
    own distinct failure mode rather than a reason to fail connectivity.
    """
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user")
    trust = verify_rbac_precheck_reliable(tag)
    out = {
        "tag": tag, "base_url": cfg["base_url"], "status": "ok",
        "logged_in_as": result.get("message"),
        "rbac_precheck_reliable": trust["reliable"],
    }
    if not trust["reliable"]:
        out["rbac_precheck_warning"] = (
            f"This tag's RBAC pre-check cannot be trusted: bot identity "
            f"{trust['bot_user']!r} is privileged={trust['privileged_identity']} "
            f"and the live has_permission probe discriminates="
            f"{trust['precheck_discriminates']}. Per-requester permission can't be "
            f"verified on this tag: a domain-scoped write (allowlisted doctype) or a "
            f"gated_mutate_resource() write with a verified advisory-draft token still "
            f"proceeds, on that reviewed control + mandatory review-before-submit as the "
            f"safety net; a write with neither (no `domain`, no verified token) is refused "
            f"outright. Provision a narrow-role dedicated bot account to restore real "
            f"per-requester verification — see init_bot.py / 00-conventions.md."
        )
    return out


def query_resource(tag: str, doctype: str, filters: list = None, fields: list = None, limit: int = 20,
                    *, session_id: str = None, domain_code: str = None,
                    requested_by: str = None, channel: str = None, channel_metadata: dict = None,
                    prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """Generic resource query — read any DocType with filters/fields.

    Fetches one extra row beyond `limit` to detect truncation, then trims
    back to `limit` — callers get an explicit `has_more` flag instead of a
    result set that's silently incomplete.

    Every read is logged to Qkeee Bot Audit Log (best-effort), unconditionally
    — accepted volume cost (a read-heavy domain like MIS makes Read rows
    the biggest source in the audit trail) in exchange for an audit row on
    every access, no exceptions.
    """
    _validate_prod_requester(tag, requested_by, doctype, "read",
                              session_id=session_id, domain_code=domain_code,
                              channel=channel, channel_metadata=channel_metadata,
                              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    cfg = get_env_config(tag)
    params = {"limit_page_length": limit + 1}
    if filters:
        params["filters"] = json.dumps(filters)
    if fields:
        params["fields"] = json.dumps(fields)
    path = f"/api/resource/{urllib.parse.quote(doctype)}"
    result = _request(cfg, "GET", path, params=params)
    rows = result.get("data", [])
    has_more = len(rows) > limit
    response = {"data": rows[:limit], "has_more": has_more, "limit": limit}

    _log_read(cfg, doctype, None, requested_by, session_id, domain_code, channel, channel_metadata,
              response_payload=response, prompt_summary=prompt_summary, latest_prompt=latest_prompt)

    return response


# Fields stripped from get_resource() output: audit/system metadata and
# presentation-only HTML/display fields that no review or reporting logic
# reads. Never strips Link fields, child tables, or anything a review step
# would check for validity. Measured live against a real instance (Sales
# Order doc): ~38% byte reduction.
_NOISE_FIELDS = {
    "owner", "creation", "modified", "modified_by", "idx", "naming_series",
    "title", "other_charges_calculation", "terms", "address_display",
    "shipping_address", "company_address_display", "in_words",
    "base_in_words", "language", "doctype", "parentfield", "parenttype",
}


def _strip_noise(obj):
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items()
                if k not in _NOISE_FIELDS and v not in (None, "")}
    if isinstance(obj, list):
        return [_strip_noise(x) for x in obj]
    return obj


def get_resource(tag: str, doctype: str, name: str, strip_noise: bool = True,
                  *, session_id: str = None, domain_code: str = None,
                  requested_by: str = None, channel: str = None, channel_metadata: dict = None,
                  prompt_summary: str = None, latest_prompt: str = None,
                  internal: bool = False) -> dict:
    """Single-resource full-doc GET — the only way to get child-table rows.

    Frappe's list endpoint (query_resource()) silently drops Table-type
    (child-table) fields even when named in `fields`, while the
    single-resource GET ignores `fields` entirely and always returns the
    full doc. Use get_resource() only when child-table Link validity
    actually needs checking (e.g. a review-before-submit step) — for
    reads that don't need child-table data, query_resource() with
    filters+fields is far cheaper.

    strip_noise=True (default) drops audit/system metadata and
    presentation-only HTML fields before returning — see _NOISE_FIELDS.

    Every read is logged to Qkeee Bot Audit Log, unconditionally — same as
    query_resource(), see that function's docstring — UNLESS
    `internal=True`: pass this only when this call is the connector's own
    plumbing (an existence/metadata check made on behalf of the gate
    itself, not a business-intent read) — see resource_exists() and
    _fetch_doctype_role_permissions(), the only two callers that ever set
    it.
    """
    _validate_prod_requester(tag, requested_by, doctype, "read", docname=name,
                              session_id=session_id, domain_code=domain_code,
                              channel=channel, channel_metadata=channel_metadata,
                              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    cfg = get_env_config(tag)
    path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
    result = _request(cfg, "GET", path)
    data = result.get("data")
    if strip_noise and data is not None:
        data = _strip_noise(data)

    _log_read(cfg, doctype, name, requested_by, session_id, domain_code, channel, channel_metadata,
              response_payload={"data": data}, prompt_summary=prompt_summary, latest_prompt=latest_prompt,
              internal=internal)

    return {"data": data}


def resource_exists(tag: str, doctype: str, name: str) -> bool:
    """404-tolerant existence check. Never logged (internal=True,
    regardless of doctype), never gated (PROD_GATE_EXEMPT_DOCTYPES covers
    "User"/"DocType"/"Role", the only doctypes this is ever called
    against)."""
    try:
        get_resource(tag, doctype, name, strip_noise=False, internal=True)
        return True
    except ConnectorError as e:
        if "(404)" in str(e):
            return False
        raise


def run_query_report(tag: str, report_name: str, filters: dict = None,
                      *, session_id: str = None, domain_code: str = None,
                      requested_by: str = None, channel: str = None, channel_metadata: dict = None,
                      prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """Run one of ERPNext's own built-in reports server-side (Query Report
    or Script Report) via frappe.desk.query_report.run, instead of hand-
    aggregating raw transactional rows into the same shape. Prefer this
    whenever a built-in report covers the need. Read-only in effect (runs
    a report, creates nothing).

    GET + query-string filters, not POST (this is what ERPNext v15's
    frappe.desk.query_report.run endpoint expects). `filters` is a plain
    dict of report-specific filter values; field names vary per report —
    confirm the exact filter
    keys a given report expects by opening it in the ERPNext UI once,
    since this generic endpoint doesn't self-document per-report filter
    schemas.

    Every read is logged to Qkeee Bot Audit Log, unconditionally, against
    reference_doctype "Report" with reference_name=report_name, since a
    query report isn't itself a DocType record being read.
    """
    _validate_prod_requester(tag, requested_by, "Report", "read", docname=report_name,
                              session_id=session_id, domain_code=domain_code,
                              channel=channel, channel_metadata=channel_metadata,
                              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    cfg = get_env_config(tag)
    params = {"report_name": report_name}
    if filters:
        params["filters"] = json.dumps(filters)
    result = _request(cfg, "GET", "/api/method/frappe.desk.query_report.run", params=params)
    message = result.get("message", {})
    response = {
        "report_name": report_name,
        "columns": message.get("columns", []),
        "result": message.get("result", []),
    }

    _log_read(cfg, "Report", report_name, requested_by, session_id, domain_code, channel, channel_metadata,
              response_payload=response, prompt_summary=prompt_summary, latest_prompt=latest_prompt)

    return response


def get_user_roles(tag: str, user: str = "", *, requested_by: str = None,
                    session_id: str = None, domain_code: str = None,
                    channel: str = None, channel_metadata: dict = None,
                    prompt_summary: str = None, latest_prompt: str = None,
                    internal: bool = False) -> dict:
    """Fetch a user's assigned roles — the standard (heuristic, not
    guaranteed) signal for whether the acting user plausibly holds
    authority for a given write, when no ERPNext Workflow doctype is
    configured for the record type in question. An org with a real
    approval Workflow should be asked about it directly rather than
    relying on this alone.

    `user` defaults to the empty string, in which case this resolves the
    currently-authenticated user's own roles via the health-check
    endpoint first — get_env_config() has no notion of "which user this
    API key belongs to" (Frappe token auth doesn't expose that directly).

    Unlike query_resource()/get_resource()/run_query_report(), this
    doesn't route through those — it logs to Qkeee Bot Audit Log directly
    via `_log_read()` unless `internal=True`. Pass
    `internal=True` only from this connector's own RBAC plumbing
    (_bot_identity(), _requester_has_role_permission()) — a caller asking
    on a requester's actual behalf (the CLI `roles` command, a domain
    script) leaves it False and gets a real row. `requested_by` here is
    who's ASKING to see the roles, distinct from `user` (whose roles are
    being looked up) — attribution only, this function isn't gated by
    _validate_prod_requester() (used, among other things, to compute the
    very permission picture that gate depends on)."""
    cfg = get_env_config(tag)
    target = user
    if not target:
        who = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user")
        target = who.get("message", "")
    path = f"/api/resource/User/{urllib.parse.quote(target)}"
    result = _request(cfg, "GET", path)
    doc = result.get("data", {})
    roles = [r.get("role") for r in doc.get("roles", []) if r.get("role")]
    # An empty roles list is ambiguous: it could mean "confirmed, this user
    # genuinely holds no relevant role" or a lookup that silently came back
    # thin. Surface that ambiguity explicitly rather than letting the
    # caller treat empty the same as "checked, no authority" — either way
    # the safe default is to treat authority as unconfirmed.
    warning = (
        "No roles returned for this user — could mean the user genuinely "
        "holds no relevant role, or that the lookup didn't resolve "
        "correctly (wrong username, or this API key lacks permission to "
        "read User.roles). Treat as 'authority not confirmed' either way, "
        "but corroborate with the user rather than assuming the former."
        if not roles else ""
    )
    if not internal:
        _log_read(cfg, "User", target, requested_by, session_id, domain_code, channel, channel_metadata,
                   response_payload={"user": target, "roles": roles},
                   prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    return {"user": target, "roles": roles, "warning": warning}


# qkeee-erp:write-path
def record_comment(cfg: dict, doctype: str, name: str, content: str) -> bool:
    """Best-effort: post a Comment onto an ERPNext record via
    frappe.desk.form.utils.add_comment, so the audit trail lives in
    ERPNext itself, not only in this session's chat transcript. Never
    raises — a comment failure must not block or roll back the actual
    write it's documenting. Returns True on success, False on failure.

    `content` is passed through redact_pii() first — a Comment is a
    permanent, human-visible ERPNext record; an SSN/credit-card number
    pasted into chat and echoed verbatim into a Comment would otherwise
    persist there indefinitely.

    `comment_email`/`comment_by`: on Frappe 16, `add_comment()`'s
    signature requires both as positional args with no default
    (`TypeError: add_comment() missing 2 required positional arguments`);
    Frappe 15 doesn't require them. Always sent as
    this connector's own authenticated bot identity (resolved via
    `_bot_identity()`, already cached per tag) — the Comment's `content`
    string already carries the actual `requested_by` attribution, these
    two fields are Frappe's own "commented by" metadata for the party
    that physically made the API call, not the human being attributed
    to. Falls back to a fixed literal if bot-identity resolution itself
    failed, rather than sending an empty string Frappe might also
    reject."""
    try:
        bot_user = _bot_identity(cfg["tag"]).get("user") or SKILL_LABEL
        _request(cfg, "POST", "/api/method/frappe.desk.form.utils.add_comment", payload={
            "reference_doctype": doctype,
            "reference_name": name,
            "content": redact_pii(content),
            "comment_email": bot_user,
            "comment_by": bot_user,
        })
        return True
    except ConnectorError:
        return False


# --------------------------------------------------------------------------
# Audit logging (Qkeee Bot Audit Log)
#
# Audit logging is best-effort, not a gate. If the target instance hasn't
# run qkeee-erp-bot-init yet, or the audit doctypes are unreachable for any
# reason, every function below swallows the failure and the caller's real
# ERPNext read/write proceeds unaffected. The alternative — refusing a
# user's actual requested action because internal bookkeeping infra isn't
# provisioned — would regress write availability behind an infra rollout,
# which is a worse failure mode than an occasional unaudited call. This
# mirrors record_comment()'s existing best-effort posture, just applied to
# a bigger piece of infrastructure.
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


SESSION_FIELD_MAX_LEN = 140  # Frappe Data fieldtype default length
CHANNEL_OPTIONS = {"Web", "Discord", "Telegram", "WhatsApp", "Email", "Slack", "Google Chat", "CLI", "API", "Other"}
# lets callers pass loose forms ("google_chat", "google-chat") and still hit the canonical option
_CHANNEL_ALIASES = {opt.lower().replace(" ", "").replace("_", "").replace("-", ""): opt for opt in CHANNEL_OPTIONS}

# Defensive caps on the three new free-form audit fields (response_payload,
# prompt_summary, latest_user_prompt) — all Long/Small Text (unbounded in
# MariaDB), but a pathological caller (a huge query result, a copy-pasted
# essay as "the prompt") shouldn't be able to bloat a single audit row
# without limit. Same truncate-don't-reject posture as error_detail below.
RESPONSE_PAYLOAD_MAX_LEN = 20000
PROMPT_SUMMARY_MAX_LEN = 500
LATEST_PROMPT_MAX_LEN = 4000


def _truncate_str(value: str, max_len: int) -> str:
    if value is None or len(value) <= max_len:
        return value
    return value[:max_len] + f"...[TRUNCATED, {len(value) - max_len} more chars]"


def _session_or_fallback(session_id: str) -> str:
    """`session` is a mandatory field on Qkeee Bot Audit Log. Callers that
    never got/passed a real session_id (e.g. CLI invocations without
    --session-id) must still produce a non-empty value here — an empty
    string fails Audit Log's mandatory-field validation, and because
    _audit_insert() swallows all exceptions by design, that failure is
    otherwise invisible (the row is just silently never written).

    Also clamps to SESSION_FIELD_MAX_LEN. A chat-platform
    session_id carried across a long-lived/resumed conversation can drift
    into something oversized or otherwise malformed (unlike requested_by/
    reference_doctype/etc, this value is never validated anywhere upstream
    of the raw ERPNext insert) — the Data field then silently rejects the
    whole row, and because the insert is swallowed by design, the only
    symptom is a blank/missing Audit Log row days later. Truncating here
    keeps the row alive even when the caller's session_id has gone bad;
    the caller should still fix why it went bad (see conventions doc)."""
    value = session_id or f"local-{_now_iso()}"
    return value[:SESSION_FIELD_MAX_LEN]


def _safe_channel(channel: str) -> str:
    """`channel` is a Select field with a fixed option list — ERPNext
    rejects any value outside it. A stale caller-side session can hand
    back a channel string from a since-changed option set; fall back to
    'Other' rather than let that reject the whole audit row."""
    if channel in CHANNEL_OPTIONS:
        return channel
    normalized = _CHANNEL_ALIASES.get(channel.lower().replace(" ", "").replace("_", "").replace("-", "")) if channel else None
    return normalized or ("Other" if channel else "")


# qkeee-erp:write-path
def _diff_fields(before: dict, after: dict) -> list:
    """Field-by-field diff for the Update action's field_diff JSON. Compares
    top-level keys only (child-table diffing isn't attempted); skips
    noise/metadata fields."""
    if not before or not after:
        return []
    keys = (set(before.keys()) | set(after.keys())) - _NOISE_FIELDS
    diff = []
    for k in sorted(keys):
        old, new = before.get(k), after.get(k)
        if old != new:
            diff.append({"fieldname": k, "old": old, "new": new})
    return diff


# Per-tag count of consecutive _audit_insert() failures — each individual
# failure already warns once (below), but a single stderr line is easy to
# miss in a long-running session. This tracks the streak so a PERSISTENT
# failure (bot-init never run, audit doctype permission revoked, instance
# unreachable) escalates to a louder, distinct warning rather than looking
# identical to one transient blip. Never raises, never blocks the real
# write either way — see module docstring's "Audit logging is best-effort,
# not a gate."
_AUDIT_FAILURE_STREAK: dict = {}
AUDIT_FAILURE_STREAK_WARN_THRESHOLD = 3


def _audit_insert(cfg: dict, fields: dict) -> str:
    """Raw best-effort insert into Qkeee Bot Audit Log. Returns the created
    record's name, or None on any failure (doctype not provisioned,
    permission denied, network error, etc.) — never raises."""
    tag = cfg.get("tag", "")
    try:
        payload = {"doctype": AUDIT_LOG_DOCTYPE, **fields}
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}", payload=payload)
        _AUDIT_FAILURE_STREAK[tag] = 0
        return (result.get("data") or {}).get("name")
    except Exception as e:
        # Broad by design: audit logging must never surface a failure mode
        # that could be mistaken for the real write failing. Still warn to
        # stderr so a persistently-failing audit path is visible in logs.
        print(f"WARN: audit log insert failed (non-fatal): {e}", file=sys.stderr)
        streak = _AUDIT_FAILURE_STREAK.get(tag, 0) + 1
        _AUDIT_FAILURE_STREAK[tag] = streak
        if streak >= AUDIT_FAILURE_STREAK_WARN_THRESHOLD:
            print(
                f"WARN: audit logging has now failed {streak} times in a row on tag "
                f"'{tag}' — this looks systemic (bot-init not run, audit doctype permission "
                f"revoked, or the instance unreachable), not a one-off. Every read/write is "
                f"still proceeding unaudited; surface this to the user/operator rather than "
                f"treating each failure as independent.",
                file=sys.stderr,
            )
        return None


# qkeee-erp:write-path
def _audit_update(cfg: dict, log_name: str, fields: dict) -> bool:
    """Raw best-effort update of an existing Audit Log row. Returns success."""
    if not log_name:
        return False
    try:
        path = f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}/{urllib.parse.quote(log_name)}"
        _request(cfg, "PUT", path, payload=fields)
        return True
    except Exception as e:
        print(f"WARN: audit log update failed (non-fatal): {e}", file=sys.stderr)
        return False


# qkeee-erp:write-path
def _audit_submit(cfg: dict, log_name: str) -> bool:
    """Best-effort submit (docstatus lock) of a finished Audit Log row.
    Failure here leaves the row as a readable draft rather than blocking
    anything — the row's content is what matters for the audit trail;
    submission is a tamper-evidence nicety on top."""
    if not log_name:
        # The insert this depends on already failed and warned (returns
        # None), most commonly a 403 on Qkeee Bot Audit Log for a
        # requester lacking the Qkeee Bot role. Without this guard,
        # urllib.parse.quote(None) raises a confusing second warning
        # ("quote_from_bytes() expected bytes") that masks the real,
        # already-reported cause.
        return False
    try:
        path = f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}/{urllib.parse.quote(log_name)}"
        existing = _request(cfg, "GET", path)
        full_doc = existing.get("data")
        if not full_doc:
            return False
        _request(cfg, "POST", "/api/method/frappe.client.submit", payload={"doc": full_doc})
        return True
    except Exception as e:
        print(f"WARN: audit log submit failed (non-fatal): {e}", file=sys.stderr)
        return False


def _log_read(cfg: dict, doctype: str, name: str, requested_by: str, session_id: str, domain_code: str,
              channel: str = None, channel_metadata: dict = None, response_payload=None,
              prompt_summary: str = None, latest_prompt: str = None, internal: bool = False) -> None:
    """Best-effort insert+submit Audit Log row for a read — called
    unconditionally by query_resource()/get_resource()/run_query_report()/
    get_user_roles(). Insert/update are collapsed into one status
    ("Success") since a read has no in-flight state to crash into, but
    submit still runs so the row doesn't sit as an unsubmitted Draft like
    two-phase write rows would if left unfinished.

    `response_payload` is the caller's already-built response body (the
    'data'/'result' the CLI/caller is about to hand back) — stored so the
    audit row shows exactly what records were returned, not just that a
    read happened. Redacted the same way channel_metadata is, then
    truncated (see RESPONSE_PAYLOAD_MAX_LEN).

    `internal=True`: skip logging regardless of doctype — set only by a
    caller that is itself connector plumbing (an existence/metadata check
    made on behalf of the gate, not a business-intent read on someone's
    behalf). A doctype-based exemption alone would also swallow a genuine
    business read of User/Role (e.g. `query User`, `roles <user>`) — see
    _LOG_READ_RECURSION_EXEMPT_DOCTYPES's own comment for why that set
    stays deliberately narrower than AUDIT_EXEMPT_DOCTYPES."""
    if internal or doctype in _LOG_READ_RECURSION_EXEMPT_DOCTYPES:
        return
    log_name = _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "domain_code": (domain_code or "")[:SESSION_FIELD_MAX_LEN],
        "environment_tag": cfg.get("tag", ""),
        "channel": _safe_channel(channel),
        "channel_metadata": json.dumps(_redact_pii_deep(channel_metadata)) if channel_metadata else None,
        "action": "Read",
        "reference_doctype": doctype,
        "reference_name": name or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Success",
        "user_approved": "Not Required",
        "response_payload": _truncate_str(json.dumps(_redact_pii_deep(response_payload)), RESPONSE_PAYLOAD_MAX_LEN)
                             if response_payload is not None else None,
        "prompt_summary": _truncate_str(redact_pii(prompt_summary), PROMPT_SUMMARY_MAX_LEN) if prompt_summary else None,
        "latest_user_prompt": _truncate_str(redact_pii(latest_prompt), LATEST_PROMPT_MAX_LEN) if latest_prompt else None,
    })
    _audit_submit(cfg, log_name)


# perm_type ("read"/"write"/"create"/"submit"/"cancel"/"delete", the
# vocabulary _validate_prod_requester()/_MUTATE_ACTION_TO_PTYPE use)
# mapped onto the "action" values Qkeee Bot Audit Log already accepts
# elsewhere (_log_read()'s "Read", mutate_resource()'s action.capitalize()
# — "Create"/"Update"/"Submit"/"Cancel"/"Delete"). Deliberately reuses
# this existing vocabulary rather than inventing a new one (e.g. a
# "Permission Check" action) — this doctype is provisioned live on each
# target instance (see init_bot.py), and a novel Select value would need
# a live schema change everywhere this ships, for a distinction
# (gate-decision row vs. real read/write row) that response_payload's
# `gate_check: true` marker below already makes unambiguous to a reader.
_PTYPE_TO_ACTION = {
    "read": "Read", "create": "Create", "write": "Update",
    "submit": "Submit", "cancel": "Cancel", "delete": "Delete",
}


def _log_gate_decision(tag: str, *, perm_type: str, doctype: str, docname: str, requested_by: str,
                        allowed: bool, detail: dict, session_id: str = None, domain_code: str = None,
                        channel: str = None, channel_metadata: dict = None,
                        prompt_summary: str = None, latest_prompt: str = None) -> None:
    """Best-effort insert+submit Audit Log row for ONE requester-
    permission-gate decision from _validate_prod_requester() — covers
    both denials and allows. Without this, a refused call would leave no
    trace anywhere in Qkeee Bot Audit Log: the gate raises before the
    read/write it's guarding, and doesn't log itself otherwise. A denial
    is exactly the row a GRC review most wants to find.

    Deliberately ONE row per gate call, not one per internal HTTP call
    the gate makes to reach its verdict (resource_exists, the
    has_permission RPC or the RBAC-reliability probe, the local
    role/DocPerm fallback) — those together answer a single question
    ("was this requester allowed to do this, and how was that decided"),
    and logging each of them separately would multiply read-audit volume
    roughly 5x for no added meaning. `detail` carries that single
    decision's shape (which path was used — has_permission RPC vs. the
    local role/DocPerm fallback vs. exempt — and why) inside
    `response_payload`, tagged `gate_check: true` so it reads distinctly
    from a real read/write row even though it reuses the same
    action/status vocabulary (see _PTYPE_TO_ACTION above).

    Never raises: called from inside a gate that may itself be the thing
    failing (e.g. this tag's environment can't be resolved at all) — that
    failure mode has no instance to log against either, so it's swallowed
    the same way every other best-effort audit call in this module is."""
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return
    try:
        cfg = get_env_config(tag)
    except ConnectorError:
        return
    log_name = _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "domain_code": (domain_code or "")[:SESSION_FIELD_MAX_LEN],
        "environment_tag": cfg.get("tag", ""),
        "channel": _safe_channel(channel),
        "channel_metadata": json.dumps(_redact_pii_deep(channel_metadata)) if channel_metadata else None,
        "action": _PTYPE_TO_ACTION.get(perm_type, perm_type.capitalize() if perm_type else "Read"),
        "reference_doctype": doctype,
        "reference_name": docname or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Success" if allowed else "Failure",
        "user_approved": "Not Required",
        "response_payload": _truncate_str(json.dumps(_redact_pii_deep(
            {"gate_check": True, "perm_type": perm_type, "allowed": allowed, **(detail or {})}
        )), RESPONSE_PAYLOAD_MAX_LEN),
        "prompt_summary": _truncate_str(redact_pii(prompt_summary), PROMPT_SUMMARY_MAX_LEN) if prompt_summary else None,
        "latest_user_prompt": _truncate_str(redact_pii(latest_prompt), LATEST_PROMPT_MAX_LEN) if latest_prompt else None,
    })
    _audit_submit(cfg, log_name)


# qkeee-erp:write-path
def record_audit_log_start(cfg: dict, *, action: str, doctype: str, name: str, requested_by: str,
                            session_id: str = None, domain_code: str = None,
                            channel: str = None, channel_metadata: dict = None,
                            payload_before: dict = None, user_approved: bool = False,
                            approval_note: str = None,
                            prompt_summary: str = None, latest_prompt: str = None) -> str:
    """Phase 1 of two-phase audit logging: insert an `Attempted` row
    BEFORE the real ERPNext write happens. If the process crashes between
    this call and record_audit_log_finish(), the orphaned `Attempted` row
    is the detectable trace of an unfinished/unknown-outcome write.
    Returns the row's name, or None if the insert itself failed — callers
    must treat None as "logging unavailable, proceed anyway", never as a
    reason to abort the real write.
    """
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return None
    return _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "domain_code": (domain_code or "")[:SESSION_FIELD_MAX_LEN],
        "environment_tag": cfg.get("tag", ""),
        "channel": _safe_channel(channel),
        "channel_metadata": json.dumps(_redact_pii_deep(channel_metadata)) if channel_metadata else None,
        "action": action,
        "reference_doctype": doctype,
        "reference_name": name or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Attempted",
        "payload_before": json.dumps(payload_before) if payload_before else None,
        "user_approved": "Approved" if user_approved else "Not Confirmed",
        "approval_note": redact_pii(approval_note) if approval_note else approval_note,
        "prompt_summary": _truncate_str(redact_pii(prompt_summary), PROMPT_SUMMARY_MAX_LEN) if prompt_summary else None,
        "latest_user_prompt": _truncate_str(redact_pii(latest_prompt), LATEST_PROMPT_MAX_LEN) if latest_prompt else None,
    })


# qkeee-erp:write-path
def record_audit_log_finish(cfg: dict, log_name: str, *, status: str, reference_name: str = None,
                             payload_before: dict = None, payload_after: dict = None,
                             error_detail: str = None, audit_comment_posted: bool = None) -> bool:
    """Phase 2: flip an `Attempted` row to `Success`/`Failure` after the
    real write completes (or fails). Computes field_diff from
    payload_before/payload_after when both are present (Update only).
    Best-effort; failures here are swallowed, same rationale as
    everywhere else in this section. Returns whether the update actually
    landed, so a caller (mutate_resource) can surface degraded audit
    logging instead of it being visible only on stderr."""
    if not log_name:
        return False
    fields = {"status": status, "timestamp": _now_iso()}
    if reference_name:
        fields["reference_name"] = reference_name
    if payload_after is not None:
        fields["payload_after"] = json.dumps(payload_after)
        diff = _diff_fields(payload_before, payload_after)
        if diff:
            fields["field_diff"] = json.dumps(diff)
    if error_detail:
        fields["error_detail"] = error_detail[:1900]  # Small Text-ish headroom
    if audit_comment_posted is not None:
        fields["audit_comment_posted"] = 1 if audit_comment_posted else 0
    ok = _audit_update(cfg, log_name, fields)
    if ok:
        _audit_submit(cfg, log_name)
    return ok


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

# qkeee-erp:write-path
def mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                     name: str = None, mode: str = "read-only", requested_by: str = None,
                     skip_comment: bool = False,
                     *, domain: str = None, skill_label: str = None,
                     session_id: str = None, domain_code: str = None,
                     channel: str = None, channel_metadata: dict = None,
                     user_approved: bool = False, approval_note: str = None,
                     confirmation_token: str = None, issued_at: int = None,
                     advisory_token_verified: bool = False,
                     prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """Generic resource mutate — create/update/submit/cancel/delete a
    DocType record. The one shared write entry point every domain module's
    own `mutate()` wrapper calls into (see scripts/domains/*.py).

    `domain` (see the write-allowlist gate section in this
    module's docstring): when given, this doctype must appear in
    DOMAIN_WRITE_ALLOWLISTS[domain] (registered by that domain module at
    import time via register_domain_allowlist()) or this call is refused
    with DoctypeNotAllowedError — refused too if `domain` itself was never
    registered (an unregistered/unimported domain is treated as unknown,
    not as unrestricted, so a typo'd domain name fails closed rather than
    silently bypassing the gate). `domain=None` (the default) applies NO
    allowlist restriction — reserved for gated_mutate_resource()'s own
    call site and any other core-level caller that intentionally operates
    outside domain scope; a domain module should always pass its own name.

    `mode` must be passed explicitly by the caller (sourced from
    metadata.hermes.config qkeee_erp.mode) — this function refuses to
    guess a safe default and refuses to write unless mode == "read-write".

    `requested_by` (the ERPNext user id/email of the human who asked for
    this change) is required for every write — the connector authenticates
    as a shared bot account, so without this the ERPNext audit trail would
    show only the bot, never who actually asked. On success, a best-effort
    Comment naming the requester is posted to the affected record (see
    record_comment()).

    `skip_comment=True` suppresses that default Comment — for a caller
    that's about to post its own, more specific attribution comment right
    after this call returns. Qkeee Bot Audit Log logging is unaffected
    either way; only the ERPNext-side Comment is skipped.

    `skill_label` overrides the "[...]" prefix on that default Comment
    (defaults to `f"qkeee-erp-associate/{domain}"` when `domain` is given,
    else the module-level SKILL_LABEL) — so a Comment reads e.g.
    "[qkeee-erp-associate/accounts] created ..." rather than a generic
    label, without every domain needing its own copy of _do_mutate().

    `user_approved` should be True only when the caller actually ran this
    write's confirm stage with the user first — it's logged to Qkeee Bot
    Audit Log's `user_approved` field for later scanning, not enforced as
    a gate here. Defaults to False deliberately: a caller that forgets to
    pass it shows up as "Not Confirmed" on scan, which is the intended
    detection behavior, not a silent default.

    `confirmation_token`/`issued_at`: required, verified in code (not just
    prompt discipline), whenever `domain` has registered this `action` via
    register_domain_token_gate() — normally submit/cancel/delete, never
    create/update (the draft steps this gate exists to be reviewed
    *before*). See DOMAIN_TOKEN_GATED_ACTIONS above. Ignored for a domain/
    action combination that hasn't opted in — either because that domain
    carries its own bespoke, stricter token scheme instead (fixed_assets,
    system_admin) or because nothing about it warrants one.

    `advisory_token_verified`: INTERNAL — set True only by
    gated_mutate_resource() calling into this function, after ITS OWN
    unconditional confirmation_token/issued_at verification against a
    rendered advisory draft already passed. Not exposed on any domain
    module's mutate() wrapper or the CLI; a caller asserting this
    directly would be lying about a verification that never happened, so
    nothing outside this file should ever pass it. Feeds
    _validate_prod_requester()'s RBAC-pre-check-unreliable fallback: a
    write on a doctype no domain owns (e.g. Company) still gets credited
    with a reviewed safety net if it went through the mandatory
    draft-then-confirm flow, same as a domain-scoped write's allowlist.
    """
    _VALID_ACTIONS = {"create", "update", "submit", "cancel", "delete"}
    if action not in _VALID_ACTIONS:
        # Common failure mode: a caller swaps the (doctype, action)
        # positional args. Catching it here, before any side effect, gives
        # the actual likely cause instead of a symptom.
        hint = (
            f" This looks like doctype/action were swapped — mutate_resource(tag, doctype, "
            f"action, ...) takes doctype BEFORE action; got doctype='{doctype}', action='{action}'."
            if doctype in _VALID_ACTIONS else ""
        )
        raise ConnectorError(
            f"Invalid action '{action}' for doctype '{doctype}'. Expected one of "
            f"{sorted(_VALID_ACTIONS)}.{hint}"
        )
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing {action} on '{doctype}': qkeee_erp.mode is '{mode}', not 'read-write'. "
            f"Switch modes explicitly if this write is intended."
        )
    if not requested_by:
        raise MissingRequesterError(
            f"Refusing {action} on '{doctype}': requested_by is missing. There is no "
            f"env-var or config default for it — resolve the inbound channel identity "
            f"(the requesting user's own work email/chat identity) as a real ERPNext "
            f"User and pass it explicitly via --requested-by / requested_by= for this "
            f"call."
        )
    if domain is not None:
        allowed = DOMAIN_WRITE_ALLOWLISTS.get(domain)
        if allowed is None:
            raise DoctypeNotAllowedError(
                f"Refusing {action} on '{doctype}': domain '{domain}' has no registered "
                f"ALLOWED_WRITE_DOCTYPES (either an unknown domain name, or its "
                f"scripts/domains/{domain}.py module hasn't been imported yet in this "
                f"process — register_domain_allowlist() runs at import time)."
            )
        if doctype not in allowed:
            raise DoctypeNotAllowedError(
                f"Refusing {action} on '{doctype}': not in domain '{domain}''s "
                f"ALLOWED_WRITE_DOCTYPES {allowed!r}. If '{doctype}' genuinely belongs to "
                f"this domain's remit, add it to that tuple deliberately; don't route "
                f"around this gate."
            )
    if domain is not None and action in DOMAIN_TOKEN_GATED_ACTIONS.get(domain, ()):
        _require_advisory_token(action, doctype, name, payload, requested_by,
                                 confirmation_token, issued_at)
    _validate_prod_requester(tag, requested_by, doctype, _MUTATE_ACTION_TO_PTYPE[action],
                              docname=name, domain=domain,
                              advisory_token_verified=advisory_token_verified,
                              session_id=session_id, domain_code=domain_code,
                              channel=channel, channel_metadata=channel_metadata,
                              prompt_summary=prompt_summary, latest_prompt=latest_prompt)

    cfg = get_env_config(tag)
    effective_skill_label = skill_label or (f"qkeee-erp-associate/{domain}" if domain else SKILL_LABEL)

    # Pre-image for Update's field_diff — an extra GET, only when this
    # doctype is actually audited (skip for any AUDIT_EXEMPT_DOCTYPES
    # entry, and skip when the doctype isn't exempt but the target
    # simply doesn't need diffing, e.g. Create has no "before"). Passes
    # the write's own `requested_by` through (not `internal=True`): the
    # pre-image read is a real, legitimate access made on the requester's
    # behalf, so it is gated by _validate_prod_requester() and logged
    # exactly like any other read, attributed to the same requester and
    # carrying the same context as the write it's supporting. A
    # ConnectorError here (e.g. the requester lacks read permission on
    # this doctype) is swallowed — `payload_before` stays None and the
    # write proceeds without a `field_diff` rather than failing the write
    # over a diagnostic-only diff.
    payload_before = None
    if action == "update" and doctype not in AUDIT_EXEMPT_DOCTYPES and name:
        try:
            payload_before = get_resource(tag, doctype, name, strip_noise=False,
                                           requested_by=requested_by,
                                           session_id=session_id, domain_code=domain_code,
                                           channel=channel, channel_metadata=channel_metadata,
                                           prompt_summary=prompt_summary, latest_prompt=latest_prompt
                                           ).get("data")
        except ConnectorError:
            payload_before = None

    audit_log_name = record_audit_log_start(
        cfg, action=action.capitalize(), doctype=doctype, name=name, requested_by=requested_by,
        session_id=session_id, domain_code=domain_code, channel=channel, channel_metadata=channel_metadata,
        payload_before=payload_before,
        user_approved=user_approved, approval_note=approval_note,
        prompt_summary=prompt_summary, latest_prompt=latest_prompt,
    )

    try:
        result = _do_mutate(cfg, doctype, action, payload, name, requested_by,
                             skip_comment=skip_comment, skill_label=effective_skill_label)
    except ConnectorError as e:
        record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=str(e))
        raise

    # Success path: extract whatever's usable as payload_after / the
    # audit-comment outcome to close out the Attempted row.
    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(result, dict):
        data = result.get("message")  # submit/cancel return {"message": {...}} instead of {"data": {...}}
    reference_name = (data or {}).get("name") if isinstance(data, dict) else name
    if not reference_name:
        print(
            f"WARN: {action} on '{doctype}' returned no usable reference name "
            f"(result keys: {sorted(result.keys()) if isinstance(result, dict) else type(result)}) "
            f"— Audit Log row {audit_log_name!r} will have a blank Reference Name despite status=Success.",
            file=sys.stderr,
        )
    audit_comment_posted = result.pop("_audit_comment_posted", None) if isinstance(result, dict) else None
    finish_ok = record_audit_log_finish(
        cfg, audit_log_name, status="Success", reference_name=reference_name,
        payload_before=payload_before, payload_after=data if isinstance(data, dict) else None,
        audit_comment_posted=audit_comment_posted,
    )
    if isinstance(result, dict):
        # Surfaced so a caller (hermes) can proactively flag degraded audit
        # logging to the user instead of relying on someone reading stderr —
        # a stale/malformed session_id can silently drop Audit Log rows
        # for days before anyone notices. "exempt" (doctype in
        # AUDIT_EXEMPT_DOCTYPES) and "ok" are both healthy; anything else
        # means this write is NOT in the audit trail.
        if doctype in AUDIT_EXEMPT_DOCTYPES:
            result["_audit_log_status"] = "exempt"
        elif audit_log_name is None:
            result["_audit_log_status"] = "insert_failed"
        elif not finish_ok:
            result["_audit_log_status"] = "update_failed"
        else:
            result["_audit_log_status"] = "ok"
    return result


def gated_mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                           name: str = None, mode: str = "read-only", requested_by: str = None,
                           *, confirmation_token: str = None, issued_at: int = None,
                           user_confirmation_text: str = None,
                           session_id: str = None, domain_code: str = None,
                           channel: str = None, channel_metadata: dict = None,
                           approval_note: str = None,
                           prompt_summary: str = None, latest_prompt: str = None) -> dict:
    """The associate's own write entry point for whatever doesn't fit a
    named domain — wraps mutate_resource() with the token-gated advisory-first
    check every such write goes through, unconditionally. Unlike a domain
    module's own `mutate()` wrapper, this is deliberately called WITHOUT
    `domain=` (no ALLOWED_WRITE_DOCTYPES restriction): nothing routed
    through here has had the design-time capability review that lets a
    named domain module declare a fixed allowlist ahead of time — the
    confirmation-token gate is the control instead.

    confirmation_token/issued_at must come from a render_draft.py's output
    for this exact (action, doctype, name, payload, requested_by) — see
    confirm_token.py for the token/freshness mechanics. A caller that
    tries to skip the render step (e.g. passing a token computed ad hoc,
    or an old one) is refused here, in code, not just by prompt
    discipline.

    `user_confirmation_text`: the literal text of the user's own reply
    confirming the rendered draft. Required, and must contain
    `confirm_token.confirmation_code(confirmation_token)` (the short code
    the render step is expected to have shown the user) — see
    UnconfirmedByUserError and confirmation_code()'s own docstring for
    exactly what this does and doesn't prove. This check is additional
    to, not instead of, the confirmation_token match below: the token
    proves the payload matches what was rendered, this proves (within
    this skill's existing trust model — see confirmation_code()'s
    docstring) that a reply from the user actually referenced it.
    """
    if not confirmation_token or issued_at is None:
        raise ConnectorError(
            f"Refusing {action} on '{doctype}': gated_mutate_resource requires "
            f"confirmation_token + issued_at — render the draft first and pass its exact "
            f"token and issued_at here."
        )
    if not is_fresh(int(issued_at)):
        raise StaleConfirmationError(
            "This draft's confirmation has expired or its issued_at is implausible — "
            "re-render the draft against current data and reconfirm before retrying."
        )
    expected = advisory_write_token(action, doctype, name, payload or {}, requested_by, int(issued_at))
    if confirmation_token != expected:
        raise ConnectorError(
            "confirmation_token does not match the (action, doctype, name, payload, "
            "requested_by, issued_at) facts — re-render the draft against the current data "
            "and use that token; don't hand-construct one."
        )
    expected_code = confirmation_code(confirmation_token)
    if not user_confirmation_text:
        raise UnconfirmedByUserError(
            f"Refusing {action} on '{doctype}': gated_mutate_resource requires "
            f"user_confirmation_text — the literal text of the user's own reply. Show them "
            f"confirmation_code {expected_code!r} in the rendered draft, ask them to include "
            f"it in their confirmation, and pass their actual reply text here. Do not "
            f"construct this string yourself — see confirm_token.confirmation_code()'s "
            f"docstring for why that would defeat the point of this check."
        )
    if expected_code not in user_confirmation_text.upper():
        raise UnconfirmedByUserError(
            f"Refusing {action} on '{doctype}': user_confirmation_text does not contain "
            f"confirmation_code {expected_code!r} — either the user replied to a different/"
            f"stale draft, or this code was never actually shown to them. Re-render and "
            f"reconfirm; never fabricate a reply that happens to contain the right code."
        )

    return mutate_resource(
        tag, doctype, action, payload=payload, name=name, mode=mode, requested_by=requested_by,
        session_id=session_id, domain_code=domain_code, channel=channel, channel_metadata=channel_metadata,
        user_approved=True, approval_note=approval_note or "gated_mutate_resource: advisory draft confirmed",
        advisory_token_verified=True,
        prompt_summary=prompt_summary, latest_prompt=latest_prompt,
    )


# qkeee-erp:write-path
def _do_mutate(cfg: dict, doctype: str, action: str, payload: dict, name: str, requested_by: str,
                skip_comment: bool = False, skill_label: str = None) -> dict:
    """The actual per-action HTTP dispatch — factored out so
    mutate_resource() can wrap it uniformly with the two-phase Attempted/
    Success/Failure logging above without duplicating this logic per
    action.

    `skip_comment` suppresses the default `record_comment()` call per
    action below — see mutate_resource()'s docstring. `skill_label`
    (threaded from mutate_resource(), defaulting to SKILL_LABEL there)
    is the "[...]" prefix on that default Comment."""
    label = skill_label or SKILL_LABEL
    if action == "create":
        path = f"/api/resource/{urllib.parse.quote(doctype)}"
        result = _request(cfg, "POST", path, payload=payload)
        created_name = (result.get("data") or {}).get("name")
        comment_posted = None
        if created_name and not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, created_name,
                f"[{label}] created — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "update":
        if not name:
            raise ConnectorError("update requires a record 'name'.")
        path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        result = _request(cfg, "PUT", path, payload=payload)
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{label}] updated — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "submit":
        if not name:
            raise ConnectorError("submit requires a record 'name'.")
        # frappe.client.submit builds its doc via frappe.get_doc(dict) — a
        # sparse {doctype, name} payload has no DB-loaded field values, so
        # validate() fails mandatory-field checks. Fetch the full record
        # first, then submit that.
        get_path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        existing = _request(cfg, "GET", get_path)
        full_doc = existing.get("data")
        if not full_doc:
            raise ConnectorError(f"Could not load '{doctype}' '{name}' before submit — nothing to submit.")
        result = _request(cfg, "POST", "/api/method/frappe.client.submit", payload={"doc": full_doc})
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{label}] submitted — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "cancel":
        if not name:
            raise ConnectorError("cancel requires a record 'name'.")
        body = {"doctype": doctype, "name": name}
        result = _request(cfg, "POST", "/api/method/frappe.client.cancel", payload=body)
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{label}] cancelled — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "delete":
        if not name:
            raise ConnectorError("delete requires a record 'name'.")
        # Post the audit comment before deleting — once the record is gone
        # there's nothing left in ERPNext to attach a Comment to.
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{label}] deleted — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        result = _request(cfg, "DELETE", path)
        if not isinstance(result, dict):
            result = {}
        result["_audit_comment_posted"] = comment_posted
        return result

    raise ConnectorError(f"Unknown action '{action}'. Expected create/update/submit/cancel/delete.")


def list_configured_tags() -> list:
    """List environment tags with a full var set (BASE_URL+API_KEY+API_SECRET)
    already present in qkeee-erp.env or os.environ."""
    tags = {}
    for var_name in _qkeee_env():
        if not var_name.startswith("QKEEE_ERP_"):
            continue
        for suffix in ("_BASE_URL", "_API_KEY", "_API_SECRET"):
            if var_name.endswith(suffix):
                tag = var_name[len("QKEEE_ERP_"):-len(suffix)]
                tags.setdefault(tag, set()).add(suffix)
                break
    return sorted(tag for tag, found in tags.items() if found == {"_BASE_URL", "_API_KEY", "_API_SECRET"})


def discover_harness_http_tool() -> dict:
    """Harness capability discovery stub — persona/host code should check for a
    harness-native HTTP-capable tool before shelling out to this script.
    Returns a map describing what this script assumes (nothing pre-discovered)."""
    return {"harness_http_tool_detected": False, "fallback": "urllib (this script)"}


def _parse_json_arg(flag: str, raw: str, expected_type: type):
    """Parse a CLI flag's JSON value, raising a clean ConnectorError (not a
    raw traceback) on malformed JSON, and a clean error on the right-shaped-
    but-wrong-type JSON. `expected_type` is `list` or `dict`."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        example = '["name","email"]' if expected_type is list else '{"company": "Acme"}'
        raise ConnectorError(
            f"{flag} must be valid JSON, e.g. {flag} '{example}' - got: {raw!r} ({e})"
        )
    if not isinstance(value, expected_type):
        raise ConnectorError(f"{flag} must be a JSON {expected_type.__name__} - got: {raw!r}")
    return value


def _cli():
    """Manual/debug CLI for the core connector. Domain-specific mutate
    calls should go through each scripts/domains/<slug>.py module's own
    `mutate()` wrapper (which always passes `domain=`) rather than this
    generic `mutate` subcommand — this one is deliberately domain-
    agnostic and exists for ad hoc/debug use and for gated-mutate (the
    associate's own advisory-first write path, see gated_mutate_resource())."""
    p = argparse.ArgumentParser(description="qkeee-erp-associate core connector CLI")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/mutate)")
    p.add_argument("--mode", choices=["read-only", "read-write"],
                   help="from qkeee_erp.mode (required for mutate/gated-mutate)")
    p.add_argument("--requested-by",
                   help="ERPNext user id/email of the human requesting the change, for THIS call "
                        "only — resolve it from the live inbound channel identity (chat/email "
                        "sender) before passing it here; there is no env-var or config default "
                        "to fall back on, mandatory on every read/write")
    p.add_argument("--session-id", help="plain string correlator threaded into Qkeee Bot Audit Log rows")
    p.add_argument("--domain-code", help="e.g. qkeee-erp-associate — threaded into audit rows")
    p.add_argument("--channel", help="conversation surface, e.g. Discord/Telegram/WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help='JSON object of channel-specific tracing detail')
    p.add_argument("--approval-note", help="free text of what was confirmed (mutate only)")
    p.add_argument("--prompt-summary", help="one-line summary of the user request that led to this "
                                             "call — threaded into Qkeee Bot Audit Log rows")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt from the driving chat "
                                            "session — threaded into Qkeee Bot Audit Log rows")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("list-envs")

    q = sub.add_parser("query")
    q.add_argument("doctype")
    q.add_argument("--filters", help="JSON list, e.g. '[[\"status\",\"=\",\"Open\"]]'")
    q.add_argument("--fields", help="JSON list, e.g. '[\"name\",\"status\"]'")
    q.add_argument("--limit", type=int, default=20)

    g = sub.add_parser("get", help="Single-resource full-doc GET (includes child tables) — noise-stripped by default")
    g.add_argument("doctype")
    g.add_argument("name")
    g.add_argument("--no-strip", action="store_true", help="skip noise-stripping, return the raw doc verbatim")

    r = sub.add_parser("report", help="Run a built-in ERPNext report (e.g. 'Accounts Receivable')")
    r.add_argument("report_name")
    r.add_argument("--filters", help="JSON object, e.g. '{\"company\":\"Acme\"}'")

    ur = sub.add_parser("roles", help="Fetch a user's assigned roles (authority-check heuristic)")
    ur.add_argument("--user", default="", help="defaults to the authenticated bot account's own user")

    m = sub.add_parser("mutate", help="Domain-agnostic write via plain mutate_resource() — pass "
                                       "--domain to apply that domain's ALLOWED_WRITE_DOCTYPES gate, "
                                       "omit it only for core-level/debug use")
    m.add_argument("doctype")
    m.add_argument("action", choices=["create", "update", "submit", "cancel", "delete"])
    m.add_argument("--payload", help="JSON object for create/update")
    m.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    m.add_argument("--domain", help="registered domain name (see scripts/domains/*.py) to gate this "
                                     "write against — import that domain module first so it's registered")
    m.add_argument("--confirmation-token", help="required for submit/cancel/delete on a domain that has "
                                                  "registered those actions via register_domain_token_gate() "
                                                  "(see scripts/core/confirm_token.py's advisory-token CLI)")
    m.add_argument("--issued-at", type=int, help="epoch seconds the confirmation token was computed at")

    gm = sub.add_parser("gated-mutate", help="Advisory-first gated write — requires a token from a "
                                              "render_*.py draft script (no domain allowlist)")
    gm.add_argument("doctype")
    gm.add_argument("action", choices=["create", "update", "submit", "cancel", "delete"])
    gm.add_argument("--payload", help="JSON object for create/update")
    gm.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    gm.add_argument("--confirmation-token", required=True)
    gm.add_argument("--issued-at", type=int, required=True)
    gm.add_argument("--user-confirmation-text", required=True,
                     help="literal text of the user's own reply confirming the rendered draft "
                          "— must contain confirm_token.confirmation_code(confirmation_token); "
                          "see gated_mutate_resource()'s docstring")

    args = p.parse_args()

    if args.command in ("health", "query", "get", "report", "roles", "mutate",
                         "gated-mutate") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command in ("mutate", "gated-mutate") and not args.mode:
        p.error(f"--mode is required for '{args.command}'")
    if args.command in ("query", "get", "report", "mutate", "gated-mutate", "roles") and not args.session_id:
        args.session_id = _session_or_fallback(None)

    # requested_by is mandatory on every read/write, on every tag — no
    # env-var or config default exists to fall back to, so this is a
    # pure pass-through of --requested-by. See resolve_requested_by().
    effective_requested_by = resolve_requested_by(args.requested_by)

    if args.command in ("query", "get", "report", "mutate", "gated-mutate") and not effective_requested_by:
        p.error(
            f"--requested-by is required for '{args.command}' — there is no env-var or "
            f"config default. Resolve the inbound channel identity (the requesting "
            f"user's own work email/chat identity) as a real ERPNext User and pass it "
            f"explicitly."
        )

    try:
        channel_metadata = _parse_json_arg("--channel-metadata", args.channel_metadata, dict)
        if args.command == "health":
            print(json.dumps(health_check(args.tag), indent=2))
        elif args.command == "list-envs":
            print(json.dumps({"configured_tags": list_configured_tags()}, indent=2))
        elif args.command == "query":
            filters = _parse_json_arg("--filters", args.filters, list)
            fields = _parse_json_arg("--fields", args.fields, list)
            print(json.dumps(query_resource(args.tag, args.doctype, filters, fields, args.limit,
                                             session_id=args.session_id,
                                             domain_code=args.domain_code,
                                             requested_by=effective_requested_by,
                                             channel=args.channel, channel_metadata=channel_metadata,
                                             prompt_summary=args.prompt_summary,
                                             latest_prompt=args.latest_prompt), indent=2))
        elif args.command == "get":
            print(json.dumps(get_resource(args.tag, args.doctype, args.name, not args.no_strip,
                                           session_id=args.session_id,
                                           domain_code=args.domain_code,
                                           requested_by=effective_requested_by,
                                           channel=args.channel, channel_metadata=channel_metadata,
                                           prompt_summary=args.prompt_summary,
                                           latest_prompt=args.latest_prompt), indent=2))
        elif args.command == "report":
            filters = _parse_json_arg("--filters", args.filters, dict)
            print(json.dumps(run_query_report(args.tag, args.report_name, filters,
                                               session_id=args.session_id,
                                               domain_code=args.domain_code,
                                               requested_by=effective_requested_by,
                                               channel=args.channel, channel_metadata=channel_metadata,
                                               prompt_summary=args.prompt_summary,
                                               latest_prompt=args.latest_prompt), indent=2))
        elif args.command == "roles":
            # Not in the --requested-by-mandatory list above (deliberate):
            # this is the RBAC-computing primitive itself, so it can't
            # depend on having already passed the gate it feeds.
            # requested_by here is attribution only (who's asking to see
            # the roles), threaded through so a real business-intent
            # lookup gets a real Audit Log row — not required.
            print(json.dumps(get_user_roles(args.tag, args.user,
                                             requested_by=effective_requested_by,
                                             session_id=args.session_id,
                                             domain_code=args.domain_code,
                                             channel=args.channel, channel_metadata=channel_metadata,
                                             prompt_summary=args.prompt_summary,
                                             latest_prompt=args.latest_prompt), indent=2))
        elif args.command == "mutate":
            payload = _parse_json_arg("--payload", args.payload, dict)
            print(json.dumps(
                mutate_resource(args.tag, args.doctype, args.action, payload, args.name,
                                 args.mode, effective_requested_by, domain=args.domain,
                                 session_id=args.session_id, domain_code=args.domain_code,
                                 channel=args.channel, channel_metadata=channel_metadata,
                                 user_approved=bool(args.confirmation_token), approval_note=args.approval_note,
                                 confirmation_token=args.confirmation_token, issued_at=args.issued_at,
                                 prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt),
                indent=2,
            ))
        elif args.command == "gated-mutate":
            payload = _parse_json_arg("--payload", args.payload, dict)
            print(json.dumps(
                gated_mutate_resource(args.tag, args.doctype, args.action, payload, args.name,
                                       args.mode, effective_requested_by,
                                       confirmation_token=args.confirmation_token,
                                       issued_at=args.issued_at,
                                       user_confirmation_text=args.user_confirmation_text,
                                       session_id=args.session_id, domain_code=args.domain_code,
                                       channel=args.channel, channel_metadata=channel_metadata,
                                       approval_note=args.approval_note,
                                       prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt),
                indent=2,
            ))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
