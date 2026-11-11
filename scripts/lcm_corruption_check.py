#!/usr/bin/env python3
"""Scheduled lcm.db corruption check.

Runs as a `no_agent` Hermes cron job every 12h (see `cron/jobs.json`), and
once at container boot (`docker/cont-init.d/0186-lcm-corruption-check`,
same entry point). Drives `/opt/adminops/lcm-db-recover.py` `diagnose`
(and, only when that reports damage, `rebuild`) against this profile's
own `lcm.db` — both read-only against the live file (adminops/README.md's
own rule: "Read-only by default... a command that writes to live data has
a dry-run mode").

Same scope boundary as the `lcm-self-heal` skill
(skills/adminops/lcm-self-heal/SKILL.md) and for the same reason: this
script never runs `swap` or touches the gateway. A reactive chat session
at least has a human to hand the remaining steps to inline; this script
has no chat session at all, so it mails and/or posts to Google Chat
instead, with the exact remaining commands.

Stdlib only (adminops/README.md's rule: the image ships python3, no pip
packages). Email reuses the EMAIL_* settings already documented for
qkeee-erp (env.example, in the profile's own `.env`) via a small
duplicated SMTP sender — NOT an import of `qkeee_erp.mailer`, which
needs the plugin's gateway-only secret-reader wiring (see that module's
own docstring) and so cannot run in a bare cron subprocess with no
gateway/plugin context. `scripts/qkeee_erp_job_status_poller.py`
duplicates `tag_env_var()`/`read_env_file()` for the identical reason;
this script duplicates those two again rather than importing that
script, to stay a single self-contained file an Operator can read start
to finish. Chat reuses a plain Google Chat incoming webhook URL
(`QKEEE_ERP_<TAG>_OPS_CHAT_WEBHOOK`, new — see
skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example) —
a bare HTTPS POST, not the plugin's gateway-internal send_message_tool,
for the same reason.

Both the admin email and the chat webhook are optional: unconfigured
means the finding only reaches this script's own stdout (captured by
`hermes cron` / cont-init.d's boot log) — never a reason to crash.
"""

import glob
import json
import os
import smtplib
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

RECOVER_TOOL = "/opt/adminops/lcm-db-recover.py"
# Distinct from the lcm-self-heal skill's manually triggered candidates
# (`/tmp/lcm-clean-<timestamp>.db`) so this script only ever cleans up its
# own unattended output, never one a human is in the middle of swapping in.
CANDIDATE_PREFIX = "/tmp/lcm-clean-autocheck-"
SUBPROCESS_TIMEOUT = 300
SMTP_TIMEOUT = 30
CHAT_TIMEOUT = 15


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def hermes_lcm_db_path() -> str:
    """The one lcm.db for this profile. `HERMES_HOME` already resolves to
    the right profile directory for whichever profile's cron ran this
    script (default's home IS the data root; a named profile's home is
    `<root>/profiles/<name>`, matching lcm-db-recover.py's own
    `resolve_db()`), so a direct path needs no `--profile` guesswork.
    `LCM_DATABASE_PATH` is the documented override (adminops/
    lcm-db-recover.md) for a non-default location."""
    return os.environ.get("LCM_DATABASE_PATH") or os.path.join(hermes_home(), "lcm.db")


def read_env_file(path: str) -> dict:
    """A plain KEY=value dotenv file, tolerant of a missing file, blank
    lines, comments and quoted values. Duplicated from
    qkeee_erp_job_status_poller.py — see this module's own docstring."""
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
    and order-preserved. Unlike the per-tag job-status poller, there is
    exactly one lcm.db for this whole profile, so every tag's admin is a
    legitimate recipient — any of them counts as "the ERP admin" for this
    profile's infra, not for one tag's ERPNext data."""
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


def run_tool(*args) -> tuple:
    """(returncode, combined stdout+stderr) for one lcm-db-recover.py
    subcommand. Never raises on a non-zero exit — that is the tool's
    normal "found a problem" signal, not a bug in this script."""
    try:
        proc = subprocess.run([sys.executable, RECOVER_TOOL, *args],
                              capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 1, f"lcm-db-recover.py {' '.join(args)} timed out after {SUBPROCESS_TIMEOUT}s"
    except OSError as exc:
        return 1, f"could not run {RECOVER_TOOL}: {exc}"
    out = proc.stdout or ""
    if proc.stderr:
        out += ("\n" if out else "") + proc.stderr
    return proc.returncode, out


def check() -> str:
    """"" when healthy or there is nothing to check yet; otherwise the
    full report to deliver (diagnose output, plus a rebuild candidate and
    its own output when the damage is more than FTS drift)."""
    db = hermes_lcm_db_path()
    if not os.path.isfile(db):
        return ""  # no lcm.db yet (fresh profile) — not corruption
    if not os.path.isfile(RECOVER_TOOL):
        return (f"lcm-corruption-check: {RECOVER_TOOL} is missing — cannot check {db}. "
               f"See adminops/README.md: images built before 2026-10-07 need it copied in "
               f"by hand (`docker cp adminops/lcm-db-recover.py <container>:/opt/adminops/`).")

    rc, diag_out = run_tool("diagnose", "--db", db, "--scan-all")
    if rc == 0:
        return ""  # healthy

    report = [f"lcm-corruption-check: diagnose found a problem on {db}", "", "--- diagnose ---",
             diag_out]

    if "only FTS index damaged" in diag_out:
        report.append(
            "\nFTS index only — try `/lcm doctor repair apply` in a chat session first. This "
            "check never runs that itself (no live gateway session); only re-run `rebuild` "
            "if that fails.")
        return "\n".join(report)

    for old in glob.glob(CANDIDATE_PREFIX + "*"):
        try:
            os.remove(old)
        except OSError:
            pass
    out_path = f"{CANDIDATE_PREFIX}{time.strftime('%Y%m%d_%H%M%S')}.db"
    rc2, rebuild_out = run_tool("rebuild", "--db", db, "--out", out_path, "--force")
    report += ["", "--- rebuild ---", rebuild_out]
    if rc2 == 0:
        report.append(
            f"\nCandidate ready at {out_path}. The remaining steps need a human operator — "
            f"this check cannot stop its own gateway, same reasoning as the lcm-self-heal "
            f"skill. See adminops/lcm-db-recover.md steps 2, 4 and 5: stop the gateway, "
            f"`swap --new {out_path}` (dry run, then `--yes`), start the gateway, confirm "
            f"with `/lcm doctor repair`.")
    else:
        report.append(
            "\nrebuild also failed, or the candidate did not pass verify — do not swap "
            "anything in. See adminops/lcm-db-recover.md's \"Root cause\" and \"If rebuild "
            "fails, or loses too much\" sections.")
    return "\n".join(report)


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
        print("lcm-corruption-check: EMAIL_* is not configured in the profile .env; "
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
        print(f"lcm-corruption-check: email send failed: {exc}", file=sys.stderr)


def send_chat(webhooks: list, text: str) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    for url in webhooks:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=CHAT_TIMEOUT).close()
        except (urllib.error.URLError, OSError) as exc:
            print(f"lcm-corruption-check: Google Chat webhook send failed ({url!r}): {exc}",
                 file=sys.stderr)


def main() -> None:
    report = check()
    if not report:
        return  # silent tick — matches qkeee_erp_job_status_poller.py's own convention
    # Non-empty stdout is delivered verbatim by `hermes cron` / captured by the boot log.
    print(report)
    emails, webhooks = admin_emails_and_webhooks()
    if emails:
        send_email(emails, f"[{socket.gethostname()}] lcm.db corruption detected", report)
    else:
        print("lcm-corruption-check: no QKEEE_ERP_<tag>_ADMIN_EMAIL configured; this report "
             "only reached the cron/boot log.", file=sys.stderr)
    if webhooks:
        send_chat(webhooks, report)


if __name__ == "__main__":
    main()
