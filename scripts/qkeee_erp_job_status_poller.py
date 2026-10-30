#!/usr/bin/env python3
"""Scheduled poller for open ERPNext kanban jobs (agents ADR 0010, issue 23).

Runs as a `no_agent` Hermes cron job (see `cron/jobs.json`), on a short
interval. ADR 0010 point 4's "monitor" role needs something to drive a
proactive update once the dispatching turn ends, without a resident
process — this script is a thin polling loop over durable state: the
"Qkeee Bot Job Approval" doctype's own `execution_log`/`status`, AND
(ADR 0010's own Consequences: "polls active ... rows and their cards'
kanban state") the linked kanban card's board status via `hermes kanban
show`, for the one card recorded on the row at render time
(`kanban_card_id`, issue 23) — never a resident process, and never more
than that one read per job per pass.

For each open (Pending/Approved) job whose `execution_log` grew OR
whose linked card's board status changed since this script's own last
report for it (the `last_notified_log_index`/`last_notified_card_status`
watermarks — two independent signals, since a card can be blocked, or
reach `done`, without a new execution_log entry of its own), collects
every open parked/failed step into ONE `notify.job_status_update` call
(agents issue 21) — never one send per individual event (ADR 0010 point
3), and never repeated while the card stays in the SAME blocked/done
state across later polls.

Deliberately calls the Operator CLI (`python -m hermes_cli.main
qkeee-erp ...` / `... kanban show ...`) rather than importing the
plugin's own Python internals directly: those CLIs are the entry
points already built, tested and production-hardened for exactly this
shape of call — an operator-level identity named via `--requested-by`
(qkeee-erp), no live gateway session needed for either. Importing
`qkeee_erp_plugin` cold from a bare cron subprocess would have to
re-solve problems (module identity, settings/credential resolution)
the CLI already solves.

NOT YET LIVE-TESTED end to end (agents .scratch/qkeee-erp-kanban-
execution-steering/issues/23-cron-job-status-poller.md) — the CLI
shell-out shapes are grounded in cli.py's/hermes_cli/kanban.py's own
argument parsing (as of this writing), but no real gateway + ERPNext
instance + kanban board has exercised this script yet. Also note: a
fully completed job stays in Pending/Approved forever (no terminal
status transition is wired up anywhere in ADR 0010's scope so far), so
this poller keeps querying it every pass — harmless (the watermarks
stop it reporting twice) but worth a follow-up if the open-job count
ever grows large."""

import json
import os
import subprocess
import sys

_OPEN_STATUSES = ("Pending", "Approved")
_PROMPT_SUMMARY = "scheduled job status poll (agents ADR 0010, issue 23)"


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def tag_env_var(tag: str, suffix: str) -> str:
    """Mirrors qkeee_erp.core.client._tag_env_var() exactly — this script
    has no import of that module (see module docstring), so the naming
    rule is duplicated here, not shared."""
    sanitized = "".join(c if c.isalnum() else "_" for c in tag.upper()) or "DEFAULT"
    return f"QKEEE_ERP_{sanitized}_{suffix}"


def read_env_file(path: str) -> dict:
    """A plain KEY=value dotenv file, tolerant of a missing file (an
    unconfigured tag just has no admin email, handled by the caller) and
    of blank lines, comments and quoted values."""
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


class HermesCliError(RuntimeError):
    """`python -m hermes_cli.main qkeee-erp ...` exited non-zero."""


def _run_hermes_cli(subcommand: str, *args) -> dict:
    proc = subprocess.run([sys.executable, "-m", "hermes_cli.main", subcommand, *args],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise HermesCliError(
            f"hermes {subcommand} {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)


def run_hermes(*args) -> dict:
    return _run_hermes_cli("qkeee-erp", *args)


def card_status(card_id: str) -> str:
    """The linked kanban card's own board status (e.g. "blocked", "done"),
    or "" when there is no card_id, the card can't be found, or the
    `hermes kanban` CLI call itself fails — best-effort: a card lookup
    gap must never stop the execution_log half of this poll from
    reporting, so every failure here degrades to "no change in card
    status" rather than raising."""
    if not card_id:
        return ""
    try:
        return (_run_hermes_cli("kanban", "show", card_id, "--json").get("task") or {}).get("status") or ""
    except (HermesCliError, ValueError, OSError):
        return ""


def item_for_log_entry(entry: dict) -> dict:
    """{step, reason, decision_needed} for one parked/failed execution_log
    entry — notify.job_status_update's own required item shape. Never
    invents a decision beyond what the stored `write_failure`/`checked`
    already say; a step with neither just needs plain human review."""
    step = f"batch {entry.get('batch_index')} step {entry.get('step_index')}"
    reason = entry.get("error") or "unknown reason"
    write_failure = entry.get("write_failure") or {}
    if write_failure.get("missing_fields"):
        decision = f"provide a value for: {', '.join(write_failure['missing_fields'])}"
    elif write_failure.get("invalid_links"):
        decision = f"fix invalid link(s): {write_failure['invalid_links']!r}"
    elif entry.get("checked"):
        decision = f"review ({entry['checked']}) and decide whether to retry or skip this step"
    else:
        decision = "needs human review — see reason"
    return {"step": step, "reason": reason, "decision_needed": decision}


def build_report(execution_log: list) -> tuple:
    """(items, summary) for notify.job_status_update from one job's full,
    durable execution_log — every currently open park/block, plus counts.
    "blocked" approximates to a logged hard-stop (`outcome == "failed"`):
    Job Approval has no separate literal "blocked" state of its own."""
    items = [item_for_log_entry(e) for e in execution_log if e.get("outcome") in ("parked", "failed")]
    summary = {"parked": sum(1 for e in execution_log if e.get("outcome") == "parked"),
              "blocked": sum(1 for e in execution_log if e.get("outcome") == "failed"),
              "completed": sum(1 for e in execution_log if e.get("outcome") == "succeeded")}
    return items, summary


def _card_status_item(status: str) -> dict:
    return {"step": "kanban card", "reason": f"the card's own board status is {status!r}",
           "decision_needed": "review the card on the board — this didn't come from a "
                              "specific execution_log step"}


def poll_tag(tag: str, admin_email: str) -> list:
    """Every job_id reported this pass for `tag`. One notify.job_status_
    update render+execute, then one job_approval.mark_notified execute,
    per changed job — never a batched/shared call across jobs (ADR 0010
    point 3 is about events WITHIN a job, not across jobs)."""

    def cli(*args, **extra_flags) -> dict:
        """run_hermes(*args, "--tag", tag, "--requested-by", admin_email,
        <extra --flag value pairs>) — every call in this function needs
        the same tag/requester pair; one place to build it so a new call
        can't silently drop one."""
        flags = []
        for key, value in extra_flags.items():
            flags += [f"--{key.replace('_', '-')}", value]
        return run_hermes(*args, "--tag", tag, "--requested-by", admin_email, *flags)

    listing = cli("query", "Qkeee Bot Job Approval",
                 filters=json.dumps([["status", "in", list(_OPEN_STATUSES)]]),
                 fields=json.dumps(["job_id", "status", "execution_log", "step_count",
                                    "last_notified_log_index", "kanban_card_id",
                                    "last_notified_card_status"]),
                 limit="100")
    reported = []
    for row in (listing.get("result") or {}).get("data") or []:
        execution_log = json.loads(row.get("execution_log") or "[]")
        log_watermark = int(row.get("last_notified_log_index") or 0)
        prior_card_status = row.get("last_notified_card_status") or ""
        current_card_status = card_status(row.get("kanban_card_id") or "")
        log_grew = len(execution_log) > log_watermark
        card_changed = current_card_status != prior_card_status
        if not log_grew and not card_changed:
            continue  # nothing new since this script's own last report
        job_id = row["job_id"]
        items, summary = build_report(execution_log)
        if card_changed and current_card_status == "blocked":
            items.append(_card_status_item(current_card_status))
        rendered = cli("render", op="notify.job_status_update",
                      args=json.dumps({"job_id": job_id, "items": items, "summary": summary}),
                      prompt_summary=_PROMPT_SUMMARY)
        out = rendered["result"]
        cli("execute-write", op="notify.job_status_update", args=json.dumps(out["args"]),
           confirmation_token=out["confirmation_token"], issued_at=str(out["issued_at"]),
           latest_prompt=_PROMPT_SUMMARY)
        # Only after the report actually sent: a failed send above raises
        # (HermesCliError), skipping this, so the next poll retries the
        # same window rather than silently marking it reported.
        cli("execute-write", op="job_approval.mark_notified",
           args=json.dumps({"job_id": job_id, "last_notified_log_index": len(execution_log),
                            "last_notified_card_status": current_card_status}))
        reported.append(job_id)
    return reported


def main() -> None:
    env = read_env_file(os.path.join(hermes_home(), "plugin-data", "qkeee-erp", "qkeee-erp.env"))
    tags = (run_hermes("list-envs").get("result") or {}).get("configured_tags") or []
    reported = []
    for tag in tags:
        admin_email = env.get(tag_env_var(tag, "ADMIN_EMAIL"))
        if not admin_email:
            print(f"qkeee-erp job status poller: no {tag_env_var(tag, 'ADMIN_EMAIL')} configured; "
                 f"skipping tag {tag!r}.", file=sys.stderr)
            continue
        reported += [f"{tag}:{job_id}" for job_id in poll_tag(tag, admin_email)]
    if reported:
        # Non-empty stdout is delivered verbatim (cron.md's no_agent
        # convention); empty stdout is a silent tick — most polls find
        # nothing new and should not generate cron output of their own.
        print(f"qkeee-erp job status poller: reported {len(reported)} job(s): "
             + ", ".join(reported))


if __name__ == "__main__":
    main()
