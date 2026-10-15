#!/usr/bin/env python3
"""
qkeee-erp-associate — task summary email to the requester.

| Operation             | What it does                                            |
|-----------------------|---------------------------------------------------------|
| notify.summary_email  | render: re-read every record of the task and build one HTML summary (subject + body) addressed to the requester. execute: send it from the agent's mailbox (mailer.py, gateway side), then log it as a Sent `Communication` on the primary record's timeline. |

The agent renders, then executes after the user has confirmed the task is
complete. The gateway sends the email through the agent's own mailbox
(the email platform's EMAIL_* settings), so a reply reaches the agent as
an email turn and the SMTP password never reaches the agent's terminal.
ERPNext only records the email; it sends nothing.

Rules held in code, not prompt:
- The recipient is always this turn's requester. No argument sets it.
- The HTML comes from one template here. Every value is escaped; the agent
  passes structured data (title, records, notes, follow-ups), never HTML.
- Render reads every record with the requester's own gated read, so a
  summary can only describe records the requester may see.
- The token (no user code) binds the exact subject and body the agent was
  given at render, so the email sent and the Communication logged are the
  rendered ones.
- The send runs after every gate and before the Audit Log row
  (Operation.pre_send). A failed send writes nothing.

ALLOWED_WRITE_DOCTYPES: "Communication". Registering it here also keeps
unscoped.generic off Communication, and lets `init-bot --domains notify`
grant the bot create on it.
"""

import datetime
import html
import urllib.parse

from .. import mailer
from ..core import client as core_client
from ..core import operations
from ..core.operations import PreparedRequest, require_args

DOMAIN_NAME = "notify"

ALLOWED_WRITE_DOCTYPES = ("Communication",)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

_ACTIONS = ("create", "update", "submit", "cancel", "delete")
_MAX_RECORDS = 20
_MAX_LINES = 10
_MAX_LINE = 300
_MAX_TITLE = 120
_DOCSTATUS = {0: "Draft", 1: "Submitted", 2: "Cancelled"}


def _records(args) -> list:
    records = args.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= _MAX_RECORDS:
        raise core_client.ConnectorError(
            f"records: a list of 1-{_MAX_RECORDS} {{doctype, name, action}}; the primary record first.")
    for i, r in enumerate(records, start=1):
        if not isinstance(r, dict) or not r.get("doctype") or not r.get("name"):
            raise core_client.ConnectorError(f"records[{i}]: needs doctype and name.")
        if r.get("action") not in _ACTIONS:
            raise core_client.ConnectorError(f"records[{i}]: action must be one of {list(_ACTIONS)}.")
    if records[0]["action"] == "delete":
        raise core_client.ConnectorError(
            "records[1] is the primary record: the email is logged on its timeline, so it must "
            "still exist. Put a deleted record after it.")
    return records


def _lines(args, key) -> list:
    value = args.get(key) or []
    if not isinstance(value, list) or len(value) > _MAX_LINES or any(
            not isinstance(v, str) or len(v) > _MAX_LINE for v in value):
        raise core_client.ConnectorError(
            f"{key}: a list of at most {_MAX_LINES} strings, each at most {_MAX_LINE} characters.")
    return value


# ---------------------------------------------------------------- render (live facts)

def _submittable(tag, doctype):
    try:
        return bool(core_client._fetch_merged_meta(tag, doctype).get("is_submittable"))
    except core_client.ConnectorError:
        return None


def _facts(ctx, record) -> dict:
    if record["action"] == "delete":
        return {"status": "Deleted"}
    doc = core_client.get_resource(ctx.tag, record["doctype"], record["name"], strip_noise=False,
                                   requested_by=ctx.requested_by, **ctx.audit_kwargs()).get("data") or {}
    submittable = _submittable(ctx.tag, record["doctype"])
    docstatus = int(doc.get("docstatus") or 0)
    status = (_DOCSTATUS.get(docstatus, str(docstatus)) if submittable is not False
              else "Saved (live)")
    out = {"status": status}
    if doc.get("status") and submittable is not False:
        out["status"] = f"{status} · {doc['status']}"
    for key in ("title", "item_name", "supplier_name", "customer_name", "employee_name"):
        if doc.get(key) and doc[key] != record["name"]:
            out["title"] = str(doc[key])
            break
    if doc.get("grand_total") is not None:
        out["amount"] = f"{doc.get('currency') or ''} {float(doc['grand_total']):,.2f}".strip()
    return out


def _render(args, ctx):
    args = dict(args)
    records = _records(args)
    args["records"] = [dict(r, facts=_facts(ctx, r)) for r in records]
    # Always from config and the clock, never from the caller: the links in
    # the email point only at this Instance.
    args["instance"] = core_client.get_env_config(ctx.tag).get("base_url", "")
    args["completed_on"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return args


# ---------------------------------------------------------------- template (pure)

def _link(base_url, doctype, name) -> str:
    slug = doctype.lower().replace(" ", "-")
    return f"{base_url.rstrip('/')}/app/{urllib.parse.quote(slug)}/{urllib.parse.quote(name, safe='')}"


def render_summary(*, title, records, notes, follow_ups, requester, tag, instance,
                   completed_on) -> tuple:
    """(subject, html_body) for the task summary. Pure: every value is
    escaped, links point only at `instance`."""
    e = html.escape
    subject = f"[{tag}] {title}"
    rows = []
    for r in records:
        f = r.get("facts") or {}
        name = e(r["name"])
        cell = (f'<a href="{e(_link(instance, r["doctype"], r["name"]))}" '
                f'style="color:#1a56db;text-decoration:none">{name}</a>'
                if instance and f.get("status") != "Deleted" else name)
        if f.get("title"):
            cell += f'<div style="color:#6b7280;font-size:12px">{e(f["title"])}</div>'
        rows.append(
            '<tr>'
            f'<td style="padding:8px 10px;border-top:1px solid #e5e7eb">{e(r["doctype"])}</td>'
            f'<td style="padding:8px 10px;border-top:1px solid #e5e7eb">{cell}</td>'
            f'<td style="padding:8px 10px;border-top:1px solid #e5e7eb">{e(r["action"].capitalize())}</td>'
            f'<td style="padding:8px 10px;border-top:1px solid #e5e7eb">{e(f.get("status", ""))}'
            + (f'<div style="color:#6b7280;font-size:12px">{e(f["amount"])}</div>' if f.get("amount") else "")
            + '</td></tr>')

    def bullets(heading, items):
        if not items:
            return ""
        lis = "".join(f'<li style="margin:4px 0">{e(i)}</li>' for i in items)
        return (f'<h3 style="font-size:14px;margin:20px 0 6px;color:#111827">{e(heading)}</h3>'
                f'<ul style="margin:0;padding-left:20px;color:#374151">{lis}</ul>')

    th = 'style="text-align:left;padding:8px 10px;font-size:12px;color:#6b7280;font-weight:600"'
    body = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;width:100%;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:8px;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827">'
        '<tr><td style="padding:20px 24px;border-bottom:1px solid #e5e7eb">'
        '<div style="font-size:12px;color:#059669;font-weight:600;letter-spacing:.04em">TASK COMPLETE</div>'
        f'<h2 style="margin:6px 0 0;font-size:18px">{e(title)}</h2>'
        '</td></tr><tr><td style="padding:16px 24px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #e5e7eb;border-radius:6px;border-collapse:separate">'
        f'<tr style="background:#f9fafb"><th {th}>DocType</th><th {th}>Record</th>'
        f'<th {th}>Action</th><th {th}>Status</th></tr>'
        + "".join(rows) + '</table>'
        + bullets("Notes", notes) + bullets("Open follow-ups", follow_ups)
        + '</td></tr><tr><td style="padding:14px 24px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280">'
        f'Instance <b>{e(tag)}</b> ({e(instance)}) · Requested by {e(requester)} · '
        f'Completed {e(completed_on)}<br>Sent by the ERPNext associate. Each change is in the '
        'Qkeee Bot Audit Log.</td></tr></table></td></tr></table></body></html>')
    return subject, body


def render_summary_text(*, title, records, notes, follow_ups, requester, tag, instance,
                        completed_on) -> str:
    """The plain-text part of the same email. Pure."""
    lines = [f"Task complete: {title}", ""]
    for r in records:
        f = r.get("facts") or {}
        extra = " · ".join(x for x in (f.get("title"), f.get("amount")) if x)
        link = (f" <{_link(instance, r['doctype'], r['name'])}>"
                if instance and f.get("status") != "Deleted" else "")
        lines.append(f"- {r['doctype']} {r['name']}: {r['action']}, {f.get('status', '')}"
                     + (f" ({extra})" if extra else "") + link)
    for heading, items in (("Notes", notes), ("Open follow-ups", follow_ups)):
        if items:
            lines += ["", f"{heading}:"] + [f"- {i}" for i in items]
    lines += ["", f"Instance {tag} ({instance}) · Requested by {requester} · Completed {completed_on}",
              "Sent by the ERPNext associate. Each change is in the Qkeee Bot Audit Log."]
    return "\n".join(lines)


def _send_email(req, args, ctx):
    """pre_send: every gate has passed. Send; the pipeline then logs."""
    body = req.body
    text = render_summary_text(
        title=str(args["title"]).strip(), records=args["records"], notes=_lines(args, "notes"),
        follow_ups=_lines(args, "follow_ups"), requester=ctx.requested_by, tag=ctx.tag,
        instance=args["instance"], completed_on=args["completed_on"])
    sent = mailer.send_html(body["recipients"], body["subject"], body["content"], text)
    body["message_id"] = sent["message_id"]
    body["sender"] = sent["from"]
    req.notes.append(f"Email sent from {sent['from']} to {sent['to']}, "
                     f"Message-ID {sent['message_id']}.")


# ---------------------------------------------------------------- prepare (no I/O)

def _prepare(args, ctx):
    require_args(args, ["title", "records", "instance", "completed_on"], ["notes", "follow_ups"])
    title = str(args["title"]).strip()
    if not title or len(title) > _MAX_TITLE:
        raise core_client.ConnectorError(f"title: 1-{_MAX_TITLE} characters.")
    records = _records(args)
    if any("facts" not in r for r in records):
        raise core_client.ConnectorError("records carry no facts: render first, then execute with "
                                         "the rendered args unchanged.")
    subject, body = render_summary(
        title=title, records=records, notes=_lines(args, "notes"),
        follow_ups=_lines(args, "follow_ups"), requester=ctx.requested_by or "", tag=ctx.tag,
        instance=args["instance"], completed_on=args["completed_on"])
    primary = records[0]
    return PreparedRequest(
        transport="resource", doctype="Communication", action="create",
        body={"communication_type": "Communication", "communication_medium": "Email",
              "sent_or_received": "Sent", "subject": subject, "content": body,
              # The requester only; the pipeline refuses a turn without one.
              "recipients": ctx.requested_by or "", "reference_doctype": primary["doctype"],
              "reference_name": primary["name"]},
        # Logging a summary of a record needs the requester's read on it.
        rbac_doctype=primary["doctype"], rbac_ptype="read",
        audit_doctype=primary["doctype"], audit_reference=primary["name"],
        notes=["Execute with these args, confirmation_token and issued_at only after the user "
               "confirmed the task is complete: the gateway sends this email to "
               "request.body.recipients from the agent's mailbox, then logs it on the "
               "timeline. Never send it any other way."])


operations.register_operation(operations.Operation(
    key="notify.summary_email", domain=DOMAIN_NAME,
    summary="task summary email to the requester: render builds it, execute sends it from the "
            "agent's mailbox and logs it on the primary record's timeline",
    prepare=_prepare, render_defaults=_render, pre_send=_send_email,
    token_policy=operations.POLICY_TOKEN,
    skip_comment=True,
    example_args={"title": "Item created: Media Marketing Services",
                  "records": [{"doctype": "Item", "name": "Media Marketing Services",
                               "action": "create"}],
                  "notes": ["Purchase service item; expense account Marketing Expenses - DL."],
                  "follow_ups": ["Add SAC 998361 before the first Purchase Invoice."]},
    args_help={"title": f"subject line, at most {_MAX_TITLE} characters",
               "records": f"[{{doctype, name, action}}], 1-{_MAX_RECORDS}, primary record first "
                          "(the email is logged on its timeline)",
               "notes": f"key values or results, at most {_MAX_LINES} lines",
               "follow_ups": f"open follow-ups, at most {_MAX_LINES} lines",
               "instance": "filled by render", "completed_on": "filled by render",
               "records[].facts": "filled by render (status, title, amount)"},
))


# ================================================================ notify.share_records
#
# Email the result of one live query to recipients in the org's own email
# domain(s) (user decision 2026-10-15: any address in the org domain, no
# per-recipient ERPNext permission check). Held in code:
# - the plugin runs the query itself, as the requester (gated read), so a
#   share holds only what the requester may read and what render returned;
# - every recipient is one plain address in an allowed domain:
#   QKEEE_ERP_EMAIL_DOMAINS (comma list, profile .env), else the
#   requester's own domain;
# - anyone beyond the requester needs the user's confirmation code (data
#   leaves the requester); the requester alone needs the token only;
# - one email per recipient (no shared To/Cc list), then one Communication.

_MAX_RECIPIENTS = 10
_MAX_ROWS = 200
_MAX_FIELDS = 12


def _allowed_domains(requester) -> set:
    raw = mailer._setting("QKEEE_ERP_EMAIL_DOMAINS")
    domains = {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}
    if not domains and requester and "@" in requester:
        domains = {requester.rsplit("@", 1)[1].lower()}
    return domains


def _recipients(args, requester, check_domain=True) -> list:
    value = args.get("recipients")
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_RECIPIENTS:
        raise core_client.ConnectorError(
            f"recipients: a list of 1-{_MAX_RECIPIENTS} email addresses.")
    allowed = _allowed_domains(requester)
    out = []
    for i, addr in enumerate(value, start=1):
        a = str(addr or "").strip().lower()
        local, _, domain = a.partition("@")
        if (a.count("@") != 1 or any(c in a for c in ",;<> \t\r\n") or not local
                or "." not in domain):
            raise core_client.ConnectorError(f"recipients[{i}]: {addr!r} is not one email address.")
        if check_domain and domain not in allowed:
            raise core_client.DoctypeNotAllowedError(
                f"Refusing to email {addr!r}: only addresses in {sorted(allowed)} may receive ERP "
                f"data. An Operator sets QKEEE_ERP_EMAIL_DOMAINS to change that.")
        if a not in out:
            out.append(a)
    return out


def _fields(args) -> list:
    fields = args.get("fields")
    if (not isinstance(fields, list) or not 1 <= len(fields) <= _MAX_FIELDS
            or any(not isinstance(f, str) or not f for f in fields)):
        raise core_client.ConnectorError(f"fields: a list of 1-{_MAX_FIELDS} field names.")
    return ["name"] + [f for f in fields if f != "name"]


def _share_render(args, ctx):
    args = dict(args)
    _recipients(args, ctx.requested_by)
    fields = _fields(args)
    limit = int(args.get("limit") or 50)
    if not 1 <= limit <= _MAX_ROWS:
        raise core_client.ConnectorError(f"limit: 1-{_MAX_ROWS}.")
    result = core_client.query_resource(
        ctx.tag, args.get("doctype"), args.get("filters"), fields, limit,
        requested_by=ctx.requested_by, **ctx.audit_kwargs())
    args["rows"] = [{f: r.get(f) for f in fields} for r in result.get("data") or []]
    args["has_more"] = bool(result.get("has_more"))
    # Always from config and the clock, never from the caller.
    args["instance"] = core_client.get_env_config(ctx.tag).get("base_url", "")
    args["generated_on"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return args


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def render_share(*, title, doctype, fields, rows, has_more, note, requester, tag, instance,
                 generated_on) -> tuple:
    """(subject, html, text) for a shared query result. Pure; every value
    is escaped and links point only at `instance`."""
    e = html.escape
    subject = f"[{tag}] {title}"
    td = 'style="padding:6px 10px;border-top:1px solid #e5e7eb;vertical-align:top"'
    th = 'style="text-align:left;padding:6px 10px;font-size:12px;color:#6b7280;font-weight:600"'
    head = "".join(f"<th {th}>{e(f)}</th>" for f in fields)
    body_rows = []
    for r in rows:
        cells = []
        for f in fields:
            v = e(_cell(r.get(f)))
            if f == "name" and instance and r.get("name"):
                v = (f'<a href="{e(_link(instance, doctype, str(r["name"])))}" '
                     f'style="color:#1a56db;text-decoration:none">{v}</a>')
            cells.append(f"<td {td}>{v}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    count = f"{len(rows)} record(s)" + (" (more exist; the list stops at the limit)" if has_more else "")
    html_body = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="760" cellpadding="0" cellspacing="0" '
        'style="max-width:760px;width:100%;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:8px;font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#111827">'
        '<tr><td style="padding:20px 24px;border-bottom:1px solid #e5e7eb">'
        '<div style="font-size:12px;color:#1a56db;font-weight:600;letter-spacing:.04em">'
        f'{e(doctype.upper())} LIST</div>'
        f'<h2 style="margin:6px 0 0;font-size:18px">{e(title)}</h2>'
        + (f'<div style="margin-top:6px;color:#374151">{e(note)}</div>' if note else "")
        + '</td></tr><tr><td style="padding:16px 24px">'
        f'<div style="color:#6b7280;margin-bottom:8px">{e(count)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #e5e7eb;border-radius:6px;border-collapse:separate">'
        f'<tr style="background:#f9fafb">{head}</tr>' + "".join(body_rows) + '</table>'
        '</td></tr><tr><td style="padding:14px 24px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280">'
        f'Instance <b>{e(tag)}</b> ({e(instance)}) · Shared by {e(requester)} · '
        f'Generated {e(generated_on)}<br>Sent by the ERPNext associate. The share is in the '
        'Qkeee Bot Audit Log.</td></tr></table></td></tr></table></body></html>')
    lines = [title, ""] + ([note, ""] if note else []) + [count, "", " | ".join(fields)]
    lines += [" | ".join(_cell(r.get(f)) for f in fields) for r in rows]
    lines += ["", f"Instance {tag} ({instance}) · Shared by {requester} · Generated {generated_on}"]
    return subject, html_body, "\n".join(lines)


def _share_prepare(args, ctx):
    require_args(args, ["title", "doctype", "fields", "recipients", "instance", "generated_on"],
                 ["filters", "limit", "note", "has_more", "rows"])
    if not isinstance(args.get("rows"), list):
        raise core_client.ConnectorError("rows are filled by render: render first, then execute "
                                         "with the rendered args unchanged.")
    title = str(args["title"]).strip()
    if not title or len(title) > _MAX_TITLE:
        raise core_client.ConnectorError(f"title: 1-{_MAX_TITLE} characters.")
    note = str(args.get("note") or "").strip()
    if len(note) > _MAX_LINE:
        raise core_client.ConnectorError(f"note: at most {_MAX_LINE} characters.")
    requester = (ctx.requested_by or "").lower()
    # No requester: the pipeline refuses the turn right after prepare.
    recipients = _recipients(args, requester, check_domain=bool(requester))
    fields = _fields(args)
    subject, body, text = render_share(
        title=title, doctype=args["doctype"], fields=fields, rows=args["rows"],
        has_more=bool(args.get("has_more")), note=note, requester=requester, tag=ctx.tag,
        instance=args["instance"], generated_on=args["generated_on"])
    return PreparedRequest(
        transport="resource", doctype="Communication", action="create",
        body={"communication_type": "Communication", "communication_medium": "Email",
              "sent_or_received": "Sent", "subject": subject, "content": body,
              "recipients": ", ".join(recipients)},
        # Sharing a list needs the requester's read on its doctype.
        rbac_doctype=args["doctype"], rbac_ptype="read",
        audit_doctype=args["doctype"],
        audit_reference=f"{args['doctype']} list ({len(args['rows'])} rows)",
        bound={"recipients": recipients, "only_requester": recipients == [requester],
               "text": text},
        notes=["Show the user the recipients, the row count and (when present) the "
               "confirmation code; never paste the HTML. Execute with these args unchanged: "
               "the gateway sends one email per recipient from the agent's mailbox. Never "
               "send it any other way."])


def _share_policy(req) -> str:
    return (operations.POLICY_TOKEN if req.bound.get("only_requester")
            else operations.POLICY_TOKEN_USER_CODE)


def _share_send(req, args, ctx):
    """pre_send: one email per recipient. None sent → raise, nothing logged.
    Some sent → log the ones that went and warn about the rest."""
    sent, failed = [], []
    for to in req.bound["recipients"]:
        try:
            sent.append(mailer.send_html(to, req.body["subject"], req.body["content"],
                                         req.bound["text"]))
        except mailer.EmailNotConfiguredError:
            raise
        except core_client.ConnectorError as e:
            failed.append(f"{to}: {e}")
    if not sent:
        raise mailer.EmailSendError("No email was sent. " + " | ".join(failed))
    req.body["recipients"] = ", ".join(s["to"] for s in sent)
    req.body["message_id"] = sent[0]["message_id"]
    req.body["sender"] = sent[0]["from"]
    req.notes.append(f"Email sent from {sent[0]['from']} to {req.body['recipients']}.")
    if failed:
        req.notes.append("WARNING: not sent to " + " | ".join(failed) + ". Tell the user.")


operations.register_operation(operations.Operation(
    key="notify.share_records", domain=DOMAIN_NAME,
    summary="email one live query result to org-domain recipients (one email each); execute "
            "sends from the agent's mailbox and logs it",
    prepare=_share_prepare, render_defaults=_share_render, pre_send=_share_send,
    token_policy=_share_policy, skip_comment=True,
    example_args={"title": "Item list", "doctype": "Item",
                  "fields": ["item_name", "item_group", "stock_uom", "disabled"],
                  "filters": [], "limit": 50, "recipients": ["admin@example.com"],
                  "note": "All Items on the Instance."},
    args_help={"title": f"subject line, at most {_MAX_TITLE} characters",
               "doctype": "DocType to list", "filters": "erp_query filters",
               "fields": f"1-{_MAX_FIELDS} field names (name is always first)",
               "limit": f"1-{_MAX_ROWS} rows (default 50)",
               "recipients": f"1-{_MAX_RECIPIENTS} addresses in the allowed org domain(s)",
               "note": f"one line, at most {_MAX_LINE} characters",
               "rows": "filled by render", "has_more": "filled by render",
               "instance": "filled by render", "generated_on": "filled by render"},
))
