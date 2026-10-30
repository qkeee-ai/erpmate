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
import json
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


def _require_bot_can_log(ctx) -> None:
    """Refuse BEFORE any email goes out when the bot cannot create the
    Communication that logs it (dev-erp 2026-10-15: the email went out,
    the log failed with 403, and the agent read it as 'not sent').
    Unreadable rights are not proof of a gap: proceed."""
    granted, _error = core_client.bot_role_grants(ctx.tag, "Communication", "create")
    if granted is False:
        base_url = core_client.get_env_config(ctx.tag).get("base_url", "")
        steps = ("An Operator (with a System Manager as --requested-by) runs `hermes qkeee-erp "
                 "init-bot --domains notify`, dry-run first, then with the printed token.")
        raise core_client.PreconditionFailedError(
            f"Refusing: no email was sent. The bot role {core_client.BOT_ROLE_NAME!r} has no "
            f"create on Communication, so the email could not be logged. {steps} Then reply "
            f"RECHECK ENV and render again.",
            gaps=[core_client.make_gap(
                tag=ctx.tag, base_url=base_url, capability="bot_create:Communication",
                who="bot", user="(the bot account)", role=core_client.BOT_ROLE_NAME,
                doctype="Communication", perm="create",
                effect="the agent cannot email ERPNext data: each email must be logged",
                grant_steps=steps)])


def _sent_but_not_logged(exc, req, args, ctx):
    """on_failure: the log failed. If the email already went out, say so,
    so nobody sends it twice."""
    message_id = (req.body or {}).get("message_id")
    if not message_id:
        return None
    return (f"The email WAS SENT to {req.body.get('recipients')} (Message-ID {message_id}). "
            f"Only logging it as a Communication failed. Do not send it again; tell the user it "
            f"was delivered and that the ERPNext log is missing.")


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
    _require_bot_can_log(ctx)
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
    _require_bot_can_log(ctx)
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
    on_failure=_sent_but_not_logged,
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
    _require_bot_can_log(ctx)
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
    _require_bot_can_log(ctx)
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
    token_policy=_share_policy, on_failure=_sent_but_not_logged, skip_comment=True,
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


# ================================================================ notify.job_approval_request
#
# ADR 0008: a kanban job's full ordered plan — every batch, every step —
# goes out once as one email to the Requester (cc the ERP admin, audit
# only) instead of one chat confirmation per batch. The Requester's reply
# binds the job-level token (issue 05, operations.verify_job_reply()).
#
# The only argument is job_id: render re-fetches the plan from the stored
# "Qkeee Bot Job Approval" row (operations.render_stored_job(), ADR 0009)
# rather than trusting whatever batches/token an agent's own args might
# claim — the email the Requester approves must show exactly what
# job_token() actually bound, never a shape the caller made up.

_JOB_APPROVAL_RENDERED_KEYS = ("status", "requested_by", "step_count", "issued_at", "expires_at",
                              "ttl_minutes", "confirmation_code", "batches")


def _job_not_pending_refusal(job_id, status) -> core_client.PreconditionFailedError:
    return core_client.PreconditionFailedError(
        f"notify.job_approval_request: job {job_id!r} is not Pending (status={status!r}); "
        f"refusing to send an approval email for it.")


def _job_approval_render(args, ctx):
    args = dict(args)
    require_args(args, ["job_id"], [])
    _require_bot_can_log(ctx)
    stored = operations.render_stored_job(args["job_id"], ctx)
    if stored["status"] != "Pending":
        raise _job_not_pending_refusal(args["job_id"], stored["status"])
    cfg = core_client.get_env_config(ctx.tag)
    admin_email = cfg.get("admin_email") or ""
    if not admin_email:
        raise core_client.ConnectorError(
            f"notify.job_approval_request needs an ERP admin address to cc — an Operator sets "
            f"{core_client._tag_env_var(ctx.tag, 'ADMIN_EMAIL')} in the profile's qkeee-erp.env. "
            f"Nothing was sent.")
    args.update(stored)
    args["admin_email"] = admin_email
    # Always from config, never from the caller: the email's own text links
    # only to this Instance.
    args["instance"] = cfg.get("base_url", "")
    return args


def _check_job_still_pending_for_email(req, args, ctx):
    """Step 7 (live, between render and send): the job could have been
    approved, or its plan changed and re-rendered under the same job_id,
    in the time between render and execute — _job_approval_render's own
    check above only saw a render-time snapshot. Mirrors
    operations._check_job_still_pending()'s same live re-check for
    job_approval.mark_approved."""
    data = operations.get_job_approval(args["job_id"], ctx)
    if data.get("status") != "Pending":
        raise _job_not_pending_refusal(args["job_id"], data.get("status"))


def render_job_approval(*, job_id, confirmation_code, step_count, batches, expires_at, ttl_minutes,
                        requester, tag, instance) -> tuple:
    """(subject, html, text) for the job's one approval email: every
    batch's every step in plain words (each submit/cancel/delete step
    already marked by _step_line()), the job's one confirmation code, and
    the fixed window it expires in. Pure: every value is escaped."""
    e = html.escape
    subject = f"[{tag}] Approval needed: job {job_id} ({step_count} step(s))"
    sections, lines_text = [], []
    for batch in batches:
        rows = "".join(f'<li style="margin:4px 0">{e(s["line"])}</li>' for s in batch["steps"])
        sections.append(
            f'<h3 style="font-size:14px;margin:20px 0 6px;color:#111827">Batch {batch["batch"]}</h3>'
            f'<ul style="margin:0;padding-left:20px;color:#374151">{rows}</ul>')
        lines_text.append(f"Batch {batch['batch']}:")
        lines_text += [f"  {s['line']}" for s in batch["steps"]]
    code_box = (
        '<div style="margin:16px 0;padding:14px 16px;background:#f0f9ff;border:1px solid #bae6fd;'
        'border-radius:6px">'
        '<div style="font-size:12px;color:#0369a1;font-weight:600">CONFIRMATION CODE</div>'
        f'<div style="font-size:22px;font-weight:700;letter-spacing:.08em;margin-top:4px">'
        f'{e(confirmation_code)}</div>'
        f'<div style="margin-top:6px;color:#374151">Reply to this email with this code to approve '
        f'every step above. This approval expires {e(expires_at)} ({ttl_minutes // 60}h from send).'
        '</div></div>')
    body = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;width:100%;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:8px;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827">'
        '<tr><td style="padding:20px 24px;border-bottom:1px solid #e5e7eb">'
        '<div style="font-size:12px;color:#b45309;font-weight:600;letter-spacing:.04em">'
        'APPROVAL NEEDED</div>'
        f'<h2 style="margin:6px 0 0;font-size:18px">Job {e(job_id)} — {step_count} step(s)</h2>'
        '</td></tr><tr><td style="padding:16px 24px">'
        + code_box + "".join(sections) +
        '</td></tr><tr><td style="padding:14px 24px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280">'
        f'Instance <b>{e(tag)}</b> ({e(instance)}) · Requested by {e(requester)}<br>Reply from this '
        'address only — a reply from anywhere else is not accepted. Sent by the ERPNext associate.'
        '</td></tr></table></td></tr></table></body></html>')
    text = (
        [f"Approval needed: job {job_id} ({step_count} step(s))", "",
         f"Confirmation code: {confirmation_code}",
         f"Reply to this email with this code to approve every step below. Expires {expires_at} "
         f"({ttl_minutes // 60}h from send).", ""] + lines_text +
        ["", f"Instance {tag} ({instance}) · Requested by {requester}",
         "Reply from this address only. Sent by the ERPNext associate."])
    return subject, body, "\n".join(text)


def _job_approval_prepare(args, ctx):
    required = ["job_id", "instance", "admin_email"] + list(_JOB_APPROVAL_RENDERED_KEYS)
    require_args(args, required, [])
    if args["status"] != "Pending":
        raise core_client.ConnectorError(
            "render first: notify.job_approval_request needs a freshly rendered Pending job — "
            "never hand-construct these args.")
    subject, html_body, text = render_job_approval(
        job_id=args["job_id"], confirmation_code=args["confirmation_code"],
        step_count=int(args["step_count"]), batches=args["batches"], expires_at=args["expires_at"],
        ttl_minutes=int(args["ttl_minutes"]), requester=args["requested_by"], tag=ctx.tag,
        instance=args["instance"])
    return PreparedRequest(
        transport="resource", doctype="Communication", action="create",
        body={"communication_type": "Communication", "communication_medium": "Email",
              "sent_or_received": "Sent", "subject": subject, "content": html_body,
              "recipients": args["requested_by"], "cc": args["admin_email"],
              "reference_doctype": core_client.JOB_APPROVAL_DOCTYPE,
              "reference_name": args["job_id"]},
        # No single business record to gate a read on (ADR 0009's consequence
        # for this issue): anchor both RBAC and audit on the Job Approval row
        # itself, the same bookkeeping-doctype exemption Audit Log/Comment get.
        rbac_doctype=core_client.JOB_APPROVAL_DOCTYPE, rbac_ptype="read",
        audit_doctype=core_client.JOB_APPROVAL_DOCTYPE, audit_reference=args["job_id"],
        bound={"text": text},
        notes=["Execute with these args, confirmation_token and issued_at only: the gateway "
               "sends this email to the Requester, cc's the ERP admin, then logs it against the "
               "Job Approval row. Never send it any other way."])


def _send_job_approval_email(req, args, ctx):
    """pre_send: every gate has passed. Send; the pipeline then logs."""
    _require_bot_can_log(ctx)
    body = req.body
    sent = mailer.send_html(body["recipients"], body["subject"], body["content"],
                            req.bound["text"], cc=body.get("cc") or None)
    body["message_id"] = sent["message_id"]
    body["sender"] = sent["from"]
    req.notes.append(
        f"Email sent from {sent['from']} to {sent['to']}"
        + (f", cc {sent['cc']}" if sent.get("cc") else "") + f", Message-ID {sent['message_id']}.")


operations.register_operation(operations.Operation(
    key="notify.job_approval_request", domain=DOMAIN_NAME,
    summary="ADR 0008: the kanban job's full ordered plan as one approval email to the Requester "
            "(cc the ERP admin, audit-only) instead of one chat confirmation per batch",
    prepare=_job_approval_prepare, render_defaults=_job_approval_render,
    preconditions=(_check_job_still_pending_for_email,),
    pre_send=_send_job_approval_email, on_failure=_sent_but_not_logged,
    token_policy=operations.POLICY_TOKEN, skip_comment=True,
    example_args={"job_id": "JOB-1"},
    args_help={"job_id": "the kanban job id operations.prepare_job() stored the plan under",
               "status": "filled by render", "requested_by": "filled by render",
               "step_count": "filled by render", "issued_at": "filled by render",
               "expires_at": "filled by render", "ttl_minutes": "filled by render",
               "confirmation_code": "filled by render", "batches": "filled by render",
               "instance": "filled by render", "admin_email": "filled by render"},
))


# ================================================================ notify.job_status_update
#
# Agents ADR 0010 point 3: every park/block a kanban job accumulates must
# reach the Requester, not just a `kanban_comment` on the card — but as one
# consolidated report per pass over the job's current state (issue 23's
# poller is the intended caller, once per pass), never one send per event.
# Live testing (28/29-Oct runs) found every block posted only as a
# kanban_comment: no email, no chat message, so nobody not watching the
# board learned a job had stalled.
#
# Render reads the job's stored monitor_platform/monitor_channel_id
# (issue 16/22, ADR 0010 point 4) and requested_by — never caller-supplied,
# same discipline as job_approval_request reading the stored plan instead
# of trusting an agent's own claim. Execute sends BOTH: an email to the
# Requester (always, logged as a Communication on the Job Approval row,
# ADR 0009's pattern) and, only when the job's monitor channel actually is
# Google Chat, a message to that stored channel/thread — never invented
# when the monitor channel was email-only. `kanban_comment` keeps logging
# each event individually on the card for audit; this is the
# Requester-facing, consolidated side of it.

_MAX_ITEMS = 25
_ITEM_KEYS = frozenset({"step", "reason", "decision_needed"})
_SUMMARY_KEYS = frozenset({"parked", "blocked", "completed"})


def _items(args) -> list:
    items = args.get("items")
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise core_client.ConnectorError(f"items: a list of at most {_MAX_ITEMS} entries.")
    for i, it in enumerate(items, start=1):
        if (not isinstance(it, dict) or set(it) != _ITEM_KEYS
                or any(not isinstance(it[k], str) or not it[k] or len(it[k]) > _MAX_LINE
                       for k in _ITEM_KEYS)):
            raise core_client.ConnectorError(
                f"items[{i}]: needs exactly {sorted(_ITEM_KEYS)}, each a non-empty string at most "
                f"{_MAX_LINE} characters.")
    return items


def _summary(args) -> dict:
    summary = args.get("summary")
    if (not isinstance(summary, dict) or set(summary) != _SUMMARY_KEYS
            or any(not isinstance(summary[k], int) or isinstance(summary[k], bool) or summary[k] < 0
                   for k in _SUMMARY_KEYS)):
        raise core_client.ConnectorError(
            f"summary: needs exactly {sorted(_SUMMARY_KEYS)}, each a non-negative integer.")
    return summary


def _job_status_render(args, ctx):
    args = dict(args)
    # "items" stays out of require_args' required list: an empty list (no
    # open park/block this pass — the good-news case) is a legitimate
    # value, and require_args treats [] as missing.
    require_args(args, ["job_id", "summary"], ["items"])
    _items(args)
    _summary(args)
    _require_bot_can_log(ctx)
    data = operations.get_job_approval(args["job_id"], ctx)
    if not data:
        raise core_client.ConnectorError(
            f"notify.job_status_update: job {args['job_id']!r} not found — nothing was sent.")
    args["requested_by"] = data.get("requested_by") or ""
    args["status"] = data.get("status") or ""
    args["monitor_platform"] = data.get("monitor_platform") or ""
    args["monitor_channel_id"] = data.get("monitor_channel_id") or ""
    # Always from config and the clock, never from the caller.
    args["instance"] = core_client.get_env_config(ctx.tag).get("base_url", "")
    args["generated_on"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return args


def render_job_status(*, job_id, items, summary, status, requester, tag, instance,
                      generated_on) -> tuple:
    """(subject, html, text) for one consolidated park/block report.
    `text` is reused as both the email's plain-text part and the Google
    Chat message body: one plain representation, two deliveries. Pure:
    every value is escaped, links point only at `instance`."""
    e = html.escape
    counts = f"{summary['parked']} parked, {summary['blocked']} blocked, {summary['completed']} completed"
    subject = f"[{tag}] Job {job_id} status: {counts}"
    rows = "".join(
        f'<li style="margin:8px 0"><b>{e(it["step"])}</b> — {e(it["reason"])}'
        f'<div style="color:#6b7280;font-size:12px">Needs: {e(it["decision_needed"])}</div></li>'
        for it in items)
    items_html = (f'<ul style="margin:0;padding-left:20px;color:#374151">{rows}</ul>' if items
                 else '<div style="color:#6b7280">No open parks or blocks since the last report.</div>')
    body = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f3f4f6;padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;width:100%;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:8px;font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827">'
        '<tr><td style="padding:20px 24px;border-bottom:1px solid #e5e7eb">'
        '<div style="font-size:12px;color:#b45309;font-weight:600;letter-spacing:.04em">JOB STATUS'
        '</div>'
        f'<h2 style="margin:6px 0 0;font-size:18px">Job {e(job_id)} ({e(status)})</h2>'
        f'<div style="margin-top:4px;color:#6b7280">{e(counts)}</div>'
        '</td></tr><tr><td style="padding:16px 24px">'
        + items_html +
        '</td></tr><tr><td style="padding:14px 24px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280">'
        f'Instance <b>{e(tag)}</b> ({e(instance)}) · Requested by {e(requester)} · '
        f'Generated {e(generated_on)}<br>Sent by the ERPNext associate.'
        '</td></tr></table></td></tr></table></body></html>')
    text_lines = [f"Job {job_id} status ({status}): {counts}", ""]
    if items:
        for it in items:
            text_lines.append(f"- {it['step']}: {it['reason']} — needs: {it['decision_needed']}")
    else:
        text_lines.append("No open parks or blocks since the last report.")
    text_lines += ["", f"Instance {tag} ({instance}) · Requested by {requester} · "
                       f"Generated {generated_on}", "Sent by the ERPNext associate."]
    return subject, body, "\n".join(text_lines)


def _job_status_prepare(args, ctx):
    required = ["job_id", "summary", "requested_by", "status", "instance", "generated_on"]
    # "items" (can be [], the good-news case) and monitor_platform/
    # monitor_channel_id (legitimately "" — no stored monitor channel, or
    # a job rendered before issue 22) stay out of required: require_args
    # treats []/"" as missing.
    require_args(args, required, ["items", "monitor_platform", "monitor_channel_id"])
    items = _items(args)
    summary = _summary(args)
    subject, body, text = render_job_status(
        job_id=args["job_id"], items=items, summary=summary, status=args["status"],
        requester=args["requested_by"], tag=ctx.tag, instance=args["instance"],
        generated_on=args["generated_on"])
    return PreparedRequest(
        transport="resource", doctype="Communication", action="create",
        body={"communication_type": "Communication", "communication_medium": "Email",
              "sent_or_received": "Sent", "subject": subject, "content": body,
              "recipients": args["requested_by"],
              "reference_doctype": core_client.JOB_APPROVAL_DOCTYPE,
              "reference_name": args["job_id"]},
        # Same bookkeeping-doctype anchor as job_approval_request (ADR 0009):
        # no single business record to gate a read on.
        rbac_doctype=core_client.JOB_APPROVAL_DOCTYPE, rbac_ptype="read",
        audit_doctype=core_client.JOB_APPROVAL_DOCTYPE, audit_reference=args["job_id"],
        bound={"text": text, "monitor_platform": args["monitor_platform"],
               "monitor_channel_id": args["monitor_channel_id"]},
        notes=["Intended as one call per poller pass (agents issue 23), reporting every "
               "currently open park/block together — never one call per individual event. "
               "Execute with these args, confirmation_token and issued_at unchanged."])


def _send_job_status(req, args, ctx):
    """pre_send: every gate has passed. Email always; a Google Chat
    message to the stored monitor channel only when that channel actually
    is Google Chat (agents ADR 0010 point 4) — a failed chat send is
    logged as a warning, never raised, since the email (the durable,
    audited half) already went out by the time this runs."""
    _require_bot_can_log(ctx)
    body = req.body
    sent = mailer.send_html(body["recipients"], body["subject"], body["content"], req.bound["text"])
    body["message_id"] = sent["message_id"]
    body["sender"] = sent["from"]
    req.notes.append(f"Email sent from {sent['from']} to {sent['to']}, "
                     f"Message-ID {sent['message_id']}.")
    platform = req.bound.get("monitor_platform")
    channel_id = req.bound.get("monitor_channel_id")
    if platform != "google_chat" or not channel_id:
        return
    try:
        from tools.send_message_tool import send_message_tool  # gateway-side helper (cron/kanban use it too)
        result = json.loads(send_message_tool(
            {"action": "send", "target": f"{platform}:{channel_id}", "message": req.bound["text"]}))
        if not result.get("success"):
            req.notes.append(f"WARNING: the Google Chat update failed ({result.get('error')!r}); "
                             f"only the email above went out. Tell the user.")
    except Exception as e:  # noqa: BLE001 — chat is the secondary channel; the email already sent
        req.notes.append(f"WARNING: the Google Chat update failed ({e!r}); only the email above "
                         f"went out. Tell the user.")


operations.register_operation(operations.Operation(
    key="notify.job_status_update", domain=DOMAIN_NAME,
    summary="agents ADR 0010: one consolidated report per poller pass of every open park/block "
            "for a kanban job, sent on email always and Google Chat when that's the job's "
            "monitor channel — never one send per event",
    prepare=_job_status_prepare, render_defaults=_job_status_render,
    pre_send=_send_job_status, on_failure=_sent_but_not_logged,
    token_policy=operations.POLICY_TOKEN, skip_comment=True,
    example_args={"job_id": "JOB-1",
                  "items": [{"step": "batch 2 step 1", "reason": "role permission 403 on submit",
                             "decision_needed": "grant Sales Invoice submit to the bot role, or "
                                                "approve submitting without it"}],
                  "summary": {"parked": 1, "blocked": 0, "completed": 3}},
    args_help={"job_id": "the kanban job id (agents ADR 0008/0010)",
               "items": f"every currently open park/block for this job, "
                       f"[{{step, reason, decision_needed}}], at most {_MAX_ITEMS}",
               "summary": "counts since the last report: {parked, blocked, completed}",
               "requested_by": "filled by render", "status": "filled by render",
               "monitor_platform": "filled by render", "monitor_channel_id": "filled by render",
               "instance": "filled by render", "generated_on": "filled by render"},
))
