# `prepare_job()` mints a fresh token on every respawn, orphaning a code the Requester may have already answered

Status: ready-for-agent
Blocked by: 16 (needs the schema in place; the lookup this issue adds is a read against the same row 16 defines)
Blocks: nothing further

## What happened

Confirmed recurring across two independent live runs:
- 28-Oct (`logs/sales-demo-28-Oct/agent-responses.log` ~line 332-333): "blocked 3x for the same cause... the earlier code 01651F came from the previous run's render token, which this run does not hold."
- 29-Oct (`logs/sales-demo-29-Oct/prompt.log` ~line 2817-2907): a Requester-approved code (`2A5BEF`, run 48) died when the worker respawned as run 49 — "bound to a render token from run 48 and cannot execute" — forcing a re-render and a new code (`A0DE59`). The card's own note: "3 prior blocks on stale confirmation codes."

Root cause: `prepare_job()` (`core/operations.py` ~line 1215) always mints a new `job_id`/`job_token` on render. Nothing checks whether a `Pending` (or already-`Approved`-but-not-yet-fully-executed) row already exists for this same card/plan before minting fresh.

## Exact fix

Before generating a new `job_id` in `prepare_job()`'s caller (`erp_tools.py`'s `_execute_write()`, `batches` render branch — issue 07's entry point), look up `Qkeee Bot Job Approval` for an existing row where the stored `plan` matches the batches being rendered (same batch count, same ops in order — a deep-equal check against the stored `plan` JSON, not a fuzzy match) and `status` is `Pending` or `Approved`. If found, return that row's `job_id`/`job_token`/`confirmation_code`/`expires_at` unchanged instead of calling `prepare_job()` again. Only mint fresh when no matching row exists, or the existing row's plan genuinely differs (a real plan change — still forces a new email/token, same as ADR 0008's existing "no partial re-approval" rule; this issue does not relax that).

Add a regression test that renders the same `batches` twice (simulating two dispatches of the same card) and asserts the second render returns the first call's `job_id`/`token` unchanged.

## Depends on

16 (schema; this issue's lookup reads the same `Qkeee Bot Job Approval` row 16's fields live on).
