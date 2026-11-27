# Weekly tasks-received-vs-completed report

Status: ready-for-human — implemented, tested, committed in erpnext-hermes
(50318fd develop); live check open (see Comments for review notes)
Type: feature
Blocked by: 02 — unblocked. Implemented and committed 5f7be1f (erpnext-hermes develop):
`request_registry.db` (`request`/`request_session`/`request_event` tables) exists and is
live (schema below), no further dependency.
Findings: none (new ask, not from the 18 Nov log audit)
Decision: D11-a..f locked 2026-11-23 (nikhil.sharma@qkeee.ai) — see Comments. Adds: a
pending/dependencies summary (§7), token/cost metrics joined from hermes-agent's `state.db`
(§8) cross-checked against OpenRouter's own API (§8a — surfaces a `hermes-agent` gap: the
authoritative per-call `cost` field is received but currently discarded), and an admin-gated
on-request monthly/custom-window pull (§9).

## Problem

Raised 2026-11-20 (nikhil.sharma@qkeee.ai) alongside issue 02's design: once every distinct request has a durable row (`request_registry.db`, see issue 02 Comments), produce a weekly report of tasks received vs. completed, and agent performance, from that data.

## Change

Not specified yet. Depends on issue 02 landing first — this is a query/output layer over its schema (`agent_status`, `requester_confirmed`, `created_at`, `closed_at`, `sender_email`, `topic`), not a new data model.

Open questions (none answered yet):
- Delivery: email digest, Google Chat message, a doc, something else?
- "Performance" — what's actually measured (time-to-first-response? agent_status vs requester_confirmed mismatch rate? something else)?
- Per-Requester breakdown, or org-wide roll-up, or both?

## Acceptance

TBD once design lands.

## Comments

**Draft design note (2026-11-21, drafted by agent, pending sign-off):**

### 1. Data source (already live)

`request_registry.py` (erpnext-hermes, committed under issue 02) is stdlib-only sqlite at
`plugin-data/qkeee-erp/request_registry.db`, one store per Hermes profile (not per ERPNext
tag — same scope as `lcm.db`/`kanban_origin.db`). Three tables:

- `request`: `request_id, sender_email, topic, anchor_doctype, anchor_name, agent_status
  ('open'/'in_progress'/'done'), requester_confirmed ('confirmed'/'disputed'/NULL), facts
  (JSON), created_at, updated_at, closed_at` (all timestamps epoch **microseconds**).
- `request_session`: every `(platform, chat_id, thread_id)` linked to a `request_id`, plus
  which `sender_email` linked it.
- `request_event`: append-only forensic trail — one row per `open`/`link`/`status`/`facts`
  call, `(request_id, action, sender_email, note, created_at)`. Built for exactly this: its
  docstring says "a later report (issue 11) ... queries this table directly."

Gap: every existing reader (`open_for_sender`, `open_in_session`) filters
`agent_status != 'done'` and scopes to one sender or one room — correct for their
surfacing-only job, wrong for a report that needs **every** request, **every** status,
**every** sender, in a time window. No existing function does this. Needs a new one.

### 2. New query function (request_registry.py)

```
def requests_in_window(store_path, since_us, until_us) -> list:
    """Every request with created_at, updated_at, or closed_at inside
    [since_us, until_us) — any sender, any status. Each row carries
    linked_sessions, same shape as _row(). A request can appear because it
    was opened, closed, or merely touched (facts/status) in-window."""
```

Plus a thin wrapper in `client.py` (`request_report(since_us, until_us)`) that is **not**
exposed through the `erp_request` tool's action enum — every other `erp_request` action is
scoped to the calling identity (own requests, own room, ownership-gated status/facts writes);
an org-wide cross-sender read is a different trust tier and must not become something any
chat participant can ask the agent to run. It is called only by the report script below, not
by the LLM tool-call path.

### 3. Metrics (the open "what's performance" question — concrete proposal)

All computed from the window's rows, no new storage beyond `requests_in_window`:

| Metric | Definition |
|---|---|
| Received | count where `created_at` falls in-window |
| Completed | count where `closed_at` falls in-window AND `agent_status='done'` |
| Carried over (backlog) | `agent_status != 'done'`, `created_at` before window start, still open now |
| Confirmed rate | of Completed: `requester_confirmed='confirmed'` / Completed |
| Disputed | of Completed: `requester_confirmed='disputed'` — surfaced as its own line, not buried in a rate |
| **Done-but-unconfirmed** | `agent_status='done'` AND `requester_confirmed IS NULL` — issue 02's own framing: "the gap between them ... is itself a useful signal," not noise |
| Stale-open | `agent_status != 'done'` AND `updated_at` older than N days (proposed N=7 — sign-off item D11-b) |
| Time-to-close | `closed_at - created_at` for Completed rows, median + p90 |

**Dropped from v1:** time-to-first-response. `request_event` logs the agent's/caller's own
`open`/`link`/`status`/`facts` calls, never the agent's actual reply send — there is no
event for "the agent answered." Computing this needs a new logged event (e.g. `notify.*`
or a reply-turn hook calling `log_event(..., action="first_reply", ...)`), which is new
scope, not a report-layer query. Flagging as a follow-up (sign-off item D11-c), not silently
shipping a wrong number.

### 4. Breakdown: both (org roll-up + per-sender table)

- Org-wide summary block: the metrics table above, one set of numbers for the window.
- Per-`sender_email` table below it: topic, request_id, agent_status, requester_confirmed,
  created_at, closed_at/age — sorted by Received desc, capped at 25 rows with "+N more"
  (same overflow convention as `notify.render_share`'s `has_more`), sign-off item D11-d.

### 5. Delivery — reuse, not new infra

The existing precedent for "org-wide, no single requester, periodic finding" is
`scripts/lcm_corruption_check.py` (`cron/jobs.json` job `lcm-corruption-check`, every 12h) —
**not** `notify.job_status_update` (which is per-kanban-job, addressed to that job's own
Requester). This report is the same shape as the lcm one: no Requester to address, an
Operator-facing finding. Model `scripts/qkeee_erp_weekly_request_report.py` on it directly:

- `no_agent` cron script, stdlib only, every Monday 09:00 (`0 9 * * MON` — sign-off item
  D11-e), registered in `cron/jobs.json` next to `lcm-corruption-check` and
  `qkeee-erp-job-status-poller`. **Ships paused**, same convention as those two — an
  Operator confirms `QKEEE_ERP_<TAG>_ADMIN_EMAIL` (and optionally `_OPS_CHAT_WEBHOOK`) is
  set, then `hermes cron resume qkeee-erp-weekly-request-report`.
- Data read: unlike `lcm_corruption_check.py`, this script **can** `import
  qkeee_erp.core.request_registry` directly instead of duplicating SQL — that module is
  already stdlib-only with no gateway-secret dependency (the thing `lcm_corruption_check.py`
  and `qkeee_erp_job_status_poller.py` duplicate `read_env_file`/`tag_env_var` to avoid is
  `qkeee_erp.mailer`'s gateway-only secret reader, not `request_registry`). So: reuse
  `request_registry.default_store_path()` + the new `requests_in_window()`, but duplicate
  the email/webhook *senders* exactly as `lcm_corruption_check.py` does (own `send_email`/
  `send_chat`, same `_smtp_settings()` shape, same reasoning in that module's docstring).
- Recipients: `admin_emails_and_webhooks()` — the exact helper already in
  `lcm_corruption_check.py` (every configured `QKEEE_ERP_<tag>_ADMIN_EMAIL`/
  `_OPS_CHAT_WEBHOOK` in this profile's `qkeee-erp.env`, deduplicated). No new env var.
  Worth lifting that helper (and `read_env_file`) into a tiny shared module the three
  scripts import, rather than a third copy — nice-to-have, not blocking.
- This settles the "email digest, Chat message, or a doc?" open question as **both email
  and Google Chat, to the ops/admin address(es) already configured for infra alerts** — not
  to each individual Requester. Confirm at sign-off (D11-f): this is an ops report, not
  something forwarded to every Requester named in it. `ADMIN_EMAIL` already sees Requester
  correspondence as a cc on `notify.job_approval_request`, so this adds no new privacy
  surface beyond that existing "admin is a trusted internal reader" boundary.
- Report text reused verbatim for both the email body and the chat webhook payload — same
  "one plain representation, two deliveries" pattern as `notify.render_job_status`.
- Silent only when the store file doesn't exist yet (fresh profile, no requests ever) —
  unlike `lcm_corruption_check.py`'s "silent when healthy," a week with zero activity is
  itself worth one line ("0 received, 0 completed this week"), not a silent skip.

### 6. Testing

- `tests/test_request_registry.py`: `requests_in_window` — empty store, boundary timestamps
  (exactly `since`/`until`), a request touched but not created in-window, mixed statuses.
- `tests/test_weekly_request_report.py`, mirroring `tests/test_lcm_corruption_check.py`'s
  shape: fake env file, a seeded `request_registry.db`, assert the computed metrics, the
  formatted subject/body, and the email/chat send calls (mocked `smtplib`/`urlopen`).

### Sign-off record (locked 2026-11-23, nikhil.sharma@qkeee.ai)

- **D11-a** — **LOCKED, revised.** Not a trailing 7 days. Window = calendar week aligned to
  the company's work calendar: Monday 00:00 → Sunday 23:59:59.999999 (the just-finished
  week, weekend included), report generated the following Monday. Matches D11-e's run time
  below, so "generated Monday 09:00" and "window ends the preceding Sunday night" are the
  same boundary, not two independent choices.
- **D11-b** — **LOCKED, revised unit.** Not calendar days. Stale-open is counted in **working
  days**, per the company's own work-week geography (India IT/services: Mon–Fri, Sat/Sun
  off) — a request untouched since Friday is not "2 days stale" Monday morning. Proposing
  **N = 5 working days** (one full work week with no update) as the threshold; flag if a
  different N is wanted, otherwise this is the shipped default. Both constants
  (`WORK_WEEK_DAYS = Mon–Fri`, `WEEK_START_WEEKDAY = Monday`) live as one named module-level
  pair in the new query helper (see §2 below) — not re-derived in three places — so a future
  profile in a different geography (different weekend, different week-start) is a one-line
  change, not a search-and-replace.
- **D11-c** — confirmed deferred. Filed below as a standalone follow-up (time-to-first-
  response needs a new logged event; not this issue's scope).
- **D11-d** — confirmed: 25-row cap, sorted Received desc, `+N more` overflow.
- **D11-e** — confirmed: Monday 09:00 server time (now also the week-boundary anchor per D11-a).
- **D11-f** — confirmed: ops-only delivery (`ADMIN_EMAIL` + `OPS_CHAT_WEBHOOK`), never
  per-Requester.

### 7. Pending-tasks / dependencies summary (new ask, 2026-11-23)

A section listing what's stuck and why, not just counted as "open." No schema change —
reuses the existing `facts` JSON bag (`merge_facts`/`erp_request facts`, issue 02) under a
reserved key, the same mechanism already used for resolved task state:

```
erp_request facts request_id=REQ-... facts={"pending": {"type": "approval"|"access"|
"clarification", "detail": "waiting on Requester's PO sign-off", "since": "<ISO8601>"}}
```

Report reads every currently open/in_progress request (not window-scoped — this is a
backlog snapshot, not a weekly delta) and groups by `facts.pending.type` when present:
Approval-pending / Access-pending / Clarification-pending / **no blocker recorded** (the
agent never flagged one — itself a signal: either it's not actually stuck, or the convention
isn't being followed). This is additive to, not a replacement for, the Stale-open metric
(§3) — a request can be fresh and blocked, or stale with no recorded reason.

**Dependency this adds:** the agent has to actually set `facts.pending` when it identifies a
stall, or the section is empty. Needs a short addition to
`plugins/qkeee-erp/skills/usage/SKILL.md` §9 (same place issue 02's `erp_request` guidance
lives) telling the agent when/how to set it. Without that doc change, this section ships
structurally but reports nothing — calling that out now so it isn't a surprise gap at review.

### 8. Token / cost metrics (new ask, 2026-11-23)

`request_registry.db` tracks none of this — tokens/cost live in **hermes-agent's own**
`state.db` (same `HERMES_HOME`/profile directory, sibling to `lcm.db`), not in this plugin's
store. Confirmed by reading `hermes-agent/hermes_state_usage.py`:

- `sessions` table: columns `input_tokens, output_tokens, cache_read_tokens,
  cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status,
  cost_source, model, api_call_count`, keyed by `id` = the gateway's `session_id` — the
  **same `session_id`** already stored per-row in `request_registry`'s `request_session`
  table (`SESSION_LINK_FIELDS` includes it, issue 02).
- `session_model_usage` table: the same counters broken out per model/task for one
  session_id — useful if a per-model cost split is ever wanted, not needed for v1.

**Join:** for a request, sum `sessions.estimated_cost_usd`/token columns over every
`session_id` in its `linked_sessions`. Read-only direct sqlite against `state.db`, same
posture as `lcm_corruption_check.py` reading `lcm.db` directly — no import of hermes-agent
internals, just the known table/column names above.

**Caveat worth flagging now, not discovering at implementation:** `sessions.*_cost_usd` and
`sessions.*_tokens` are **lifetime cumulative** totals for that session_id, not per-call or
per-window — there is no timestamped per-call row to filter by the report's week boundary
(`session_model_usage` is also an upsert-accumulated row, not one row per API call). A
session that spans two report weeks would otherwise be fully counted in both, or fully
missed. Fix: the report keeps its own tiny checkpoint — one new table in
`request_registry.db` (same stdlib-sqlite pattern as everything else here), e.g.
`session_cost_checkpoint(session_id TEXT PRIMARY KEY, tokens_as_of INTEGER, cost_as_of_usd
REAL, checkpointed_at INTEGER)`. Each report run computes **delta = current cumulative −
checkpoint**, attributes the delta to the request(s) that session is linked to, then writes
the new cumulative back as the checkpoint. This is the same delta-over-cumulative principle
`hermes-agent`'s own `turn_usage.py` already uses internally (`agent.session_estimated_cost_usd
+= _cost_delta`) — reporting layer reuses the pattern, not a new idea.

Report output: a new column/line per request and in the org roll-up — **tokens (in/out),
estimated cost (USD)** — using `estimated_cost_usd` (not `actual_cost_usd`, which is
provider-invoice-reconciled and often null; `cost_status`/`cost_source` columns already
exist for exactly this "which number is this" question — surface `cost_status` alongside the
figure when it isn't `"ok"`, so a `"included"/subscription` session doesn't silently read as
free). A request linked to zero sessions with usable usage rows (shouldn't happen post-issue-02,
but a pre-issue-02 request has no session_id at all) shows "—", not a false 0.

### 8a. OpenRouter as the authoritative cost source (researched 2026-11-23)

The profile's model is `openrouter/auto-beta` (`profile.md` Config Summary) — OpenRouter is
the gateway for every call this report would cost out. Checked whether OpenRouter's own API
can replace or correct §8's local `state.db` estimate, instead of trusting
`estimated_cost_usd` alone. Three relevant surfaces, confirmed against OpenRouter's own docs
and this codebase:

**(i) The authoritative per-call cost is already in every response — and already discarded.**
OpenRouter's Chat Completions `usage` object includes `cost` ("the total amount charged to
your account") plus `cost_details.upstream_inference_cost` (BYOK only) on **every** response,
no request flag needed (`usage.include`/`stream_options.include_usage` are now deprecated
no-ops — it's unconditional). `hermes-agent/agent/usage_pricing.py::normalize_usage()`
already captures the whole raw response into `CanonicalUsage.raw_usage` — so `cost` is sitting
in memory on every call — but `estimate_usage_cost()` never reads it for OpenRouter: it always
falls through to the local models-API pricing-table estimate, with its own code comment
admitting it (`usage_pricing.py:745`, `route.provider == "openrouter"` →
`"OpenRouter cost is estimated from the models API until reconciled."`). The hook built for
exactly this — `providers.base.ProviderProfile.get_usage_cost()`, docstring *"distinguish
estimates from invoices"* — is never overridden for OpenRouter (`providers/base.py:396`
returns `None`; grep found no OpenRouter-specific override).

**This is a `hermes-agent` fix, not an `erpnext-hermes` one** — out of this issue's repo, filed
as a cross-repo follow-up, not blocking issue 11:
- Add a `get_usage_cost()` override on OpenRouter's provider profile reading
  `usage.raw_usage["cost"]` (and `cost_details.upstream_inference_cost` for BYOK routes,
  not relevant to `auto-beta`), returning `CostResult(status="actual", source=
  "openrouter_response")`.
- `turn_usage.py` already threads whatever `estimate_usage_cost()` returns into the cost
  delta; it would need one more line passing that same figure as `actual_cost_usd=` (not
  only `estimated_cost_usd=`) to `queue_token_counts()` when `cost_result.status == "actual"`
  — `sessions.actual_cost_usd` exists in the schema (§8) but, confirmed by grep, nothing
  anywhere in `agent/` ever writes a non-null value into it today.
- Once landed: §8's join needs **no change** — it already reads `cost_status`/`cost_source`
  alongside the figure and was already written to prefer `actual_cost_usd` over
  `estimated_cost_usd` when present. The report gets more accurate automatically; this issue
  doesn't re-open. Until it lands, every figure in this report is honestly labeled
  "estimated" (already the plan).

**(ii) `GET /api/v1/generation?id=<id>` — redundant for this use.** Returns the same
cost/token figures as (i), fetched after the fact by generation id. Since (i)'s fix would
already capture `cost` inline at call time, polling this per-call for a weekly/monthly
report (hundreds of calls) adds HTTP round-trips for data already available for free. Not
proposed.

**(iii) `GET /api/v1/activity` — an independent, authoritative cross-check, with a real new
dependency.** Returns per-day, per-model USD totals (`usage`, `byok_usage_inference`,
`requests`, `prompt_tokens`, `completion_tokens`, ...) for the **last 30 completed UTC days
only** — no custom date range. Two real constraints:
- Needs a **management key** — a *different* credential from the `OPENROUTER_API_KEY` already
  configured for inference ("management keys cannot be used to make API calls to OpenRouter's
  completion endpoints"). Created at openrouter.ai/settings/management-keys. This is a genuine
  new access dependency, not a code-only change — an Operator has to go create it before this
  cross-check can run at all. If the org's OpenRouter account is shared beyond this bot, the
  response's `api_key_hash` field is needed to filter to just this bot's own key.
- 30-day window means this can corroborate the **weekly** report always, and the **monthly**
  report (§9) only when the requested month falls inside the trailing 30 days — a monthly pull
  for anything older has no authoritative OpenRouter cross-check available, local figures only.

**(iv) `GET /api/v1/key` — a cheaper, zero-new-dependency partial check.** Already called in
this codebase (`agent/account_usage.py::_fetch_openrouter_account_usage`, reusing the existing
`OPENROUTER_API_KEY`, no new credential) and already returns `usage_weekly`/`usage_monthly`
— a single rolling $ total for the whole key. Coarser than (iii) (no per-day/per-model split,
windowing semantics not confirmed from the docs — "weekly"/"monthly" may be rolling-to-date,
not calendar-aligned) but free. Proposed as the **default** org-total sanity line in both
reports (no sign-off/provisioning needed); (iii) as an **optional upgrade** once an Operator
provisions a management key, for the richer per-day breakdown.

**Net for this issue:** §8's design doesn't change shape — it already reads `cost_status`
and already distinguishes estimated from actual. What's new: (a) a filed cross-repo follow-up
to make `actual_cost_usd` real for OpenRouter (i), and (b) an optional org-total cross-check
line using `/key` by default and `/activity` if a management key gets provisioned, surfaced
next to the local figure so a reader can see "local estimate vs. OpenRouter's own number" and
trust the latter once (i) lands.

Sources: [OpenRouter usage accounting guide](https://openrouter.ai/docs/guides/administration/usage-accounting), [Get Activity API reference](https://openrouter.ai/docs/api/api-reference/analytics/get-user-activity-grouped-by-endpoint), [Activity guide](https://openrouter.ai/docs/guides/features/activity.md), [Management API keys](https://openrouter.ai/docs/guides/overview/auth/management-api-keys.md).

### 9. On-request monthly summary (new ask, 2026-11-23)

Same dataset, same metrics (§3, §7, §8), different trigger and audience boundary:

- The cron script (§5) stays the scheduled, push, ops-only weekly delivery.
- Separately, expose the **same compute function** (refactor §2/§3/§7/§8's logic into one
  pure `build_report(store_path, state_db_path, since_us, until_us) -> dict`, importable by
  both callers — no duplicated metric logic between the push and pull paths) through a new,
  **admin-gated** `erp_request` action, e.g. `action: "org_report"`, `period: "week"|"month"`
  or explicit `since`/`until`. "On request" means a human asks the agent in chat ("send me
  last month's numbers") and the agent calls this action and relays the result in its reply —
  it does not also email/chat-webhook it (that stays the cron script's job); a human who wants
  the on-demand pull also emailed can ask the agent to use `notify.share_records`-style
  send afterward, out of scope here.
- **Gate, not a new trust model:** every other `erp_request` action is scoped to the caller's
  own identity; this one is explicitly cross-sender (§2 already ruled this out of the normal
  action enum for that reason). Reuse the existing `QKEEE_ERP_<TAG>_ADMIN_EMAIL` value(s) as
  the allowlist — the caller's `sender_email` must case-insensitively match one, across any
  tag configured for this profile (same multi-tag collection as `admin_emails_and_webhooks()`
  in §5, ported into `client.py` since this runs inside the gateway, not a bare script).
  Refuse with a new `ReportAccessError(GateRefusal)` (same family as `RequestOwnershipError`)
  for anyone else — logged via `log_event()` like every other `erp_request` action, so a
  refused attempt is itself in the audit trail.
- Default window when `period` is given without explicit dates: `"week"` = the last
  completed Mon–Sun (§D11-a's definition); `"month"` = the last full calendar month (1st to
  last day). An explicit `since`/`until` always overrides `period`.

### Follow-ups filed, not in this issue's scope

- **Time-to-first-response** (D11-c): needs a new logged event (e.g. a `log_event(...,
  action="first_reply", ...)` call from wherever the agent's reply actually sends) before it
  can be measured at all. Separate issue once D11-c's deferral is acted on.
- **SKILL.md §9 addition** for `facts.pending` (§7): small doc change, not code, but called out
  so it doesn't get silently skipped when this ships.
- **OpenRouter `actual_cost_usd` wiring** (§8a-i): a `hermes-agent` fix (provider
  `get_usage_cost()` override + one `turn_usage.py` write), not `erpnext-hermes` — files
  against that repo, not this one. Issue 11 ships without it (figures labeled "estimated");
  this just makes them exact once it lands.

### Implemented 2026-11-23, committed 50318fd (erpnext-hermes develop)

Full scope shipped: §1-§9 as locked, minus the two items explicitly filed above
(D11-c, OpenRouter wiring) and §8a's optional `/key`/`/activity` cross-check line
(research-only for this issue, not built — a hermes-agent-side account_usage.py
call, not required by the D11 sign-off).

- `request_registry.py`: `requests_in_window()`, `open_requests()` (backlog
  snapshot for §7/§3), `session_cost_checkpoint` table +
  `get_cost_checkpoints()`/`set_cost_checkpoints()`, `WORK_WEEK_WEEKDAYS`/
  `WEEK_START_WEEKDAY`/`PENDING_TYPES` constants.
- New `request_report.py`: pure `build_report()` (shared by both callers) +
  `format_report_text()`. Window/staleness math is UTC-based — a known
  simplification flagged in its own docstring for a deployment whose actual
  day boundary is IST; worth revisiting if report boundaries are ever
  user-visibly off by a day.
- `client.request_org_report()` + `ReportAccessError`; `erp_tools.py` action
  `org_report` (period week/month, explicit since/until, ISO8601).
- `scripts/qkeee_erp_weekly_request_report.py` (modeled on
  `lcm_corruption_check.py`) + `cron/jobs.json` entry (`0 9 * * MON`, ships
  paused per convention).
- SKILL.md §9: `facts.pending` guidance + `org_report` admin-only note.
- Tests: `test_request_registry.py`, new `test_request_report.py`,
  `test_client.py`/`test_plugin_erp_tools.py` org_report coverage, new
  `tests/test_weekly_request_report.py`. Full suite green except 6 pre-existing,
  unrelated failures (README/host-runbook drift, confirmed present on a clean
  `git stash` of this change too).

**Code review (Standards + Spec axes, both run as sub-agents) — findings applied:**
- Moved an inline `import datetime` in `erp_tools.py` to the module's top-level
  imports (every sibling function imports at module scope).
- `client.request_org_report()` now routes its `build_report()` call through
  `_request_registry_call()` (same sqlite3.Error → ConnectorError translation
  every other `request_*` facade here gets) instead of calling it directly.
- `facts.pending.type`'s three values were hardcoded independently in
  `request_report.py`'s bucket grouping and its text-rendering labels; both now
  read from the new `request_registry.PENDING_TYPES` constant.
- Spec gap: §8 asks for `cost_status` surfaced "alongside the figure" both
  per-request AND "in the org roll-up" — the org total had no `cost_status`.
  Added (worst non-"ok" status across the window's sessions, surfaced in both
  `build_report()`'s `org_usage` and the rendered text).
- Spec gap: §4's table column list is "closed_at**/age**" — a still-open row
  showed neither. Added `age_us` per row and an "open Xh" line in the text
  rendering for rows with no `closed_at` yet.
- Renamed `build_report()`'s `now_us_` param to `at_us` (matches the window
  helpers' own naming; the trailing underscore was reviewer-flagged as a
  Mysterious Name).
- Not changed: the cron script's duplicated `read_env_file`/`admin_emails_and_
  webhooks`/SMTP senders (deliberate, same reasoning as `lcm_corruption_check.py`'s
  own docstring); the UTC-based calendar math (documented limitation, not a bug);
  §8a's `/key` cross-check line (correctly out of scope per the spec's own "Net
  for this issue" framing).

Live check open: no real weekly cron tick or `org_report` chat call has been
exercised against a live profile yet.
