#!/usr/bin/env python3
"""
qkeee-erp-associate — system-admin domain (Users, roles, permissions).

The widest-blast-radius, heaviest-GRC domain. Every write is a named
operation in core/operations.py's pipeline, sent with the separate ADMIN
credential (QKEEE_ERP_<TAG>_ADMIN_API_KEY/_SECRET, decision D1), and every
one — system_admin.generic included — needs a rendered token plus the
user's own confirmation code:

| Operation                        | Write                                             |
|----------------------------------|---------------------------------------------------|
| system_admin.create_user         | User create (roles must exist; elevated roles flagged in the render) |
| system_admin.disable_user        | User update, body exactly {"enabled": 0}           |
| system_admin.set_user_roles      | User update of the roles table; refused if roles changed since render |
| system_admin.delete              | delete a User/Role/Custom Field/Property Setter/Webhook/Workflow |
| system_admin.create_webhook      | Webhook create; https + public host only           |
| system_admin.toggle_workflow     | Workflow update, body exactly {"is_active": 0|1}   |
| system_admin.permission_add/update/remove/reset | Role Permission Manager RPCs        |
| system_admin.generic             | Role create/update, Custom Field/Property Setter create/update (confirmed too) |

The requester must hold the matching permission (User/Role/DocType
writes are never exempt from the requester gate) — in practice System
Manager. Reads (get_roles_and_doctypes, get_permissions,
get_scheduler_status) are gated and logged like any other read and use
the admin credential, since the Role Permission Manager and scheduler
methods are System-Manager-only.
"""


from ..core import client as core_client
from ..core import operations
from ..core.operations import Operation, PreparedRequest, require_args

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

DELETABLE_DOCTYPES = ("User", "Role", "Custom Field", "Property Setter", "Webhook", "Workflow")

_READ_CTX = ("session_id", "domain_code", "channel", "channel_metadata",
             "prompt_summary", "latest_prompt")

# Role / Custom Field / Property Setter writes need System Manager too, so
# the generic operation also sends with the admin credential — and because
# it holds the admin key, EVERY action needs the rendered confirmation +
# the user's reply code, drafts included (unlike other domains' generic
# operations, whose create/update are ungated drafts).
operations.generic_operation(DOMAIN_NAME, credential="admin",
                             token_actions=frozenset(operations.RESOURCE_ACTIONS),
                             summary="Role create/update, Custom Field/Property Setter "
                                     "create/update — every action confirmed (admin credential)",
                             example_args={"doctype": "Role", "action": "create",
                                           "payload": {"role_name": "Expense Approver",
                                                       "desk_access": 1}},
                             refuse=(
    (("Webhook", "update"), "this skill has no gated path for editing a webhook (it could "
                            "repoint the destination) — give the user UI-level guidance."),
    (("Workflow", "create"), "creating workflows is guidance only — give the user UI-level "
                             "guidance."),
))
def _admin_op(**kw) -> Operation:
    kw.setdefault("domain", DOMAIN_NAME)
    kw.setdefault("credential", "admin")
    return operations.register_operation(Operation(**kw))


def _role_exists(ctx, role: str) -> bool:
    """Role is System-Manager-readable only, so this checks with the admin
    credential (404 means absent)."""
    return core_client.resource_exists(ctx.tag, "Role", role, credential="admin")


def _roles_exist(roles, ctx) -> None:
    missing = [r for r in roles if not _role_exists(ctx, r)]
    if missing:
        raise core_client.PreconditionFailedError(
            f"Role(s) {missing} do not exist on this instance — resolve exact role names "
            f"(query Role) before rendering.")


def _role_list(args) -> list:
    roles = args.get("roles")
    if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
        raise core_client.ConnectorError("roles must be a non-empty JSON list of role names.")
    return sorted(set(roles))


# ---------------------------------------------------------------- users

def _create_user_prepare(args, ctx):
    require_args(args, ["email", "first_name", "roles"], ["last_name", "send_welcome_email"])
    roles = _role_list(args)
    body = {"email": args["email"], "first_name": args["first_name"],
            "send_welcome_email": int(bool(args.get("send_welcome_email"))),
            "roles": [{"role": r} for r in roles]}
    if args.get("last_name"):
        body["last_name"] = args["last_name"]
    return PreparedRequest(transport="resource", doctype="User", action="create", body=body,
                           bound={"elevated_roles": sorted(set(roles) & ELEVATED_ROLES)})


_admin_op(key="system_admin.create_user",
          summary="create a User with exact, existing roles (elevated roles flagged)",
          prepare=_create_user_prepare, owns=frozenset({("User", "create")}),
          preconditions=(lambda req, args, ctx: _roles_exist(_role_list(args), ctx),),
          example_args={"email": "new.joiner@example.com", "first_name": "New",
                        "roles": ["Accounts User"]},
          args_help={"email": "", "first_name": "", "last_name": "optional",
                     "roles": "list of exact role names", "send_welcome_email": "bool"})


def _disable_user_prepare(args, ctx):
    require_args(args, ["name", "reason"])
    return PreparedRequest(transport="resource", doctype="User", action="update",
                           name=args["name"], body={"enabled": 0}, bound={"reason": args["reason"]})


_admin_op(key="system_admin.disable_user",
          summary="disable a User (enabled=0) — reversible; prefer over delete",
          prepare=_disable_user_prepare, owns=frozenset({("User", "update")}),
          example_args={"name": "leaver@example.com", "reason": "left the company"},
          args_help={"name": "User id", "reason": "stated reason"})


def _current_roles(ctx, user) -> list:
    data = core_client.get_resource(ctx.tag, "User", user, strip_noise=False,
                                    requested_by=ctx.requested_by, **ctx.audit_kwargs()).get("data") or {}
    return sorted({r.get("role") for r in data.get("roles") or [] if r.get("role")})


def _set_roles_render(args, ctx):
    args = dict(args)
    if args.get("name") and "roles_before" not in args:
        args["roles_before"] = _current_roles(ctx, args["name"])
    return args


def _set_roles_prepare(args, ctx):
    require_args(args, ["name", "roles", "reason", "roles_before"])
    roles = _role_list(args)
    before = sorted(set(args["roles_before"]))
    return PreparedRequest(
        transport="resource", doctype="User", action="update", name=args["name"],
        body={"roles": [{"role": r} for r in roles]},
        bound={"reason": args["reason"], "roles_before": before,
               "added": sorted(set(roles) - set(before)), "removed": sorted(set(before) - set(roles)),
               "elevated_added": sorted((set(roles) - set(before)) & ELEVATED_ROLES)})


def _roles_unchanged_since_render(req, args, ctx):
    _roles_exist(_role_list(args), ctx)
    if _current_roles(ctx, args["name"]) != req.bound["roles_before"]:
        raise core_client.PreconditionFailedError(
            f"Refusing set_user_roles: {args['name']!r}'s roles changed since render — re-render.")


_admin_op(key="system_admin.set_user_roles",
          summary="replace a User's role list (render shows exact before/after)",
          prepare=_set_roles_prepare, render_defaults=_set_roles_render,
          preconditions=(_roles_unchanged_since_render,),
          example_args={"name": "staff@example.com", "roles": ["Accounts User", "Sales User"],
                        "reason": "moved to sales ops"},
          args_help={"name": "User id", "roles": "complete new role list", "reason": "",
                     "roles_before": "filled by render"})


# ---------------------------------------------------------------- delete

def _delete_prepare(args, ctx):
    require_args(args, ["doctype", "name", "reason"])
    if args["doctype"] not in DELETABLE_DOCTYPES:
        raise core_client.DoctypeNotAllowedError(
            f"system_admin.delete only deletes {DELETABLE_DOCTYPES}, not {args['doctype']!r}.")
    return PreparedRequest(transport="resource", doctype=args["doctype"], action="delete",
                           name=args["name"], bound={"reason": args["reason"]})


# No "deleted" Comment: the record is gone afterwards, and a Comment posted
# BEFORE a delete that then fails is a false record (W16). The audit row is
# the record.
_admin_op(key="system_admin.delete",
          summary="delete a User/Role/Custom Field/Property Setter/Webhook/Workflow (prefer disable_user for users)",
          prepare=_delete_prepare, skip_comment=True,
          owns=frozenset((d, "delete") for d in DELETABLE_DOCTYPES),
          example_args={"doctype": "Webhook", "name": "HOOK-0001", "reason": "integration retired"},
          args_help={"doctype": f"one of {DELETABLE_DOCTYPES}", "name": "", "reason": ""})


# ---------------------------------------------------------------- config

def _webhook_prepare(args, ctx):
    # `name` is required: Webhook is prompt-named on Frappe v16, so a create
    # without one fails live with "Please set the document name".
    require_args(args, ["name", "payload", "reason"])
    if not isinstance(args["payload"], dict):
        raise core_client.ConnectorError("payload must be a JSON object.")
    return PreparedRequest(transport="resource", doctype="Webhook", action="create",
                           body=dict(args["payload"], name=args["name"]),
                           bound={"reason": args["reason"]})


_admin_op(key="system_admin.create_webhook",
          summary="create a Webhook (an outbound data destination)",
          prepare=_webhook_prepare, owns=frozenset({("Webhook", "create")}),
          preconditions=(lambda req, args, ctx: operations.check_public_https_url(
              (req.body or {}).get("request_url")),),
          example_args={"name": "Supplier sync to procurement portal",
                        "payload": {"webhook_doctype": "Supplier", "webhook_docevent": "after_insert",
                                    "request_url": "https://hooks.example.com/erp/supplier"},
                        "reason": "sync suppliers to procurement portal"},
          args_help={"name": "Webhook name (Frappe v16 prompts for it)",
                     "payload": "Webhook fields incl. request_url (https, public host)",
                     "reason": ""})


def _workflow_prepare(args, ctx):
    require_args(args, ["name", "reason"], ["is_active"])
    if args.get("is_active") not in (0, 1, True, False):
        raise core_client.ConnectorError("is_active must be 0 or 1.")
    return PreparedRequest(transport="resource", doctype="Workflow", action="update",
                           name=args["name"], body={"is_active": int(args["is_active"])},
                           bound={"reason": args["reason"]})


_admin_op(key="system_admin.toggle_workflow",
          summary="switch a Workflow on/off — can halt every in-flight approval on its doctype",
          prepare=_workflow_prepare, owns=frozenset({("Workflow", "update")}),
          example_args={"name": "Purchase Order Approval", "is_active": 0,
                        "reason": "pause approvals during migration"},
          args_help={"name": "Workflow name", "is_active": "0 or 1", "reason": ""})


# ---------------------------------------------------------------- permissions

_PM = "/api/method/frappe.core.page.permission_manager.permission_manager."
_PERMISSION_ACTIONS = {
    # action: (audit action, requester ptype on Custom DocPerm)
    "add": ("Create", "create"),
    "update": ("Update", "write"),
    "remove": ("Delete", "delete"),
    "reset": ("Delete", "delete"),
}


def _permission_prepare(action):
    def prepare(args, ctx):
        if action == "reset":
            require_args(args, ["doctype", "reason"])
            body = {"doctype": args["doctype"]}
        else:
            required = ["doctype", "role", "reason"] + (["ptype"] if action == "update" else [])
            optional = ["permlevel"] + (["value", "current_value"] if action == "update" else [])
            require_args(args, required, optional)
            permlevel = int(args.get("permlevel") or 0)
            if action == "add":
                body = {"parent": args["doctype"], "role": args["role"], "permlevel": permlevel}
            elif action == "remove":
                body = {"doctype": args["doctype"], "role": args["role"], "permlevel": permlevel}
            else:
                body = {"doctype": args["doctype"], "role": args["role"], "permlevel": permlevel,
                        "ptype": args["ptype"], "value": int(bool(args.get("value"))), "if_owner": 0}
        audit_action, ptype = _PERMISSION_ACTIONS[action]
        bound = {"reason": args["reason"]}
        if action == "update":
            bound["current_value"] = args.get("current_value")
        # Audit reference is the target DocType itself: a real record, so the
        # Audit Log's Dynamic Link validates (a synthetic "role@permlevel"
        # string would make the audit insert fail silently).
        return PreparedRequest(
            transport="rpc", doctype="Custom DocPerm", action=f"permission_{action}",
            rpc_path=_PM + action, body=body, rbac_ptype=ptype, audit_action=audit_action,
            audit_doctype="DocType", audit_reference=args["doctype"], bound=bound)
    return prepare


def _permission_update_render(args, ctx):
    args = dict(args)
    if "current_value" not in args and args.get("doctype") and args.get("role") and args.get("ptype"):
        level = int(args.get("permlevel") or 0)
        rows = get_permissions(ctx.tag, args["doctype"], requested_by=ctx.requested_by,
                               **ctx.audit_kwargs())
        match = [r for r in rows if r.get("role") == args["role"] and int(r.get("permlevel") or 0) == level]
        args["current_value"] = match[0].get(args["ptype"]) if match else None
    return args


for _action in _PERMISSION_ACTIONS:
    _admin_op(key=f"system_admin.permission_{_action}",
              summary={"add": "add a bare permission row (grants nothing by itself)",
                       "update": "flip ONE right on a role's permission row",
                       "remove": "remove a CUSTOM permission row (a standard row may still grant it)",
                       "reset": "wipe ALL custom permission overrides on a doctype"}[_action],
              prepare=_permission_prepare(_action), allowlist_domain=None, skip_comment=True,
              render_defaults=_permission_update_render if _action == "update" else None,
              example_args=dict({"doctype": "Supplier", "reason": "least privilege review"},
                                **({} if _action == "reset" else {"role": "Accounts User"}),
                                **({"ptype": "write", "value": 0} if _action == "update" else {})),
              args_help={"doctype": "target DocType", "role": "", "permlevel": "default 0",
                         "ptype": "update only", "value": "update only, 0/1", "reason": "",
                         "current_value": "update only, filled by render"})


# ---------------------------------------------------------------- reads

def get_roles_and_doctypes(tag: str, *, requested_by: str, **read_ctx) -> dict:
    """Read: the role list + doctype list the Role Permission Manager uses."""
    result = core_client.read_rpc(
        tag, "GET", _PM + "get_roles_and_doctypes", gate_doctype="Custom DocPerm",
        requested_by=requested_by, credential="admin",
        **{k: v for k, v in read_ctx.items() if k in _READ_CTX})
    return result.get("message", {})


def get_permissions(tag: str, doctype: str, *, requested_by: str, **read_ctx) -> list:
    """Read: every permission row (standard + Custom DocPerm overrides) for
    a DocType, as the Role Permission Manager shows them. Querying DocPerm
    directly fails with a PermissionError — this is the working path."""
    result = core_client.read_rpc(
        tag, "GET", _PM + "get_permissions", params={"doctype": doctype},
        gate_doctype="Custom DocPerm", requested_by=requested_by, credential="admin",
        log_doctype="DocType", log_name=doctype,
        **{k: v for k, v in read_ctx.items() if k in _READ_CTX})
    return result.get("message", [])


def get_scheduler_status(tag: str, *, requested_by: str, **read_ctx) -> dict:
    """Read: frappe.utils.scheduler.get_scheduler_status. Combine with a
    Scheduled Job Type query and recent Error Log rows for the full health
    check. The RQ Job doctype is not usable via this REST API (500
    TypeError) — report that gap rather than omitting queue depth."""
    result = core_client.read_rpc(
        tag, "GET", "/api/method/frappe.utils.scheduler.get_scheduler_status",
        gate_doctype="Scheduled Job Type", requested_by=requested_by, credential="admin",
        **{k: v for k, v in read_ctx.items() if k in _READ_CTX})
    return result.get("message", {})
