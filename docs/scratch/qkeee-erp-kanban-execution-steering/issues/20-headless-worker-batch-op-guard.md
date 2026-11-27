# A kanban-dispatched worker can still render a plain per-batch chat code, defeating ADR 0008/0010's entire point

Status: ready-for-agent
Blocked by: none (independent context check, no schema dependency)
Blocks: nothing further, but should land before 24 (doc refresh) so the cookbook example reflects the enforced rule

## What happened

29-Oct run: mid-job, the agent rendered a plain `batch`/`op` confirmation (ADR 0007's 15/60-minute, process-local token) inside a kanban-dispatched (headless) worker — the exact case ADR 0008 exists to prevent, because a headless worker has no live chat session to receive a pasted code. This is the proximate cause of the 60-minute-TTL token mismatch observed in the 28-Oct run's HSN block, and a contributing cause of repeat-code churn generally: a batch token is bound to the rendering process's `WriteContext`, which does not survive a respawn, unlike the job-level token (durable in `Qkeee Bot Job Approval`).

## Exact fix

In `erp_tools.py`'s `_execute_write()`, when `HERMES_SESSION_SOURCE == "kanban"` (the same signal `SKILL.md` step 3.3 already checks for requester resolution) and the call is a plain `render`/`execute` with `batch`/`op` (not `batches`) on a step that needs confirmation, refuse with a clear message naming the `batches`/job-level path as the required alternative instead of silently rendering a token that cannot survive this context. A step that needs no confirmation (a domain `generic` draft) is unaffected — this guard only applies to confirmation-gated writes.

Add a regression test: call `_execute_write(phase="render", batch=[...])` with a mocked `HERMES_SESSION_SOURCE=kanban` and assert refusal naming `batches`.

## Depends on

Nothing.
