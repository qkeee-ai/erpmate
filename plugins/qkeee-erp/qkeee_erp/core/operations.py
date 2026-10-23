#!/usr/bin/env python3
"""
qkeee-erp-associate core — the operation registry and the ONE write pipeline.

Every write this skill can make is a named Operation registered here, and
every write runs through run_operation(). No domain module calls the
transport (`_request` with POST/PUT/DELETE, `_do_mutate`) itself — a test
(domains/test_operations_matrix.py) enforces that invariant.

Why (agents/.scratch/qkeee-erp-write-path-hardening/spec.md): the skill
used to have a generic write path plus five bespoke gated functions with
copy-pasted, drifting checks; the generic path could bypass the bespoke
gates, and several tokens weren't bound to what was actually sent.

## The pipeline — always this order

 1. resolve      the operation (unknown key -> refused)
 2. prepare      args -> PreparedRequest, no I/O: target doctype/action
 3. mode         refuse unless "read-write"
 4. requester    must be present
 5. allowlist    target doctype in the operation's domain allowlist
 6. ownership    a generic op may not do what a gated op owns; the
                 unscoped op may not touch domain-owned or system doctypes
 6b. enrich      the operation's live-read shaping of the request (schema
                 mapping, defaults) — only after the cheap gates passed, and
                 identically at render and execute, so both hash the same
                 bytes
 6c. self-escalation  admin-credential ops and UNSCOPED_DENY doctypes may
                 never change a Service Account's user, roles, or a
                 permission row on its roles (agents ADR 0003); also at render
 7. precondition operation-specific live checks (concurrency, KYC, ...)
 8. token        per policy: presence, freshness, match against
                 operation_token() over the prepared request, and (policy
                 "token+user_code") the user's own reply containing
                 confirmation_code(token)
 9. RBAC         _validate_prod_requester(for_write=True), never exempt
10. audit start  with the full WriteContext
11. send         any exception -> audit Failure, re-raised as ConnectorError
11b. read-back   submit/cancel: the record's docstatus must have changed,
                 else audit Failure + StateNotConfirmedError (HRMS run
                 review issue 03); an unreadable record -> state_confirmed None
12. audit finish then post hooks (best-effort ones never fail the write)
13. return       result always carries `_audit_log_status`

## Render

prepare_only() runs steps 1-2 (plus the operation's render_defaults, which
fill live facts such as `expected_modified`), the cheap gates and step 9 as
a dry run (no gate row), and returns the prepared
request, the full args to pass back unchanged, and the token +
confirmation code. erp_execute_write phase `render` runs it.

One stated exception: prepare_job()'s render DOES write (job_approval.create,
through the normal pipeline) — a job's approval is confirmed later, in its
own turn/process, so the render has nowhere else to durably put the plan
and token for that later process to read (ADR 0008/0009).
"""

import dataclasses
import ipaddress
import json
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import client as _c  # module object: patches in tests apply

POLICY_NONE = "none"
POLICY_TOKEN = "token"
POLICY_TOKEN_USER_CODE = "token+user_code"
POLICIES = (POLICY_NONE, POLICY_TOKEN, POLICY_TOKEN_USER_CODE)

RESOURCE_ACTIONS = ("create", "update", "submit", "cancel", "delete")
# D5: every finalizing/destructive step is token-gated in every domain.
FINALIZING_ACTIONS = frozenset({"submit", "cancel", "delete"})

# Audit "action" is a Select on the live Qkeee Bot Audit Log doctype:
# Read/Create/Update/Submit/Cancel/Delete only. Anything else is rejected
# by Frappe and the (best-effort) audit insert silently fails (W27).
AUDIT_ACTIONS = ("Create", "Update", "Submit", "Cancel", "Delete")

_SAME = object()  # sentinel: allowlist domain == operation domain


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class WriteContext:
    """Everything about WHO/WHERE/WHY a write happens — one object, so no
    wrapper can drop part of the audit context (W12)."""
    tag: str
    mode: str = "read-only"
    requested_by: Optional[str] = None
    session_id: Optional[str] = None
    domain_code: Optional[str] = None
    channel: Optional[str] = None
    channel_metadata: Optional[dict] = None
    prompt_summary: Optional[str] = None
    latest_prompt: Optional[str] = None
    confirmation_token: Optional[str] = None
    issued_at: Optional[object] = None
    user_confirmation_text: Optional[str] = None
    user_approved: bool = False
    approval_note: Optional[str] = None
    # Set only by run_batch() after it verified the one batch confirmation
    # (HRMS run review issue 02): "batch <code> step i/n", or, for a
    # kanban child task consuming a job-level approval instead (agents
    # issue 06, ADR 0008): "job <job_id> batch i/n step j/m". Never a tool
    # argument; without_confirmation() clears it.
    batch_approval: Optional[str] = None

    def audit_kwargs(self) -> dict:
        return dict(session_id=self.session_id, domain_code=self.domain_code,
                    channel=self.channel, channel_metadata=self.channel_metadata,
                    prompt_summary=self.prompt_summary, latest_prompt=self.latest_prompt)

    def without_confirmation(self) -> "WriteContext":
        return dataclasses.replace(self, confirmation_token=None, issued_at=None,
                                   user_confirmation_text=None, batch_approval=None)


@dataclass
class PreparedRequest:
    """The exact request an operation will send. `bound` holds facts that
    are folded into the token but never sent (display facts the user
    confirmed, `expected_modified`, a stated reason)."""
    transport: str                       # "resource" | "rpc"
    doctype: str                         # target doctype: allowlist + ownership
    action: str                          # resource action, or an rpc label
    name: Optional[str] = None
    body: Optional[dict] = None
    rpc_path: Optional[str] = None
    rpc_method: str = "POST"
    rbac_doctype: Optional[str] = None   # default: doctype
    rbac_ptype: Optional[str] = None     # default: from action
    audit_action: Optional[str] = None   # default: action.capitalize()
    audit_doctype: Optional[str] = None  # default: doctype
    audit_reference: Optional[str] = None  # default: name
    bound: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if self.transport not in ("resource", "rpc"):
            raise ValueError(f"bad transport {self.transport!r}")
        if self.transport == "resource" and self.action not in RESOURCE_ACTIONS:
            raise _c.ConnectorError(
                f"Invalid action '{self.action}' for doctype '{self.doctype}'. Expected one of "
                f"{sorted(RESOURCE_ACTIONS)}."
            )
        self.rbac_doctype = self.rbac_doctype or self.doctype
        self.rbac_ptype = self.rbac_ptype or _c._MUTATE_ACTION_TO_PTYPE.get(self.action, "write")
        self.audit_action = self.audit_action or self.action.capitalize()
        if self.audit_action not in AUDIT_ACTIONS:
            raise ValueError(f"audit_action {self.audit_action!r} is not an Audit Log Select value")
        self.audit_doctype = self.audit_doctype or self.doctype
        if self.audit_reference is None:
            self.audit_reference = self.name

    def token_material(self) -> dict:
        return dict(transport=self.transport, doctype=self.doctype, action=self.action,
                    name=self.name or "", body=self.body or {}, rpc_path=self.rpc_path or "",
                    rpc_method=self.rpc_method if self.transport == "rpc" else "",
                    bound=self.bound or {})

    def summary(self) -> dict:
        out = {"transport": self.transport, "doctype": self.doctype, "action": self.action}
        if self.name:
            out["name"] = self.name
        if self.rpc_path:
            out["rpc"] = self.rpc_path
        out["body"] = self.body or {}
        if self.bound:
            out["confirmed_facts"] = self.bound
        return out


@dataclass(frozen=True)
class Operation:
    key: str
    domain: Optional[str]
    summary: str
    prepare: Callable                    # (args, ctx) -> PreparedRequest, NO I/O
    token_policy: object = POLICY_TOKEN_USER_CODE   # str, or (req) -> str
    credential: str = "bot"
    allowlist_domain: object = _SAME     # None: skip allowlist (named RPC ops)
    generic: bool = False                # subject to the ownership check
    owns: frozenset = frozenset()        # (doctype, action) pairs generic ops may not do
    refuse: tuple = ()                   # ((doctype, action), message) the generic op refuses
    preconditions: tuple = ()            # (req, args, ctx) -> None, raise to refuse
    render_defaults: Optional[Callable] = None   # (args, ctx) -> args (live facts)
    enrich: Optional[Callable] = None    # (req, args, ctx) -> None; live shaping, after cheap gates
    # (req, args, ctx) -> None: an external side effect that must happen only
    # after every gate (token, RBAC) and before the audit row + write; raise
    # to stop with nothing written (notify.summary_email sends its email).
    pre_send: Optional[Callable] = None
    post: Optional[Callable] = None      # (result, req, args, ctx, hooks) -> result
    on_failure: Optional[Callable] = None  # (exc, req, args, ctx) -> str | None
    skip_comment: bool = False
    comment_label: Optional[str] = None
    audit: bool = True                   # False only for init_bot provisioning (logs itself)
    # True only for init_bot's provisioning.grant_bot_read: admin-run, not a
    # CLI op, and limited to the fixed read rows in doctype_defs.py.
    self_escalation_exempt: bool = False
    args_help: dict = field(default_factory=dict)
    # A minimal, valid argument set — printed by erp_execute_write
    # phase list_ops as the operation's worked example, and driven through
    # every gate by domains/test_operations.py, so it can't silently rot.
    example_args: dict = field(default_factory=dict)
    cli: bool = True

    def policy_for(self, req: PreparedRequest) -> str:
        policy = self.token_policy(req) if callable(self.token_policy) else self.token_policy
        if policy not in POLICIES:
            raise ValueError(f"{self.key}: bad token policy {policy!r}")
        return policy


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict = {}
_OWNERS: dict = {}  # (doctype, action) -> op key


def register_operation(op: Operation) -> Operation:
    """Idempotent per key (re-importing a domain module re-registers its
    current definition, same as register_domain_allowlist()). Two
    DIFFERENT operations claiming the same (doctype, action) is a design
    error and raises at import time."""
    for pair in op.owns:
        holder = _OWNERS.get(pair)
        if holder and holder != op.key:
            raise ValueError(f"{op.key} claims {pair}, already owned by {holder}")
    for pair, holder in list(_OWNERS.items()):
        if holder == op.key:
            del _OWNERS[pair]
    for pair in op.owns:
        _OWNERS[pair] = op.key
    REGISTRY[op.key] = op
    return op


def owned_by(doctype: str, action: str) -> Optional[str]:
    return _OWNERS.get((doctype, action))


def get_operation(key: str) -> Operation:
    """A domain's operations register when its module is imported —
    execute_write.py (and confirm_token.py render, via it) import every
    domain up front. This module never imports a domain module itself, so
    the dependency runs one way: domains -> operations -> client."""
    if key not in REGISTRY:
        raise _c.DoctypeNotAllowedError(
            f"Unknown operation {key!r} (is its domain module imported? execute_write.py imports "
            f"them all). Registered: {sorted(REGISTRY)}."
        )
    return REGISTRY[key]


AUTO_OP = "auto"


def resolve_op_key(op_key: str, args: dict) -> str:
    """`auto` (HRMS demo run review, D5) -> the bot-credential
    `<domain>.generic` whose allowlist holds args["doctype"], else
    `unscoped.generic`. Any other key is returned unchanged. It never
    resolves to a named gated operation or to an admin-key operation; the
    resolved operation's gates then run unchanged (a deny-list doctype is
    still refused), and the token binds the resolved key."""
    if op_key != AUTO_OP:
        return op_key
    doctype = (args or {}).get("doctype")
    if not doctype:
        raise _c.InvalidArgumentsError("op 'auto' needs args.doctype to pick the operation.")
    owners = sorted(
        domain for domain, allowed in _c.DOMAIN_WRITE_ALLOWLISTS.items()
        if doctype in allowed and f"{domain}.generic" in REGISTRY
        and REGISTRY[f"{domain}.generic"].credential == "bot")
    if len(owners) > 1:
        raise _c.DoctypeNotAllowedError(
            f"op 'auto' is ambiguous for '{doctype}': domains {owners} all allow it. Name the "
            f"operation yourself (erp_execute_write phase list_ops).")
    return f"{owners[0]}.generic" if owners else "unscoped.generic"


def list_operations(cli_only: bool = True) -> list:
    return [REGISTRY[k] for k in sorted(REGISTRY) if REGISTRY[k].cli or not cli_only]


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

def operation_token(op_key: str, req: PreparedRequest, requested_by: str, issued_at) -> str:
    """The ONE token constructor: binds the operation, the full prepared
    request (transport, doctype, action, name, body, rpc path, bound
    facts), the requester and the render time."""
    return _c.compute_token(kind="op", op=op_key, requested_by=requested_by or "",
                            issued_at=_c.coerce_issued_at(issued_at), **req.token_material())


def _verify_token(op: Operation, policy: str, req: PreparedRequest, ctx: WriteContext) -> bool:
    if policy == POLICY_NONE:
        return False
    if ctx.batch_approval:
        return True  # run_batch() verified the whole batch's one confirmation
    if not ctx.confirmation_token or ctx.issued_at is None:
        raise _c.ConfirmationRequiredError(
            f"Refusing {req.action} on '{req.doctype}' ({op.key}): a confirmation_token + "
            f"issued_at are required. Render it first — erp_execute_write phase=render, "
            f"op={op.key} — show the rendered request and confirmation code to the user, and "
            f"pass the printed args/token/issued_at back unchanged. Never hand-construct one."
        )
    issued = _c.coerce_issued_at(ctx.issued_at)
    ttl = _c.token_ttl_seconds(ctx.tag)
    if not _c.is_fresh(issued, ttl):
        raise _c.StaleConfirmationError(
            f"The confirmation for {op.key} on '{req.doctype}' has expired (it expired at "
            f"{_c.expires_at(issued, ttl)}; lifetime {ttl // 60} minutes on this "
            f"{_c.tag_environment(ctx.tag)} Instance) or its issued_at is implausible — "
            f"re-render against current data and reconfirm before retrying."
        )
    expected = operation_token(op.key, req, ctx.requested_by, issued)
    if ctx.confirmation_token != expected:
        raise _c.TokenMismatchError(
            f"Refusing {op.key}: confirmation_token does not match the request about to be "
            f"sent (operation, doctype, action, name, body, confirmed facts, requester, "
            f"issued_at). Re-render with the exact args you will execute; don't reuse a token."
        )
    if policy == POLICY_TOKEN_USER_CODE:
        code = _c.confirmation_code(expected)
        if not ctx.user_confirmation_text:
            raise _c.UnconfirmedByUserError(
                f"Refusing {op.key}: user_confirmation_text is required — the literal text of "
                f"the user's own reply. Show them confirmation code {code!r} in the rendered "
                f"request and pass their actual reply here. Never construct it yourself."
            )
        if code not in ctx.user_confirmation_text.upper():
            raise _c.UnconfirmedByUserError(
                f"Refusing {op.key}: user_confirmation_text does not contain confirmation code "
                f"{code!r} — the user replied to a different/stale render, or never saw this "
                f"one. Re-render and reconfirm; never fabricate a reply."
            )
    return True


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

# Doctypes the unscoped (no-domain) operation may never write (W28 in
# spec.md): privilege and permission doctypes, code that runs server- or
# client-side, outbound data destinations (Webhook-like exfiltration
# routes), stored credentials, and audit/history records.
UNSCOPED_DENY = frozenset({
    # privilege / permissions
    "User", "Role", "Has Role", "Role Profile", "Module Profile", "User Permission",
    "DocType", "DocPerm", "Custom DocPerm", "Custom Field", "Property Setter",
    "System Settings", "Workflow",
    # code execution
    "Server Script", "Client Script", "Report",
    # outbound destinations
    "Webhook", "Notification", "Auto Email Report", "Email Account",
    # stored credentials / identity providers
    "OAuth Client", "Social Login Key", "Connected App", "LDAP Settings",
    # audit / history
    "Version", "Deleted Document", "Comment", _c.AUDIT_LOG_DOCTYPE, _c.JOB_APPROVAL_DOCTYPE,
    # manufacturing has no write path yet (references/domains/manufacturing.md)
    "BOM", "Work Order", "Job Card", "Production Plan", "Routing", "Workstation", "Operation",
})


def _check_allowlist(op: Operation, req: PreparedRequest) -> None:
    domain = op.domain if op.allowlist_domain is _SAME else op.allowlist_domain
    if domain is None:
        return
    allowed = _c.DOMAIN_WRITE_ALLOWLISTS.get(domain)
    if allowed is None:
        raise _c.DoctypeNotAllowedError(
            f"Refusing {req.action} on '{req.doctype}': domain '{domain}' has no registered "
            f"ALLOWED_WRITE_DOCTYPES (unknown domain, or its module isn't imported)."
        )
    if req.doctype not in allowed:
        raise _c.DoctypeNotAllowedError(
            f"Refusing {req.action} on '{req.doctype}': not in domain '{domain}''s "
            f"ALLOWED_WRITE_DOCTYPES {allowed!r}. If '{req.doctype}' genuinely belongs to this "
            f"domain's remit, add it to that tuple deliberately; don't route around this gate."
        )


def _check_ownership(op: Operation, req: PreparedRequest) -> None:
    if not op.generic:
        return
    for (pair, message) in op.refuse:
        if pair == (req.doctype, req.action):
            raise _c.DoctypeNotAllowedError(f"Refusing {req.action} on '{req.doctype}': {message}")
    owner = owned_by(req.doctype, req.action)
    if owner and owner != op.key:
        raise _c.DoctypeNotAllowedError(
            f"Refusing {req.action} on '{req.doctype}' via {op.key}: it is a gated operation — "
            f"use '{owner}' (see erp_execute_write phase list_ops)."
        )
    if op.domain is None:
        if req.doctype in UNSCOPED_DENY:
            raise _c.DoctypeNotAllowedError(
                f"Refusing {req.action} on '{req.doctype}': privilege/system doctypes are never "
                f"written through the unscoped operation."
            )
        for domain, allowed in sorted(_c.DOMAIN_WRITE_ALLOWLISTS.items()):
            if req.doctype in allowed:
                raise _c.DoctypeNotAllowedError(
                    f"Refusing {req.action} on '{req.doctype}' via {op.key}: '{req.doctype}' "
                    f"belongs to domain '{domain}' — use '{domain}.generic' so that domain's own "
                    f"rules apply."
                )


_PERMISSION_DOCTYPES = frozenset({"Custom DocPerm", "DocPerm"})

# What the Admin Account key can reach (agents ADR 0003 amendment
# 2026-10-13, option b): the permission and identity doctypes, plus every
# doctype the system_admin domain writes (read from its allowlist at call
# time, so a new admin doctype is covered without editing this list).
_ADMIN_KEY_BASE_DOCTYPES = frozenset({
    "User", "Role", "Has Role", "Role Profile", "DocType", "DocPerm", "Custom DocPerm",
})


def admin_key_doctypes() -> frozenset:
    return _ADMIN_KEY_BASE_DOCTYPES | frozenset(_c.DOMAIN_WRITE_ALLOWLISTS.get("system_admin", ()))


def _self_escalation_target(req: PreparedRequest, ctx: WriteContext) -> Optional[str]:
    """What this request would change about a Service Account, or None.

    Every Service Account's User record and role assignment is protected,
    and so is every Role record it holds. Permission rows differ by
    account (agents ADR 0003, amended 2026-10-13):
    - a role the Bot Account (or an unlabelled account) holds: protected
      on every doctype — it is what the agent does business with;
    - a role only the Admin Account holds: protected only on the doctypes
      the admin key reaches (admin_key_doctypes()). A rule on Sales
      Invoice for System Manager gives the agent nothing: it never writes
      Sales Invoice with the admin key."""
    accounts = _c.service_account_identities(ctx.tag)
    users = {a["user"].lower() for a in accounts}
    full = set().union(set(), *(a["roles"] for a in accounts if a.get("label") != "Admin"))
    full -= _c.AUTOMATIC_ROLES
    admin_only = set().union(set(), *(a["roles"] for a in accounts if a.get("label") == "Admin"))
    admin_only -= _c.AUTOMATIC_ROLES | full
    held = full | admin_only
    body = req.body or {}
    if req.doctype == "User":
        target = (req.name or body.get("email") or body.get("name") or "").lower()
        if target in users:
            return f"Service Account user {target!r}"
        # The same rule as Has Role below, for create_user / set_user_roles:
        # handing the Bot's role to a person hands them the agent's rights.
        bound = req.bound or {}
        added = (set(bound["added"]) if "added" in bound
                 else {r.get("role") for r in body.get("roles") or [] if isinstance(r, dict)})
        if added & full:
            return f"role(s) {sorted(added & full)}, held by the Bot Account, given to {target!r}"
    if req.doctype == "Role":
        role = req.name or body.get("role_name") or body.get("name")
        if role in held:
            return f"role {role!r}, which a Service Account holds"
    if req.doctype == "Has Role":
        # Granting the Bot's role to someone else is still refused; granting
        # a stock role the Admin Account also holds is a normal admin task.
        if (body.get("parent") or "").lower() in users or body.get("role") in full:
            return "a Service Account's role assignment"
    if req.doctype in _PERMISSION_DOCTYPES:
        target_doctype = body.get("doctype") or body.get("parent")
        guarded = full | (admin_only if target_doctype in admin_key_doctypes() else set())
        if body.get("role"):
            touched = {body["role"]} & guarded
        else:  # a reset: every custom row on the doctype goes
            touched = {r.get("role") for r in _c.fetch_permission_rows(
                ctx.tag, target_doctype)} & guarded
        if touched:
            return (f"permission rows of role(s) {sorted(touched)} on '{target_doctype}', held by "
                    f"a Service Account")
    return None


def _check_no_self_escalation(op: Operation, req: PreparedRequest, ctx: WriteContext) -> None:
    """Target rule (agents ADR 0003): may ANYONE, through the agent, make
    this change? Never, when it widens or alters a Service Account's own
    rights — separate from the Requester Gate, which asks whether this
    requester may write. Runs for every admin-credential op and every
    UNSCOPED_DENY doctype; no confirmation unlocks it."""
    if op.self_escalation_exempt:
        return
    if op.credential != "admin" and req.doctype not in UNSCOPED_DENY:
        return
    target = _self_escalation_target(req, ctx)
    if target:
        raise _c.SelfEscalationError(
            f"Refusing {op.key} ({req.action} on '{req.doctype}'): it would change {target}. The "
            f"agent never changes the rights of an account it runs as, whatever credential sends "
            f"it and whoever asks (agents ADR 0003, self-escalation). Nothing was sent. A human "
            f"admin makes this change in the ERPNext UI, in their own session.")


def _check_mode_and_requester(req: PreparedRequest, ctx: WriteContext) -> None:
    if ctx.mode != "read-write":
        raise _c.ReadOnlyModeError(
            f"Refusing {req.action} on '{req.doctype}': the qkeee-erp plugin setting mode is '{ctx.mode}', not "
            f"'read-write'. Switch modes explicitly if this write is intended."
        )
    if not ctx.requested_by:
        raise _c.MissingRequesterError(
            f"Refusing {req.action} on '{req.doctype}': requested_by is missing. There is no "
            f"env-var or config default for it — resolve the inbound channel identity (the "
            f"requesting user's own work email/chat identity) as a real ERPNext User and pass "
            f"it explicitly via --requested-by / requested_by= for this call."
        )


def _audit_status(doctype: str, log_name, finish_ok: bool) -> str:
    if doctype in _c.AUDIT_EXEMPT_DOCTYPES:
        return "exempt"
    if log_name is None:
        return "insert_failed"
    if not finish_ok:
        return "update_failed"
    return "ok"


# ---------------------------------------------------------------------------
# Structured write failures (issue 07)
# ---------------------------------------------------------------------------

STRUCTURED_FAILURE_CLASSES = frozenset({
    "MandatoryError", "LinkValidationError", "ValidationError", "DuplicateEntryError",
    "UniqueValidationError",
})
_TAG_RE = re.compile(r"<[^>]+>")
_MANDATORY_RE = re.compile(r"\]:\s*(.+)$")
_LINK_RE = re.compile(r"Could not find (?:Row #\d+:\s*)?(.+?):\s*(.+)$")


def _server_messages(body: dict) -> list:
    """Frappe's `_server_messages`: a JSON list of JSON-encoded {message}
    dicts. Messages come back with HTML tags stripped."""
    try:
        raw = json.loads(body.get("_server_messages") or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            msg = json.loads(item).get("message") if isinstance(item, str) else item.get("message")
        except (AttributeError, ValueError):
            msg = item
        if msg:
            out.append(_TAG_RE.sub("", str(msg)).strip())
    return out


def parse_write_failure(exc) -> Optional[dict]:
    """ERPNext's rejection of a create/update as {error_class,
    missing_fields[], invalid_links[{field, value}], message}, for the
    classes in STRUCTURED_FAILURE_CLASSES. None for anything else (a gate
    refusal, a transport error, a WAF page, another exc_type): those keep
    their own handling. `invalid_links[].field` is the label Frappe names,
    not always the fieldname."""
    if not isinstance(exc, _c.ERPNextAPIError):
        return None
    try:
        body = json.loads(exc.body)
    except ValueError:
        return None
    error_class = body.get("exc_type") if isinstance(body, dict) else None
    if error_class not in STRUCTURED_FAILURE_CLASSES:
        return None
    exception = str(body.get("exception") or "")
    detail = exception.split(": ", 1)[1] if ": " in exception else exception
    messages = _server_messages(body)
    missing, links = [], []
    if error_class == "MandatoryError":
        m = _MANDATORY_RE.search(detail)
        if m:
            missing = [f.strip() for f in m.group(1).split(",") if f.strip()]
    if error_class == "LinkValidationError":
        for text in messages or [detail]:
            m = _LINK_RE.search(text)
            if m:
                links.append({"field": m.group(1).strip(), "value": m.group(2).strip()})
    return {"error_class": error_class, "missing_fields": missing, "invalid_links": links,
            "message": messages[0] if messages else detail}


# ---------------------------------------------------------------------------
# Post-hook capabilities
# ---------------------------------------------------------------------------

class PostHooks:
    """Handed to an operation's `post` hook — the only write capabilities
    a post hook gets: run another registered operation (same context, no
    confirmation reused) and compensate THIS operation's own create."""

    def __init__(self, op: Operation, req: PreparedRequest, ctx: WriteContext, created_name):
        self._op, self._req, self._ctx, self._created = op, req, ctx, created_name

    def run(self, op_key: str, args: dict) -> dict:
        return run_operation(op_key, args, self._ctx.without_confirmation())

    def compensate_create(self, reason: str) -> dict:
        """Delete the record THIS operation just created — nothing else.
        RBAC (delete permission) and audit still apply; no token, since
        undoing our own just-made create is not a user-initiated delete."""
        if self._req.action != "create" or not self._created:
            raise _c.ConnectorError("compensate_create: only valid after a successful create.")
        req = PreparedRequest(transport="resource", doctype=self._req.doctype, action="delete",
                              name=self._created)
        ctx = dataclasses.replace(self._ctx.without_confirmation(),
                                  approval_note=f"compensating delete: {reason}")
        _c._validate_prod_requester(ctx.tag, ctx.requested_by, req.rbac_doctype, req.rbac_ptype,
                                     docname=req.name, for_write=True, domain=self._op.domain,
                                     credential=self._op.credential, **ctx.audit_kwargs())
        return _send(self._op, req, ctx, user_approved=False, skip_comment=True)


# ---------------------------------------------------------------------------
# Send (steps 10-13)
# ---------------------------------------------------------------------------

def _approval_basis(policy: str, verified: bool, agent_asserted: bool) -> str:
    """What the Audit Log's `user_approved` rests on. Only a verified token
    is proof; `user_approved=True` on a no-token operation is the agent's
    word, so the note says so (item-run review issue 02)."""
    if verified:
        return "token+code" if policy == POLICY_TOKEN_USER_CODE else "token"
    return "agent-asserted" if agent_asserted else "none"


_EXPECTED_DOCSTATUS = {"submit": 1, "cancel": 2}
_READ_BACK_FIELDS = ("docstatus", "status", "error_message")
_UNCHECKED = object()


def _read_back_state(cfg: dict, req: PreparedRequest) -> Optional[dict]:
    """The record's live docstatus, plus status and error_message when the
    doctype has them, read with the credential that sent the write: plumbing
    for the write just sent, so no gate row and no Read audit row. None
    when the read fails or returns no docstatus; the write is not undone."""
    path = (f"/api/resource/{urllib.parse.quote(req.doctype)}/"
            f"{urllib.parse.quote(req.name or '')}")
    try:
        data = (_c._request(cfg, "GET", path) or {}).get("data")
    except Exception:  # noqa: BLE001 — the write was sent; a failed read never fails it
        return None
    if not isinstance(data, dict) or "docstatus" not in data:
        return None
    return {k: data[k] for k in _READ_BACK_FIELDS if k in data}


def _send(op: Operation, req: PreparedRequest, ctx: WriteContext, *, user_approved: bool,
          skip_comment: bool = None, args: dict = None, approval_basis: str = None) -> dict:
    audit_cfg = _c.get_env_config(ctx.tag)
    send_cfg = audit_cfg if op.credential == "bot" else _c.get_env_config(ctx.tag, credential=op.credential)
    skip_comment = op.skip_comment if skip_comment is None else skip_comment
    label = op.comment_label or (f"qkeee-erp-associate/{op.domain}" if op.domain else _c.SKILL_LABEL)
    approval_note = ctx.approval_note or op.summary
    if approval_basis:
        approval_note = f"[approval: {approval_basis}] {approval_note}"

    payload_before = None
    if (req.transport == "resource" and req.action == "update"
            and req.doctype not in _c.AUDIT_EXEMPT_DOCTYPES and req.name):
        try:
            payload_before = _c.get_resource(ctx.tag, req.doctype, req.name, strip_noise=False,
                                             requested_by=ctx.requested_by,
                                             **ctx.audit_kwargs()).get("data")
        except _c.ConnectorError:
            payload_before = None

    log_name = _c.record_audit_log_start(
        audit_cfg, action=req.audit_action, doctype=req.audit_doctype, name=req.audit_reference,
        requested_by=ctx.requested_by, payload_before=payload_before,
        user_approved=user_approved, approval_note=f"[{op.key}] {approval_note}",
        **ctx.audit_kwargs(),
    ) if op.audit else None
    try:
        if req.transport == "resource":
            result = _c._do_mutate(send_cfg, req.doctype, req.action, req.body, req.name,
                                   ctx.requested_by, skip_comment=skip_comment, skill_label=label)
        else:
            result = _c._request(send_cfg, req.rpc_method, req.rpc_path, payload=req.body)
            if not isinstance(result, dict):
                result = {"message": result}
    except Exception as e:  # noqa: BLE001 — every failure must close the audit row
        exc = e
        failure = (parse_write_failure(e)
                   if req.transport == "resource" and req.action in ("create", "update") else None)
        if failure:
            exc = _c.WriteRejectedError(f"{e}", failure)
            exc.__cause__ = e
        if op.on_failure:
            try:
                extra = op.on_failure(e, req, args or {}, ctx)
            except Exception as hook_err:  # noqa: BLE001
                extra = f"(could not determine partial outcome: {hook_err})"
            if extra:
                # Keep a timeout a timeout (audit row flagged outcome_unknown).
                cls = (_c.TransportTimeoutError if isinstance(e, _c.TransportTimeoutError)
                       else _c.PartialOutcomeError)
                exc = cls(f"{e} — {extra}")
                exc.__cause__ = e
        _c.finish_audit_failure_and_reraise(audit_cfg, log_name, exc)

    state_confirmed = _UNCHECKED
    if req.transport == "resource" and req.action in _EXPECTED_DOCSTATUS:
        read_back = _read_back_state(send_cfg, req)
        if read_back is None:
            state_confirmed = None
            req.notes.append(f"{req.action} of '{req.doctype}' '{req.name}': could not read the "
                             f"record back, so its new state is not confirmed. Read it before "
                             f"reporting the step as done.")
        elif read_back.get("docstatus") != _EXPECTED_DOCSTATUS[req.action]:
            detail = ", ".join(f"{k}={v!r}" for k, v in read_back.items())
            exc = _c.StateNotConfirmedError(
                f"{req.action} of '{req.doctype}' '{req.name}' was NOT applied: the call returned, "
                f"but the record now shows {detail} (expected docstatus "
                f"{_EXPECTED_DOCSTATUS[req.action]}). Report it as failed, with the "
                f"error_message if any; never as done.", read_back)
            _c.finish_audit_failure_and_reraise(audit_cfg, log_name, exc)
        else:
            state_confirmed = True

    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(result, dict):
        data = result.get("message")
    if req.transport == "resource":
        reference_name = (data or {}).get("name") if isinstance(data, dict) else req.name
    else:
        reference_name = req.audit_reference
    if not reference_name:
        print(f"WARN: {op.key} ({req.action} on '{req.doctype}') returned no usable reference "
              f"name — Audit Log row {log_name!r} will have a blank Reference Name.",
              file=sys.stderr)
    comment_posted = result.pop("_audit_comment_posted", None) if isinstance(result, dict) else None
    if (req.transport == "rpc" and not skip_comment and req.audit_reference
            and req.audit_doctype not in _c.AUDIT_EXEMPT_DOCTYPES):
        comment_posted = _c.record_comment(
            audit_cfg, req.audit_doctype, req.audit_reference,
            f"[{label}] {op.summary} — requested by {ctx.requested_by}, applied via qkeee-erp bot.",
        )
    finish_ok = _c.record_audit_log_finish(
        audit_cfg, log_name, status="Success", reference_name=reference_name,
        payload_before=payload_before,
        payload_after=data if (isinstance(data, dict) and req.transport == "resource") else None,
        audit_comment_posted=comment_posted,
    ) if op.audit else False
    if isinstance(result, dict):
        result["_audit_log_status"] = (_audit_status(req.audit_doctype, log_name, finish_ok)
                                       if op.audit else "exempt")
        result["_operation"] = op.key
        if state_confirmed is not _UNCHECKED:
            result["state_confirmed"] = state_confirmed
    return result


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def _prepare(op: Operation, args: dict, ctx: WriteContext) -> PreparedRequest:
    """prepare() does no I/O, so a non-gate error from it is always a
    malformed argument set: surface it as InvalidArgumentsError (CLI exit
    2, nothing sent) rather than as an ERPNext error."""
    try:
        return op.prepare(args, ctx)
    except (_c.GateRefusal, _c.InvalidArgumentsError):
        raise
    except _c.ConnectorError as e:
        raise _c.InvalidArgumentsError(f"{op.key}: {e}") from e


def run_operation(op_key: str, args: dict, ctx: WriteContext) -> dict:
    op = get_operation(resolve_op_key(op_key, args))              # 1
    args = dict(args or {})
    req = _prepare(op, args, ctx)                                 # 2
    _check_mode_and_requester(req, ctx)                           # 3, 4
    _check_allowlist(op, req)                                     # 5
    _check_ownership(op, req)                                     # 6
    if op.enrich:                                                 # 6b
        op.enrich(req, args, ctx)
    _check_no_self_escalation(op, req, ctx)                       # 6c
    for check in op.preconditions:                                # 7
        check(req, args, ctx)
    policy = op.policy_for(req)
    verified = _verify_token(op, policy, req, ctx)                # 8
    _c._validate_prod_requester(                                  # 9
        ctx.tag, ctx.requested_by, req.rbac_doctype, req.rbac_ptype, docname=req.name,
        for_write=True, domain=op.domain, credential=op.credential, **ctx.audit_kwargs())
    if op.pre_send:                                               # 9b
        op.pre_send(req, args, ctx)
    result = _send(op, req, ctx, user_approved=verified or ctx.user_approved, args=args,  # 10-12
                   approval_basis=_approval_basis(policy, verified, ctx.user_approved))
    if op.post:
        created = None
        if req.transport == "resource" and req.action == "create" and isinstance(result, dict):
            created = (result.get("data") or {}).get("name")
        result = op.post(result, req, args, ctx, PostHooks(op, req, ctx, created))
    if req.notes and isinstance(result, dict):
        result["_notes"] = list(req.notes)
    return result                                                 # 13


def _render_request(op_key: str, args: dict, ctx: WriteContext, *, render: bool = True):
    """Steps 1-2, the cheap gates, enrich and self-escalation, as render
    runs them; with `render`, also render_defaults and the requester check
    as a dry run (no gate row): no code for a step the requester cannot
    run (HRMS run review issue 09). Execute runs the real gate again; the
    right can change between. Returns (op, args, req, policy)."""
    op = get_operation(resolve_op_key(op_key, args))
    args = dict(args or {})
    if render and op.render_defaults:
        args = op.render_defaults(args, ctx)
    req = _prepare(op, args, ctx)
    # Refuse early: no point rendering a request the gates will refuse.
    _check_allowlist(op, req)
    _check_ownership(op, req)
    if op.enrich:
        op.enrich(req, args, ctx)
    _check_no_self_escalation(op, req, ctx)
    if render:
        _c._validate_prod_requester(
            ctx.tag, ctx.requested_by, req.rbac_doctype, req.rbac_ptype, docname=req.name,
            for_write=True, domain=op.domain, credential=op.credential, dry=True,
            **ctx.audit_kwargs())
    return op, args, req, op.policy_for(req)


def _token_fields(token: str, policy: str, issued: int, tag: str) -> dict:
    ttl = _c.token_ttl_seconds(tag)
    out = {"issued_at": issued, "expires_at": _c.expires_at(issued, ttl), "ttl_minutes": ttl // 60}
    if policy == POLICY_TOKEN_USER_CODE:
        out["confirmation_code"] = _c.confirmation_code(token)
    return out


def prepare_only(op_key: str, args: dict, ctx: WriteContext, issued_at: int = None) -> dict:
    """Render: steps 1-2 plus render_defaults; no write. The
    returned `args` are what the execute step must be given back
    unchanged — they include live facts the render filled in."""
    op, args, req, policy = _render_request(op_key, args, ctx)
    out = {"op": op.key, "summary": op.summary, "policy": policy, "args": args,
           "request": req.summary(), "notes": list(req.notes)}
    if op_key == AUTO_OP:
        out["resolved_op"] = op.key
    if policy != POLICY_NONE:
        issued = int(time.time()) if issued_at is None else _c.coerce_issued_at(issued_at)
        token = operation_token(op.key, req, ctx.requested_by, issued)
        out.update(confirmation_token=token, **_token_fields(token, policy, issued, ctx.tag))
    return out


# ---------------------------------------------------------------------------
# One confirmation per batch (HRMS demo run review issue 02, agents ADR 0007)
# ---------------------------------------------------------------------------

MAX_BATCH_STEPS = 25
_BATCH_RENDER_KEYS = {"op", "args"}
_FINAL_MARKS = {"submit": "SUBMIT (final)", "cancel": "CANCEL (final)", "delete": "DELETE"}


def _check_batch_shape(steps) -> None:
    if not isinstance(steps, list) or not steps:
        raise _c.InvalidArgumentsError("batch must be a non-empty list of {op, args} steps.")
    if len(steps) > MAX_BATCH_STEPS:
        raise _c.InvalidArgumentsError(
            f"batch has {len(steps)} steps; at most {MAX_BATCH_STEPS}. Split it into batches.")
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or not step.get("op") or set(step) - _BATCH_RENDER_KEYS:
            raise _c.InvalidArgumentsError(
                f"batch step {i}: needs exactly 'op' and 'args' (one confirmation covers the "
                f"whole batch; no per-step confirmation fields).")


def _from_step(args: dict):
    """The k of `expected_modified: {"from_step": k}`, else None."""
    ref = (args or {}).get("expected_modified")
    if not isinstance(ref, dict):
        return None
    k = ref.get("from_step")
    if set(ref) != {"from_step"} or isinstance(k, bool) or not isinstance(k, int):
        raise _c.InvalidArgumentsError(
            f'expected_modified must be the record\'s modified or {{"from_step": k}}, got {ref!r}.')
    return k


def _batch_requests(steps: list, ctx: WriteContext, *, render: bool) -> list:
    prepared = []
    for i, step in enumerate(steps, start=1):
        op, args, req, policy = _render_request(step["op"], step.get("args") or {}, ctx,
                                                render=render)
        k = _from_step(args)
        if k is not None:
            if not 1 <= k < i:
                raise _c.InvalidArgumentsError(
                    f"batch step {i}: from_step {k} must name an earlier step (1..{i - 1}).")
            if req.action not in FINALIZING_ACTIONS:
                raise _c.InvalidArgumentsError(
                    f"batch step {i}: from_step only applies to submit/cancel/delete.")
            prev = prepared[k - 1][2]
            if prev.doctype != req.doctype or (prev.name and prev.name != req.name):
                raise _c.InvalidArgumentsError(
                    f"batch step {i}: from_step {k} writes {prev.doctype} {prev.name!r}, a "
                    f"different record from {req.doctype} {req.name!r}.")
        prepared.append((op, args, req, policy))
    return prepared


def _batch_policy(prepared: list) -> str:
    policies = {p for *_, p in prepared}
    for policy in (POLICY_TOKEN_USER_CODE, POLICY_TOKEN):
        if policy in policies:
            return policy
    return POLICY_NONE


def batch_token(prepared: list, requested_by: str, issued_at) -> str:
    """The one batch token: binds, in order, each step's operation and full
    prepared request (doctype, action, name, body, confirmed facts; a
    from_step reference stays a reference), plus the requester, the batch
    length and the render time."""
    return _c.compute_token(
        kind="batch", requested_by=requested_by or "", issued_at=_c.coerce_issued_at(issued_at),
        length=len(prepared),
        steps=[dict(op=op.key, **req.token_material()) for op, _, req, _ in prepared])


def _step_line(i: int, op: Operation, req: PreparedRequest, policy: str) -> str:
    target = req.name or (req.body or {}).get("name") or ""
    line = f"{i}. {req.action} {req.doctype} {target}".rstrip() + f" ({op.key})"
    if req.action in _FINAL_MARKS:
        line += f" — {_FINAL_MARKS[req.action]}"
    if policy == POLICY_NONE:
        line += " — no code needed"
    return line


def prepare_batch(steps: list, ctx: WriteContext, issued_at: int = None) -> dict:
    """Render a batch: every step prepared as prepare_only() does, then ONE
    token and ONE code over the ordered list. `batch` holds the {op, args}
    to pass back unchanged (resolved op keys, live facts filled in)."""
    _check_batch_shape(steps)
    prepared = _batch_requests(steps, ctx, render=True)
    policy = _batch_policy(prepared)
    out = {"policy": policy,
           "batch": [{"op": op.key, "args": args} for op, args, _, _ in prepared],
           "steps": [{"step": i, "op": op.key, "policy": p, "line": _step_line(i, op, req, p),
                      "request": req.summary(), "notes": list(req.notes)}
                     for i, (op, _, req, p) in enumerate(prepared, start=1)]}
    if policy != POLICY_NONE:
        issued = int(time.time()) if issued_at is None else _c.coerce_issued_at(issued_at)
        token = batch_token(prepared, ctx.requested_by, issued)
        out.update(batch_token=token, **_token_fields(token, policy, issued, ctx.tag))
    return out


def _verify_batch(steps: list, ctx: WriteContext, token: str) -> tuple:
    """Execute side of the batch binding, before step 1: the same steps,
    in the same order, unchanged; fresh; and, when a step needs it, the
    user's reply holds the code. Returns (code, prepared requests)."""
    _check_batch_shape(steps)
    prepared = _batch_requests(steps, ctx, render=False)
    policy = _batch_policy(prepared)
    if ctx.issued_at is None:
        raise _c.ConfirmationRequiredError("Refusing the batch: issued_at is required with "
                                           "batch_token. Pass both back from the render.")
    issued = _c.coerce_issued_at(ctx.issued_at)
    ttl = _c.token_ttl_seconds(ctx.tag)
    if not _c.is_fresh(issued, ttl):
        raise _c.StaleConfirmationError(
            f"The batch confirmation has expired (it expired at {_c.expires_at(issued, ttl)}; "
            f"lifetime {ttl // 60} minutes on this {_c.tag_environment(ctx.tag)} Instance). "
            f"Nothing was sent. Render the batch again and reconfirm.")
    expected = batch_token(prepared, ctx.requested_by, issued)
    if token != expected:
        raise _c.TokenMismatchError(
            "Refusing the batch: its steps differ from the render (a step added, removed, moved "
            "or changed, another operation, or another requester). Nothing was sent. Render the "
            "batch again and reconfirm.")
    code = _c.confirmation_code(expected)
    if policy != POLICY_TOKEN_USER_CODE:
        return code, prepared
    if not ctx.user_confirmation_text or code not in ctx.user_confirmation_text.upper():
        raise _c.UnconfirmedByUserError(
            f"Refusing the batch: user_confirmation_text must be the user's own reply, holding "
            f"confirmation code {code!r}. Nothing was sent. Never write the reply yourself.")
    return code, prepared


def _written_record(result) -> dict:
    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(result, dict):
        data = result.get("message")
    return data if isinstance(data, dict) else {}


_BATCH_STEP_KEYS = {"op", "args", "confirmation_token", "issued_at", "user_confirmation_text",
                    "user_approved", "approval_note"}


def _resolve_from_step(i: int, args: dict, req: PreparedRequest, written: dict) -> dict:
    """Replace `expected_modified: {"from_step": k}` with step k's result
    `modified`, refusing when step k wrote a different record than step
    i's prepared request targets."""
    k = _from_step(args)
    if k is None:
        return args
    doctype, name, modified = written.get(k, (None, None, None))
    if (doctype, name) != (req.doctype, req.name):
        raise _c.PreconditionFailedError(
            f"batch step {i}: from_step {k} wrote {doctype} {name!r}, a different record from "
            f"{req.doctype} {req.name!r}. Nothing was sent for this step.")
    if not modified:
        raise _c.PreconditionFailedError(
            f"batch step {i}: step {k}'s result has no `modified` to check against. Nothing was "
            f"sent for this step; render the rest again.")
    return dict(args, expected_modified=modified)


def run_batch(steps: list, ctx: WriteContext, batch_token: str = None, job_id: str = None,
             job_batch_index: int = None) -> dict:
    """Run several operations in order and STOP AT THE FIRST FAILURE (issue
    07): a gate refusal, an ERPNext rejection or an unknown outcome. Returns
    {"steps": [...], "stopped_at": <1-based step> | None, "counts": {...}};
    every step reports `succeeded`, `failed` or `not_attempted`, so the
    agent can show created / failed / not attempted as one table. Never
    retry or patch a failed step here: ask the user.

    With `batch_token` (from prepare_batch(); ctx carries issued_at and the
    user's reply): each step is exactly {"op", "args"}, the whole batch is
    checked against the render before step 1, the one code is verified
    once, and every step's Audit Log row names "batch <code> step i/n". A
    step's `expected_modified: {"from_step": k}` takes step k's result
    `modified`. Without it, each step carries its own confirmation fields.

    With `job_id` + `job_batch_index` instead (ADR 0008, agents issue 06):
    a kanban child task consumes an already-Approved job (issue 03's
    store, approved by verify_job_reply()) rather than running its own
    fresh render-then-chat-code round trip — there is no live chat session
    to answer one. `steps` must be exactly batch `job_batch_index` of that
    job's approved plan; every step's Audit Log row names
    "job <job_id> batch i/n step j/m". Mutually exclusive with
    `batch_token` — a batch is approved by a fresh chat code or a
    job-level email approval, never both."""
    if job_id is not None and batch_token is not None:
        raise _c.InvalidArgumentsError(
            "batch_token and job_id are mutually exclusive — a batch is approved by a fresh "
            "chat code or a job-level email approval, never both.")
    if job_id is not None:
        total_batches, prepared = _verify_job_batch(job_id, job_batch_index, steps, ctx)
        label_prefix = f"job {job_id} batch {job_batch_index}/{total_batches}"
    elif batch_token is not None:
        code, prepared = _verify_batch(steps, ctx, batch_token)
        label_prefix = f"batch {code}"
    else:
        prepared, label_prefix = None, None
    written = {}
    rows, stopped_at = [], None
    for i, step in enumerate(steps, start=1):
        args = step.get("args") or {}
        row = {"step": i, "op": step.get("op"), "doctype": args.get("doctype"),
               "action": args.get("action")}
        if stopped_at is not None:
            rows.append(dict(row, status="not_attempted"))
            continue
        try:
            unknown = set(step) - _BATCH_STEP_KEYS
            if unknown or not step.get("op"):
                raise _c.InvalidArgumentsError(
                    f"batch step {i}: needs 'op' and 'args'; unknown key(s) {sorted(unknown)}")
            if label_prefix is not None:
                label = f"{label_prefix} step {i}/{len(steps)}"
                args = _resolve_from_step(i, args, prepared[i - 1][2], written)
                step_ctx = dataclasses.replace(ctx.without_confirmation(), batch_approval=label,
                                               approval_note=label)
            else:
                step_ctx = dataclasses.replace(
                    ctx.without_confirmation(), confirmation_token=step.get("confirmation_token"),
                    issued_at=step.get("issued_at"),
                    user_confirmation_text=step.get("user_confirmation_text"),
                    # Per step only: a batch-level flag would mark every write
                    # approved whether or not its own confirm ran.
                    user_approved=bool(step.get("user_approved")),
                    approval_note=step.get("approval_note"))
            result = run_operation(step["op"], args, step_ctx)
        except _c.ConnectorError as e:
            stopped_at = i
            row.update(status="failed", error=str(e), refused=isinstance(e, _c.GateRefusal),
                       outcome_unknown=isinstance(e, (_c.TransportTimeoutError,
                                                      _c.PartialOutcomeError)))
            if isinstance(e, _c.WriteRejectedError):
                row["write_failure"] = e.failure
            if isinstance(e, _c.StateNotConfirmedError):
                row.update(outcome="not_applied", state_confirmed=False, read_back=e.read_back)
            rows.append(row)
            continue
        record = _written_record(result)
        if prepared:
            req = prepared[i - 1][2]
            written[i] = (req.doctype, record.get("name") or req.name, record.get("modified"))
        data = result.get("data") if isinstance(result, dict) else None
        row.update(status="succeeded",
                   name=(data or {}).get("name") if isinstance(data, dict) else args.get("name"),
                   audit_log_status=(result or {}).get("_audit_log_status"))
        if isinstance(result, dict) and "state_confirmed" in result:
            row["state_confirmed"] = result["state_confirmed"]
        if isinstance(result, dict) and result.get("_notes"):
            row["notes"] = list(result["_notes"])
        rows.append(row)
    counts = {s: sum(r["status"] == s for r in rows)
              for s in ("succeeded", "failed", "not_attempted")}
    return {"steps": rows, "stopped_at": stopped_at, "counts": counts}


_COMPACT_KEYS = ("name", "doctype", "docstatus", "modified")


def compact_record(data, payload) -> object:
    """A written record cut to what the agent uses (HRMS demo run review,
    issue 05): name, doctype, docstatus, modified and the top-level fields
    `payload` set. A child table becomes "<n> rows". Anything that is not
    a record dict is returned as is."""
    if not isinstance(data, dict):
        return data
    keys = list(_COMPACT_KEYS) + [k for k in (payload or {}) if k not in _COMPACT_KEYS]
    out = {}
    for key in keys:
        if key in data:
            value = data[key]
            out[key] = f"{len(value)} rows" if isinstance(value, list) else value
    return out


def requires_confirmation(op_key: str, args: dict) -> bool:
    """Cheap, I/O-free answer to "will this need a token?" — for the CLI's
    mandatory-context check, before prepare() does any live read."""
    op = get_operation(resolve_op_key(op_key, args))
    if not callable(op.token_policy):
        return op.token_policy != POLICY_NONE
    action = (args or {}).get("action")
    probe = PreparedRequest(transport="resource", doctype="?", action=action or "submit")
    return op.policy_for(probe) != POLICY_NONE


# ---------------------------------------------------------------------------
# One approval for a whole kanban job (agents ADR 0008/0009; issue 03 of
# .scratch/qkeee-erp-kanban-execution-steering)
#
# batch_token()/WriteContext.batch_approval above work because render and
# execute happen in the same turn, same process, same WriteContext. A job's
# confirmation does not: it is rendered once, approved later by an email
# reply that arrives as its own turn (possibly its own process, ADR 0008
# issue 05), and consumed by separate kanban child-task workers (issue 06),
# each a different process with its own fresh WriteContext. So the plan,
# token and approval status are durably stored in the "Qkeee Bot Job
# Approval" doctype (_c.JOB_APPROVAL_DOCTYPE), through the normal pipeline
# (job_approval.create / job_approval.mark_approved below) rather than only
# handed back to the caller to round-trip the way a batch's `batch` key is.
# ---------------------------------------------------------------------------

# ADR 0008: a fixed 24h window, sized for a multi-child-task kanban job
# rather than one chat round trip — token_ttl_seconds(tag)'s 15/60 minute
# batch/op lifetime is deliberately NOT reused here; it's too short.
JOB_TOKEN_TTL_SECONDS = 24 * 60 * 60


def _job_requests(batches: list, ctx: WriteContext, *, render: bool) -> list:
    """Each batch's steps prepared as _batch_requests() does, one list per
    batch, same per-batch shape check (MAX_BATCH_STEPS) prepare_batch()
    already applies — a job is an ordered list of batches, not one flat
    list of steps."""
    if not isinstance(batches, list) or not batches:
        raise _c.InvalidArgumentsError("a job needs a non-empty list of batches.")
    prepared_batches = []
    for steps in batches:
        _check_batch_shape(steps)
        prepared_batches.append(_batch_requests(steps, ctx, render=render))
    return prepared_batches


def _job_policy(prepared_batches: list) -> str:
    """Unlike _batch_policy() (one already-flat list of steps), takes the
    job's list-of-batches shape and flattens it before the same
    none/token/token+user_code aggregation."""
    return _batch_policy([step for batch in prepared_batches for step in batch])


def job_token(prepared_batches: list, requested_by: str, job_id: str, issued_at) -> str:
    """The one job token: binds, in order, every batch's every step's
    operation and full prepared request, plus the requester, the job id,
    the total step count and the render time (ADR 0008)."""
    steps = [dict(batch=bi + 1, step=si + 1, op=op.key, **req.token_material())
             for bi, batch in enumerate(prepared_batches)
             for si, (op, _, req, _) in enumerate(batch)]
    return _c.compute_token(
        kind="job", requested_by=requested_by or "", job_id=job_id or "",
        issued_at=_c.coerce_issued_at(issued_at), length=len(steps), steps=steps)


def prepare_job(batches: list, ctx: WriteContext, job_id: str, issued_at: int = None) -> dict:
    """Render a whole kanban job: every batch's every step prepared as
    prepare_batch() does, then ONE token over the full ordered plan across
    every batch. When at least one step needs confirmation, durably stores
    the plan (job_approval.create) before returning, so the email this
    feeds (issue 04) is backed by a row a later, unrelated process can
    verify against — never only in-memory here.

    `job` holds each batch's {op, args} list to pass back unchanged, the
    same role prepare_batch()'s `batch` key plays for a single batch."""
    if not job_id or not isinstance(job_id, str):
        raise _c.InvalidArgumentsError("prepare_job needs a non-empty job_id.")
    prepared_batches = _job_requests(batches, ctx, render=True)
    policy = _job_policy(prepared_batches)
    out = {
        "policy": policy,
        "job": [[{"op": op.key, "args": args} for op, args, _, _ in batch]
                for batch in prepared_batches],
        "batches": [
            {"batch": bi + 1,
             "steps": [{"step": i, "op": op.key, "policy": p, "line": _step_line(i, op, req, p),
                        "request": req.summary(), "notes": list(req.notes)}
                       for i, (op, _, req, p) in enumerate(batch, start=1)]}
            for bi, batch in enumerate(prepared_batches)
        ],
    }
    if policy == POLICY_NONE:
        return out
    issued = int(time.time()) if issued_at is None else _c.coerce_issued_at(issued_at)
    token = job_token(prepared_batches, ctx.requested_by, job_id, issued)
    step_count = sum(len(batch) for batch in prepared_batches)
    out.update(
        job_id=job_id, job_token=token, step_count=step_count, issued_at=issued,
        expires_at=_c.expires_at(issued, JOB_TOKEN_TTL_SECONDS),
        ttl_minutes=JOB_TOKEN_TTL_SECONDS // 60,
        # ADR 0008's two-factor proof (code in the reply + sender match)
        # always applies to a gated job, whatever an individual step's own
        # token_policy is — it's the job-level approval the email + reply
        # mechanism authenticates, not any one step.
        confirmation_code=_c.confirmation_code(token))
    run_operation("job_approval.create", {
        "job_id": job_id, "token": token, "issued_at": issued, "step_count": step_count,
        "plan": json.dumps(out["job"])}, ctx)
    return out


def _job_approval_create_prepare(args: dict, ctx: WriteContext) -> PreparedRequest:
    required = {"job_id", "token", "issued_at", "step_count", "plan"}
    if set(args) != required:
        raise _c.InvalidArgumentsError(
            f"job_approval.create needs exactly {sorted(required)}, got {sorted(args)}.")
    return PreparedRequest(
        transport="resource", doctype=_c.JOB_APPROVAL_DOCTYPE, action="create",
        body={"job_id": args["job_id"], "requested_by": ctx.requested_by, "token": args["token"],
              "issued_at": args["issued_at"], "step_count": args["step_count"],
              "status": "Pending", "plan": args["plan"]})


register_operation(Operation(
    key="job_approval.create", domain=None, summary="store a rendered job-level approval plan",
    prepare=_job_approval_create_prepare, token_policy=POLICY_NONE, allowlist_domain=None,
    owns=frozenset({(_c.JOB_APPROVAL_DOCTYPE, "create")}),
    # Internal plumbing prepare_job() calls right after computing the token
    # from the real render — never a direct CLI/agent entry point (that
    # would let a caller store an unrendered, fabricated plan + token).
    cli=False,
))


def _check_job_still_pending(req: PreparedRequest, args: dict, ctx: WriteContext) -> None:
    data = get_job_approval(req.name, ctx)
    if data.get("status") != "Pending":
        raise _c.PreconditionFailedError(
            f"job_approval.mark_approved: job '{req.name}' is not Pending (status="
            f"{data.get('status')!r}); refusing to approve twice or out of order.")


def _job_approval_mark_approved_prepare(args: dict, ctx: WriteContext) -> PreparedRequest:
    if set(args) != {"job_id"}:
        raise _c.InvalidArgumentsError("job_approval.mark_approved needs exactly {'job_id'}.")
    return PreparedRequest(transport="resource", doctype=_c.JOB_APPROVAL_DOCTYPE, action="update",
                           name=args["job_id"], body={"status": "Approved"})


register_operation(Operation(
    key="job_approval.mark_approved", domain=None,
    summary="mark a job-level approval plan Approved",
    prepare=_job_approval_mark_approved_prepare, token_policy=POLICY_NONE, allowlist_domain=None,
    owns=frozenset({(_c.JOB_APPROVAL_DOCTYPE, "update")}),
    preconditions=(_check_job_still_pending,),
    # The two-factor proof (code + sender match, ADR 0008) runs before this
    # is ever called (issue 05) — never a direct CLI/agent entry point.
    cli=False,
))


def get_job_approval(job_id: str, ctx: WriteContext) -> dict:
    """The stored "Qkeee Bot Job Approval" row for `job_id`, read through
    the same connector-plumbing bypass as _check_job_still_pending above
    (internal=True — a business Requester holds no direct permission on
    this bookkeeping doctype, ADR 0009). The one place both issue 04's
    email render and issue 05's reply-binding read the authoritative
    plan/token from — never an agent's own freeform args, which could
    claim a plan or token that was never actually rendered."""
    return _c.get_resource(ctx.tag, _c.JOB_APPROVAL_DOCTYPE, job_id, requested_by=ctx.requested_by,
                           internal=True, **ctx.audit_kwargs()).get("data") or {}


def render_stored_job(job_id: str, ctx: WriteContext) -> dict:
    """Rebuild prepare_job()'s human-readable `batches` (ADR 0008's full
    itemized plan) from the STORED plan, not from caller-supplied args —
    notify.job_approval_request's email must show exactly what job_token()
    actually bound. Uses each step's op.prepare() only (render=False, no
    I/O, same as _job_requests() itself) rather than re-rendering live
    facts a second time: the confirmation_code below is derived from the
    token stored at the original render, so the display must not drift
    from it."""
    data = get_job_approval(job_id, ctx)
    prepared_batches = _job_requests(json.loads(data["plan"]), ctx, render=False) if data else []
    issued_at = _c.coerce_issued_at(data.get("issued_at")) if data else 0
    return {
        "job_id": job_id, "status": data.get("status"), "requested_by": data.get("requested_by"),
        "step_count": int(data.get("step_count") or 0), "issued_at": issued_at,
        "expires_at": _c.expires_at(issued_at, JOB_TOKEN_TTL_SECONDS),
        "ttl_minutes": JOB_TOKEN_TTL_SECONDS // 60,
        "confirmation_code": _c.confirmation_code(data.get("token") or ""),
        "batches": [
            {"batch": bi + 1,
             "steps": [{"step": i, "op": op.key, "policy": p, "line": _step_line(i, op, req, p)}
                       for i, (op, _, req, p) in enumerate(batch, start=1)]}
            for bi, batch in enumerate(prepared_batches)],
    }


def verify_job_reply(job_id: str, reply_text: str, ctx: WriteContext) -> dict:
    """ADR 0008's two-factor proof that an inbound reply is the Requester's
    own approval of job `job_id`: (1) `reply_text` — the literal text of
    the reply, same discipline as user_confirmation_text — must contain the
    job's confirmation code, and (2) this turn's resolved sender
    (core/client.py session_identity(), the same gateway-authenticated
    identity `erp_discover(action="whoami")` reports) must equal the
    Requester the job was rendered for. Either alone is refused; both
    failing says both. Binds against the STORED job (issue 03/ADR 0009),
    never a token recomputed here, so a separate kanban-worker process
    (issue 06) can later trust the same row.

    Detection of WHICH job a given inbound turn replies to (matching this
    `job_id`) is left to the caller — the job id is in the approval
    email's subject (notify.job_approval_request), so no email-thread/
    Message-ID plumbing is needed; the Job Approval doctype stores no
    Message-ID (issue 03's schema), so thread-matching was not chosen."""
    if not job_id or not isinstance(job_id, str):
        raise _c.InvalidArgumentsError("verify_job_reply needs a non-empty job_id.")
    data = get_job_approval(job_id, ctx)
    if data.get("status") != "Pending":
        raise _c.PreconditionFailedError(
            f"verify_job_reply: job '{job_id}' is not Pending (status={data.get('status')!r}); "
            f"refusing to approve twice or out of order.")
    issued_at = _c.coerce_issued_at(data.get("issued_at"))
    if not _c.is_fresh(issued_at, JOB_TOKEN_TTL_SECONDS):
        raise _c.StaleConfirmationError(
            f"verify_job_reply: job '{job_id}' approval window expired at "
            f"{_c.expires_at(issued_at, JOB_TOKEN_TTL_SECONDS)} (ADR 0008's fixed 24h window from "
            f"issuance) — re-render the job and send a fresh approval email; there is no silent "
            f"extension.")
    expected_code = _c.confirmation_code(data.get("token") or "")
    code_ok = bool(reply_text) and expected_code in reply_text.upper()
    sender = (_c.session_identity().get("resolved_sender_email") or "").strip()
    sender_ok = bool(sender) and sender.lower() == (data.get("requested_by") or "").lower()
    if not (code_ok and sender_ok):
        failed = []
        if not code_ok:
            failed.append(f"the reply does not contain confirmation code {expected_code!r}")
        if not sender_ok:
            failed.append(
                f"the reply's resolved sender ({sender or 'none'}) does not match the "
                f"Requester this job was rendered for ({data.get('requested_by')!r})")
        raise _c.UnconfirmedByUserError(
            f"Refusing to approve job '{job_id}': " + " and ".join(failed) + ". ADR 0008 requires "
            f"BOTH the code and a matching sender; neither alone is an approval.")
    return run_operation("job_approval.mark_approved", {"job_id": job_id}, ctx)


def _verify_job_batch(job_id: str, job_batch_index: int, steps: list, ctx: WriteContext) -> tuple:
    """Execute side of a kanban child task's job-level binding (ADR 0008,
    issue 06): instead of a fresh render-then-chat-code round trip, this
    batch must be EXACTLY batch `job_batch_index` of job `job_id`'s stored,
    Approved plan (issue 03's store, marked Approved by verify_job_reply()
    above) — same steps, same order, unchanged; the job's 24h approval
    window (ADR 0008) still fresh. Returns (total_batches, prepared
    requests) — total_batches for the Audit Log label
    ("job <job_id> batch i/n step j/m", run_batch()'s job-approved branch).

    Verified the same way _verify_batch() verifies a chat-code batch: ONE
    hash compared once, not a hand-rolled per-step comparison. The
    submitted `steps` replace batch `job_batch_index` in a copy of the
    stored plan; job_token() over that spliced plan must equal the stored
    `token` exactly as it does at render (job_token()/prepare_job()).
    Reusing job_token() — rather than comparing each step's
    PreparedRequest pairwise — also binds `requested_by` and `job_id` into
    the check for free, the same way the stored token already does;
    recomputing it with the STORED `requested_by` (never ctx.requested_by,
    which is the executing kanban worker, not necessarily the Requester
    who was emailed) means this never demands a kanban worker impersonate
    that Requester to pass."""
    if not job_id or not isinstance(job_id, str):
        raise _c.InvalidArgumentsError("job_id must be a non-empty string.")
    if not isinstance(job_batch_index, int) or isinstance(job_batch_index, bool) or job_batch_index < 1:
        raise _c.InvalidArgumentsError("job_batch_index must be a positive integer (1-based).")
    data = get_job_approval(job_id, ctx)
    if data.get("status") != "Approved":
        raise _c.PreconditionFailedError(
            f"Refusing: job '{job_id}' is not Approved (status={data.get('status')!r}) — a kanban "
            f"child task may only consume an approved job's plan.")
    issued_at = _c.coerce_issued_at(data.get("issued_at"))
    if not _c.is_fresh(issued_at, JOB_TOKEN_TTL_SECONDS):
        raise _c.StaleConfirmationError(
            f"Refusing: job '{job_id}''s approval window expired at "
            f"{_c.expires_at(issued_at, JOB_TOKEN_TTL_SECONDS)} (ADR 0008's fixed 24h window from "
            f"issuance). Nothing was sent; a fresh approval email is needed — there is no silent "
            f"extension.")
    plan = json.loads(data.get("plan") or "[]")
    if not 1 <= job_batch_index <= len(plan):
        raise _c.InvalidArgumentsError(
            f"job_batch_index {job_batch_index} is out of range for job '{job_id}' "
            f"({len(plan)} batch(es)).")
    _check_batch_shape(steps)
    candidate_plan = list(plan)
    candidate_plan[job_batch_index - 1] = steps
    prepared_batches = _job_requests(candidate_plan, ctx, render=False)
    recomputed = job_token(prepared_batches, data.get("requested_by"), job_id, issued_at)
    if recomputed != data.get("token"):
        raise _c.TokenMismatchError(
            f"Refusing: this batch differs from batch {job_batch_index} of job '{job_id}''s "
            f"approved plan (a step added, removed, moved or changed — anywhere in the job, not "
            f"only this batch). Nothing was sent — the whole job needs a fresh approval email; "
            f"there is no partial re-approval (ADR 0008).")
    return len(plan), prepared_batches[job_batch_index - 1]


# ---------------------------------------------------------------------------
# Generic resource operations (one per domain, plus "unscoped.generic")
# ---------------------------------------------------------------------------

GENERIC_ARGS_HELP = {
    "doctype": "target DocType",
    "action": "create | update | submit | cancel | delete",
    "payload": "JSON object (create/update)",
    "name": "record name (update/submit/cancel/delete); on create, the new record's name for a "
            "doctype named by the user (autoname Prompt / naming rule \"Set by user\", e.g. "
            "Shift Type, Salary Structure)",
    "expected_modified": "submit/cancel/delete: the record's `modified` — filled by render",
    "staged_fields": "create/update: doc-extraction staged report (see schema_mapping.py)",
    "confirmed_mappings": "create/update: {candidate_key: live_fieldname} the user confirmed",
    "purchase_sourced_item": "Item create only: apply purchase-sourced defaults",
}


def _schema_map(ctx: WriteContext, doctype: str, payload: dict, notes: list,
                staged_fields=None, confirmed_mappings=None) -> dict:
    from .. import schema_mapping  # lazy (imports discover -> client)
    mapping = schema_mapping.map_payload_for_write(
        ctx.tag, doctype, payload, requested_by=ctx.requested_by,
        staged_fields=staged_fields, confirmed_mappings=confirmed_mappings, **ctx.audit_kwargs())
    if mapping["status"] == "unavailable":
        notes.append(f"schema-first field mapping unavailable for '{doctype}' "
                     f"({mapping['detail']}) — payload used as given, unmapped.")
        return dict(payload or {})
    if mapping["suggested_mappings"]:
        notes.append(f"{len(mapping['suggested_mappings'])} field(s) on '{doctype}' matched a live "
                     f"schema field only via a synonym hint, NOT applied without confirmation: "
                     f"{mapping['suggested_mappings']!r} — re-render with confirmed_mappings for "
                     f"the ones the user confirms.")
    if mapping["unmatched"]:
        notes.append(f"field(s) with no matching live schema field on '{doctype}', dropped: "
                     f"{mapping['unmatched']!r}")
    if mapping["status"] == "high_risk":
        raise _c.PreconditionFailedError(
            f"Refusing to write '{doctype}': staged field(s) {mapping['high_risk']!r} are BOTH "
            f"low-confidence AND unmatched against the live schema. Resolve with the user "
            f"(correct the value, or confirm the right live fieldname) first."
        )
    return mapping["payload"]


def prepare_generic(args: dict, ctx: WriteContext, *, extra_keys=()) -> PreparedRequest:
    allowed_keys = set(GENERIC_ARGS_HELP) | set(extra_keys)
    unknown = set(args) - allowed_keys
    if unknown:
        raise _c.ConnectorError(f"Unknown argument(s) {sorted(unknown)}; expected {sorted(allowed_keys)}.")
    doctype, action = args.get("doctype"), args.get("action")
    if not doctype or action not in RESOURCE_ACTIONS:
        raise _c.ConnectorError(f"generic write needs doctype and action in {RESOURCE_ACTIONS}.")
    name = args.get("name")
    payload = args.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise _c.ConnectorError("payload must be a JSON object.")
    bound = {}
    if action in ("create", "update"):
        if action == "update" and not name:
            raise _c.ConnectorError("update requires a record 'name'.")
        if args.get("purchase_sourced_item") and not (doctype == "Item" and action == "create"):
            raise _c.ConnectorError("purchase_sourced_item only applies to Item create.")
        payload = dict(payload or {})
    else:
        if args.get("staged_fields") or args.get("confirmed_mappings") or args.get("purchase_sourced_item"):
            raise _c.ConnectorError("staged_fields/confirmed_mappings/purchase_sourced_item only "
                                    "apply to create/update.")
        if not name:
            raise _c.ConnectorError(f"{action} requires a record 'name'.")
        if payload:
            raise _c.ConnectorError(f"{action} takes no payload.")
        payload = None
        bound["expected_modified"] = args.get("expected_modified")
    # A create's `name` is the new record's name (enrich_generic puts it in
    # the body), never a record to check or update.
    return PreparedRequest(transport="resource", doctype=doctype, action=action,
                           name=None if action == "create" else name, body=payload, bound=bound)


def enrich_generic(req: PreparedRequest, args: dict, ctx: WriteContext) -> None:
    """create/update: purchase-sourced Item defaults, then schema-first
    mapping against the live doctype schema (W05: done here, identically
    at render and execute)."""
    if req.action not in ("create", "update"):
        return
    payload = req.body or {}
    if args.get("purchase_sourced_item"):
        from ..item_write_helpers import (BareStandardRateOnPurchaseSourcedItemError,
                                        apply_purchase_sourced_item_defaults)
        try:
            payload = apply_purchase_sourced_item_defaults(payload)
        except BareStandardRateOnPurchaseSourcedItemError as e:
            raise _c.PreconditionFailedError(str(e)) from e
    new_name = (args.get("name") or payload.get("__newname") or payload.get("name")
                if req.action == "create" else None)
    req.body = _schema_map(ctx, req.doctype, payload, req.notes,
                           staged_fields=args.get("staged_fields"),
                           confirmed_mappings=args.get("confirmed_mappings"))
    if new_name:
        # `name` is not a schema field, so the mapping drops it. A doctype
        # named by the user (autoname Prompt) needs it: Frappe keeps a
        # given `name` there and reads `__newname` when it is empty
        # (dev-erp 2026-10-22: "Please set the document name" on Shift
        # Type, Employee Grade, Salary Structure). Other naming rules
        # replace it, so sending it is harmless.
        req.body = dict(req.body or {}, name=str(new_name), __newname=str(new_name))


def _generic_policy(token_actions):
    def policy(req: PreparedRequest) -> str:
        return POLICY_TOKEN_USER_CODE if req.action in token_actions else POLICY_NONE
    return policy


def _live_modified(ctx: WriteContext, doctype: str, name: str):
    try:
        data = _c.get_resource(ctx.tag, doctype, name, strip_noise=False,
                               requested_by=ctx.requested_by, **ctx.audit_kwargs()).get("data") or {}
    except _c.GateRefusal:
        raise
    except _c.ConnectorError as e:
        if "(404)" in str(e):
            # A gate refusal, not an ERPNext rejection: nothing was sent.
            raise _c.PreconditionFailedError(
                f"Refusing: {doctype} {name!r} does not exist (it may have been deleted or "
                f"renamed since render, or the args were changed) — re-render.") from e
        raise
    return data.get("modified")


def render_expected_modified(args: dict, ctx: WriteContext) -> dict:
    """render_defaults for generic ops: a finalizing action binds the
    record's live `modified` at render time (W10)."""
    if args.get("action") in FINALIZING_ACTIONS and args.get("name") and not args.get("expected_modified"):
        args = dict(args, expected_modified=_live_modified(ctx, args["doctype"], args["name"]))
    return args


def check_not_modified_since_render(req: PreparedRequest, args: dict, ctx: WriteContext) -> None:
    """Precondition for finalizing actions: the record must not have
    changed since the user confirmed it (W10, generalizing the old
    fixed_assets-only concurrency check)."""
    if req.transport != "resource" or req.action not in FINALIZING_ACTIONS:
        return
    expected = req.bound.get("expected_modified")
    if not expected:
        raise _c.PreconditionFailedError(
            f"Refusing {req.action} on '{req.doctype}' '{req.name}': expected_modified is missing "
            f"— render it first (erp_execute_write phase render fills it from the live record)."
        )
    current = _live_modified(ctx, req.doctype, req.name)
    if current != expected:
        raise _c.PreconditionFailedError(
            f"Refusing {req.action} on '{req.doctype}' '{req.name}': it changed since the user "
            f"confirmed it (expected modified={expected!r}, now {current!r}) — re-render, "
            f"re-review and reconfirm."
        )


def generic_operation(domain: Optional[str], *, token_actions=FINALIZING_ACTIONS, refuse=(),
                      preconditions=(), post=None, prepare=None, enrich=None,
                      extra_args_help=None, summary=None, credential: str = "bot",
                      example_args: dict = None) -> Operation:
    key = f"{domain}.generic" if domain else "unscoped.generic"
    return register_operation(Operation(
        key=key, domain=domain,
        summary=summary or (f"plain {domain} write (create/update drafts; submit/cancel/delete "
                            f"token-gated)" if domain else
                            "write to a doctype no domain owns — every action token-gated"),
        prepare=prepare or prepare_generic,
        token_policy=_generic_policy(token_actions), credential=credential,
        allowlist_domain=domain if domain else None,
        generic=True, refuse=tuple(refuse),
        preconditions=(check_not_modified_since_render,) + tuple(preconditions),
        render_defaults=render_expected_modified, post=post, enrich=enrich or enrich_generic,
        args_help=dict(GENERIC_ARGS_HELP, **(extra_args_help or {})),
        example_args=dict(example_args or {}),
    ))


# The domain-less operation: every action needs the full confirmation.
generic_operation(None, token_actions=frozenset(RESOURCE_ACTIONS), example_args={
    "doctype": "Item", "action": "create", "purchase_sourced_item": True,
    "payload": {"item_code": "WIDGET-01", "item_name": "Widget", "item_group": "Products",
                "stock_uom": "Nos"}})


# ---------------------------------------------------------------------------
# Keyword-style convenience for generic operations
# ---------------------------------------------------------------------------

_CTX_KEYS = {f.name for f in dataclasses.fields(WriteContext)} - {"tag", "mode", "requested_by",
                                                                  "batch_approval"}



def call_generic(op_key: str, tag: str, doctype: str, action: str, *, payload: dict = None,
                 name: str = None, mode: str = "read-only", requested_by: str = None,
                 extra_args: dict = None, **kwargs) -> dict:
    """Run a generic operation (`<domain>.generic` / `unscoped.generic`)
    from keyword arguments instead of an args dict + WriteContext:
    `call_generic("sales.generic", tag, "Sales Order", "create",
    payload={...}, mode=..., requested_by=..., session_id=..., ...)`.
    Context keywords are WriteContext's fields; domain-specific args (e.g.
    procurement's kyc) go in `extra_args`. Same pipeline, same gates as
    run_operation() — this only changes how the call is spelled."""
    ctx_kwargs = {k: kwargs.pop(k) for k in list(kwargs) if k in _CTX_KEYS}
    expected_modified = kwargs.pop("expected_modified", None)
    if kwargs:
        raise TypeError(f"unexpected keyword argument(s) {sorted(kwargs)}")
    args = {"doctype": doctype, "action": action}
    if payload is not None:
        args["payload"] = payload
    if name is not None:
        args["name"] = name
    if expected_modified is not None:
        args["expected_modified"] = expected_modified
    args.update({k: v for k, v in (extra_args or {}).items() if v is not None})
    ctx = WriteContext(tag=tag, mode=mode, requested_by=requested_by, **ctx_kwargs)
    return run_operation(op_key, args, ctx)


# ---------------------------------------------------------------------------
# Helpers shared by bespoke operations
# ---------------------------------------------------------------------------

def require_args(args: dict, required, allowed=()) -> None:
    missing = [k for k in required if args.get(k) in (None, "", [])]
    if missing:
        raise _c.ConnectorError(f"missing required argument(s): {missing}")
    unknown = set(args) - set(required) - set(allowed)
    if unknown:
        raise _c.ConnectorError(f"unknown argument(s) {sorted(unknown)}; allowed: "
                                f"{sorted(set(required) | set(allowed))}")


_PRIVATE_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")


def check_public_https_url(url: str) -> None:
    """SSRF guard for an outbound destination: https, a hostname that isn't
    obviously internal, and not a private/loopback/link-local/reserved IP
    literal. Literal checks only — the ERPNext server resolves the name
    itself, so a hostname that RESOLVES to a private address is a residual
    risk this can't see; the human confirmation is the backstop."""
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise _c.PreconditionFailedError(f"Refusing webhook URL {url!r}: must be an https:// URL.")
    host = parsed.hostname.lower()
    if host in ("localhost",) or host.endswith(_PRIVATE_HOST_SUFFIXES) or "." not in host:
        raise _c.PreconditionFailedError(f"Refusing webhook URL {url!r}: internal host name.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global:
        raise _c.PreconditionFailedError(f"Refusing webhook URL {url!r}: non-public IP address.")
