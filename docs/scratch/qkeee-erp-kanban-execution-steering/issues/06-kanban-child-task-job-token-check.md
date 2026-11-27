# Kanban child tasks consume the job-level approval instead of a fresh batch code

Status: blocked (code done: erpnext-hermes@67445af; unreachable from a live session — see issue 07)
Blocked by: 03, 05, 07

## What this builds (ADR 0008)

Extend the write pipeline so a kanban child task's `erp_execute_write`
call can bind against a job-level approval (issue 03's store, marked
approved by issue 05) instead of requiring its own fresh
render-then-chat-code round trip — the actual point of ADR 0008, and the
piece that lets a kanban child task stop needing a live chat session to
answer a confirmation.

- `core/operations.py`'s `WriteContext` already has a `batch_approval`
  field threaded through `run_batch()` (line ~1061) that marks a step as
  pre-approved for the pipeline's token check (`_resolve_token_ok`, line
  ~304: `if ctx.batch_approval: return True`). The natural shape is a
  parallel `job_approval` field: a child task's `erp_execute_write` call
  names which job + which batch-within-the-job it's executing, the
  pipeline looks up issue 03's store, confirms that job is approved AND
  this exact batch (by its position/hash in the stored plan) matches
  what was approved, and sets `job_approval` instead of asking for a
  fresh code.
- **The 24h lifetime and no-partial-reapproval rule (ADR 0008) enforce
  here**: the lookup must check the stored `issued_at` against the fixed
  window, and must recompute/compare the batch's own content against
  what's in the stored plan — a step added, removed, reordered, or
  changed anywhere in the job invalidates the whole job's approval, not
  just the changed batch (same strictness `_verify_batch()` already
  applies to a single batch, extended to "does this batch still match
  the one in the approved job plan").
- **Audit Log `approval_note`**: ADR 0008 says a job-level write should
  record the email-approval job id, same spirit as a batch write's
  `batch <code> step i/n` (`run_batch()` line ~1059). Add the equivalent
  string, e.g. `job <job_id> batch i/n step j/m`.

## Depends on

Issue 03 (the store to look up) and issue 05 (what marks a job approved).
This is the last piece — once it lands, a kanban child task that reaches
a confirmation-gated write no longer has to `kanban_block`.

## Comments

2026-10-23: implemented. `core/operations.py`'s `run_batch()` gains
`job_id`/`job_batch_index`, parallel to `batch_token` (mutually exclusive
with it) rather than a separate `job_approval` WriteContext field — it
still sets `ctx.batch_approval` under the hood, reusing the existing
pre-approved-step machinery (`_resolve_token_ok`'s `if ctx.batch_approval:
return True` already covers it) instead of adding a second field with the
same role.

New `operations._verify_job_batch()`: checks the stored job is Approved
and still within ADR 0008's 24h window (reusing `JOB_TOKEN_TTL_SECONDS`/
`get_job_approval()` from issues 04/05, not a new TTL), then recomputes
`job_token()` over the stored plan with the submitted batch spliced in at
`job_batch_index` and compares to the stored `token` — one hash compared
once, the same shape `_verify_batch()` already uses for a chat-code batch,
rather than a hand-rolled per-step comparison (a first draft did exactly
that and a code review caught it: comparing prepared requests pairwise
silently dropped the `requested_by`/`job_id` binding `job_token()` gets
for free). Recomputing with the row's STORED `requested_by` — never
`ctx.requested_by` — matters because the executing kanban worker's own
identity is not the Requester who was emailed; the check never demands
that worker impersonate them. Any difference anywhere in the job's full
plan (not only the submitted batch) breaks the hash, matching ADR 0008's
"no partial re-approval" rule. Audit Log label: `job <job_id> batch i/n
step j/m`.

`erp_tools.py`: `job_id`/`job_batch_index` wired into `erp_execute_write`'s
execute phase, mutually exclusive with `batch_token`, and — the actual
point of this issue — the `latest_prompt`-is-mandatory gate is waived on
this path, since a headless kanban worker has no live chat message to
cite; the job's own email-approval trail is the audit record instead.

Code-reviewed (Standards + Spec axes). Standards caught two real issues,
both fixed: the pairwise-comparison gap above, and a 3-way branch
(unconfirmed / batch-token / job-approval) that had been patched in as a
2-branch-plus-OR rather than a clean one-shot `if/elif/else`. Spec came
back clean (no gaps, no creep) other than suggesting a reorder-specific
test, added. 821 tests pass offline (`tests/test_batch_confirmation.py`).
**Not yet done:** no live kanban-worker round trip against a real
dispatched job — same open item issues 03-05 left, now covering the full
ADR 0008 chain end to end.

2026-10-24: live sales-dataset test found this isn't just untested — it's
**unreachable**. Nothing calls `operations.prepare_job()` outside test
code; there is no `erp_execute_write` phase that starts a job (only
`list_ops`/`render`/`execute` exist, and `render` only handles one op or
a flat `batch`, never a list of batches to become a job). The live agent
correctly self-diagnosed this and fell back to per-batch chat codes
rather than guess at a call that doesn't exist. See issue 07 — it blocks
this issue (and 04/05) from ever running in a live session even though
all are individually code-complete and unit-tested.
