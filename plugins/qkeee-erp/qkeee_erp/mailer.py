#!/usr/bin/env python3
"""
Gateway-side email for the qkeee-erp plugin: one HTML message from the
agent's own mailbox (the Hermes email platform's EMAIL_* settings), so a
reply reaches the agent as an email turn.

Runs only in the gateway. The SMTP password never reaches the agent's
terminal sandbox, and the caller (notify.summary_email) fixes the
recipient in code. The Hermes email adapter sends plain text only; when it
gains HTML, this module can call it instead (agents
.scratch/qkeee-erp-item-run-review issue 07).

Settings, read like the adapter reads them: EMAIL_ADDRESS, EMAIL_PASSWORD,
EMAIL_SMTP_HOST, EMAIL_SMTP_PORT (default 587), EMAIL_SMTP_SECURITY
(tls | starttls | none; default tls on 465, else starttls),
EMAIL_SMTP_TLS_VERIFY (default true), optional EMAIL_FROM_NAME.
"""

import os
import smtplib
import ssl
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from .core.client import ConnectorError

SMTP_TIMEOUT = 30

_secret_reader = None


def set_secret_reader(fn) -> None:
    """register() passes Hermes' get_scoped_secret (profile .env scope)."""
    global _secret_reader
    _secret_reader = fn


def _setting(name: str, default: str = "") -> str:
    value = None
    if _secret_reader is not None:
        try:
            value = _secret_reader(name, None)
        except Exception:
            value = None
    if value in (None, ""):
        value = os.getenv(name)
    return str(value).strip() if value not in (None, "") else default


class EmailNotConfiguredError(ConnectorError):
    """No mailbox: nothing was sent."""


class EmailSendError(ConnectorError):
    """The SMTP server refused or failed: nothing was sent (or unknown)."""


def mailbox() -> dict:
    """The sending mailbox, without the password. Raises when incomplete."""
    cfg = {"address": _setting("EMAIL_ADDRESS"), "host": _setting("EMAIL_SMTP_HOST")}
    missing = [n for n, v in (("EMAIL_ADDRESS", cfg["address"]), ("EMAIL_SMTP_HOST", cfg["host"]),
                              ("EMAIL_PASSWORD", _setting("EMAIL_PASSWORD"))) if not v]
    if missing:
        raise EmailNotConfiguredError(
            f"The agent's mailbox is not configured ({', '.join(missing)} missing in the profile "
            f".env). Nothing was sent. An Operator sets them; the agent never does.")
    try:
        cfg["port"] = int(_setting("EMAIL_SMTP_PORT", "587"))
    except ValueError:
        raise EmailNotConfiguredError("EMAIL_SMTP_PORT is not a number. Nothing was sent.")
    default_security = "tls" if cfg["port"] == 465 else "starttls"
    cfg["security"] = (_setting("EMAIL_SMTP_SECURITY", default_security) or default_security).lower()
    if cfg["security"] not in ("tls", "ssl", "starttls", "none"):
        raise EmailNotConfiguredError(
            f"EMAIL_SMTP_SECURITY {cfg['security']!r}: use tls, starttls or none. Nothing was sent.")
    cfg["verify"] = _setting("EMAIL_SMTP_TLS_VERIFY", "true").lower() not in ("0", "false", "no", "off")
    cfg["from_name"] = _setting("EMAIL_FROM_NAME")
    return cfg


def build_message(cfg: dict, to: str, subject: str, html: str, text: str, cc: str = None) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    domain = cfg["address"].rsplit("@", 1)[-1] or "localhost"
    msg["Message-ID"] = f"<qkeee-{uuid.uuid4().hex[:16]}@{domain}>"
    msg["From"] = formataddr((cfg["from_name"], cfg["address"])) if cfg["from_name"] else cfg["address"]
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Auto-Submitted"] = "auto-generated"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _context(cfg) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not cfg["verify"]:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def send_html(to: str, subject: str, html: str, text: str, cc: str = None) -> dict:
    """Send one message to `to`, optionally cc'ing one more address (agents
    issue 04: the job-approval email, To the Requester, Cc the ERP admin —
    audit-only, gets no gating role). Returns {message_id, from, to, cc}."""
    if not to or "@" not in to or any(c in to for c in ",;\r\n"):
        raise EmailSendError(f"Refusing to send: {to!r} is not one email address.")
    if cc and ("@" not in cc or any(c in cc for c in ",;\r\n")):
        raise EmailSendError(f"Refusing to send: cc {cc!r} is not one email address.")
    cfg = mailbox()
    msg = build_message(cfg, to, subject, html, text, cc=cc)
    to_addrs = [to] + ([cc] if cc else [])
    try:
        if cfg["security"] in ("tls", "ssl"):
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=_context(cfg),
                                    timeout=SMTP_TIMEOUT)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
        try:
            if cfg["security"] == "starttls":
                smtp.starttls(context=_context(cfg))
            smtp.login(cfg["address"], _setting("EMAIL_PASSWORD"))
            refused = smtp.send_message(msg, from_addr=cfg["address"], to_addrs=to_addrs)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()
    except smtplib.SMTPAuthenticationError as e:
        raise EmailSendError(f"SMTP login failed for {cfg['address']}: {e}. Nothing was sent; "
                             f"an Operator checks EMAIL_PASSWORD.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise EmailSendError(f"SMTP send to {to} failed: {e}. The email was not sent, or its "
                             f"delivery is unknown; do not resend without telling the user.") from e
    if refused:
        raise EmailSendError(f"SMTP server refused recipient(s) {refused}. Nothing was sent, or "
                             f"only partially delivered — do not resend without checking.")
    return {"message_id": msg["Message-ID"], "from": cfg["address"], "to": to, "cc": cc}
