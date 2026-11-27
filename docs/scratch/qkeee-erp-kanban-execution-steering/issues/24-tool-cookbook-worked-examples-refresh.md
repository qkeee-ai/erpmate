# Refresh `tool-cookbook.md`'s job-path examples once the real behavior ships

Status: done 2026-10-30
Blocked by: 17, 18, 19, 20, 21, 22, 23 (all shipped)
Blocks: nothing

## Done

All three `tool-cookbook.md` updates landed, grounded in the actual
`run_batch()`/`_resolve_from_batch()` code (`qkeee_erp/core/operations.py`):

1. The `<FROM_BATCH_N_STEP_M>` caveat no longer says "unverified, probe
   it" — replaced with a worked two-call example (batch 1 creates a
   Customer, batch 2 references it) and the real mechanics (resolves at
   execute time from the durable `execution_log`, works across separate
   worker dispatches). Kept the one caveat that's still true: it only
   resolves to the record's own `name`, never a child-table row name
   (`_batch_log_lookup()` only stores top-level `name`).
2. Added a worked JSON example of a parked step's `result.steps[]` row
   (status/error/refused/outcome_unknown/write_failure/checked fields,
   read straight off `run_batch()`'s own construction) plus what to tell
   the user and the `kanban_block` threshold.
3. Documented the render-is-idempotent-on-respawn behavior (issue 17):
   calling render again with the same batches returns the existing
   job's id/token unchanged — this is `prepare_job()`'s own internal
   `_find_resumable_job()` call, not a separate agent-facing "resume
   check" tool, so the right documentation is "this is safe and normal,"
   not a new call to make first.

**The `03-spec-driven-execution.md` sub-task did not apply as written:**
checked the "Kanban instead of a spec file" section and its full git
history — no line there (or anywhere else in the skill) ever rejected
the job-email path for "runtime-discovered names." The section's actual
content (kanban_create fan-out vs. the job-email path) is unrelated to
issue 18's placeholder fix and needed no change. Left as-is rather than
editing a premise that doesn't hold.

## What's needed

Once issues 17-23 ship, `tool-cookbook.md`'s "Starting a kanban job" section needs three updates:

1. Replace the "unverified, probe it" caveat on `<FROM_BATCH_N_STEP_M>` (currently accurate — it's confirmed broken, issue 18) with a correct worked example showing it resolving across two separate `execute` calls.
2. Add a worked example of a step parking as Draft (issue 19): what the response looks like, what the agent should tell the user, and that the card keeps running rather than blocking.
3. Add the resume-check call (issue 17) as the documented first step before every `batches` render on a card that might be a respawn, not just as an internal safety net — the agent should know to expect "this job already exists" as a normal, non-error outcome.

Also update `03-spec-driven-execution.md`'s "Kanban instead of a spec file" section: the current line rejecting the job-email path for anything with runtime-discovered names is no longer accurate once issue 18 ships — narrow it to name only genuinely unresolvable cases (something outside the approved template entirely, which now parks per issue 19, rather than failing the whole path).

## Depends on

17, 18, 19, 20, 21, 22, 23 — this is documentation of the shipped behavior, not a design decision; don't start until the behavior it describes is real, or the examples will be wrong on arrival.
