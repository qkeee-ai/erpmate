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
it. It's a direct `_request()` call, after
`validate_environment_metadata_requester()` and with a manual
`_log_read()` call, mirroring `run_query_report()`'s own pattern.

`modules` uses an explicit `limit=1000` instead of Frappe's
`limit_page_length: 0` ("no limit" sentinel): `query_resource()` doesn't
expose that sentinel — it always fetches `limit + 1` and reports
`has_more`. 1000 is comfortably above any real org's `Module Def` count;
`has_more` is still surfaced so a caller isn't silently handed a
truncated list.

Whose permission each read needs (agents/docs/adr/0001): everything here
is Environment Metadata — `DocType`, `Module Def` and the `get_versions`
RPC. The REQUESTER's own permission on them is never checked (it says
nothing about the business task); the requester must still be given and
bound to the session sender, and every read is audit-logged under them.
The BOT account needs read on `DocType` (getdoctype) and `Module Def`;
`init_bot.py` grants the latter to the `Qkeee Bot` role. A bot without
it gets a real 403 here — a permission gap to report with the role-gap
prompt (`erp_discover health` lists it), not a bug in this script.

Non-negotiable this script exists to serve: never propose a field/doctype
that isn't confirmed live on the target instance (Non-negotiable 4,
`00-conventions.md`). GitHub docs and docs.frappe.io describe the general
shape of an app; only this script's output (backed by
`/api/resource/DocType/<name>` and friends) confirms what a specific
org's instance actually has.
"""


from .core.client import (
    BOT_ROLE_NAME,
    ConnectorError,
    GateRefusal,
    make_gap,
    _log_read,
    _request,
    get_env_config,
    get_resource,
    query_resource,
    read_rpc,
    validate_environment_metadata_requester,
)

_GET_VERSIONS = "frappe.utils.change_log.get_versions"

# Frappe's merged meta — the base DocType plus Custom Fields and Property
# Setters, exactly what the desk form uses. The bare DocType record
# (`/api/resource/DocType/<name>`) has neither (W39).
_MERGED_META = "/api/method/frappe.desk.form.load.getdoctype"

# Fields kept from a DocType meta doc — everything else (permissions,
# print settings, form layout hints, etc.) is noise for the "what does
# this doctype actually look like" question this script answers.
_META_FIELD_KEYS = {
    "fieldname", "label", "fieldtype", "reqd", "options", "read_only",
    "hidden", "default", "unique", "in_list_view",
    # Conditional rules and limits a create can fail on (issue 05):
    # `mandatory_depends_on` makes a field mandatory only under a condition
    # `reqd` doesn't show; `fetch_from` fills a field from a Link.
    "mandatory_depends_on", "depends_on", "read_only_depends_on", "fetch_from",
    "permlevel", "length", "non_negative",
}


def list_installed_apps(tag: str, *, requested_by: str = None, session_id: str = None,
                         domain_code: str = None, channel: str = None,
                         channel_metadata: dict = None, prompt_summary: str = None,
                         latest_prompt: str = None) -> dict:
    """Installed-apps + version list — the same data the ERPNext desk's
    Help > About dialog shows. Frappe exposes this via a whitelisted RPC
    method (any logged-in user may call it), not a REST resource, so it
    can't route through get_resource()/query_resource() directly.
    Environment Metadata (ADR 0001): the requester must be present and
    session-bound, but no permission of theirs is checked — there used to
    be a 'Module Def' proxy check here, and it failed every HR user.

    Not stable across every Frappe/ERPNext version — if this method name
    has moved, or is blocked by the instance's whitelist policy entirely
    (`PermissionError: ... is not whitelisted`), `_request` raises a
    normal ConnectorError, and the `modules` subcommand (a plain REST
    read that works even when this RPC is blocked) is the expected next
    step — not a rare fallback.
    """
    validate_environment_metadata_requester(
        tag, requested_by, _GET_VERSIONS, session_id=session_id, domain_code=domain_code,
        channel=channel, channel_metadata=channel_metadata, prompt_summary=prompt_summary,
        latest_prompt=latest_prompt)
    cfg = get_env_config(tag)
    try:
        result = _request(cfg, "GET", f"/api/method/{_GET_VERSIONS}")
        payload = {"source": _GET_VERSIONS, "apps": result.get("message", result)}
    except ConnectorError as e:
        payload = {
            "source": _GET_VERSIONS, "error": str(e),
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
    (mandatory flag)/options (Link target or Select choices) and the
    conditional keys (mandatory_depends_on, depends_on, fetch_from, ...)
    for every field actually on this instance, plus module/istable/
    issubmittable/custom flags, the naming rule (autoname, naming_rule,
    title_field, naming_series_options), and `conditional_mandatory`:
    every field with a mandatory_depends_on expression. Authoritative over any GitHub README or docs.frappe.io
    page, which describe the general shape but not this org's
    customizations (custom fields, altered mandatory flags, etc.).

    Reads Frappe's merged meta (`frappe.desk.form.load.getdoctype`): the
    DocType plus its Custom Fields and Property Setters — e.g. India
    Compliance's `gstin`/`pan`/mandatory `gst_category`. If that RPC fails
    (other than "doesn't exist"), falls back to a single-resource GET on
    the DocType record itself (`/api/resource/DocType/<name>`), which
    returns its base `fields` child table inline but NO custom fields —
    `custom_fields_merged: false` then says so. Deliberately NOT a query against the standalone
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
    read_ctx = dict(session_id=session_id, domain_code=domain_code, requested_by=requested_by,
                    channel=channel, channel_metadata=channel_metadata,
                    prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    merged_error = None
    try:
        merged = read_rpc(tag, "GET", _MERGED_META, params={"doctype": doctype},
                          gate_doctype="DocType", log_doctype="DocType", log_name=doctype,
                          **read_ctx)
        doc = next((d for d in merged.get("docs") or [] if d.get("name") == doctype), None)
        if doc is None:
            raise ConnectorError(f"getdoctype returned no meta for {doctype!r}")
    except ConnectorError as e:
        if "(404)" in str(e) or "DoesNotExistError" in str(e):
            raise
        # Fallback: the bare DocType record — Custom Fields / Property
        # Setters NOT included; flagged so callers can say so.
        merged_error = str(e)
        doc = get_resource(tag, "DocType", doctype, **read_ctx).get("data") or {}
    fields = [
        {k: f.get(k) for k in _META_FIELD_KEYS if k in f}
        for f in doc.get("fields", [])
    ]
    series = next((f for f in fields if f.get("fieldname") == "naming_series"), None)
    return {
        "doctype": doc.get("name"),
        "module": doc.get("module"),
        "custom": bool(doc.get("custom")),
        "istable": bool(doc.get("istable")),
        # DocType field is `is_submittable` (W40); key name kept for callers
        "issubmittable": bool(doc.get("is_submittable") or doc.get("issubmittable")),
        "description": doc.get("description"),
        "fields": fields,
        # Doc-level naming: an `autoname` of "naming_series:" needs one of
        # `naming_series_options`; "field:<x>" makes <x> the record name.
        "autoname": doc.get("autoname"),
        "naming_rule": doc.get("naming_rule"),
        "title_field": doc.get("title_field"),
        "naming_series_options": ([o for o in (series.get("options") or "").splitlines()
                                   if o.strip()] if series else None),
        # Listed for the user to confirm, never evaluated here.
        "conditional_mandatory": [
            {"fieldname": f["fieldname"], "expression": f["mandatory_depends_on"]}
            for f in fields if f.get("mandatory_depends_on")
        ],
        # getdoctype's FormMeta attaches the doctype's ACTIVE workflow, plus
        # its Workflow State docs, as `__workflow_docs`. None: not known
        # (bare-DocType fallback, or a Frappe build without the key) —
        # never read as "no workflow".
        "active_workflows": ([w.get("name") for w in doc["__workflow_docs"]
                              if w.get("name") and w.get("doctype", "Workflow") == "Workflow"]
                             if isinstance(doc.get("__workflow_docs"), list) else None),
        "custom_fields_merged": merged_error is None,
        "custom_fields_error": merged_error,
    }


def _module_app(tag: str, module: str, read_ctx: dict) -> tuple:
    """(app_name, error) for a module. (None, None): no module to look up."""
    if not module:
        return None, None
    try:
        data = get_resource(tag, "Module Def", module, **read_ctx).get("data") or {}
        return data.get("app_name"), None
    except ConnectorError as e:
        return None, str(e)


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
    app_name, app_lookup_error = _module_app(
        tag, module, dict(session_id=session_id, domain_code=domain_code,
                          requested_by=requested_by, channel=channel,
                          channel_metadata=channel_metadata, prompt_summary=prompt_summary,
                          latest_prompt=latest_prompt))
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


# Apps every ERPNext instance has; anything else installed is reported as
# `custom_apps_installed` (its validate hooks are invisible to preflight).
_CORE_APPS = {"frappe", "erpnext"}


def _employee_settings_check(tag: str, payload, read_ctx: dict, gap_for) -> tuple:
    """HR Settings `emp_created_by`: "Employee Number" names each Employee
    by its employee_number, so that field becomes mandatory."""
    try:
        settings = get_resource(tag, "HR Settings", "HR Settings", **read_ctx).get("data") or {}
    except ConnectorError as e:
        gap = gap_for(e, "HR Settings", "read", "HR Manager",
                      "preflight cannot read HR Settings emp_created_by, so it cannot tell whether "
                      "employee_number is mandatory")
        return None, [f"could not read HR Settings emp_created_by: {gap['effect']}"], [gap]
    rule = settings.get("emp_created_by")
    blockers = []
    if rule == "Employee Number" and payload is not None and not payload.get("employee_number"):
        blockers.append("HR Settings emp_created_by = Employee Number: 'employee_number' names the "
                        "record and is not in the payload")
    return f"HR Settings emp_created_by = {rule}", blockers, []


# Per-doctype settings checks (preflight rule 5). Each returns
# (settings_rule text or None, blockers, gaps).
_SETTINGS_CHECKS = {"Employee": _employee_settings_check}


def preflight(tag: str, doctype: str, *, payload: dict = None, requested_by: str = None,
              session_id: str = None, domain_code: str = None, channel: str = None,
              channel_metadata: dict = None, prompt_summary: str = None,
              latest_prompt: str = None) -> dict:
    """Write-readiness gate for one create on `doctype` (issue 06). Run it
    for every doctype a spec writes and paste the result into the spec.

    `ready` is False when any blocker exists:
    1. custom fields were not merged (bare-DocType meta): the agent must not
       create until the user explicitly overrides, recorded in the spec;
    2. a mandatory field is missing from `payload` (a field filled by its
       default or by fetch_from is listed, never a blocker). Without a
       payload, fields are listed and not judged;
    3. — conditional mandatory fields are listed for the user to confirm,
       never evaluated, never a blocker;
    4. an active Workflow (named), or the workflow state is unknown;
    5. a doctype settings check (Employee: HR Settings emp_created_by)
       fails, or cannot be read.
    6. Permission gaps use the `health` gap shape (client.make_gap()). A
       gap that stops a rule from running is also a blocker.

    Preflight cannot see server scripts or custom-app validate hooks; they
    run only on save. execute_write.py's structured failure covers those.
    """
    read_ctx = dict(requested_by=requested_by, session_id=session_id, domain_code=domain_code,
                    channel=channel, channel_metadata=channel_metadata,
                    prompt_summary=prompt_summary, latest_prompt=latest_prompt)
    base_url = get_env_config(tag).get("base_url", "")

    def gap_for(exc, gap_doctype, perm, requester_role, effect):
        if isinstance(exc, GateRefusal):
            who, user, role = "requester", requested_by, requester_role
        else:
            who, user, role = "bot", "(the bot account)", BOT_ROLE_NAME
        return make_gap(tag=tag, base_url=base_url, capability=f"preflight:{gap_doctype}", who=who,
                        user=user, role=role, doctype=gap_doctype, perm=perm, effect=effect,
                        error=str(exc)[:300])

    meta = doctype_meta(tag, doctype, **read_ctx)
    blockers, gaps = [], []
    merged = bool(meta.get("custom_fields_merged"))
    if not merged:
        blockers.append("The custom fields were not merged. Custom mandatory fields may be "
                        "missing. Do not create until the user overrides. Record the override in "
                        f"the spec. (Cause: {meta.get('custom_fields_error')})")

    def in_payload(fieldname):
        return None if payload is None else payload.get(fieldname) not in (None, "", [])

    mandatory = []
    for f in meta.get("fields", []):
        if not f.get("reqd"):
            continue
        entry = {"fieldname": f["fieldname"], "in_payload": in_payload(f["fieldname"])}
        if f.get("default") not in (None, ""):
            entry["filled_by"] = "default"
        elif f.get("fetch_from"):
            entry["filled_by"] = "fetch_from"
        mandatory.append(entry)
        if entry["in_payload"] is False and "filled_by" not in entry:
            blockers.append(f"mandatory field '{f['fieldname']}' is not in the payload")
    conditional = [dict(c, in_payload=in_payload(c["fieldname"]))
                   for c in meta.get("conditional_mandatory", [])]

    workflows = meta.get("active_workflows")
    if workflows is None:
        workflow_active = None
        blockers.append(f"Preflight could not confirm whether a Workflow is active on "
                        f"'{doctype}'. Confirm it with the user or an admin.")
    else:
        workflow_active = bool(workflows)
        if workflows:
            blockers.append(f"Workflow {', '.join(workflows)} is active on '{doctype}'. The new "
                            f"record may land in a workflow state the user did not expect.")

    owning_app, app_error = _module_app(tag, meta.get("module"), read_ctx)
    try:
        apps = list_modules(tag, **read_ctx).get("apps_seen_via_modules") or []
        custom_apps = sorted(set(apps) - _CORE_APPS)
    except ConnectorError as e:
        custom_apps = None
        gaps.append(gap_for(e, "Module Def", "read", BOT_ROLE_NAME,
                            "preflight cannot list installed custom apps, whose validation "
                            "this create may meet"))

    settings_rule = None
    check = _SETTINGS_CHECKS.get(doctype)
    if check:
        settings_rule, check_blockers, check_gaps = check(tag, payload, read_ctx, gap_for)
        blockers += check_blockers
        gaps += check_gaps

    return {
        "doctype": meta.get("doctype") or doctype,
        "meta_source": "getdoctype" if merged else "bare_doctype",
        "custom_fields_merged": merged,
        "owning_app": owning_app,
        "owning_app_error": app_error,
        "custom_apps_installed": custom_apps,
        "mandatory": mandatory,
        "conditional_mandatory": conditional,
        "naming": {"autoname": meta.get("autoname"),
                   "series_options": meta.get("naming_series_options"),
                   "settings_rule": settings_rule},
        "workflow_active": workflow_active,
        "ready": not blockers,
        "blockers": blockers,
        "gaps": gaps,
    }
