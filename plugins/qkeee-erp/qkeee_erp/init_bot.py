#!/usr/bin/env python3
"""
qkeee-erp-associate — init_bot (admin-invoked, one-time provisioning helper).

Not a domain module and not part of the associate's normal activation
sequence — a human/admin runs this deliberately, once per target
environment (or after a schema change).

This version provisions ONLY the `Qkeee Bot` Role, the `Qkeee Bot Audit
Log` DocType, and the role's read-only rows on doctype_defs.BOT_READ_GRANTS
(Module Def, Workflow) — there is no `--bot-email`/bot-user provisioning
path in this skill; bot-user provisioning needs a separate tool until one
is added here.

CODE-ONLY — this script has not been run against any live ERPNext
instance in this form. The dry-run/confirm-token discipline below
applies regardless: nothing here executes a write without a prior
--dry-run's token, and a real run recomputes its plan against the
target's actual current state before trusting a passed-in token.

Requires the tag's ELEVATED admin key pair —
QKEEE_ERP_<TAG>_ADMIN_API_KEY/_ADMIN_API_SECRET (decision D1, write-path
hardening) — held by a System Manager account: creating a DocType/Role
record needs permission the shared qkeee-erp-bot@<org> steady-state
service account must not hold. The two creates run as the provisioning
operations below (core/operations.py pipeline: requester gate + audit),
restricted to exactly the Role and Audit Log DocType this script defines,
and are not reachable from execute_write.py.

**Re-run it on an already-provisioned instance after upgrading this skill.**
The plan also converges existing provisioned doctypes on doctype_defs.py's
MIGRATABLE_FIELDS — currently the audit log's `reference_name`, changed
from Dynamic Link to Data because a Dynamic Link made every audited record
undeletable (LinkExistsError) and silently dropped audit rows naming a
record that doesn't exist. Until that migration runs, deletes of records
the skill has audited fail on that instance.

`--requested-by` must be a real ERPNext User who holds create permission
on Role and DocType (in practice: System Manager). Role/DocType writes
are no longer exempt from core.client's requester gate — see
core/client.py's WRITE_GATE_EXEMPT_DOCTYPES — so a requester without
that permission is refused before anything is created.

**The steady-state bot account this provisions FOR must itself never be
Administrator or hold System Manager once it's created and switched to.**
Under a privileged bot identity, ERPNext's frappe.client.has_permission
doesn't reliably discriminate by the `user=` param it's given, which
makes core.client's RBAC pre-check (_validate_prod_requester()) fall
back to a local, RPC-independent role/DocPerm check instead — see
core/client.py's verify_rbac_precheck_reliable() /
_requester_has_role_permission(). This script runs under a DIFFERENT
(elevated) identity than that steady-state account and has no way to
provision or verify the steady-state account's own key directly (no
--bot-email path exists here yet). Once that account's credentials are
configured in qkeee-erp.env, run `health` and confirm `bot_roles` is
exactly `["Qkeee Bot"]` and `gaps` is empty before treating the
environment as ready — see run_real()'s final step below for the exact
reminder printed.

Usage:
    python init_bot.py --tag qa --requested-by admin@org.com --dry-run
    python init_bot.py --tag qa --requested-by admin@org.com \\
        --confirm-token <token> --issued-at <epoch>
"""

import json
import os
import sys
import time

from .core import client as core_client
from .core import operations
from .core.client import ConnectorError, _qkeee_env_file_path, _audit_insert, _audit_submit, _now_iso
from .core.confirm_token import compute_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS
import urllib.parse

from .doctype_defs import (ALL_DOCTYPES, AUDIT_LOG, BOT_READ_GRANTS, MIGRATABLE_FIELDS,
                           PROVISION_PTYPES, ROLE_NAME, ROLE_PAYLOAD, UNSCOPED_PROVISION_DOCTYPES,
                           DOMAIN_MASTER_DOCTYPES, DOMAIN_TEMPLATE_ROLES, is_bot_read_link)


# ---------------------------------------------------------------------------
# Provisioning operations — exactly two records, nothing else
# ---------------------------------------------------------------------------
# Token policy "none": this script's own dry-run plan token (verified in
# run_real() before either runs) is the confirmation. Each prepare() refuses
# anything but the exact Role / DocType definitions in doctype_defs.py, so
# these can't be used to create arbitrary Roles or DocTypes. cli=False:
# execute_write.py never lists or runs them (it doesn't import this file).

def _provision_role_prepare(args, ctx):
    if set(args) != {"role"} or args["role"] != ROLE_NAME:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.create_role only creates the {ROLE_NAME!r} Role.")
    return operations.PreparedRequest(transport="resource", doctype="Role", action="create",
                                      body=dict(ROLE_PAYLOAD))


def _provision_doctype_prepare(args, ctx):
    defs = {d["name"]: d for d in ALL_DOCTYPES}
    if set(args) != {"doctype_name"} or args["doctype_name"] not in defs:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.create_doctype only creates {sorted(defs)}.")
    return operations.PreparedRequest(transport="resource", doctype="DocType", action="create",
                                      body=dict(defs[args["doctype_name"]]))


def _definition(doctype_name: str) -> dict:
    return next(d for d in ALL_DOCTYPES if d["name"] == doctype_name)


def _provision_migrate_prepare(args, ctx):
    """Field-type migration of an existing provisioned doctype. The body
    must carry every shipped field with exactly the shipped type, so this
    can only converge the live doctype on doctype_defs.py. Fields the
    definition doesn't name (e.g. a Frappe-added `amended_from` on an
    instance provisioned by an older version) may ride along, but only
    unchanged — _check_live_only_fields_unchanged() enforces that against
    the live doctype. Never adds a field, drops one, or sets another type."""
    name = args.get("doctype_name")
    if set(args) != {"doctype_name", "fields"} or name not in MIGRATABLE_FIELDS:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.migrate_fields only migrates {sorted(MIGRATABLE_FIELDS)}.")
    want = {f["fieldname"]: f["fieldtype"] for f in _definition(name)["fields"]}
    got = {f.get("fieldname"): f.get("fieldtype") for f in args["fields"]}
    if {k: got.get(k) for k in want} != want:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.migrate_fields: the fields sent for {name!r} must match "
            f"doctype_defs.py exactly (names and types).")
    return operations.PreparedRequest(transport="resource", doctype="DocType", action="update",
                                      name=name, body={"fields": args["fields"]})


def _check_live_only_fields_unchanged(req, args, ctx):
    """Precondition for provisioning.migrate_fields: the fields sent must be
    exactly the live doctype's fields (none added, none dropped), and every
    field doctype_defs.py doesn't define must keep its live type/options."""
    name = args["doctype_name"]
    shipped = {f["fieldname"] for f in _definition(name)["fields"]}
    live = {f.get("fieldname"): f for f in _live_doctype(ctx.tag, name).get("fields", [])}
    sent = {f.get("fieldname"): f for f in args["fields"]}
    if set(sent) != set(live) | shipped:
        raise core_client.PreconditionFailedError(
            f"provisioning.migrate_fields: fields sent for {name!r} add or drop a field "
            f"(sent {sorted(set(sent) ^ (set(live) | shipped))} differ from live+definition).")
    for fieldname in set(sent) - shipped:
        s, l = sent[fieldname], live[fieldname]
        if (s.get("fieldtype"), s.get("options")) != (l.get("fieldtype"), l.get("options")):
            raise core_client.PreconditionFailedError(
                f"provisioning.migrate_fields: live-only field {fieldname!r} on {name!r} must be "
                f"sent unchanged.")


for _key, _prep, _summary, _pre in (
        ("provisioning.create_role", _provision_role_prepare, f"create the {ROLE_NAME} Role", ()),
        ("provisioning.create_doctype", _provision_doctype_prepare, "create the audit DocType", ()),
        ("provisioning.migrate_fields", _provision_migrate_prepare,
         "converge a provisioned doctype's field types on doctype_defs.py",
         (_check_live_only_fields_unchanged,))):
    operations.register_operation(operations.Operation(
        key=_key, domain=None, summary=_summary, prepare=_prep,
        token_policy=operations.POLICY_NONE, credential="admin", allowlist_domain=None,
        preconditions=_pre,
        cli=False, audit=False,  # log_role_provisioning() is this flow's one audit record
        # Creates the bot's own role: exempt from the self-escalation rule
        # (agents ADR 0003) like grant_bot_read below — admin-run, fixed
        # definitions only, never reachable from execute_write.py.
        self_escalation_exempt=True))


# Read grants for the bot role (doctype_defs.BOT_READ_GRANTS). "add" creates
# the row (the RPC ticks read and nothing else); "set_read" ticks read on a
# row that exists without it. Audited through the pipeline: the audit
# DocType exists by the time run_real() reaches this step.
_PM = "/api/method/frappe.core.page.permission_manager.permission_manager."
_GRANT_STEPS = ("add", "set_read")


def _grant_bot_read_prepare(args, ctx):
    if (set(args) != {"doctype", "step"} or args["doctype"] not in BOT_READ_GRANTS
            or args["step"] not in _GRANT_STEPS):
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.grant_bot_read only grants {ROLE_NAME!r} read on {BOT_READ_GRANTS} "
            f"(step one of {_GRANT_STEPS}).")
    doctype = args["doctype"]
    if args["step"] == "add":
        path, body, ptype, audit_action = (_PM + "add", {"parent": doctype, "role": ROLE_NAME,
                                                         "permlevel": 0}, "create", "Create")
    else:
        path, body, ptype, audit_action = (
            _PM + "update", {"doctype": doctype, "role": ROLE_NAME, "permlevel": 0,
                             "ptype": "read", "value": 1, "if_owner": 0}, "write", "Update")
    return operations.PreparedRequest(
        transport="rpc", doctype="Custom DocPerm", action=f"grant_bot_read_{args['step']}",
        rpc_path=path, body=body, rbac_ptype=ptype, audit_action=audit_action,
        audit_doctype="DocType", audit_reference=doctype)


operations.register_operation(operations.Operation(
    key="provisioning.grant_bot_read", domain=None,
    summary=f"grant the {ROLE_NAME} role read on {', '.join(BOT_READ_GRANTS)}",
    prepare=_grant_bot_read_prepare, token_policy=operations.POLICY_NONE, credential="admin",
    allowlist_domain=None, skip_comment=True, cli=False,
    # These rows ARE on the bot's own role, so the self-escalation rule
    # (agents ADR 0003) would refuse them. Exempt only this op: run by an
    # admin from this script, never by execute_write.py, and its prepare()
    # allows nothing but read on doctype_defs.BOT_READ_GRANTS.
    self_escalation_exempt=True))


def _live_permissions(tag: str, doctype: str) -> list:
    """The Role Permission Manager's view of `doctype`'s effective rows,
    read with the admin credential (System-Manager-only RPC). Plumbing for
    the plan, like _live_doctype() — not a business read."""
    cfg = core_client.get_env_config(tag, credential="admin")
    return core_client._request(cfg, "GET", _PM + "get_permissions",
                                params={"doctype": doctype}).get("message") or []


def _bot_read_grants_needed(tag: str) -> list:
    """`"<doctype>: add"` when the role has no permlevel-0 row on it,
    `"<doctype>: set_read"` when it has one without read. Sorted by
    BOT_READ_GRANTS order."""
    needed = []
    for doctype in BOT_READ_GRANTS:
        rows = [r for r in _live_permissions(tag, doctype)
                if r.get("role") == ROLE_NAME and not int(r.get("permlevel") or 0)]
        if not rows:
            needed.append(f"{doctype}: add")
        elif not any(int(r.get("read") or 0) for r in rows):
            needed.append(f"{doctype}: set_read")
    return needed


def grant_bot_reads(tag: str, requested_by: str, grants: list, approval_note: str) -> list:
    for grant in grants:
        doctype, _, step = grant.partition(": ")
        operations.run_operation("provisioning.grant_bot_read", {"doctype": doctype, "step": step},
                                 operations.WriteContext(tag=tag, mode="read-write",
                                                         requested_by=requested_by,
                                                         user_approved=True,
                                                         approval_note=approval_note))
        print(f"Granted {ROLE_NAME} read on '{doctype}' ({step}).")
    return list(grants)


# ---------------------------------------------------------------------------
# Bot user + business rights (agents ADR 0003 amendments 2026-10-13 and
# 2026-10-21: template-role rows)
# ---------------------------------------------------------------------------
# Both ops write the bot's own rights, so both are exempt from the
# self-escalation rule like the two above: Operator-run from this script,
# cli=False (the agent can never reach them), and each prepare() accepts
# only the narrow shape below. The exact rows are bound by the plan token.

def _privileged_doctypes() -> frozenset:
    return operations.admin_key_doctypes() | operations.UNSCOPED_DENY


def _assign_bot_role_prepare(args, ctx):
    """Set the bot user's roles: may add ROLE_NAME and remove roles, never
    add any other role."""
    if set(args) != {"user", "roles_before", "roles_after"}:
        raise core_client.DoctypeNotAllowedError(
            "provisioning.assign_bot_role takes exactly user, roles_before, roles_after.")
    before, after = set(args["roles_before"]), set(args["roles_after"])
    if ROLE_NAME not in after or not after <= before | {ROLE_NAME}:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.assign_bot_role may only add {ROLE_NAME!r} and remove roles; "
            f"it would add {sorted(after - before - {ROLE_NAME})}.")
    return operations.PreparedRequest(
        transport="resource", doctype="User", action="update", name=args["user"],
        body={"roles": [{"role": r} for r in sorted(after)]},
        bound={"roles_before": sorted(before), "roles_after": sorted(after)})


def _is_the_bot_with_roles_before(req, args, ctx):
    """Precondition: `user` is the bot key's own user, and its roles are
    still exactly roles_before (read live with the Admin Account)."""
    core_client.reset_identity_caches(ctx.tag)
    identity = core_client._bot_identity(ctx.tag)
    if identity.get("roles_error"):
        raise core_client.PreconditionFailedError(
            f"provisioning.assign_bot_role: the bot's roles can't be read: {identity['roles_error']}")
    if (identity.get("user") or "").lower() != str(args["user"]).lower():
        raise core_client.PreconditionFailedError(
            f"provisioning.assign_bot_role: {args['user']!r} is not the bot key's user "
            f"({identity.get('user')!r}).")
    if sorted(identity.get("roles") or []) != sorted(args["roles_before"]):
        raise core_client.PreconditionFailedError(
            "provisioning.assign_bot_role: the bot's roles changed since the plan — run --dry-run again.")


def _grant_bot_permission_prepare(args, ctx):
    """One permlevel-0 step on ROLE_NAME: "add" (a new row, read ticked) or
    tick one of PROVISION_PTYPES. Never delete, never a privileged doctype."""
    steps = ("add",) + PROVISION_PTYPES
    if set(args) != {"doctype", "step"} or args["step"] not in steps:
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.grant_bot_permission takes doctype and step (one of {steps}).")
    doctype = args["doctype"]
    if doctype in _privileged_doctypes():
        raise core_client.DoctypeNotAllowedError(
            f"provisioning.grant_bot_permission never grants on privileged doctype {doctype!r}.")
    if args["step"] == "add":
        path, body, ptype, audit_action = (_PM + "add", {"parent": doctype, "role": ROLE_NAME,
                                                         "permlevel": 0}, "create", "Create")
    else:
        path, body, ptype, audit_action = (
            _PM + "update", {"doctype": doctype, "role": ROLE_NAME, "permlevel": 0,
                             "ptype": args["step"], "value": 1, "if_owner": 0}, "write", "Update")
    return operations.PreparedRequest(
        transport="rpc", doctype="Custom DocPerm", action=f"grant_bot_permission_{args['step']}",
        rpc_path=path, body=body, rbac_ptype=ptype, audit_action=audit_action,
        audit_doctype="DocType", audit_reference=doctype)


operations.register_operation(operations.Operation(
    key="provisioning.assign_bot_role", domain=None,
    summary=f"give the bot user the {ROLE_NAME} role (and optionally strip stock roles)",
    prepare=_assign_bot_role_prepare, preconditions=(_is_the_bot_with_roles_before,),
    token_policy=operations.POLICY_NONE, credential="admin", allowlist_domain=None,
    skip_comment=True, cli=False, self_escalation_exempt=True))

operations.register_operation(operations.Operation(
    key="provisioning.grant_bot_permission", domain=None,
    summary=f"grant the {ROLE_NAME} role one business permission (never delete)",
    prepare=_grant_bot_permission_prepare, token_policy=operations.POLICY_NONE,
    credential="admin", allowlist_domain=None, skip_comment=True, cli=False,
    self_escalation_exempt=True))


def provisionable_domains() -> dict:
    """{domain: [doctypes]} the bot writes with its own key."""
    from .discover import bot_permissions
    return bot_permissions()["bot_write_doctypes"]


def _meta_bundle(tag: str, doctype: str) -> list:
    """getdoctype's bundle (the doctype, then its child tables), read with
    the Admin Account."""
    cfg = core_client.get_env_config(tag, credential="admin")
    return core_client._request(cfg, "GET", core_client._MERGED_META_PATH,
                                params={"doctype": doctype}).get("docs") or []


def resolve_domains(raw) -> list:
    """`--domains` → the domain list. Omitted, empty or "all": every bot
    domain, so one init-bot run gives the bot everything the agent needs
    (dev-erp 2026-10-16: users should not have to know which domain owns
    which doctype). "none": no business grants. Otherwise a comma list."""
    value = (raw or "").strip().lower()
    if value in ("", "all"):
        return sorted(provisionable_domains())
    if value == "none":
        return []
    return [d.strip() for d in value.split(",") if d.strip()]


def requester_can_grant(tag: str, requested_by: str) -> tuple:
    """(ok, reason): may `requested_by` approve the bot permission grants?
    The real run's requester gate checks create on Custom DocPerm; check it
    here so a dry-run never prints a token that cannot succeed."""
    try:
        readiness = core_client.requester_permission_readiness(
            tag, "Custom DocPerm", "create", requested_by)
    except ConnectorError as e:
        return None, f"the requester check failed: {e}"
    return readiness.get("ready"), readiness.get("reason")


def _role_permissions(tag: str, role: str) -> list:
    """Every permission row of `role` (Role Permission Manager, admin key)."""
    cfg = core_client.get_env_config(tag, credential="admin")
    return core_client._request(cfg, "GET", _PM + "get_permissions",
                                params={"role": role}).get("message") or []


def template_rights(tag: str, domains) -> tuple:
    """({doctype: {ptype}}, empty_roles): what the domains' template roles
    (doctype_defs.DOMAIN_TEMPLATE_ROLES) hold at permlevel 0, cut to
    PROVISION_PTYPES. An if_owner row is left out: the bot's row would hold
    the right on every record, not only its own. `empty_roles`: template
    roles with no such row (missing on this instance)."""
    rights, empty = {}, []
    for role in sorted({r for d in domains for r in DOMAIN_TEMPLATE_ROLES.get(d, ())}):
        found = False
        for row in _role_permissions(tag, role):
            if int(row.get("permlevel") or 0) or int(row.get("if_owner") or 0):
                continue
            held = {p for p in PROVISION_PTYPES if int(row.get(p) or 0)}
            if held and row.get("parent"):
                rights.setdefault(row["parent"], set()).update(held)
                found = True
        if not found:
            empty.append(role)
    return rights, empty


def template_domains(tag: str, doctype: str, ptype: str = "create") -> list:
    """The bot domains whose template roles hold `ptype` on `doctype`
    (permlevel 0, not if_owner), read live: init-bot grants it with them.
    [] for a privileged doctype — init-bot never grants on one."""
    if doctype in _privileged_doctypes():
        return []
    roles = {r.get("role") for r in _live_permissions(tag, doctype)
             if not int(r.get("permlevel") or 0) and not int(r.get("if_owner") or 0)
             and int(r.get(ptype) or 0)}
    return sorted(d for d in provisionable_domains()
                  if roles & set(DOMAIN_TEMPLATE_ROLES.get(d, ())))


def bot_permission_matrix(tag: str, domains) -> tuple:
    """({doctype: {ptype}}, skipped, not_found, empty_template_roles) for
    `domains`. Code rows: each write doctype of each domain, its
    DOMAIN_MASTER_DOCTYPES and UNSCOPED_PROVISION_DOCTYPES get
    read/create/write, and submit/cancel when submittable. Template rows
    (template_rights()): what the domain's template roles hold, the same
    ticks. Both cut the same way: submit/cancel only when submittable, no
    child tables, read only on a Single. Each doctype the bot may create or
    write gets read on each Link target a user can set or must set
    (doctype_defs.is_bot_read_link(); child tables included). A privileged
    doctype is skipped, never granted: `skipped` lists it for an admin to
    decide by hand. A doctype missing on the instance (its app not
    installed) goes to `not_found`."""
    available = provisionable_domains()
    unknown = sorted(set(domains) - set(available))
    if unknown:
        raise ConnectorError(f"Unknown or non-bot domain(s) {unknown}. Choose from "
                             f"{sorted(available)}.")
    privileged = _privileged_doctypes()
    code_doctypes = ({dt for d in domains for dt in available[d]}
                     | {dt for d in domains for dt in DOMAIN_MASTER_DOCTYPES.get(d, ())}
                     | set(UNSCOPED_PROVISION_DOCTYPES))
    templates, empty_roles = template_rights(tag, domains)
    matrix, skipped, not_found = {}, set(), []

    def grant(doctype, ptypes):
        if doctype in privileged:
            skipped.add(doctype)
        elif ptypes:
            matrix.setdefault(doctype, set()).update(ptypes)

    for doctype in sorted(code_doctypes | set(templates)):
        if doctype in privileged:
            skipped.add(doctype)
            continue
        try:
            bundle = _meta_bundle(tag, doctype)
        except ConnectorError as e:
            if "(404)" in str(e) or "DoesNotExistError" in str(e):
                not_found.append(doctype)
                continue
            raise
        main = next((d for d in bundle if d.get("name") == doctype), {})
        if main.get("istable"):
            continue
        ptypes = set(templates.get(doctype, ()))
        if doctype in code_doctypes:
            ptypes |= {"read", "create", "write", "submit", "cancel"}
        if not main.get("is_submittable"):
            ptypes -= {"submit", "cancel"}
        if main.get("issingle"):
            ptypes &= {"read"}
        grant(doctype, ptypes)
        if ptypes & {"create", "write"}:
            for doc in bundle:
                for f in doc.get("fields") or []:
                    if is_bot_read_link(f):
                        grant(f["options"], {"read"})
    return matrix, sorted(skipped), not_found, empty_roles


def _live_role_permissions(tag: str) -> list:
    """Every permission row of ROLE_NAME (Role Permission Manager, admin key)."""
    return _role_permissions(tag, ROLE_NAME)


def _business_plan(tag: str, domains) -> dict:
    """The steps to reach the matrix, and the role's rights beyond it
    (reported, never removed)."""
    matrix, skipped, not_found, empty_roles = bot_permission_matrix(tag, domains)
    rows = {r.get("parent"): r for r in _live_role_permissions(tag)
            if not int(r.get("permlevel") or 0)}
    steps = []
    for doctype in sorted(matrix):
        row = rows.get(doctype)
        have = {"read"} if row is None else {p for p in PROVISION_PTYPES if int(row.get(p) or 0)}
        if row is None:
            steps.append(f"{doctype}: add")
        steps += [f"{doctype}: {p}" for p in PROVISION_PTYPES if p in matrix[doctype] - have]
    expected = {dt: set(p) for dt, p in matrix.items()}
    for dt in BOT_READ_GRANTS:
        expected.setdefault(dt, set()).add("read")
    audit_row = next(r for r in AUDIT_LOG["permissions"] if r["role"] == ROLE_NAME)
    expected[AUDIT_LOG["name"]] = {p for p in PROVISION_PTYPES if audit_row.get(p)}
    extras = []
    for doctype, row in sorted(rows.items()):
        held = {p for p in PROVISION_PTYPES + ("delete", "amend") if int(row.get(p) or 0)}
        beyond = held - expected.get(doctype, set())
        if beyond:
            extras.append(f"{doctype}: {', '.join(sorted(beyond))}")
    return {"business_grants": steps, "skipped_privileged": skipped, "extra_rights": extras,
            "doctypes_not_found": not_found, "template_roles_empty": empty_roles}


def _bot_role_plan(tag: str, strip_stock_roles: bool) -> dict:
    """The bot user's role change, if any. Reads the bot's roles with the
    Admin Account; an unreadable identity is reported, not guessed."""
    core_client.reset_identity_caches(tag)
    identity = core_client._bot_identity(tag)
    if identity.get("roles_error"):
        return {"bot_role_change": None, "bot_stock_roles": [],
                "bot_identity_error": identity["roles_error"]}
    before = sorted(identity.get("roles") or [])
    stock = core_client.stock_roles(before)
    after = set(before) | {ROLE_NAME}
    if strip_stock_roles:
        after -= set(stock)
    after = sorted(after)
    change = None if after == before else {"user": identity["user"], "roles_before": before,
                                           "roles_after": after}
    return {"bot_role_change": change, "bot_stock_roles": stock, "bot_identity_error": None}


def assign_bot_role(tag: str, requested_by: str, change: dict, approval_note: str) -> dict:
    operations.run_operation("provisioning.assign_bot_role", dict(change),
                             operations.WriteContext(tag=tag, mode="read-write",
                                                     requested_by=requested_by,
                                                     user_approved=True,
                                                     approval_note=approval_note))
    print(f"Set {change['user']}'s roles: {change['roles_before']} -> {change['roles_after']}.")
    return change


def grant_business_permissions(tag: str, requested_by: str, steps: list,
                               approval_note: str) -> list:
    for step in steps:
        doctype, _, what = step.partition(": ")
        operations.run_operation("provisioning.grant_bot_permission",
                                 {"doctype": doctype, "step": what},
                                 operations.WriteContext(tag=tag, mode="read-write",
                                                         requested_by=requested_by,
                                                         user_approved=True,
                                                         approval_note=approval_note))
        print(f"Granted {ROLE_NAME} {'a new row (read)' if what == 'add' else what} on "
              f"'{doctype}'.")
    return list(steps)


def ensure_qkeee_env_file_skeleton() -> bool:
    """Create qkeee-erp.env with a header comment ONLY (no tag lines, no
    secrets) if it doesn't already exist. Returns True if it was created,
    False if it was already there.

    Deliberately does NOT write BASE_URL/API_KEY/API_SECRET into this
    file, even when this process has them (e.g. from os.environ) —
    credentials only ever land in this file via the operator's own manual
    copy-paste from a one-time stdout print (see
    qkeee-erp-bot-init/scripts/ensure_bot_user.py, not yet ported here),
    never by this tooling reading them back and re-writing them. This
    function exists only so a user running init_bot on a truly fresh
    profile isn't left hunting for where to put the lines it tells them
    to add — it's the empty-file convenience half of that instruction,
    not a partial reversal of it."""
    path = _qkeee_env_file_path()
    if os.path.exists(path):
        return False
    header = (
        "# qkeee-erp.env — ERPNext credentials for the qkeee-erp-associate skill.\n"
        "# Created by init_bot.py. Add three lines per environment tag:\n"
        "#   QKEEE_ERP_<TAG>_BASE_URL=https://org.erpnext.com\n"
        "#   QKEEE_ERP_<TAG>_API_KEY=...\n"
        "#   QKEEE_ERP_<TAG>_API_SECRET=...\n"
        "# Optional per-tag: QKEEE_ERP_<TAG>_ALLOW_INSECURE.\n"
        "# There is no REQUESTED_BY var — requested_by is never a config\n"
        "# default, it's resolved fresh per call from the live channel identity.\n"
        "# Never committed, never read back by this tooling — you paste values in\n"
        "# yourself, once, after generating/rotating a key.\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
    return True


def _init_plan_token(tag: str, requested_by: str, role_needed: bool,
                      doctypes_needed: list, issued_at: int = None,
                      fields_to_migrate: list = (), bot_read_grants: list = (),
                      bot_role_change: dict = None, business_grants: list = ()) -> str:
    """Token over the init plan: which tag, who's running it, whether the
    Role needs creating, and exactly which doctype names need creating
    (sorted, so ordering never causes a spurious mismatch). issued_at
    (epoch seconds) is required — it's the dry-run timestamp, baked into
    the token so it can't outlive DEFAULT_TOKEN_TTL_SECONDS (checked
    separately via is_fresh(), not by this function)."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the dry-run-time epoch seconds.")
    return compute_token(
        kind="qkeee_erp_associate_init_plan",
        tag=tag,
        requested_by=requested_by,
        role_needed=bool(role_needed),
        doctypes_needed=sorted(doctypes_needed),
        fields_to_migrate=sorted(fields_to_migrate),
        bot_read_grants=sorted(bot_read_grants),
        bot_role_change=bot_role_change,
        business_grants=sorted(business_grants),
        issued_at=int(issued_at),
    )


def _step(label):
    print(f"--- {label} ---")


def compute_plan(tag: str, domains=None, strip_stock_roles: bool = False) -> dict:
    """Existence-checks the Role and the (now-single) Audit Log doctype
    against the live target right now. Returns what's actually needed —
    re-run fresh on every dry-run AND every real run, so a real run's
    token check always compares against current target state, not a
    cached plan."""
    role_needed = not core_client.resource_exists(tag, "Role", ROLE_NAME, credential="admin")
    doctypes_needed = [
        d["name"] for d in ALL_DOCTYPES
        if not core_client.resource_exists(tag, "DocType", d["name"], credential="admin")
    ]
    fields_to_migrate = []
    for name, fieldnames in sorted(MIGRATABLE_FIELDS.items()):
        if name in doctypes_needed:
            continue  # created fresh from the current definition
        live = {f.get("fieldname"): f.get("fieldtype") for f in _live_doctype(tag, name).get("fields", [])}
        want = {f["fieldname"]: f["fieldtype"] for f in _definition(name)["fields"]}
        fields_to_migrate += [f"{name}.{fn}: {live.get(fn)} -> {want[fn]}"
                              for fn in fieldnames if live.get(fn) != want[fn]]
    plan = {"role_needed": role_needed, "doctypes_needed": doctypes_needed,
            "fields_to_migrate": fields_to_migrate,
            "bot_read_grants": _bot_read_grants_needed(tag),
            **_bot_role_plan(tag, strip_stock_roles),
            "business_grants": [], "skipped_privileged": [], "extra_rights": [],
            "doctypes_not_found": [], "template_roles_empty": []}
    if domains:
        plan.update(_business_plan(tag, domains))
    return plan


def _plan_token(tag, requested_by, plan, issued_at):
    return _init_plan_token(tag, requested_by, plan["role_needed"], plan["doctypes_needed"],
                            issued_at=issued_at, fields_to_migrate=plan["fields_to_migrate"],
                            bot_read_grants=plan.get("bot_read_grants", []),
                            bot_role_change=plan.get("bot_role_change"),
                            business_grants=plan.get("business_grants", []))


def _needs_action(plan) -> bool:
    return bool(plan["role_needed"] or plan["doctypes_needed"] or plan["fields_to_migrate"]
                or plan.get("bot_read_grants") or plan.get("bot_role_change")
                or plan.get("business_grants"))


def _print_bot_findings(plan) -> None:
    if plan.get("bot_identity_error"):
        print(f"WARN: the bot's roles could not be read with the Admin Account "
              f"({plan['bot_identity_error']}). Set QKEEE_ERP_<TAG>_ADMIN_API_KEY/SECRET; the "
              f"role assignment is left out of this plan.")
    stock = set(plan.get("bot_stock_roles") or [])
    change = plan.get("bot_role_change")
    kept_stock = sorted(stock & set(change["roles_after"]) if change else stock)
    if kept_stock:
        print(f"WARN: the bot user holds stock role(s) {kept_stock}. Every business call is "
              f"refused while it does (agents ADR 0003). Re-run with --strip-stock-roles to "
              f"remove them.")
    for doctype in plan.get("skipped_privileged") or []:
        print(f"NOTE: '{doctype}' is a privileged doctype (a Link target or a template-role "
              f"row); init-bot never grants on it. An admin decides by hand.")
    for role in plan.get("template_roles_empty") or []:
        print(f"NOTE: template role '{role}' has no permlevel-0 rows on this instance (role "
              f"missing, or its app is not installed); it adds nothing.")
    for doctype in plan.get("doctypes_not_found") or []:
        print(f"NOTE: '{doctype}' does not exist on this instance (its app is not installed); "
              f"skipped.")
    for extra in plan.get("extra_rights") or []:
        print(f"NOTE: {ROLE_NAME} holds rights beyond the plan (not removed): {extra}.")


def _live_doctype(tag: str, name: str) -> dict:
    """Read a provisioned DocType with the admin credential (DocType meta is
    System-Manager-readable). A read, not a write — no operation needed."""
    cfg = core_client.get_env_config(tag, credential="admin")
    return core_client._request(cfg, "GET", f"/api/resource/DocType/{urllib.parse.quote(name)}").get("data") or {}


def migrate_fields(tag: str, requested_by: str, approval_note: str) -> list:
    """Converge each provisioned doctype's MIGRATABLE_FIELDS on
    doctype_defs.py. Keeps every live field row (so Frappe updates rows in
    place) and changes only the type/options of the migratable ones."""
    migrated = []
    for name, fieldnames in sorted(MIGRATABLE_FIELDS.items()):
        live_fields = _live_doctype(tag, name).get("fields", [])
        want = {f["fieldname"]: f for f in _definition(name)["fields"]}
        changed = [f["fieldname"] for f in live_fields
                   if f.get("fieldname") in fieldnames
                   and f.get("fieldtype") != want[f["fieldname"]]["fieldtype"]]
        if not changed:
            continue
        patched = []
        for f in live_fields:
            f = dict(f)
            if f.get("fieldname") in changed:
                f["fieldtype"] = want[f["fieldname"]]["fieldtype"]
                f["options"] = want[f["fieldname"]].get("options")
            patched.append(f)
        operations.run_operation("provisioning.migrate_fields",
                                 {"doctype_name": name, "fields": patched},
                                 operations.WriteContext(tag=tag, mode="read-write",
                                                         requested_by=requested_by,
                                                         user_approved=True,
                                                         approval_note=approval_note))
        migrated += [f"{name}.{fn}" for fn in changed]
        print(f"Migrated {name}: {changed} -> doctype_defs.py types.")
    return migrated


def ensure_role(tag: str, requested_by: str, approval_note: str) -> bool:
    """Returns True if the role was created, False if it already existed."""
    if core_client.resource_exists(tag, "Role", ROLE_NAME, credential="admin"):
        print(f"Role '{ROLE_NAME}' already exists — skipping.")
        return False
    operations.run_operation("provisioning.create_role", {"role": ROLE_NAME}, operations.WriteContext(
        tag=tag, mode="read-write", requested_by=requested_by,
        user_approved=True, approval_note=approval_note))
    print(f"Created Role '{ROLE_NAME}'.")
    return True


def ensure_doctype(tag: str, doctype_def: dict, requested_by: str, approval_note: str) -> bool:
    name = doctype_def["name"]
    if core_client.resource_exists(tag, "DocType", name, credential="admin"):
        print(f"DocType '{name}' already exists — skipping.")
        return False
    operations.run_operation("provisioning.create_doctype", {"doctype_name": name},
                             operations.WriteContext(tag=tag, mode="read-write",
                                                     requested_by=requested_by,
                                                     user_approved=True,
                                                     approval_note=approval_note))
    print(f"Created DocType '{name}'.")
    return True


def log_role_provisioning(tag: str, requested_by: str, role_created: bool, approval_note: str) -> None:
    """Manual Qkeee Bot Audit Log entries for the Role provisioning step
    that already happened in ensure_role() above.

    `Role` is in core.client.AUDIT_EXEMPT_DOCTYPES — never auto-logged
    this way, same recursion-avoidance reasoning the Audit Log doctype
    itself gets (it's "managed by qkeee-erp-bot-init... under its own
    elevated-credential/confirm-token controls, not read/written by a
    business requester"). This function deliberately bypasses that
    exemption, once, for exactly this one bootstrap event: it calls the
    raw `_audit_insert()`/`_audit_submit()` primitives directly instead
    of `_log_read()`/`record_audit_log_start()` (both of which check
    AUDIT_EXEMPT_DOCTYPES themselves and would silently no-op for
    "Role" — that's the whole reason this can't just reuse them).

    Only meaningful AFTER the Qkeee Bot Audit Log DocType itself exists
    — call this from run_real() only after the DocType-creation loop,
    never before (the row has nowhere to land otherwise). Because Role
    is a hard dependency of that DocType's own `permissions` table (a
    DocPerm row's `role` is a validated Link — create fails with
    `LinkValidationError` if the role doesn't already exist), Role is
    necessarily created FIRST, before the DocType — these audit rows are
    inserted after the fact, timestamped now, not backdated to when the
    Role check/creation actually happened.

    Logs a Read row unconditionally (the existence check ensure_role()
    always performs, whether or not a Create followed) and a Create row
    ONLY when `role_created` is True — logging a fabricated Create event
    for a Role that already existed would misrepresent what happened."""
    cfg = core_client.get_env_config(tag)
    common = {
        "session": "init_bot", "domain_code": "qkeee-erp-associate/init_bot",
        "environment_tag": tag, "channel": "CLI",
        "reference_doctype": "Role", "reference_name": ROLE_NAME,
        "requested_by": requested_by,
    }

    read_name = _audit_insert(cfg, {
        **common, "action": "Read", "timestamp": _now_iso(), "status": "Success",
        "user_approved": "Not Required",
    })
    _audit_submit(cfg, read_name)
    print(f"Logged Read of Role '{ROLE_NAME}' to Qkeee Bot Audit Log"
          + (f" ({read_name})." if read_name else " — insert failed, see WARN above."))

    if not role_created:
        print(f"Role '{ROLE_NAME}' already existed — skipping the Create audit entry "
              f"(nothing to record).")
        return

    create_name = _audit_insert(cfg, {
        **common, "action": "Create", "timestamp": _now_iso(), "status": "Success",
        "user_approved": "Approved", "approval_note": approval_note,
    })
    _audit_submit(cfg, create_name)
    print(f"Logged Create of Role '{ROLE_NAME}' to Qkeee Bot Audit Log"
          + (f" ({create_name})." if create_name else " — insert failed, see WARN above."))


def run_dry_run(tag: str, requested_by: str, domains=None, strip_stock_roles: bool = False) -> dict:
    _step("Health check")
    print(json.dumps(core_client.health_check(tag), indent=2))

    _step("Plan")
    plan = compute_plan(tag, domains, strip_stock_roles)
    _print_bot_findings(plan)
    if not _needs_action(plan):
        print("Nothing to do — role, doctype, bot role and grants already in place.")
        return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None}

    grants_planned = bool(plan.get("business_grants") or plan.get("bot_read_grants"))
    if grants_planned:
        ok, reason = requester_can_grant(tag, requested_by)
        if ok is False:
            print(f"ERROR: {requested_by} cannot approve these grants: {reason}. The real run "
                  f"would be refused, so no confirm token is printed. Re-run this dry-run with "
                  f"--requested-by <a user who holds System Manager>; that person approves the "
                  f"plan and is recorded in the Audit Log.", file=sys.stderr)
            return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None,
                    "requester_can_grant": False}
        if ok is None:
            print(f"WARNING: could not check whether {requested_by} may approve the grants "
                  f"({reason}). The real run checks it again.", file=sys.stderr)

    issued_at = int(time.time())
    token = _plan_token(tag, requested_by, plan, issued_at)

    if plan["role_needed"]:
        print(f"[dry-run] Would create Role '{ROLE_NAME}'.")
    for name in plan["doctypes_needed"]:
        doctype_def = next(d for d in ALL_DOCTYPES if d["name"] == name)
        print(f"[dry-run] Would create DocType '{name}' with "
              f"{len(doctype_def['fields'])} fields, "
              f"{len(doctype_def['permissions'])} permission rows.")
    for change in plan["fields_to_migrate"]:
        print(f"[dry-run] Would migrate field {change}.")
    for grant in plan.get("bot_read_grants", []):
        doctype, _, step = grant.partition(": ")
        print(f"[dry-run] Would grant Role '{ROLE_NAME}' read on '{doctype}' (permlevel 0, "
              f"read only; {'new Custom DocPerm row' if step == 'add' else 'tick read on its row'}).")
    change = plan.get("bot_role_change")
    if change:
        print(f"[dry-run] Would set bot user {change['user']}'s roles: {change['roles_before']} "
              f"-> {change['roles_after']}.")
    for grant in plan.get("business_grants", []):
        doctype, _, what = grant.partition(": ")
        print(f"[dry-run] Would grant Role '{ROLE_NAME}' "
              f"{'a new row (read)' if what == 'add' else what} on '{doctype}'.")

    _step("Confirm token")
    print(f"To run this for real, re-invoke with:\n"
          f"  --confirm-token {token} --issued-at {issued_at}\n"
          f"Valid for {DEFAULT_TOKEN_TTL_SECONDS // 60} minutes from now. "
          f"Only pass this back after the user has explicitly confirmed "
          f"the plan printed above.")
    return {"tag": tag, "dry_run": True, **plan, "confirm_token": token, "issued_at": issued_at}


def run_real(tag: str, requested_by: str, confirm_token: str, issued_at: int, domains=None,
             strip_stock_roles: bool = False) -> dict:
    _step("Health check")
    print(json.dumps(core_client.health_check(tag), indent=2))

    _step("Plan (recomputed against current target state)")
    plan = compute_plan(tag, domains, strip_stock_roles)
    _print_bot_findings(plan)

    grants = plan.get("bot_read_grants", [])
    if _needs_action(plan):
        if not confirm_token or issued_at is None:
            raise ConnectorError(
                "This run would create/change records but no --confirm-token/--issued-at "
                "was given. Run with --dry-run first, show the plan to the user, and only "
                "re-invoke with the token it prints after they explicitly confirm."
            )
        if not is_fresh(issued_at):
            raise ConnectorError(
                f"--issued-at is stale or invalid (must be within "
                f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes). Run --dry-run again to get "
                f"a fresh token."
            )
        expected = _plan_token(tag, requested_by, plan, issued_at)
        if expected != confirm_token:
            raise ConnectorError(
                "--confirm-token does not match the current plan for this target. Either "
                "the target's state changed since the dry-run (something was created/"
                "removed out of band), or requested_by/tag differs from the dry-run. Run "
                "--dry-run again and re-confirm with the new token."
            )

    approval_note = (f"dry-run confirmed by {requested_by}, token {confirm_token[:8]}..."
                      if confirm_token else "no records needed creating — nothing to confirm")

    _step(f"Role: {ROLE_NAME}")
    role_created = ensure_role(tag, requested_by, approval_note)

    results = {}
    for doctype_def in ALL_DOCTYPES:
        _step(f"DocType: {doctype_def['name']}")
        results[doctype_def["name"]] = ensure_doctype(tag, doctype_def, requested_by, approval_note)

    _step("Field migrations")
    migrated = migrate_fields(tag, requested_by, approval_note) if plan["fields_to_migrate"] else []

    _step(f"Read grants for {ROLE_NAME}")
    granted = grant_bot_reads(tag, requested_by, grants, approval_note) if grants else []

    _step("Bot user role")
    role_change = plan.get("bot_role_change")
    if role_change:
        assign_bot_role(tag, requested_by, role_change, approval_note)

    _step(f"Business permissions for {ROLE_NAME}")
    business = plan.get("business_grants", [])
    business_granted = (grant_business_permissions(tag, requested_by, business, approval_note)
                        if business else [])

    _step("Qkeee Bot Audit Log: recording Role provisioning")
    log_role_provisioning(tag, requested_by, role_created, approval_note)

    _step("qkeee-erp.env")
    env_created = ensure_qkeee_env_file_skeleton()
    print(f"{'Created empty' if env_created else 'Found existing'} "
          f"{_qkeee_env_file_path()}.")

    _step("Summary")
    summary = {
        "tag": tag,
        "dry_run": False,
        "role_created": role_created,
        "doctypes_created": [name for name, created in results.items() if created],
        "doctypes_already_present": [name for name, created in results.items() if not created],
        "fields_migrated": migrated,
        "bot_read_grants": granted,
        "bot_role_change": role_change,
        "business_grants": business_granted,
        "skipped_privileged": plan.get("skipped_privileged", []),
        "extra_rights": plan.get("extra_rights", []),
        "template_roles_empty": plan.get("template_roles_empty", []),
        "qkeee_env_file_created": env_created,
    }
    print(json.dumps(summary, indent=2))

    _step("Before going live — steady-state bot account check")
    print(
        "This run provisioned the Role/DocType under THIS script's own elevated "
        "credentials, not the steady-state bot account. Before treating this "
        "environment as ready for real writes:\n"
        "  1. Confirm the steady-state QKEEE_ERP_<TAG>_API_KEY/SECRET in "
        "qkeee-erp.env belong to a dedicated bot user that is NOT Administrator "
        "and does NOT hold System Manager.\n"
        "  2. The bot user holds '" + ROLE_NAME + "' and no other role "
        "(this run assigns it; --strip-stock-roles removes stock roles).\n"
        "  3. Keep QKEEE_ERP_<TAG>_ADMIN_API_KEY/SECRET set: the Admin Account "
        "reads the bot's and the requester's roles for every business call.\n"
        "  4. Run `erp_discover health` on tag " + tag + " and confirm "
        "bot_roles is ['" + ROLE_NAME + "'] and gaps is []. A run without --domains grants "
        "every domain's business permissions, including its template roles' rows "
        "(doctype_defs.DOMAIN_TEMPLATE_ROLES); re-run it after an upgrade adds doctypes."
    )
    return summary
