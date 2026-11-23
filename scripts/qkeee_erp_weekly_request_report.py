#!/usr/bin/env python3
"""Scheduled weekly tasks-received-vs-completed report (agents issue 11).

Runs as a `no_agent` Hermes cron job every Monday 09:00 server time
(`cron/jobs.json` job `qkeee-erp-weekly-request-report`), the same
boundary as the report window itself (D11-a/D11-e: Mon 00:00 -> Sun
23:59:59.999999, generated the following Monday). Modeled directly on
`lcm_corruption_check.py` — the existing precedent for "org-wide, no
single requester, periodic finding" (`cron/jobs.json`'s `lcm-corruption-
check`), not `notify.job_status_update` (which is per-kanban-job,
addressed to that job's own Requester). **Ships paused**, same
convention as that script and `qkeee_erp_job_status_poller.py`: an
Operator confirms `QKEEE_ERP_<TAG>_ADMIN_EMAIL` (and optionally
`_OPS_CHAT_WEBHOOK`) is set, then
`hermes cron resume qkeee-erp-weekly-request-report`.

Data read: unlike `lcm_corruption_check.py`, this script CAN `import
qkeee_erp.core.request_registry`/`request_report` directly instead of
duplicating SQL — those modules are stdlib-only with no gateway-secret
dependency (the thing `lcm_corruption_check.py` and
`qkeee_erp_job_status_poller.py` duplicate `read_env_file`/
`tag_env_var` to avoid is `qkeee_erp.mailer`'s gateway-only secret
reader, not `request_registry`/`request_report`). So: reuse
`request_registry.default_store_path()` and `request_report.
build_report()`, but duplicate the email/webhook *senders* exactly as
`lcm_corruption_check.py` does (own `send_email`/`send_chat`, same
`_smtp_settings()` shape, same reasoning in that module's docstring).

Recipients: `admin_emails_and_webhooks()` — the exact helper already in
`lcm_corruption_check.py` (every configured `QKEEE_ERP_<tag>_ADMIN_EMAIL`/
`_OPS_CHAT_WEBHOOK` in this profile's `qkeee-erp.env`, deduplicated). No
new env var. Delivery is both email and Google Chat, to the ops/admin
address(es) already configured for infra alerts — never to each
individual Requester named in the report (D11-f): `ADMIN_EMAIL` already
sees Requester correspondence as a cc on `notify.job_approval_request`,
so this adds no new privacy surface beyond that existing "admin is a
trusted internal reader" boundary.

Silent only when `request_registry.db` doesn't exist yet (fresh profile,
no requests ever) — unlike `lcm_corruption_check.py`'s "silent when
healthy", a week with zero activity is itself worth one line ("0
received, 0 completed this week"), not a silent skip.

Stdlib only (adminops/README.md's rule: the image ships python3, no pip
packages) — `qkeee_erp.core.request_registry`/`request_report` are
themselves stdlib-only, so importing them does not break this.
"""

import json
import os
import smtplib
import socket
import ssl
import sys
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

SMTP_TIMEOUT = 30
CHAT_TIMEOUT = 15

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN_DIR = os.path.join(_REPO_ROOT, "plugins", "qkeee-erp")
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from qkeee_erp.core import request_registry, request_report  # noqa: E402


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def state_db_path() -> str:
    """hermes-agent's own token/cost ledger, sibling to `lcm.db` (agents
    issue 11 §8)."""
    return os.environ.get("HERMES_STATE_DB_PATH") or os.path.join(hermes_home(), "state.db")


def read_env_file(path: str) -> dict:
    """A plain KEY=value dotenv file, tolerant of a missing file, blank
    lines, comments and quoted values. Duplicated from
    lcm_corruption_check.py — see this module's own docstring."""
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def admin_emails_and_webhooks() -> tuple:
    """Every configured `QKEEE_ERP_<tag>_ADMIN_EMAIL` /
    `_OPS_CHAT_WEBHOOK` across every tag in qkeee-erp.env, deduplicated
    and order-preserved. Duplicated from lcm_corruption_check.py."""
    env = read_env_file(os.path.join(hermes_home(), "plugin-data", "qkeee-erp", "qkeee-erp.env"))
    emails, webhooks = [], []
    for key, value in env.items():
        if not value:
            continue
        if key.endswith("_ADMIN_EMAIL") and value not in emails:
            emails.append(value)
        elif key.endswith("_OPS_CHAT_WEBHOOK") and value not in webhooks:
            webhooks.append(value)
    return emails, webhooks


def build() -> str:
    """"" when there is nothing to report yet (no request_registry.db:
    a fresh profile that has never tracked a request); otherwise the
    full plain-text report for the last completed week, with the cost
    checkpoint advanced (this is the one authoritative scheduled run —
    see request_report.build_report()'s own docstring on why an on-demand
    pull must not also do this)."""
    store = request_registry.default_store_path(hermes_home())
    if not os.path.isfile(store):
        return ""
    since_us, until_us = request_report.last_completed_week_window()
    report = request_report.build_report(store, state_db_path(), since_us, until_us,
                                          advance_checkpoint=True)
    return request_report.format_report_text(report)


def _smtp_settings(env: dict) -> dict:
    """Mirrors qkeee_erp.mailer.mailbox()'s settings shape — duplicated,
    not imported (see this module's own docstring)."""
    address, password, host = env.get("EMAIL_ADDRESS", ""), env.get("EMAIL_PASSWORD", ""), \
        env.get("EMAIL_SMTP_HOST", "")
    if not (address and password and host):
        return {}
    try:
        port = int(env.get("EMAIL_SMTP_PORT") or "587")
    except ValueError:
        return {}
    security = (env.get("EMAIL_SMTP_SECURITY") or ("tls" if port == 465 else "starttls")).lower()
    if security not in ("tls", "ssl", "starttls", "none"):
        return {}
    verify = (env.get("EMAIL_SMTP_TLS_VERIFY") or "true").lower() not in ("0", "false", "no", "off")
    return {"address": address, "password": password, "host": host, "port": port,
           "security": security, "verify": verify, "from_name": env.get("EMAIL_FROM_NAME", "")}


def send_email(to_addrs: list, subject: str, body: str) -> None:
    cfg = _smtp_settings(read_env_file(os.path.join(hermes_home(), ".env")))
    if not cfg:
        print("weekly-request-report: EMAIL_* is not configured in the profile .env; "
             "skipping email.", file=sys.stderr)
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((cfg["from_name"], cfg["address"])) if cfg["from_name"] else cfg["address"]
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Auto-Submitted"] = "auto-generated"
    tls_ctx = ssl.create_default_context()
    if not cfg["verify"]:
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
    try:
        if cfg["security"] in ("tls", "ssl"):
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=tls_ctx, timeout=SMTP_TIMEOUT)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
        try:
            if cfg["security"] == "starttls":
                smtp.starttls(context=tls_ctx)
            smtp.login(cfg["address"], cfg["password"])
            smtp.send_message(msg, from_addr=cfg["address"], to_addrs=to_addrs)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()
    except (smtplib.SMTPException, OSError) as exc:
        print(f"weekly-request-report: email send failed: {exc}", file=sys.stderr)


def send_chat(webhooks: list, text: str) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    for url in webhooks:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=CHAT_TIMEOUT).close()
        except (urllib.error.URLError, OSError) as exc:
            print(f"weekly-request-report: Google Chat webhook send failed ({url!r}): {exc}",
                 file=sys.stderr)


def main() -> None:
    report = build()
    if not report:
        return  # fresh profile, no requests ever tracked — not a reason to report
    print(report)
    emails, webhooks = admin_emails_and_webhooks()
    subject = f"[{socket.gethostname()}] Weekly request report"
    if emails:
        send_email(emails, subject, report)
    else:
        print("weekly-request-report: no QKEEE_ERP_<tag>_ADMIN_EMAIL configured; this report "
             "only reached the cron/boot log.", file=sys.stderr)
    if webhooks:
        send_chat(webhooks, report)


if __name__ == "__main__":
    main()
