"""Weekly tasks-received-vs-completed report (agents issue 11): pure
computation over the Request Registry (`request_registry.py`, issue 02)
plus hermes-agent's own token/cost ledger (`state.db`'s `sessions`
table, same `HERMES_HOME` directory, sibling to `lcm.db`).

`build_report()` is the one compute function imported by BOTH callers —
the scheduled push (`scripts/qkeee_erp_weekly_request_report.py`) and the
admin-gated on-request pull (`erp_request` action `org_report`,
`core/client.py::request_org_report`) — so the metrics can never drift
between the two. It does no I/O beyond its two sqlite files and never
sends email/chat itself; that stays each caller's own job (the cron
script's `send_email`/`send_chat`, the chat tool's own reply).

`state.db` read: read-only direct sqlite against the known `sessions`
table/column names (hermes-agent's `hermes_state_usage.py`), the same
posture as `scripts/lcm_corruption_check.py` reading `lcm.db` directly —
no import of hermes-agent internals, and this module (like
`request_registry.py`) stays stdlib-only.

Cost caveat (issue 11 §8, confirmed against hermes-agent's
`usage_pricing.py`): `sessions.estimated_cost_usd`/`actual_cost_usd` and
its token columns are lifetime-cumulative per `session_id`, not
timestamped per API call. This report attributes the DELTA since the
last checkpoint (`request_registry.session_cost_checkpoint`) to whichever
request(s) that session is linked to — not a figure cleanly scoped to
the exact calendar window when a session's activity straddles more than
one report run. Only the scheduled weekly run advances the checkpoint
(`advance_checkpoint=True`); an on-demand pull reads the same delta
without consuming it, so repeated ad-hoc asks never desync the weekly
cursor. Every cost figure is therefore labeled against `cost_status`/
`cost_source` (never silently presented as exact) until the filed
cross-repo follow-up (OpenRouter `actual_cost_usd` wiring, issue 11 §8a)
lands in hermes-agent.

Window/staleness calendar math is UTC-based (epoch microseconds are
`time.time_ns()`-derived, not localized) — a known simplification for a
deployment whose actual day boundary is IST (UTC+5:30): a touch within
~5.5h of UTC midnight can land on the "wrong" side of a day/week
boundary relative to local wall-clock intent. Documented here rather
than silently assumed exact; revisit if report boundaries are ever user-
visibly off by one day.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from . import request_registry as rr

STALE_OPEN_WORKING_DAYS = 5  # issue 11 D11-b
PER_SENDER_TABLE_CAP = 25  # issue 11 D11-d

_SESSION_USAGE_COLUMNS = ("input_tokens", "output_tokens", "estimated_cost_usd",
                          "actual_cost_usd", "cost_status", "cost_source")


def _utc_date(epoch_us: int):
    return datetime.fromtimestamp(epoch_us / 1_000_000, tz=timezone.utc).date()


def _us(dt) -> int:
    return int(dt.timestamp() * 1_000_000)


def now_us() -> int:
    return time.time_ns() // 1000


def working_days_elapsed(start_us: int, end_us: int,
                         work_week=rr.WORK_WEEK_WEEKDAYS) -> int:
    """Count of *work_week* days strictly after `start_us`'s date, up to
    and including `end_us`'s date (issue 11 D11-b: working days, not
    calendar days — a request untouched since Friday is not "2 days
    stale" Monday morning, it's one working day stale: the Monday)."""
    start_date, end_date = _utc_date(start_us), _utc_date(end_us)
    if end_date <= start_date:
        return 0
    days, d = 0, start_date
    while d < end_date:
        d += timedelta(days=1)
        if d.weekday() in work_week:
            days += 1
    return days


def last_completed_week_window(at_us: int = None) -> tuple:
    """(since_us, until_us) for the most recently finished Mon 00:00 ->
    Sun 23:59:59.999999 UTC week, as of *at_us* (default now) — issue 11
    D11-a. Called with the Monday-09:00 cron tick (D11-e), this is
    "yesterday and the six days before it"."""
    at_date = _utc_date(at_us if at_us is not None else now_us())
    this_week_start = at_date - timedelta(days=(at_date.weekday() - rr.WEEK_START_WEEKDAY) % 7)
    last_week_start = this_week_start - timedelta(days=7)
    since = datetime(last_week_start.year, last_week_start.month, last_week_start.day,
                     tzinfo=timezone.utc)
    until = since + timedelta(days=7)
    return _us(since), _us(until)


def last_completed_month_window(at_us: int = None) -> tuple:
    """(since_us, until_us) for the last full calendar month (1st ->
    1st), UTC — issue 11 §9's `period="month"` default."""
    at_date = _utc_date(at_us if at_us is not None else now_us())
    this_month_start = at_date.replace(day=1)
    last_month_end = this_month_start
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
    since = datetime(last_month_start.year, last_month_start.month, last_month_start.day,
                     tzinfo=timezone.utc)
    until = datetime(last_month_end.year, last_month_end.month, last_month_end.day,
                     tzinfo=timezone.utc)
    return _us(since), _us(until)


def _percentile(values: list, pct: float):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


def _time_to_close_hours(rows: list) -> dict:
    deltas = [(row["closed_at"] - row["created_at"]) / 3_600_000_000
             for row in rows if row.get("closed_at") is not None]
    return {"median_hours": _percentile(deltas, 0.5), "p90_hours": _percentile(deltas, 0.9),
           "n": len(deltas)}


def _pending_breakdown(open_rows: list) -> dict:
    """Issue 11 §7: group the backlog by `facts.pending.type`, when the
    agent recorded one (`erp_request facts request_id=... facts=
    {"pending": {...}}`, see SKILL.md §9). A row with no `pending` key at
    all is its own bucket — "the agent never flagged a blocker" is itself
    a signal (not actually stuck, or the convention isn't followed), not
    noise to drop."""
    buckets = {t: [] for t in rr.PENDING_TYPES}
    buckets["no_blocker_recorded"] = []
    for row in open_rows:
        pending = (row.get("facts") or {}).get("pending")
        kind = pending.get("type") if isinstance(pending, dict) else None
        bucket = kind if kind in rr.PENDING_TYPES else "no_blocker_recorded"
        entry = {"request_id": row["request_id"], "sender_email": row["sender_email"],
                 "topic": row.get("topic")}
        if isinstance(pending, dict):
            entry["detail"] = pending.get("detail")
            entry["since"] = pending.get("since")
        buckets[bucket].append(entry)
    return buckets


def _session_ids(rows: list) -> set:
    ids = set()
    for row in rows:
        for session in row.get("linked_sessions") or ():
            if session.get("session_id"):
                ids.add(session["session_id"])
    return ids


def _read_session_usage(state_db_path: str, session_ids: set) -> dict:
    """`{session_id: {column: value}}` current cumulative usage rows from
    hermes-agent's `state.db`. Missing file or missing id is simply
    absent — a pre-issue-02 request with no `session_id` at all, or a
    state.db that hasn't been created yet, never raises."""
    if not session_ids or not state_db_path or not os.path.exists(state_db_path):
        return {}
    conn = sqlite3.connect(state_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(session_ids))
        rows = conn.execute(
            f"SELECT id, {', '.join(_SESSION_USAGE_COLUMNS)} FROM sessions "
            f"WHERE id IN ({placeholders})", tuple(session_ids)).fetchall()
        return {r["id"]: dict(r) for r in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _usage_deltas(session_ids: set, usage_rows: dict, checkpoints: dict) -> dict:
    """`{session_id: {tokens_in, tokens_out, cost_usd, cost_status,
    cost_source, next_checkpoint}}` — current cumulative minus the last
    checkpoint (0 when this session has never been checkpointed, i.e. its
    first report run: the delta is the session's full lifetime total)."""
    deltas = {}
    for session_id in session_ids:
        usage = usage_rows.get(session_id)
        if usage is None:
            continue
        checkpoint = checkpoints.get(session_id) or {}
        tokens_in = int(usage.get("input_tokens") or 0) - int(checkpoint.get("tokens_in_as_of") or 0)
        tokens_out = int(usage.get("output_tokens") or 0) - int(checkpoint.get("tokens_out_as_of") or 0)
        cost = (float(usage.get("actual_cost_usd") or usage.get("estimated_cost_usd") or 0.0)
               - float(checkpoint.get("cost_as_of_usd") or 0.0))
        deltas[session_id] = {
            "tokens_in": max(tokens_in, 0), "tokens_out": max(tokens_out, 0), "cost_usd": max(cost, 0.0),
            "cost_status": usage.get("cost_status"), "cost_source": usage.get("cost_source"),
            "next_checkpoint": {
                "tokens_in_as_of": int(usage.get("input_tokens") or 0),
                "tokens_out_as_of": int(usage.get("output_tokens") or 0),
                "cost_as_of_usd": float(usage.get("actual_cost_usd") or usage.get("estimated_cost_usd") or 0.0)}}
    return deltas


def _request_usage(row: dict, deltas: dict) -> dict:
    session_ids = [s.get("session_id") for s in row.get("linked_sessions") or () if s.get("session_id")]
    matched = [deltas[sid] for sid in session_ids if sid in deltas]
    if not matched:
        return {"tokens_in": None, "tokens_out": None, "cost_usd": None, "cost_status": None}
    return {
        "tokens_in": sum(d["tokens_in"] for d in matched),
        "tokens_out": sum(d["tokens_out"] for d in matched),
        "cost_usd": sum(d["cost_usd"] for d in matched),
        "cost_status": next((d["cost_status"] for d in matched if d["cost_status"] not in (None, "ok")), "ok")}


def build_report(store_path: str, state_db_path: str, since_us: int, until_us: int, *,
                 at_us: int = None, advance_checkpoint: bool = False) -> dict:
    """The full issue-11 report for `[since_us, until_us)`: org-wide
    metrics (§3), a capped per-request table (§4), the pending/
    dependencies backlog (§7), and a token/cost join (§8). Never sends
    anything — pure over its two sqlite files.

    `advance_checkpoint=True` persists the new cost checkpoint (the
    scheduled weekly run's job only — see `request_registry.
    set_cost_checkpoints()`'s own docstring for why an on-demand pull
    must pass False)."""
    at = at_us if at_us is not None else now_us()
    window_rows = rr.requests_in_window(store_path, since_us, until_us)
    open_rows = rr.open_requests(store_path)

    received = [r for r in window_rows if since_us <= r["created_at"] < until_us]
    completed = [r for r in window_rows
                if r["agent_status"] == "done" and r.get("closed_at") is not None
                and since_us <= r["closed_at"] < until_us]
    carried_over = [r for r in open_rows if r["created_at"] < since_us]
    confirmed = [r for r in completed if r["requester_confirmed"] == "confirmed"]
    disputed = [r for r in completed if r["requester_confirmed"] == "disputed"]
    done_unconfirmed = [r for r in completed if r["requester_confirmed"] is None]
    stale_open = [r for r in open_rows
                 if working_days_elapsed(r["updated_at"], at) >= STALE_OPEN_WORKING_DAYS]

    all_session_ids = _session_ids(window_rows) | _session_ids(open_rows)
    checkpoints = rr.get_cost_checkpoints(store_path, all_session_ids)
    usage_rows = _read_session_usage(state_db_path, all_session_ids)
    deltas = _usage_deltas(all_session_ids, usage_rows, checkpoints)
    if advance_checkpoint and deltas:
        rr.set_cost_checkpoints(store_path, {sid: d["next_checkpoint"] for sid, d in deltas.items()})

    org_cost = {"tokens_in": sum(d["tokens_in"] for d in deltas.values()),
               "tokens_out": sum(d["tokens_out"] for d in deltas.values()),
               "cost_usd": sum(d["cost_usd"] for d in deltas.values()),
               # issue 11 §8: surface a non-"ok" status on the org total too
               # — an "included"/subscription session must not make the
               # roll-up silently read as free the way a bare total would.
               "cost_status": next((d["cost_status"] for d in deltas.values()
                                   if d["cost_status"] not in (None, "ok")), "ok")}

    per_request = sorted(received, key=lambda r: r["created_at"], reverse=True)
    table_rows = []
    for row in per_request[:PER_SENDER_TABLE_CAP]:
        table_rows.append({
            "request_id": row["request_id"], "sender_email": row["sender_email"],
            "topic": row.get("topic"), "agent_status": row["agent_status"],
            "requester_confirmed": row["requester_confirmed"],
            "created_at": row["created_at"], "closed_at": row.get("closed_at"),
            # issue 11 §4 "closed_at/age": a still-open row has no closed_at
            # yet, so show how long it's been open instead.
            "age_us": (at - row["created_at"]) if row.get("closed_at") is None else None,
            "usage": _request_usage(row, deltas)})

    return {
        "window": {"since_us": since_us, "until_us": until_us},
        "metrics": {
            "received": len(received), "completed": len(completed),
            "carried_over": len(carried_over), "confirmed": len(confirmed),
            "confirmed_rate": (len(confirmed) / len(completed)) if completed else None,
            "disputed": len(disputed), "done_unconfirmed": len(done_unconfirmed),
            "stale_open": len(stale_open), "stale_open_threshold_working_days": STALE_OPEN_WORKING_DAYS,
            "time_to_close": _time_to_close_hours(completed)},
        "per_sender_table": {"rows": table_rows, "has_more": len(per_request) > PER_SENDER_TABLE_CAP,
                             "total": len(per_request)},
        "pending": _pending_breakdown(open_rows),
        "org_usage": org_cost,
        "generated_at_us": at,
    }


def _fmt_ts(epoch_us) -> str:
    if epoch_us is None:
        return "—"
    return datetime.fromtimestamp(epoch_us / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_usage(usage: dict) -> str:
    if usage.get("tokens_in") is None:
        return "—"
    cost = f"${usage['cost_usd']:.4f}"
    if usage.get("cost_status") and usage["cost_status"] != "ok":
        cost += f" ({usage['cost_status']})"
    return f"{usage['tokens_in']}in/{usage['tokens_out']}out, {cost}"


def format_report_text(report: dict, *, tag: str = "") -> str:
    """Plain-text rendering reused verbatim for both the email body and
    the chat webhook payload — the same "one plain representation, two
    deliveries" pattern as `notify.render_job_status` (issue 11 §5)."""
    m = report["metrics"]
    since, until = report["window"]["since_us"], report["window"]["until_us"]
    lines = [
        f"Weekly request report{f' [{tag}]' if tag else ''}: "
        f"{_fmt_ts(since)} -> {_fmt_ts(until)}", "",
        f"Received: {m['received']}", f"Completed: {m['completed']}",
        f"Carried over (backlog): {m['carried_over']}",
        f"Confirmed: {m['confirmed']}" +
        (f" ({m['confirmed_rate']:.0%} of completed)" if m['confirmed_rate'] is not None else ""),
        f"Disputed: {m['disputed']}",
        f"Done but unconfirmed: {m['done_unconfirmed']}",
        f"Stale-open (>= {m['stale_open_threshold_working_days']} working days untouched): "
        f"{m['stale_open']}",
    ]
    tt = m["time_to_close"]
    if tt["n"]:
        lines.append(f"Time-to-close: median {tt['median_hours']:.1f}h, p90 {tt['p90_hours']:.1f}h "
                     f"(n={tt['n']})")
    else:
        lines.append("Time-to-close: no completed requests this window")
    org = report["org_usage"]
    org_cost = f"${org['cost_usd']:.4f}"
    if org.get("cost_status") and org["cost_status"] != "ok":
        org_cost += f" ({org['cost_status']})"
    lines += ["", f"Org usage this window (estimated, since last checkpoint): "
             f"{org['tokens_in']} in / {org['tokens_out']} out tokens, {org_cost}"]

    pending = report["pending"]
    lines += ["", "Pending/blocked backlog (current, not window-scoped):"]
    for bucket in (*rr.PENDING_TYPES, "no_blocker_recorded"):
        label = "No blocker recorded" if bucket == "no_blocker_recorded" else f"{bucket.capitalize()}-pending"
        lines.append(f"  {label}: {len(pending[bucket])}")

    table = report["per_sender_table"]
    lines += ["", f"Requests received this window ({table['total']}" +
             (f", showing first {len(table['rows'])}" if table["has_more"] else "") + "):"]
    for row in table["rows"]:
        status = row["agent_status"] + (f"/{row['requester_confirmed']}" if row["requester_confirmed"] else "")
        if row.get("closed_at"):
            when = f"closed {_fmt_ts(row['closed_at'])}"
        elif row.get("age_us") is not None:
            when = f"open {row['age_us'] / 3_600_000_000:.1f}h"
        else:
            when = ""
        lines.append(f"  {row['request_id']}  {row['sender_email']}  {row.get('topic') or ''}  "
                     f"[{status}]  opened {_fmt_ts(row['created_at'])}"
                     + (f"  {when}" if when else "")
                     + f"  usage: {_fmt_usage(row['usage'])}")
    if table["has_more"]:
        lines.append(f"  +{table['total'] - len(table['rows'])} more")
    return "\n".join(lines)
