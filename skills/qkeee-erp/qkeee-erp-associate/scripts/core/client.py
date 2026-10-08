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
enforced by the operation pipeline (core/operations.py), not just in the calling domain's
prompt/reference doc.

Bot account + requester attribution: the API key/secret above must belong
to a dedicated ERPNext integration/bot user, never an individual's personal
login. Every write additionally requires `requested_by` (the ERPNext user
id/email of the human who asked for the change) and, on success, posts a
best-effort audit Comment on the affected record naming that requester — so
ERPNext's own audit trail shows who asked, not just that the bot acted. See
record_comment() below and the operation pipeline.

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
register_domain_allowlist() at import time. The operation pipeline
(core/operations.py) refuses any write whose doctype isn't in its
operation's domain allowlist, raising DoctypeNotAllowedError. `mis.py`
registers an EMPTY allowlist, so every doctype is refused there.

Every write goes through core/operations.py's run_operation() — one
registry of named operations, one pipeline, one token constructor
(write-path hardening, agents/.scratch/qkeee-erp-write-path-hardening).
This module is the LOWER layer the pipeline is built from (requester
gate, audit logging, transport, _do_mutate) and never imports
core/operations.py — the dependency runs one way only. It has no public
write function of its own.
"""

import argparse
import http.client
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
    from confirm_token import compute_token, confirmation_code, is_fresh
except ImportError:
    from core.confirm_token import compute_token, confirmation_code, is_fresh

# Default attribution label for audit Comments when no domain-specific
# label is supplied — see _do_mutate()'s `skill_label` param.
SKILL_LABEL = "qkeee-erp-associate"

# Qkeee Bot audit-trail doctype (see scripts/init_bot.py). A target
# instance may not have it provisioned yet — every call into it below is
# best-effort and never blocks or fails the caller's actual ERPNext
# read/write.
AUDIT_LOG_DOCTYPE = "Qkeee Bot Audit Log"

# Doctypes whose WRITES are never audited: only this connector's own
# bookkeeping. The audit log itself, and Comment, because the attribution
# Comment every write may post (record_comment(), below) is itself a write;
# without this exemption every audited write would double-log itself.
#
# "User"/"Role"/"DocType" used to be listed here too (to keep init_bot.py's
# bootstrap from double-logging). That silently removed every system-admin
# write — user creation, role changes, disable/delete, permission changes
# — from the audit trail (write-path hardening W29). init_bot's two
# provisioning operations now opt out of pipeline auditing explicitly
# (Operation(audit=False), core/operations.py) and keep logging themselves
# via log_role_provisioning(); everything else is audited.
#
# The READ path (_log_read(), consulted by query_resource()/get_resource()/
# run_query_report()/get_user_roles()) does NOT use this set — see
# _LOG_READ_RECURSION_EXEMPT_DOCTYPES below. Its plumbing calls
# (resource_exists(), _fetch_doctype_role_permissions()) are exempted via
# an `internal=True` kwarg instead.
AUDIT_EXEMPT_DOCTYPES = {
    AUDIT_LOG_DOCTYPE,
    "Comment",
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
#
# User/Role moved out 2026-10-07: exempting them on EVERY read meant a
# business read of the User list ("fetch all active users") skipped the
# gate entirely — no requester check, no permission check — and leaked the
# user directory on dev-erp. The recursion reason above only ever applies
# to the gate's own plumbing, which always passes internal=True
# (resource_exists(), _fetch_doctype_role_permissions()), so User/Role are
# now exempt only on those internal calls — see
# INTERNAL_READ_GATE_EXEMPT_DOCTYPES below.
#
# Environment Metadata (agents/docs/adr/0001-environment-metadata-exempt-
# from-requester-gate.md) is exempt from the requester's per-doctype
# permission check: a requester's Module Def or DocType permission says
# nothing about whether they may create an Employee, and both are
# System-Manager-only in stock ERPNext. A business read of it still needs
# a present requester bound to the session sender, and still writes its
# audit row under that requester. CLOSED LIST: DocType, Module Def, and the
# get_versions RPC (ENVIRONMENT_METADATA_RPCS). Adding to it needs a new ADR.
ENVIRONMENT_METADATA_DOCTYPES = frozenset({"DocType", "Module Def"})
ENVIRONMENT_METADATA_RPCS = frozenset({"frappe.utils.change_log.get_versions"})
PROD_GATE_EXEMPT_DOCTYPES = set(ENVIRONMENT_METADATA_DOCTYPES) | {AUDIT_LOG_DOCTYPE, "Comment"}

# Exempt from the READ gate only when the call is the connector's own
# internal plumbing (internal=True) — never on a business-intent read.
INTERNAL_READ_GATE_EXEMPT_DOCTYPES = {"User", "Role"}

# WRITES get a much narrower exemption (write-path hardening, W02).
# PROD_GATE_EXEMPT_DOCTYPES above exists for READ recursion (validating a
# requester reads User) - that reason never applies to a write. Before
# this split, a write to User/Role/DocType skipped both the permission
# check AND the "requested_by is a real ERPNext User" check, so any
# non-empty string could e.g. create a System Manager user. Now only the
# connector's own bookkeeping doctypes are exempt for writes; a User/Role/
# DocType write needs a real requester holding that permission (in
# practice System Manager - that includes init_bot.py's provisioning
# requester).
WRITE_GATE_EXEMPT_DOCTYPES = {AUDIT_LOG_DOCTYPE, "Comment"}

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

# A resource write's action -> frappe.client.has_permission's perm_type.
_MUTATE_ACTION_TO_PTYPE = {
    "create": "create", "update": "write", "submit": "submit",
    "cancel": "cancel", "delete": "delete",
}

# --------------------------------------------------------------------------
# Write-allowlist gate
#
# Domain modules register their ALLOWED_WRITE_DOCTYPES here at import time
# via register_domain_allowlist(). The operation pipeline then checks
# against this registry before any create/update/submit/cancel/delete. A
# domain that hasn't been imported yet (so hasn't registered) is treated as
# unknown, not as unrestricted — see core/operations.py _check_allowlist().
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


# Confirmation-token policy lives on each operation in core/operations.py
# (the former DOMAIN_TOKEN_GATED_ACTIONS / register_domain_token_gate() /
# _require_advisory_token() registry was folded into it — write-path
# hardening ticket 10).


class ConnectorError(Exception):
    """Raised for missing config / auth / HTTP failures with a specific, actionable message."""


class GateRefusal(ConnectorError):
    """Base for every refusal by a write/read GATE (mode, requester,
    allowlist, ownership, precondition, confirmation token) — as opposed to
    a transport failure or an error ERPNext itself returned. A refusal
    always happens before the write is sent. execute_write.py maps this to
    its own exit code so an agent can tell "refused, nothing happened"
    apart from "ERPNext rejected it" or "outcome unknown"."""


class ReadOnlyModeError(GateRefusal):
    """Raised when a write call is attempted while qkeee_erp.mode == read-only."""


class MissingRequesterError(GateRefusal):
    """Raised when a write call is attempted without a requested_by identity."""


class UnvalidatedProdRequesterError(GateRefusal):
    """Raised, on every tag (name reflects a narrower PROD-only origin —
    the gate is universal, see _validate_prod_requester()), when
    requested_by is missing, isn't a real ERPNext User, or lacks the
    permission this call needs per ERPNext's own
    frappe.client.has_permission check."""


class SandboxedCallerError(GateRefusal):
    """Raised when the connector runs inside Hermes' execute_code sandbox.
    That sandbox strips every HERMES_SESSION_* var, so the requester cannot
    be bound to the gateway sender there; ERPNext calls go through the
    terminal scripts instead."""


class InvalidArgumentsError(ConnectorError):
    """An operation's arguments are malformed (missing, unknown, wrong
    type). Raised by the pipeline when prepare() — which does no I/O —
    rejects them, so nothing was sent. The CLIs map it to exit 2 (usage)."""


class DoctypeNotFoundError(GateRefusal):
    """The target DocType does not exist on this instance (its app isn't
    installed) — refused before anything is sent, and reported as a
    missing capability rather than as a requester permission gap."""


class ERPNextAPIError(ConnectorError):
    """ERPNext answered with an HTTP error status. The message keeps the
    long-standing "ERPNext API error (<status>) on ..." shape (callers match
    on "(404)"); `.status` and the full `.body` let a caller parse Frappe's
    exc_type and messages (operations.parse_write_failure())."""

    def __init__(self, status: int, method: str, path: str, cfg: dict, body: str):
        self.status, self.body = status, body or ""
        super().__init__(f"ERPNext API error ({status}) on {method} {path} against "
                         f"'{cfg.get('tag')}' ({cfg.get('base_url')}): {self.body[:500]}")


class WriteRejectedError(ConnectorError):
    """ERPNext rejected a create/update for a reason it named (a missing
    mandatory field, a Link that doesn't resolve, a validation rule, a
    duplicate). `.failure` is {error_class, missing_fields[],
    invalid_links[{field, value}], message}. Stop, show it to the user, and
    ask — never fill a value the user did not give."""

    def __init__(self, message: str, failure: dict):
        super().__init__(message)
        self.failure = failure


class TransportTimeoutError(ConnectorError):
    """Raised when ERPNext did not answer in time (connect or read
    timeout). For a write the outcome is UNKNOWN: the request may have
    landed. Re-read the target record before any retry - never blindly
    resend a create."""


class MalformedResponseError(ConnectorError):
    """Raised when ERPNext (or something in front of it, e.g. a WAF
    challenge page) answered with a body that is not valid JSON."""


class InvalidIssuedAtError(GateRefusal):
    """Raised when a confirmation token's issued_at is not an integer
    epoch-seconds value."""


def coerce_issued_at(issued_at) -> int:
    """The one place a confirmation token's `issued_at` is turned into an
    int. A bad value raises InvalidIssuedAtError (a ConnectorError, so
    every caller's existing handler catches it) instead of a bare
    ValueError/TypeError traceback. Callers check for None themselves
    first, since "missing" gets its own clearer message."""
    if isinstance(issued_at, bool):
        raise InvalidIssuedAtError(f"issued_at must be integer epoch seconds, got {issued_at!r}.")
    try:
        return int(issued_at)
    except (TypeError, ValueError):
        raise InvalidIssuedAtError(
            f"issued_at must be integer epoch seconds (as printed by the render/token "
            f"step), got {issued_at!r}."
        ) from None


def finish_audit_failure_and_reraise(cfg: dict, audit_log_name, exc: BaseException):
    """Shared failure path for every audited write: close the audit row as
    Failure for ANY exception (not just ConnectorError - an uncaught
    exception would otherwise leave the row stuck at 'Attempted'), then
    re-raise as a ConnectorError so callers' handlers and the CLI's
    `ERROR:` path still apply. A TransportTimeoutError is flagged
    outcome_unknown in the audit row."""
    detail = str(exc) or type(exc).__name__
    if isinstance(exc, TransportTimeoutError):
        detail = f"outcome_unknown=True; {detail}"
    record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=detail)
    if isinstance(exc, ConnectorError):
        raise exc
    raise ConnectorError(f"Unexpected {type(exc).__name__} during write: {exc}") from exc


class StaleConfirmationError(GateRefusal):
    """Raised when a confirmation_token's issued_at is too old (or implausibly
    future) — re-render the draft against current data and reconfirm."""


class UnconfirmedByUserError(GateRefusal):
    """Raised by the operation pipeline when user_confirmation_text is
    missing, or doesn't contain the confirmation_token's derived
    confirmation_code — see confirm_token.confirmation_code()'s own
    docstring for what this does and doesn't prove. A matching
    confirmation_token alone proves the payload wasn't tampered with
    since render; it does NOT prove the render was ever shown to the
    actual requester — the same process can compute and verify that
    token in one turn. Required by every operation whose token policy is
    "token+user_code" (core/operations.py)."""


class ConfirmationRequiredError(GateRefusal):
    """Raised when an operation needs a confirmation_token + issued_at and
    none was given — render it first (confirm_token.py render)."""


class TokenMismatchError(GateRefusal):
    """Raised when confirmation_token doesn't match the operation token
    recomputed over the exact request about to be sent."""


class PreconditionFailedError(GateRefusal):
    """Raised by an operation precondition (concurrency check, KYC
    completeness, kind/doctype binding, ...) before anything is sent."""


class PartialOutcomeError(ConnectorError):
    """Raised when a write failed part-way and some of its effects DID
    land (e.g. a depreciation run that posted some Journal Entries before
    failing). The message says what is known to have happened."""


class SelfEscalationError(GateRefusal):
    """A write would change a Service Account's own rights: its User record,
    a Role it holds, or a permission row on such a Role (agents ADR 0003).
    Always refused, with no confirm-to-proceed path; a human admin makes the
    change in the ERPNext UI. Also raised, failing closed, when the Service
    Accounts can't be resolved."""


class ServiceAccountRoleError(GateRefusal):
    """The Bot Account holds a stock role (agents ADR 0003): Service Accounts
    hold only dedicated `Qkeee ` roles. Every gated call is refused until an
    admin moves the bot's rights onto `Qkeee Bot`."""


class DoctypeNotAllowedError(GateRefusal):
    """Raised when a write operation targets a doctype outside
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


# The Hermes gateway binds the inbound sender per session and bridges it
# into every terminal child's env (tools/environments/local.py,
# _inject_session_context_env — per-session ContextVar, cross-session leak
# guarded). On Google Chat it is the sender's email; on platforms whose id
# is not an email (Discord/Telegram numeric ids) it is ignored here.
_SESSION_SENDER_ENV = "HERMES_SESSION_USER_ID"


def _session_sender_email() -> str:
    """The gateway-authenticated sender email for this session, or "" when
    there is none (CLI, cron, a non-email platform id)."""
    value = (os.environ.get(_SESSION_SENDER_ENV) or "").strip()
    return value if "@" in value else ""


# Platforms whose sender id is always an email. A session on one of these
# with no sender email means identity plumbing broke; the gate refuses
# rather than accept a requester typed by the agent or the user.
_EMAIL_SENDER_PLATFORMS = frozenset({"google_chat", "email"})
_SESSION_PLATFORM_ENV = "HERMES_SESSION_PLATFORM"
# Set by Hermes only inside the execute_code sandbox child
# (tools/code_execution_env.py), which also strips HERMES_SESSION_*.
_EXECUTE_CODE_SANDBOX_ENV = "HERMES_RPC_SOCKET"


def _refuse_if_execute_code_sandbox() -> None:
    """Fail closed before any ERPNext request from the execute_code sandbox:
    there the session sender is invisible, so resolve_requested_by() and the
    gate's session binding would silently pass any requested_by through."""
    if os.environ.get(_EXECUTE_CODE_SANDBOX_ENV):
        raise SandboxedCallerError(
            "Refusing: the ERPNext connector cannot run inside execute_code. That "
            "sandbox hides the gateway session identity, so the requester cannot be "
            "bound. Nothing was sent. Run the skill scripts from the terminal instead "
            "(python ${HERMES_SKILL_DIR}/scripts/core/client.py ... / execute_write.py ...)."
        )


_SESSION_IDENTITY_ENVS = (
    "HERMES_SESSION_PLATFORM", "HERMES_SESSION_USER_ID",
    "HERMES_SESSION_USER_ID_ALT", "HERMES_SESSION_USER_NAME",
)


def session_identity() -> dict:
    """Diagnostic snapshot of the gateway identity env vars this process
    sees, plus the sender email resolve_requested_by() would bind to.
    No network call. On Google Chat, USER_ID is the sender email when the
    event carried one, else the stable `users/{id}`; USER_ID_ALT is
    `users/{id}`."""
    snapshot = {name: os.environ.get(name) for name in _SESSION_IDENTITY_ENVS}
    snapshot["resolved_sender_email"] = _session_sender_email() or None
    return snapshot


def resolve_requested_by(cli_value: str) -> str:
    """CLI-level requested_by resolution, called from `_cli()`,
    execute_write.py and confirm_token.py.

    There is no tag-level or config default to fall back to — every prior
    fallback (QKEEE_ERP_<TAG>_REQUESTED_BY, any metadata.hermes.config key)
    has been removed. The two sources are the channel's own authenticated
    sender and --requested-by on THIS call:

    - Gateway session with a sender email (`HERMES_SESSION_USER_ID`): that
      email IS the requester. An absent --requested-by resolves to it; a
      --requested-by that names anyone else is refused
      (UnvalidatedProdRequesterError). The agent never picks the
      requester itself — on dev-erp it once passed the bot's own account,
      and the gate then validated the bot against itself.
    - No sender email (CLI, cron, non-email platform): `cli_value` passes
      through unchanged. An absent value is returned as "" and caught
      downstream by `_validate_prod_requester()` / the operation pipeline,
      which fail closed rather than silently proceeding unattributed.

    Limitation: the env var is set by the gateway, but the agent writes
    the shell command, so an inline `HERMES_SESSION_USER_ID=… python …`
    override is possible. This removes the accidental wrong-identity case
    and makes a deliberate one visible in the command; it is not a
    cryptographic binding. The bot-identity guard in
    `_validate_prod_requester()` holds either way."""
    value = (cli_value or "").strip()
    sender = _session_sender_email()
    if not sender:
        return value
    if not value:
        return sender
    if value.lower() != sender.lower():
        raise UnvalidatedProdRequesterError(
            f"Refusing requester '{value}': this session's authenticated sender is "
            f"'{sender}' (gateway {_SESSION_SENDER_ENV}). requested_by must be the "
            f"channel's own sender — omit --requested-by to use it. "
            + _NEVER_SUBSTITUTE_REQUESTER
        )
    return value


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


# Service Accounts hold only dedicated roles (agents ADR 0003): a role
# named with this prefix, plus the roles Frappe gives every user itself.
DEDICATED_ROLE_PREFIX = "Qkeee "
AUTOMATIC_ROLES = frozenset({"All", "Guest", "Desk User"})


def stock_roles(roles) -> list:
    """The roles in `roles` that are neither dedicated nor automatic."""
    return sorted(r for r in roles or () if r not in AUTOMATIC_ROLES
                  and not r.startswith(DEDICATED_ROLE_PREFIX))


_ADMIN_IDENTITY_CACHE: dict = {}


def _admin_identity(tag: str):
    """The Admin Account's identity (user + roles) for this tag, read with
    the admin key pair; None when no admin key pair is configured. Cached
    like _bot_identity(); a lookup failure is cached as unknown."""
    if tag not in _ADMIN_IDENTITY_CACHE:
        try:
            cfg = get_env_config(tag, credential="admin")
        except ConnectorError:
            _ADMIN_IDENTITY_CACHE[tag] = None
            return None
        try:
            user = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user").get("message") or ""
            doc = _request(cfg, "GET", f"/api/resource/User/{urllib.parse.quote(user)}").get("data") or {}
            _ADMIN_IDENTITY_CACHE[tag] = {"user": user,
                                          "roles": [r.get("role") for r in doc.get("roles", [])
                                                    if r.get("role")]}
        except ConnectorError:
            _ADMIN_IDENTITY_CACHE[tag] = {"user": "", "roles": []}
    return _ADMIN_IDENTITY_CACHE[tag]


def service_account_identities(tag: str) -> list:
    """[{user, roles:set}] for every Service Account the agent holds keys
    for: the Bot Account, and the Admin Account when configured. Raises
    SelfEscalationError (fail closed) when one can't be resolved — the
    self-escalation rule can't be checked without knowing who they are."""
    accounts = []
    for label, identity in (("Bot", _bot_identity(tag)), ("Admin", _admin_identity(tag))):
        if identity is None:
            continue
        user = (identity.get("user") or "").strip()
        if not user:
            raise SelfEscalationError(
                f"Refusing: the {label} Account's identity on tag '{tag}' could not be resolved, so "
                f"this write can't be checked for self-escalation (agents ADR 0003). Nothing was "
                f"sent. Check `client.py --tag {tag} health`, or make the change in the ERPNext UI.")
        accounts.append({"user": user, "roles": set(identity.get("roles") or [])})
    return accounts


def fetch_permission_rows(tag: str, doctype: str) -> list:
    """Every permission row on `doctype`, as the Role Permission Manager
    shows them (admin key: the RPC is System-Manager-only). Plumbing for
    the self-escalation rule — not gated, not logged."""
    cfg = get_env_config(tag, credential="admin")
    rows = _request(cfg, "GET", "/api/method/frappe.core.page.permission_manager."
                    "permission_manager.get_permissions", params={"doctype": doctype}).get("message")
    return rows if isinstance(rows, list) else []


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
        "bot_stock_roles": stock_roles(bot_roles),
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


_MERGED_META_PATH = "/api/method/frappe.desk.form.load.getdoctype"


def _fetch_merged_meta(tag: str, doctype: str) -> dict:
    """Frappe's merged meta for `doctype` (base + Custom Fields + Property
    Setters, and the EFFECTIVE permission rows: Custom DocPerm replaces
    DocPerm once a doctype has any). Internal: no gate, no read log — used
    from inside the requester gate itself (W39)."""
    result = _request(get_env_config(tag), "GET", _MERGED_META_PATH, params={"doctype": doctype})
    doc = next((d for d in result.get("docs") or [] if d.get("name") == doctype), None)
    if doc is None:
        raise ConnectorError(f"getdoctype returned no meta for {doctype!r}")
    return doc


def _fetch_doctype_role_permissions(tag: str, doctype: str):
    """Effective permission rows (permlevel 0 only — see _requester_has_role_
    permission()'s docstring for why) for `doctype`, from merged meta
    (_fetch_merged_meta(): Custom DocPerm when present, else DocPerm) — a
    failure is inconclusive, never a fallback to possibly-stale standard
    rows. Fetched here rather than through discover.py: discover.py already
    imports FROM this module, so this module importing discover.py back
    would be circular. Returns `(rows_or_None, error_message_or_None)`,
    cached per (tag, doctype) including a cached failure — same shape and
    reasoning as schema_mapping.py's get_doctype_schema()."""
    cache_key = (tag, doctype)
    if cache_key not in _DOCTYPE_PERMISSIONS_CACHE:
        try:
            doc = _fetch_merged_meta(tag, doctype)
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


def _require_bound_requester(tag: str, requested_by: str, subject: str, log) -> None:
    """The requester-presence and session-binding half of the gate. Runs
    for every gated call AND for Environment Metadata reads, which skip
    only the permission half. `log(allowed, detail)` records a refusal."""
    if not requested_by:
        log(False, {"reason": "no_requester_given"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{subject}' on tag '{tag}': no requester was "
            f"given. A validated, explicit requester is mandatory on every call, on "
            f"every environment — there is no env-var or config default to fall back "
            f"to. Resolve the inbound channel identity (the Google Chat/Teams/Slack "
            f"sender's own work email, the email channel's From address, etc.) as a "
            f"real ERPNext User first, then pass it explicitly via --requested-by / "
            f"requested_by= on this call."
        )
    # Session binding at the gate itself, not only in the CLI wrappers:
    # a direct `requested_by=` caller (code_execution, domain modules)
    # cannot carry a stale or substituted email past the live sender.
    sender = _session_sender_email()
    platform = (os.environ.get(_SESSION_PLATFORM_ENV) or "").strip().lower()
    if not sender and platform in _EMAIL_SENDER_PLATFORMS:
        log(False, {"reason": "email_platform_without_session_sender", "platform": platform})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{subject}' on tag '{tag}': this is a "
            f"'{platform}' session, but the gateway gave no sender email "
            f"({_SESSION_SENDER_ENV}). On this platform the requester comes only "
            f"from the gateway, never from the conversation. Nothing was sent. "
            f"Report it to an admin: the gateway's identity plumbing is broken."
        )
    if sender and requested_by.strip().lower() != sender.lower():
        log(False, {"reason": "requester_not_session_sender"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{subject}' on tag '{tag}': requester "
            f"'{requested_by}' is not this session's authenticated sender "
            f"'{sender}' (gateway {_SESSION_SENDER_ENV}). " + _NEVER_SUBSTITUTE_REQUESTER
        )


def validate_environment_metadata_requester(tag: str, requested_by: str, rpc: str, *,
                                            session_id: str = None, domain_code: str = None,
                                            channel: str = None, channel_metadata: dict = None,
                                            prompt_summary: str = None,
                                            latest_prompt: str = None) -> None:
    """Gate for an Environment Metadata RPC (ENVIRONMENT_METADATA_RPCS,
    ADR 0001): requester present and session-bound, no permission check.
    The caller still writes the read's audit row via _log_read(). Refusals
    are logged against Module Def, the doctype the read is audited under."""
    if rpc not in ENVIRONMENT_METADATA_RPCS:
        raise ValueError(f"{rpc!r} is not Environment Metadata (ADR 0001 closed list: "
                         f"{sorted(ENVIRONMENT_METADATA_RPCS)}).")

    def _log(allowed: bool, detail: dict) -> None:
        _log_gate_decision(
            tag, perm_type="read", doctype="Module Def", docname=None, requested_by=requested_by,
            allowed=allowed, detail=dict(detail, rpc=rpc), session_id=session_id,
            domain_code=domain_code, channel=channel, channel_metadata=channel_metadata,
            prompt_summary=prompt_summary, latest_prompt=latest_prompt,
        )

    _require_bound_requester(tag, requested_by, rpc, _log)


def _validate_prod_requester(tag: str, requested_by: str, doctype: str, perm_type: str,
                              docname: str = None, *, for_write: bool = False, internal: bool = False,
                              domain: str = None,
                              advisory_token_verified: bool = False,
                              session_id: str = None, domain_code: str = None,
                              channel: str = None, channel_metadata: dict = None,
                              prompt_summary: str = None, latest_prompt: str = None) -> None:
    """The requester-validation gate — RBAC pre-check, every environment.
    (Name reflects a narrower PROD-only origin; the check itself is
    universal.)

    `domain`/`advisory_token_verified` are still accepted
    (the operation pipeline threads them through — the
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

    No-op for any doctype in PROD_GATE_EXEMPT_DOCTYPES on a read, or in the
    much narrower WRITE_GATE_EXEMPT_DOCTYPES when `for_write=True` (always
    passed by the operation pipeline) - see that set's comment for why User/
    Role/DocType writes are gated even though their reads are not. Exception:
    a non-internal read of Environment Metadata (ENVIRONMENT_METADATA_DOCTYPES,
    ADR 0001) still runs the presence and session-binding checks below and
    skips only the permission check. User/Role
    reads are exempt only when `internal=True` (the gate's own plumbing —
    see INTERNAL_READ_GATE_EXEMPT_DOCTYPES); a business read of either is
    gated like any other doctype.
    Otherwise:

    - Presence of `requested_by` is mandatory on EVERY tag, no exceptions.
      There is no env-var or config default to fall back to (see the
      module docstring); a caller must resolve the live inbound channel
      identity and pass it explicitly on every call.
    - `requested_by` must never be this connector's OWN bot account
      (`_bot_identity()`). Validating the bot against itself always passes
      — it holds every permission the connector does — so a call
      attributed to the bot is refused as "no real requester", same as an
      absent one.
    - In a gateway session with a sender email (`HERMES_SESSION_USER_ID`),
      `requested_by` must equal that email (case-insensitive) — the same
      binding resolve_requested_by() applies on the CLI path, enforced here
      too so library callers get it. On a platform whose sender id is
      always an email (`_EMAIL_SENDER_PLATFORMS`), a session WITHOUT a
      sender email is refused outright.
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
    run_query_report()/the operation pipeline — every read and write. Every
    raise, and the final allow, is also logged to Qkeee Bot Audit Log as
    one gate-decision row — a denial otherwise leaves zero trace."""
    if for_write:
        exempt = WRITE_GATE_EXEMPT_DOCTYPES
    elif internal:
        exempt = PROD_GATE_EXEMPT_DOCTYPES | INTERNAL_READ_GATE_EXEMPT_DOCTYPES
    else:
        exempt = PROD_GATE_EXEMPT_DOCTYPES

    def _log(allowed: bool, detail: dict) -> None:
        _log_gate_decision(
            tag, perm_type=perm_type, doctype=doctype, docname=docname, requested_by=requested_by,
            allowed=allowed, detail=detail, session_id=session_id, domain_code=domain_code,
            channel=channel, channel_metadata=channel_metadata,
            prompt_summary=prompt_summary, latest_prompt=latest_prompt,
        )

    if doctype in exempt:
        # Environment Metadata (ADR 0001): skip only the permission check.
        # A business read still needs a present, session-bound requester.
        if doctype in ENVIRONMENT_METADATA_DOCTYPES and not internal:
            _require_bound_requester(tag, requested_by, doctype, _log)
        return

    _require_bound_requester(tag, requested_by, doctype, _log)
    if not resource_exists(tag, "User", requested_by):
        _log(False, {"reason": "requester_not_a_known_user"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{doctype}' on tag '{tag}': requester "
            f"'{requested_by}' is not a known ERPNext User. Never proceed with an "
            f"unvalidated channel identity — confirm the real ERPNext user id/email "
            f"before retrying."
        )
    trust = verify_rbac_precheck_reliable(tag)
    # Service Accounts hold only dedicated roles (agents ADR 0003). A bot
    # holding a stock role is refused outright, failing closed: otherwise
    # the self-escalation rule either blocks normal admin edits to that
    # stock role, or those edits silently widen the bot.
    bot_stock = trust.get("bot_stock_roles") or []
    if bot_stock:
        _log(False, {"reason": "bot_holds_stock_roles", "roles": bot_stock})
        raise ServiceAccountRoleError(
            f"Refusing this call on tag '{tag}': the Bot Account "
            f"{trust.get('bot_user')!r} holds stock role(s) {bot_stock}. Service Accounts hold "
            f"only dedicated roles (agents ADR 0003). Nothing was sent. Tell an admin: move the "
            f"bot's rights to DocPerms on '{BOT_ROLE_NAME}' and remove {bot_stock} from the bot "
            f"user, in the ERPNext UI. The agent never makes this change itself.")
    # Self-attribution guard, using the bot identity the trust check above
    # already resolved (cached, no extra round trip). A lookup failure
    # leaves bot_user empty and skips this — that case is already treated
    # as privileged/untrusted, so the requester still has to pass the local
    # role/DocPerm check below on its own.
    bot_user = (trust.get("bot_user") or "").strip().lower()
    if bot_user and requested_by.strip().lower() == bot_user:
        _log(False, {"reason": "requester_is_bot_identity"})
        raise UnvalidatedProdRequesterError(
            f"Refusing this call against '{doctype}' on tag '{tag}': requester "
            f"'{requested_by}' is this connector's own bot account. The bot is never a "
            f"requester — checking its permissions against itself proves nothing. Pass "
            f"the human sender's own ERPNext user (the chat/email channel's authenticated "
            f"sender). " + _NEVER_SUBSTITUTE_REQUESTER
        )
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
        # Peek at the local check's cached DocPerm fetch (no new I/O): a 404
        # there means the doctype itself is missing, not a permission gap.
        cached = _DOCTYPE_PERMISSIONS_CACHE.get((tag, doctype))
        fetch_error = cached[1] if isinstance(cached, tuple) and cached[0] == "__error__" else ""
        if "(404)" in fetch_error or "DoesNotExistError" in fetch_error:
            _log(False, {"path": "local_role_docperm_fallback", "reason": "doctype_not_found"})
            raise DoctypeNotFoundError(
                f"Refusing this call on tag '{tag}': DocType '{doctype}' does not exist on this "
                f"instance — the app that provides it is probably not installed (check "
                f"`discover.py modules`). Nothing was sent. This is not a permission problem; "
                f"tell the user this capability isn't available on this ERPNext.")
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


CREDENTIALS = ("bot", "admin")


def get_env_config(tag: str = "default", credential: str = "bot") -> dict:
    """Resolve base_url/api_key/api_secret for a given environment tag.

    `credential="admin"` (decision D1, write-path hardening) selects the
    separate elevated key pair QKEEE_ERP_<TAG>_ADMIN_API_KEY/_SECRET —
    held by a System Manager account, used ONLY by operations declared
    with credential="admin" (system_admin writes, provisioning). The base
    URL is shared with the tag. The everyday bot credential stays narrow.

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
    if credential not in CREDENTIALS:
        raise ConnectorError(f"Unknown credential {credential!r}; expected one of {CREDENTIALS}.")
    prefix = "ADMIN_" if credential == "admin" else ""
    env = _qkeee_env()
    base_url = env.get(_tag_env_var(tag, "BASE_URL"))
    api_key = env.get(_tag_env_var(tag, f"{prefix}API_KEY"))
    api_secret = env.get(_tag_env_var(tag, f"{prefix}API_SECRET"))

    missing = [
        name
        for name, val in (
            (_tag_env_var(tag, "BASE_URL"), base_url),
            (_tag_env_var(tag, f"{prefix}API_KEY"), api_key),
            (_tag_env_var(tag, f"{prefix}API_SECRET"), api_secret),
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
        "credential": credential,
    }


def _request(cfg: dict, method: str, path: str, params: dict = None, payload: dict = None) -> dict:
    _refuse_if_execute_code_sandbox()
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

    is_write = method.upper() != "GET"
    unknown_outcome = (" The write's outcome is UNKNOWN - it may have landed. Re-read the "
                       "target record before any retry; never blindly resend." if is_write else "")
    timeout_msg = (f"Timed out waiting for '{cfg['tag']}' ({cfg['base_url']}) on "
                   f"{method} {path}.{unknown_outcome}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ERPNextAPIError(e.code, method, path, cfg, body) from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TransportTimeoutError(timeout_msg) from e
        raise ConnectorError(
            f"Could not reach '{cfg['tag']}' ({cfg['base_url']}): {e.reason}. "
            f"Check the base URL and network connectivity."
        ) from e
    except TimeoutError as e:  # socket.timeout is an alias of TimeoutError since 3.10
        raise TransportTimeoutError(timeout_msg) from e
    except (ConnectionError, http.client.HTTPException) as e:
        raise ConnectorError(
            f"Connection to '{cfg['tag']}' ({cfg['base_url']}) failed during {method} {path}: "
            f"{type(e).__name__}: {e}.{unknown_outcome}"
        ) from e
    try:
        body = raw.decode("utf-8")
        return json.loads(body) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        snippet = raw[:200].decode("utf-8", errors="replace")
        raise MalformedResponseError(
            f"'{cfg['tag']}' ({cfg['base_url']}) returned a non-JSON body on {method} {path} "
            f"(often a WAF/proxy page, not ERPNext itself): {snippet!r}"
        ) from e


# The bot account's dedicated role (agents/docs/adr/0003) — the role a
# bot-side gap is granted on. Same value as doctype_defs.ROLE_NAME.
BOT_ROLE_NAME = "Qkeee Bot"

# What `health` probes, as the bot, before any task: (capability, path,
# params, doctype, perm, effect when missing). Each failure becomes one gap.
_CAPABILITY_PROBES = (
    ("module_def_read", "/api/resource/Module Def",
     {"fields": '["name"]', "limit_page_length": 1}, "Module Def", "read",
     "discover.py modules/resolve cannot map a DocType to its app; the environment "
     "catalog stays partial"),
    ("installed_apps", "/api/method/frappe.utils.change_log.get_versions", None,
     "frappe.utils.change_log.get_versions", "call",
     "discover.py apps cannot list installed apps and versions; use discover.py modules"),
    ("merged_meta", "/api/method/frappe.desk.form.load.getdoctype", {"doctype": "User"},
     "frappe.desk.form.load.getdoctype", "call",
     "discover.py meta cannot read merged meta (custom fields, property setters); "
     "preflight cannot confirm mandatory fields"),
    ("workflow_read", "/api/resource/Workflow",
     {"fields": '["name"]', "limit_page_length": 1}, "Workflow", "read",
     "preflight cannot check for an active Workflow on the target doctype"),
)

_ROLE_GAP_PROMPT = (
    "⚠ Environment catalog incomplete on {tag} ({base_url}).\n"
    "Missing: {perm} on \"{doctype}\" for {who} {user} (role \"{role}\").\n"
    "Effect: {effect}.\n"
    "Fix (System Manager): {grant_steps}\n"
    "Then reply RECHECK ENV."
)


def make_gap(*, tag: str, base_url: str, capability: str, who: str, user: str, role: str,
             doctype: str, perm: str, effect: str, grant_steps: str = None,
             error: str = None) -> dict:
    """One permission gap, in the shape `health` and `discover.py preflight`
    both return. `prompt` is the role-gap prompt (00-conventions.md),
    already filled in: the agent shows it as is and never paraphrases."""
    if grant_steps is None:
        grant_steps = (f"Role Permission Manager → Document Type \"{doctype}\" → Add rule → "
                       f"Role \"{role}\", Level 0 → tick {perm} → Save.")
    gap = {"capability": capability, "who": who, "user": user, "role": role, "doctype": doctype,
           "perm": perm, "effect": effect, "grant_steps": grant_steps}
    gap["prompt"] = _ROLE_GAP_PROMPT.format(tag=tag, base_url=base_url, **gap)
    if error is not None:
        gap["error"] = error
    return gap


def probe_capabilities(tag: str, cfg: dict, bot_user: str) -> tuple:
    """Run _CAPABILITY_PROBES as the bot. Returns ({capability: bool}, gaps)."""
    capabilities, gaps = {}, []
    for capability, path, params, doctype, perm, effect in _CAPABILITY_PROBES:
        try:
            _request(cfg, "GET", path, params=params)
            capabilities[capability] = True
        except ConnectorError as e:
            capabilities[capability] = False
            steps = None
            if perm == "call":
                # Whitelisted for any logged-in user on stock Frappe: a
                # failure is instance policy, an app override or version,
                # not a missing role.
                steps = (f"ask the ERPNext admin whether this instance blocks {doctype} "
                         f"(whitelist policy, an app override, or Frappe version). No known "
                         f"role grant fixes it.")
            gaps.append(make_gap(tag=tag, base_url=cfg.get("base_url", ""), capability=capability,
                                 who="bot", user=bot_user or "(unknown bot user)",
                                 role=BOT_ROLE_NAME, doctype=doctype, perm=perm, effect=effect,
                                 grant_steps=steps, error=str(e)[:300]))
    return capabilities, gaps


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

    `capabilities`/`gaps` (issue 04): probes, as the bot, the reads that
    discovery and preflight need (_CAPABILITY_PROBES). Each failure is one
    gap (make_gap()) with a filled-in role-gap `prompt`. A clean bot
    returns `gaps: []`. Gaps never fail the health check either.
    """
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user")
    trust = verify_rbac_precheck_reliable(tag)
    capabilities, gaps = probe_capabilities(tag, cfg, trust.get("bot_user") or result.get("message"))
    out = {
        "tag": tag, "base_url": cfg["base_url"], "status": "ok",
        "logged_in_as": result.get("message"),
        "rbac_precheck_reliable": trust["reliable"],
        "capabilities": capabilities,
        "gaps": gaps,
    }
    if trust.get("bot_stock_roles"):
        out["service_account_warning"] = (
            f"The Bot Account holds stock role(s) {trust['bot_stock_roles']}. Service Accounts "
            f"hold only dedicated roles (agents ADR 0003). Every business read and every write "
            f"on this tag is refused. An admin must move the bot's rights to DocPerms on "
            f"'{BOT_ROLE_NAME}' and remove those roles, in the ERPNext UI.")
    if not trust["reliable"]:
        out["rbac_precheck_warning"] = (
            f"This tag's RBAC pre-check cannot be trusted: bot identity "
            f"{trust['bot_user']!r} is privileged={trust['privileged_identity']} "
            f"and the live has_permission probe discriminates="
            f"{trust['precheck_discriminates']}. frappe.client.has_permission can't verify "
            f"the requester on this tag, so every read and write falls back to a local check "
            f"(the requester's live roles against the doctype's DocPerm rows) and proceeds "
            f"only on a confirmed grant — an allowlist or a confirmation token never rescues "
            f"an unverified requester. The fallback can't see User Permissions or if_owner "
            f"rows. Provision a narrow-role dedicated bot account to restore the real check — "
            f"see init_bot.py / 00-conventions.md."
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
    _validate_prod_requester(tag, requested_by, doctype, "read", docname=name, internal=internal,
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


def read_rpc(tag: str, method: str, path: str, *, gate_doctype: str, requested_by: str,
             params: dict = None, payload: dict = None, gate_ptype: str = "read",
             log_doctype: str = None, log_name: str = None,
             session_id: str = None, domain_code: str = None, channel: str = None,
             channel_metadata: dict = None, prompt_summary: str = None,
             latest_prompt: str = None, credential: str = "bot") -> dict:
    """A READ made through a whitelisted RPC method (GET, or POST for an
    RPC that only computes and persists nothing) — gated and logged
    exactly like query_resource()/get_resource() (write-path hardening
    W13). `gate_doctype`/`gate_ptype` name the permission the requester
    must hold for this read; `log_doctype`/`log_name` name what the audit
    Read row points at (defaults: gate_doctype, no name). `credential`
    selects the key pair that SENDS the read ("admin" for System-Manager-
    only methods); the gate and the audit row always use the bot's."""
    _validate_prod_requester(tag, requested_by, gate_doctype, gate_ptype, docname=log_name,
                              session_id=session_id, domain_code=domain_code,
                              channel=channel, channel_metadata=channel_metadata,
                              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    cfg = get_env_config(tag)
    send_cfg = cfg if credential == "bot" else get_env_config(tag, credential=credential)
    result = _request(send_cfg, method, path, params=params, payload=payload)
    _log_read(cfg, log_doctype or gate_doctype, log_name, requested_by, session_id, domain_code,
              channel, channel_metadata, response_payload=result,
              prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    return result


def resource_exists(tag: str, doctype: str, name: str, credential: str = "bot") -> bool:
    """404-tolerant existence check. Never logged and never gated
    (internal=True — INTERNAL_READ_GATE_EXEMPT_DOCTYPES/PROD_GATE_EXEMPT_
    DOCTYPES cover "User"/"DocType"/"Role", the only doctypes this is ever
    called against)."""
    try:
        if credential == "bot":
            get_resource(tag, doctype, name, strip_noise=False, internal=True)
        else:
            # Same internal, unlogged existence check, made with another key
            # pair — for doctypes only that credential can read (e.g. Role,
            # System-Manager-readable only, checked with the admin key).
            _request(get_env_config(tag, credential=credential), "GET",
                     f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")
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
# elsewhere (_log_read()'s "Read", a write's PreparedRequest.audit_action
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
# Field names whose VALUES are masked in audit payload_before/payload_after/
# field_diff regardless of their shape (write-path hardening W19) — on top
# of the pattern-based redact_pii() pass. Extend per environment with
# QKEEE_ERP_AUDIT_MASK_FIELDS (comma-separated).
AUDIT_MASK_FIELDS = {
    "bank_ac_no", "iban", "pan", "aadhaar", "aadhaar_number", "passport_number",
    "api_key", "api_secret", "new_password", "password",
}
_MASKED = "***masked***"


def _audit_mask_fields() -> set:
    extra = _qkeee_env().get("QKEEE_ERP_AUDIT_MASK_FIELDS") or ""
    return AUDIT_MASK_FIELDS | {x.strip() for x in extra.split(",") if x.strip()}


def _redact_audit_payload(obj, mask=None):
    """Mask denylisted field values (any depth), then pattern-redact."""
    mask = _audit_mask_fields() if mask is None else mask
    if isinstance(obj, dict):
        return {k: (_MASKED if k in mask and v not in (None, "") else _redact_audit_payload(v, mask))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_audit_payload(x, mask) for x in obj]
    return _redact_pii_deep(obj)


def _redact_field_diff(diff: list) -> list:
    """field_diff is computed on the RAW payloads (so a masked field still
    shows up as changed), then each entry is redacted."""
    mask = _audit_mask_fields()
    out = []
    for entry in diff:
        if entry.get("fieldname") in mask:
            out.append({"fieldname": entry["fieldname"], "old": "***changed***", "new": "***changed***"})
        else:
            out.append(_redact_audit_payload(entry, mask))
    return out


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
        "payload_before": json.dumps(_redact_audit_payload(payload_before)) if payload_before else None,
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
    landed, so a caller (the operation pipeline) can surface degraded audit
    logging instead of it being visible only on stderr."""
    if not log_name:
        return False
    fields = {"status": status, "timestamp": _now_iso()}
    if reference_name:
        fields["reference_name"] = reference_name
    if payload_after is not None:
        fields["payload_after"] = json.dumps(_redact_audit_payload(payload_after))
        diff = _diff_fields(payload_before, payload_after)
        if diff:
            fields["field_diff"] = json.dumps(_redact_field_diff(diff))
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
# qkeee-erp:write-path
def _do_mutate(cfg: dict, doctype: str, action: str, payload: dict, name: str, requested_by: str,
                skip_comment: bool = False, skill_label: str = None) -> dict:
    """The actual per-action HTTP dispatch — factored out so
    the operation pipeline can wrap it uniformly with the two-phase Attempted/
    Success/Failure logging above without duplicating this logic per
    action.

    `skip_comment` suppresses the default `record_comment()` call per
    action below. `skill_label`
    (from the operation, defaulting to SKILL_LABEL)
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
    """Manual/debug CLI for the core connector: read-only subcommands only
    (health, list-envs, whoami, query, get, report, roles).

    There is deliberately no write subcommand here. Every write goes
    through scripts/execute_write.py, which imports every domain module
    (so allowlists and token gates are registered), applies schema
    mapping, and warns on missing audit context. The former `mutate`
    subcommand wrote with no allowlist and
    no token, and `gated-mutate` duplicated execute_write.py's domain-less
    path without its checks - both removed (write-path hardening, W04/D4)."""
    p = argparse.ArgumentParser(description="qkeee-erp-associate core connector CLI")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/get/report/roles)")
    p.add_argument("--requested-by",
                   help="ERPNext user id/email of the human requesting the change, for THIS call "
                        "only — resolve it from the live inbound channel identity (chat/email "
                        "sender) before passing it here; there is no env-var or config default "
                        "to fall back on, mandatory on every read")
    p.add_argument("--session-id", help="plain string correlator threaded into Qkeee Bot Audit Log rows")
    p.add_argument("--domain-code", help="e.g. qkeee-erp-associate — threaded into audit rows")
    p.add_argument("--channel", help="conversation surface, e.g. Discord/Telegram/WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help='JSON object of channel-specific tracing detail')
    p.add_argument("--prompt-summary", help="one-line summary of the user request that led to this "
                                             "call — threaded into Qkeee Bot Audit Log rows")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt from the driving chat "
                                            "session — threaded into Qkeee Bot Audit Log rows")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("list-envs")
    sub.add_parser("whoami", help="Print the gateway session identity this process sees (no network call)")

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

    args = p.parse_args()

    if args.command in ("health", "query", "get", "report", "roles") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command in ("query", "get", "report", "roles") and not args.session_id:
        args.session_id = _session_or_fallback(None)

    # requested_by is mandatory on every read/write, on every tag — no
    # env-var or config default exists to fall back to. Bound to the
    # gateway session's sender when there is one. See resolve_requested_by().
    try:
        effective_requested_by = resolve_requested_by(args.requested_by)
    except GateRefusal as e:
        print(f"ERROR: refused, nothing was sent: {e}", file=sys.stderr)
        sys.exit(3)

    if args.command in ("query", "get", "report") and not effective_requested_by:
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
        elif args.command == "whoami":
            print(json.dumps(session_identity(), indent=2))
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
    except GateRefusal as e:  # same exit contract as execute_write.py: 3 = refused, nothing sent
        print(f"ERROR: refused, nothing was sent: {e}", file=sys.stderr)
        sys.exit(3)
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
