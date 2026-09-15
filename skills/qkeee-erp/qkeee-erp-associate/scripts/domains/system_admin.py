#!/usr/bin/env python3
"""
qkeee-erp-associate — system-admin domain (Users, roles, permissions).

This is the widest-blast-radius, heaviest-GRC domain, and it carries the
most business logic of any domain module: _record_attribution_comment(),
destructive_mutate(), get_roles_and_doctypes(), get_permissions(),
call_permission_manager(), create_user(), gated_config_mutate(), and
get_scheduler_status().

permission_change_token(), destructive_action_token(),
elevated_user_token(), and config_change_token() live here (not in
core/confirm_token.py — see that module's docstring for why
capability-specific token constructors stay with their owning domain)
alongside their consumers.

ALLOWED_WRITE_DOCTYPES covers the doctypes this domain's business logic
actually targets: User (create_user/destructive_mutate), Role, Custom
Field/Property Setter (customization), Webhook/Workflow
(gated_config_mutate's two CONFIG_CHANGE_KINDS). destructive_mutate()/
call_permission_manager()/gated_config_mutate() route through
mutate_resource(domain="system_admin") for their underlying write (or,
for call_permission_manager, a whitelisted-method RPC outside
mutate_resource() entirely, same as fixed_assets.call_whitelisted_method())
— see each function's docstring.
"""

import hashlib
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core.client import (
    ConnectorError, ReadOnlyModeError, MissingRequesterError, StaleConfirmationError,
    SKILL_LABEL, get_env_config, mutate_resource, record_comment,
    record_audit_log_start, record_audit_log_finish, _request,
)
from core.confirm_token import is_fresh

DOMAIN_NAME = "system_admin"

ALLOWED_WRITE_DOCTYPES = (
    "User",
    "Role",
    "Custom Field",
    "Property Setter",
    "Webhook",
    "Workflow",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# The single highest-privilege grant this domain can make.
ELEVATED_ROLES = {"System Manager", "Administrator"}

# The two moderate-but-real-risk config writes gated by gated_config_mutate().
CONFIG_CHANGE_KINDS = {"create_webhook", "toggle_workflow"}


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="system_admin"). Prefer the more
    specific gated wrappers below (destructive_mutate/create_user/
    gated_config_mutate) for anything they cover — this is the fallback
    for plain create/update on an already-allowlisted doctype."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)


# --------------------------------------------------------------------------
# Token constructors — see module docstring's "SCOPE NOTE on confirm_token.py"
# --------------------------------------------------------------------------

def _compute_token(**fields) -> str:
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def permission_change_token(action: str, doctype: str, role: str, permlevel: int,
                             ptype: str = "", value=None, issued_at: int = None) -> str:
    """action: 'add' | 'update' | 'remove' | 'reset'."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return _compute_token(
        kind="permission_change", action=action, doctype=doctype, role=role,
        permlevel=int(permlevel), ptype=ptype, value=value, issued_at=int(issued_at),
    )


def destructive_action_token(action: str, target_doctype: str, target_name: str,
                              reason: str, issued_at: int = None) -> str:
    """action e.g. 'disable_user' | 'delete_user' | 'delete_custom_field' |
    'delete_property_setter' | 'delete_webhook' | 'delete_workflow'."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return _compute_token(
        kind="destructive_action", action=action, target_doctype=target_doctype,
        target_name=target_name, reason=reason, issued_at=int(issued_at),
    )


def elevated_user_token(email: str, roles: list, issued_at: int = None) -> str:
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return _compute_token(kind="elevated_user", email=email, roles=sorted(roles), issued_at=int(issued_at))


def config_change_token(kind: str, doctype: str, identifier: str, reason: str,
                         issued_at: int = None) -> str:
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return _compute_token(
        kind="config_change", change_kind=kind, doctype=doctype, identifier=identifier,
        reason=reason, issued_at=int(issued_at),
    )


# --------------------------------------------------------------------------
# Genuine domain-specific business logic beyond the shared core connector
# --------------------------------------------------------------------------

def _record_attribution_comment(cfg: dict, doctype: str, name: str, action_label: str,
                                 requested_by: str, reason: str = None) -> None:
    """Standard audit-comment shape for this domain's gated write paths
    (destructive_mutate/gated_config_mutate): always names the requester,
    and appends the stated reason when one was given."""
    content = f"[{SKILL_LABEL}/{DOMAIN_NAME}] {action_label} — requested by {requested_by}, applied via qkeee-erp bot."
    if reason:
        content += f" Reason: {reason}"
    record_comment(cfg, doctype, name, content)


def destructive_mutate(tag: str, doctype: str, action: str, name: str, reason: str,
                        mode: str = "read-only", confirmation_token: str = None,
                        issued_at: int = None, payload: dict = None,
                        requested_by: str = None) -> dict:
    """Gated wrapper around mutate_resource() for this domain's highest-
    blast-radius single-record actions: disabling/deleting a User, or
    deleting a Custom Field / Property Setter / Webhook / Workflow.
    `action` is "update" (disable, User only) or "delete".

    Requires `reason`, `requested_by`, and a `confirmation_token` +
    `issued_at` matching what a render script computed from the same
    (action, doctype, name, reason, issued_at) facts, within 15 minutes of
    now — the call is refused without a fresh match, same double-confirm
    code-level backstop as the fixed_assets domain's depreciation-run/
    disposal gate.

    Best-effort: on success, writes `reason` + `requested_by` onto the
    affected record as a single ERPNext Comment via
    _record_attribution_comment() (for delete, before the delete — a
    deleted record can't be commented on afterward). Calls
    mutate_resource() with skip_comment=True so this doesn't also post
    mutate_resource's own plain comment on top, and with
    domain="system_admin" so the doctype is still checked against
    ALLOWED_WRITE_DOCTYPES like any other write.
    """
    if action == "update":
        if doctype != "User":
            raise ConnectorError(
                "destructive_mutate 'update' is only defined for User (disable) — got "
                f"doctype={doctype!r}. Every other supported doctype is delete-only here."
            )
        action_key = "disable_user"
    elif action == "delete":
        action_key = f"delete_{doctype.lower().replace(' ', '_')}"
    else:
        raise ConnectorError(f"destructive_mutate only supports 'update' (disable) or 'delete', got {action!r}.")

    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing {action} on '{doctype}' '{name}': qkeee_erp.mode is '{mode}', not "
            f"'read-write'. Switch modes explicitly if this write is intended."
        )
    if not reason:
        raise ConnectorError("destructive_mutate requires a non-empty reason.")
    if not requested_by:
        raise MissingRequesterError(
            "Refusing destructive_mutate: requested_by is missing. There is no config "
            "default — resolve the requester's ERPNext user id/email from the live "
            "inbound channel identity and pass it explicitly via requested_by= on this call."
        )
    if not confirmation_token or issued_at is None:
        raise ConnectorError(
            "destructive_mutate requires confirmation_token + issued_at — render the "
            "double-confirm output first and pass its exact token and issued_at here."
        )
    if not is_fresh(int(issued_at)):
        raise StaleConfirmationError(
            "This confirmation has expired or its issued_at is implausible — re-render "
            "the confirmation against current data and reconfirm before retrying."
        )
    expected = destructive_action_token(action_key, doctype, name, reason, int(issued_at))
    if confirmation_token != expected:
        raise ConnectorError(
            "confirmation_token does not match the (action, doctype, name, reason, issued_at) "
            "facts — re-render the confirmation against the current data and use that token."
        )

    cfg = get_env_config(tag)
    if action == "delete":
        _record_attribution_comment(cfg, doctype, name, "deleted", requested_by, reason)
        return mutate_resource(tag, doctype, action, payload=payload, name=name, mode=mode,
                                requested_by=requested_by, skip_comment=True, domain=DOMAIN_NAME,
                                user_approved=True, approval_note=f"destructive_mutate: {reason}")

    result = mutate_resource(tag, doctype, action, payload=payload, name=name, mode=mode,
                              requested_by=requested_by, skip_comment=True, domain=DOMAIN_NAME,
                              user_approved=True, approval_note=f"destructive_mutate: {reason}")
    _record_attribution_comment(cfg, doctype, name, "disabled", requested_by, reason)
    return result


PERMISSION_MANAGER_METHODS = {
    "get_roles_and_doctypes": "/api/method/frappe.core.page.permission_manager.permission_manager.get_roles_and_doctypes",
    "get_permissions": "/api/method/frappe.core.page.permission_manager.permission_manager.get_permissions",
    "add": "/api/method/frappe.core.page.permission_manager.permission_manager.add",
    "update": "/api/method/frappe.core.page.permission_manager.permission_manager.update",
    "remove": "/api/method/frappe.core.page.permission_manager.permission_manager.remove",
    "reset": "/api/method/frappe.core.page.permission_manager.permission_manager.reset",
}

# add/update/remove/reset all change what a role can do — every one of
# them carries this domain's double-confirm non-negotiable. get_* are
# read-only lookups and are never token-gated.
TOKEN_REQUIRED_PERMISSION_ACTIONS = {"add", "update", "remove", "reset"}


def get_roles_and_doctypes(tag: str) -> dict:
    """Read-only: the full role list + doctype list the Role Permission
    Manager page itself uses. Always allowed regardless of mode."""
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", PERMISSION_MANAGER_METHODS["get_roles_and_doctypes"])
    return result.get("message", {})


def get_permissions(tag: str, doctype: str) -> list:
    """Read-only: every permission row (standard DocPerm rows merged with
    any Custom DocPerm override rows) for a DocType, exactly as the Role
    Permission Manager page displays them. Querying DocPerm directly via
    query_resource() fails with a PermissionError — this whitelisted
    method is the only working read path for a DocType's permission
    matrix."""
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", PERMISSION_MANAGER_METHODS["get_permissions"], params={"doctype": doctype})
    return result.get("message", [])


def call_permission_manager(tag: str, action: str, doctype: str, role: str, permlevel: int,
                             ptype: str = None, value=None, mode: str = "read-only",
                             confirmation_token: str = None, issued_at: int = None,
                             requested_by: str = None, *, session_id: str = None,
                             domain_code: str = None) -> dict:
    """Call the Role Permission Manager's add/update/remove/reset
    whitelisted methods.

    action: "add" (role, permlevel — creates a bare new perm row with no
      rights set yet), "update" (doctype, role, permlevel, ptype, value —
      flips one specific right, e.g. ptype="write", value=1), "remove"
      (doctype, role, permlevel — deletes that row entirely), "reset"
      (doctype — wipes ALL custom overrides for the doctype back to
      shipped defaults; the single most blast-radius-heavy call in this
      domain, always requires a token).

    All four require mode == "read-write", `requested_by`, AND a
    confirmation_token matching a render script's output for these exact
    facts — no permission change reaches ERPNext without all three. No
    audit Comment is posted here: a permission row (DocPerm/Custom
    DocPerm) isn't a document instance with its own timeline, so there's
    no natural record to attach one to.

    KNOWN GAP (see references/domains/system-admin.md): `has_permission`'s
    `user=` parameter honoring is unresolved — this carries into the
    universal RBAC check too, and needs a live test before this is relied
    on as a per-requester gate.

    This RPC shape doesn't fit mutate_resource()'s create/update/submit/
    cancel signature and so bypasses it (and its ALLOWED_WRITE_DOCTYPES
    check) entirely — audited directly here (two-phase
    Attempted -> Success/Failure), same as mutate_resource()'s own path.
    """
    if action not in PERMISSION_MANAGER_METHODS:
        raise ConnectorError(f"Unknown permission_manager action '{action}'.")
    if action != "reset" and not role:
        raise ConnectorError(f"permission {action} requires a role.")
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing permission {action} on '{doctype}'/'{role}': qkeee_erp.mode is '{mode}', "
            f"not 'read-write'. Switch modes explicitly if this write is intended."
        )
    if not requested_by:
        raise MissingRequesterError(
            f"Refusing permission {action} on '{doctype}'/'{role}': requested_by is missing. "
            f"There is no config default — resolve the requester's ERPNext user id/email "
            f"from the live inbound channel identity and pass it explicitly via requested_by= "
            f"on this call."
        )
    if action in TOKEN_REQUIRED_PERMISSION_ACTIONS:
        if not confirmation_token or issued_at is None:
            raise ConnectorError(
                f"permission {action} requires confirmation_token + issued_at — render the "
                f"double-confirm output first and pass its exact token and issued_at here."
            )
        if not is_fresh(int(issued_at)):
            raise StaleConfirmationError(
                "This confirmation has expired or its issued_at is implausible — re-render "
                "and reconfirm before retrying."
            )
        expected = permission_change_token(action, doctype, role, permlevel, ptype or "", value, int(issued_at))
        if confirmation_token != expected:
            raise ConnectorError(
                "confirmation_token does not match these permission-change facts — re-render "
                "the confirmation against the current data and use that token."
            )

    cfg = get_env_config(tag)
    body = {"role": role, "permlevel": permlevel}
    if action == "add":
        body["parent"] = doctype
    else:
        body["doctype"] = doctype
    if action == "update":
        body["ptype"] = ptype
        body["value"] = value
        body["if_owner"] = 0
    if action == "reset":
        body = {"doctype": doctype}

    reference_name = f"{role or ''}@permlevel{permlevel}"
    audit_log_name = record_audit_log_start(
        cfg, action=f"Permission {action.capitalize()}", doctype=doctype, name=reference_name,
        requested_by=requested_by, session_id=session_id, domain_code=domain_code,
        user_approved=True, approval_note="call_permission_manager: double-confirm token verified",
    )
    try:
        result = _request(cfg, "POST", PERMISSION_MANAGER_METHODS[action], payload=body)
    except ConnectorError as e:
        record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=str(e))
        raise
    record_audit_log_finish(cfg, audit_log_name, status="Success", reference_name=reference_name)
    return result


def create_user(tag: str, email: str, first_name: str, roles: list, mode: str = "read-only",
                 send_welcome_email: bool = False, elevated_confirmation_token: str = None,
                 issued_at: int = None, requested_by: str = None) -> dict:
    """User creation & role assignment. If `roles` contains an elevated
    role (System Manager / Administrator — the single highest-privilege
    grant this domain can make), requires elevated_confirmation_token +
    issued_at matching a render script's output, fresh within 15 minutes
    — the same code-level backstop permission changes and destructive
    actions get. Non-elevated role grants are unaffected — still a single
    confirm, no token required.

    Delegates to mutate_resource(domain="system_admin") for the actual
    create, so it inherits both Qkeee Bot Audit Log logging and the
    ALLOWED_WRITE_DOCTYPES check automatically.
    """
    elevated = sorted(set(roles) & ELEVATED_ROLES)
    if elevated:
        if not elevated_confirmation_token or issued_at is None:
            raise ConnectorError(
                f"Creating a user with elevated role(s) ({', '.join(elevated)}) requires "
                f"elevated_confirmation_token + issued_at — render the draft with "
                f"elevated_roles_acknowledged=true first and pass its exact token and "
                f"issued_at here."
            )
        if not is_fresh(int(issued_at)):
            raise StaleConfirmationError(
                "This elevated-role confirmation has expired or its issued_at is implausible "
                "— re-render and reconfirm before retrying."
            )
        expected = elevated_user_token(email, roles, int(issued_at))
        if elevated_confirmation_token != expected:
            raise ConnectorError(
                "elevated_confirmation_token does not match the (email, roles) facts — "
                "re-render the draft against the current request and use that exact token."
            )

    payload = {
        "email": email,
        "first_name": first_name,
        "send_welcome_email": int(bool(send_welcome_email)),
        "roles": [{"role": r} for r in roles],
    }
    return mutate_resource(tag, "User", "create", payload=payload, mode=mode, requested_by=requested_by,
                            domain=DOMAIN_NAME, user_approved=True,
                            approval_note="create_user" + (" (elevated role)" if elevated else ""))


def gated_config_mutate(tag: str, kind: str, doctype: str, identifier: str, reason: str,
                         action: str, name: str = None, payload: dict = None,
                         mode: str = "read-only", confirmation_token: str = None,
                         issued_at: int = None, requested_by: str = None) -> dict:
    """Token-gated wrapper for the two moderate-but-real-risk config
    writes: kind='create_webhook' (an outbound data destination — a real
    SSRF/exfiltration surface) and kind='toggle_workflow' (can halt every
    in-flight approval on that document type). `identifier` is the
    webhook's request_url or the workflow's document_type — whatever was
    shown in the render step.

    Delegates to mutate_resource(domain="system_admin") for the actual
    write, so it inherits both Qkeee Bot Audit Log logging and the
    ALLOWED_WRITE_DOCTYPES check automatically.
    """
    if kind not in CONFIG_CHANGE_KINDS:
        raise ConnectorError(f"Unknown config-change kind {kind!r}. Expected one of {CONFIG_CHANGE_KINDS}.")
    if action not in ("create", "update"):
        raise ConnectorError("gated_config_mutate only supports 'create' or 'update'.")
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing {kind} on '{doctype}': qkeee_erp.mode is '{mode}', not 'read-write'. "
            f"Switch modes explicitly if this write is intended."
        )
    if not reason:
        raise ConnectorError("gated_config_mutate requires a non-empty reason.")
    if not requested_by:
        raise MissingRequesterError(
            "Refusing gated_config_mutate: requested_by is missing. There is no config "
            "default — resolve the requester's ERPNext user id/email from the live "
            "inbound channel identity and pass it explicitly via requested_by= on this call."
        )
    if not confirmation_token or issued_at is None:
        raise ConnectorError(
            "gated_config_mutate requires confirmation_token + issued_at — render the "
            "config-change confirmation first and pass its exact token and issued_at here."
        )
    if not is_fresh(int(issued_at)):
        raise StaleConfirmationError(
            "This confirmation has expired or its issued_at is implausible — re-render "
            "and reconfirm before retrying."
        )
    expected = config_change_token(kind, doctype, identifier, reason, int(issued_at))
    if confirmation_token != expected:
        raise ConnectorError(
            "confirmation_token does not match these config-change facts — re-render the "
            "confirmation against the current data and use that token."
        )
    cfg = get_env_config(tag)
    result = mutate_resource(tag, doctype, action, payload=payload, name=name, mode=mode,
                              requested_by=requested_by, skip_comment=True, domain=DOMAIN_NAME,
                              user_approved=True, approval_note=f"gated_config_mutate ({kind}): {reason}")
    comment_name = name or (result.get("data") or {}).get("name")
    if comment_name:
        _record_attribution_comment(cfg, doctype, comment_name, f"{kind} ({action})", requested_by, reason)
    return result


def get_scheduler_status(tag: str) -> dict:
    """Read-only system health signal
    (frappe.utils.scheduler.get_scheduler_status, returns
    {"status": "active"} or "inactive"/"paused"). Combine with Scheduled
    Job Type (query_resource, last_execution/stopped fields) and Error
    Log (query_resource, most recent rows) for the full System health
    check capability — the RQ Job doctype is not usable via this REST API
    (returns a 500 TypeError, unrelated to auth/permissions), so live
    background-job-queue depth cannot be read this way; report that gap
    explicitly rather than silently omitting queue depth from a health
    report."""
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", "/api/method/frappe.utils.scheduler.get_scheduler_status")
    return result.get("message", {})
