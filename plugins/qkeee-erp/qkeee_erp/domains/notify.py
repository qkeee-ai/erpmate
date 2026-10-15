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
