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
 7. precondition operation-specific live checks (concurrency, KYC, ...)
 8. token        per policy: presence, freshness, match against
                 operation_token() over the prepared request, and (policy
                 "token+user_code") the user's own reply containing
                 confirmation_code(token)
 9. RBAC         _validate_prod_requester(for_write=True), never exempt
10. audit start  with the full WriteContext
11. send         any exception -> audit Failure, re-raised as ConnectorError
12. audit finish then post hooks (best-effort ones never fail the write)
13. return       result always carries `_audit_log_status`

## Render

prepare_only() runs steps 1-2 (plus the operation's render_defaults, which
fill live facts such as `expected_modified`) and returns the prepared
request, the full args to pass back unchanged, and the token +
confirmation code. `confirm_token.py render` is its CLI.
"""

import dataclasses
import ipaddress
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_CORE_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as _c  # noqa: E402  (module object: patches in tests apply)

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

    def audit_kwargs(self) -> dict:
        return dict(session_id=self.session_id, domain_code=self.domain_code,
                    channel=self.channel, channel_metadata=self.channel_metadata,
                    prompt_summary=self.prompt_summary, latest_prompt=self.latest_prompt)

    def without_confirmation(self) -> "WriteContext":
        return dataclasses.replace(self, confirmation_token=None, issued_at=None,
                                   user_confirmation_text=None)


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
    post: Optional[Callable] = None      # (result, req, args, ctx, hooks) -> result
    on_failure: Optional[Callable] = None  # (exc, req, args, ctx) -> str | None
    skip_comment: bool = False
    comment_label: Optional[str] = None
    audit: bool = True                   # False only for init_bot provisioning (logs itself)
    args_help: dict = field(default_factory=dict)
    # A minimal, valid argument set — printed by `execute_write.py
    # --list-ops` as the operation's worked example, and driven through
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
    if not ctx.confirmation_token or ctx.issued_at is None:
        raise _c.ConfirmationRequiredError(
            f"Refusing {req.action} on '{req.doctype}' ({op.key}): a confirmation_token + "
            f"issued_at are required. Render it first — `confirm_token.py render --op {op.key} "
            f"--args ...` — show the rendered request and confirmation code to the user, and "
            f"pass the printed args/token/issued_at back unchanged. Never hand-construct one."
        )
    issued = _c.coerce_issued_at(ctx.issued_at)
    if not _c.is_fresh(issued):
        raise _c.StaleConfirmationError(
            f"The confirmation for {op.key} on '{req.doctype}' has expired or its issued_at is "
            f"implausible — re-render against current data and reconfirm before retrying."
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
    "Version", "Deleted Document", "Comment", _c.AUDIT_LOG_DOCTYPE,
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
            f"use '{owner}' (see `execute_write.py --list-ops`)."
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


def _check_mode_and_requester(req: PreparedRequest, ctx: WriteContext) -> None:
    if ctx.mode != "read-write":
        raise _c.ReadOnlyModeError(
            f"Refusing {req.action} on '{req.doctype}': qkeee_erp.mode is '{ctx.mode}', not "
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
                                     **ctx.audit_kwargs())
        return _send(self._op, req, ctx, user_approved=False, skip_comment=True)


# ---------------------------------------------------------------------------
# Send (steps 10-13)
# ---------------------------------------------------------------------------

def _send(op: Operation, req: PreparedRequest, ctx: WriteContext, *, user_approved: bool,
          skip_comment: bool = None, args: dict = None) -> dict:
    audit_cfg = _c.get_env_config(ctx.tag)
    send_cfg = audit_cfg if op.credential == "bot" else _c.get_env_config(ctx.tag, credential=op.credential)
    skip_comment = op.skip_comment if skip_comment is None else skip_comment
    label = op.comment_label or (f"qkeee-erp-associate/{op.domain}" if op.domain else _c.SKILL_LABEL)
    approval_note = ctx.approval_note or op.summary

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
    op = get_operation(op_key)                                    # 1
    args = dict(args or {})
    req = _prepare(op, args, ctx)                                 # 2
    _check_mode_and_requester(req, ctx)                           # 3, 4
    _check_allowlist(op, req)                                     # 5
    _check_ownership(op, req)                                     # 6
    if op.enrich:                                                 # 6b
        op.enrich(req, args, ctx)
    for check in op.preconditions:                                # 7
        check(req, args, ctx)
    verified = _verify_token(op, op.policy_for(req), req, ctx)    # 8
    _c._validate_prod_requester(                                  # 9
        ctx.tag, ctx.requested_by, req.rbac_doctype, req.rbac_ptype, docname=req.name,
        for_write=True, domain=op.domain, **ctx.audit_kwargs())
    result = _send(op, req, ctx, user_approved=verified or ctx.user_approved, args=args)  # 10-12
    if op.post:
        created = None
        if req.transport == "resource" and req.action == "create" and isinstance(result, dict):
            created = (result.get("data") or {}).get("name")
        result = op.post(result, req, args, ctx, PostHooks(op, req, ctx, created))
    if req.notes and isinstance(result, dict):
        result["_notes"] = list(req.notes)
    return result                                                 # 13


def prepare_only(op_key: str, args: dict, ctx: WriteContext, issued_at: int = None) -> dict:
    """Render: steps 1-2 plus render_defaults; no gate, no write. The
    returned `args` are what the execute step must be given back
    unchanged — they include live facts the render filled in."""
    op = get_operation(op_key)
    args = dict(args or {})
    if op.render_defaults:
        args = op.render_defaults(args, ctx)
    req = _prepare(op, args, ctx)
    # Refuse early: no point rendering a request the gates will refuse.
    _check_allowlist(op, req)
    _check_ownership(op, req)
    if op.enrich:
        op.enrich(req, args, ctx)
    policy = op.policy_for(req)
    out = {"op": op.key, "summary": op.summary, "policy": policy, "args": args,
           "request": req.summary(), "notes": list(req.notes)}
    if policy != POLICY_NONE:
        issued = int(time.time()) if issued_at is None else _c.coerce_issued_at(issued_at)
        token = operation_token(op.key, req, ctx.requested_by, issued)
        out.update(confirmation_token=token, issued_at=issued)
        if policy == POLICY_TOKEN_USER_CODE:
            out["confirmation_code"] = _c.confirmation_code(token)
    return out


def requires_confirmation(op_key: str, args: dict) -> bool:
    """Cheap, I/O-free answer to "will this need a token?" — for the CLI's
    mandatory-context check, before prepare() does any live read."""
    op = get_operation(op_key)
    if not callable(op.token_policy):
        return op.token_policy != POLICY_NONE
    action = (args or {}).get("action")
    probe = PreparedRequest(transport="resource", doctype="?", action=action or "submit")
    return op.policy_for(probe) != POLICY_NONE


# ---------------------------------------------------------------------------
# Generic resource operations (one per domain, plus "unscoped.generic")
# ---------------------------------------------------------------------------

GENERIC_ARGS_HELP = {
    "doctype": "target DocType",
    "action": "create | update | submit | cancel | delete",
    "payload": "JSON object (create/update)",
    "name": "record name (update/submit/cancel/delete)",
    "expected_modified": "submit/cancel/delete: the record's `modified` — filled by render",
    "staged_fields": "create/update: doc-extraction staged report (see schema_mapping.py)",
    "confirmed_mappings": "create/update: {candidate_key: live_fieldname} the user confirmed",
    "purchase_sourced_item": "Item create only: apply purchase-sourced defaults",
}


def _schema_map(ctx: WriteContext, doctype: str, payload: dict, notes: list,
                staged_fields=None, confirmed_mappings=None) -> dict:
    import schema_mapping  # scripts/ root; lazy (imports discover -> client)
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
    return PreparedRequest(transport="resource", doctype=doctype, action=action, name=name,
                           body=payload, bound=bound)


def enrich_generic(req: PreparedRequest, args: dict, ctx: WriteContext) -> None:
    """create/update: purchase-sourced Item defaults, then schema-first
    mapping against the live doctype schema (W05: done here, identically
    at render and execute)."""
    if req.action not in ("create", "update"):
        return
    payload = req.body or {}
    if args.get("purchase_sourced_item"):
        from item_write_helpers import (BareStandardRateOnPurchaseSourcedItemError,
                                        apply_purchase_sourced_item_defaults)
        try:
            payload = apply_purchase_sourced_item_defaults(payload)
        except BareStandardRateOnPurchaseSourcedItemError as e:
            raise _c.PreconditionFailedError(str(e)) from e
    req.body = _schema_map(ctx, req.doctype, payload, req.notes,
                           staged_fields=args.get("staged_fields"),
                           confirmed_mappings=args.get("confirmed_mappings"))


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
            f"— render it first (confirm_token.py render fills it from the live record)."
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

_CTX_KEYS = {f.name for f in dataclasses.fields(WriteContext)} - {"tag", "mode", "requested_by"}



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
