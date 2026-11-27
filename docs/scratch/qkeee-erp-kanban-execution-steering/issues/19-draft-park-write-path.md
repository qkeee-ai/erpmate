# A step outside the approved template should park as Draft, not block the whole card

Status: ready-for-agent
Blocked by: 16, 18 (needs the ledger and placeholder resolution so "outside the approved template" is a real, checkable condition rather than a guess)
Blocks: 21 (notify needs something to report), 24 (doc refresh)

## What's needed

ADR 0010 point 2: a step that needs a genuinely new human decision not covered by the approved job template (a value the template didn't anticipate, a permission gap) should not halt the whole card. Today, the only outcome `erp_execute_write`/kanban integration supports for a confirmation-gated step it can't complete is `kanban_block` — stopping everything in that card, including independent later steps that don't depend on the blocked one.

Per ADR 0010 point 3 (revised): a step doesn't park just because it looks unresolvable. First check `erp_discover` (`meta`/`preflight`/`resolve`) and the environment's latched `qkeee-erp-learned` skill for an answer — most "surprises" (a wrong HSN digit count, a field name) are answerable from live ERPNext metadata or prior learning, not an open question for the Requester. Park only what's left after that check, and record which check ran and came up empty.

## Exact fix

1. In `_execute_write()`'s `execute` phase, when a step's resolved payload still has an unresolvable field (not a literal, not a placeholder 18 can resolve, and not matched by the approved template) or a precondition fails that the template didn't anticipate (e.g. a role-permission 403 on an action not flagged in the plan):
   - First, attempt resolution via `erp_discover` (`meta`/`preflight`/`resolve`) and the latched `qkeee-erp-learned` skill for this environment. Only proceed to parking if neither resolves it — log which check ran and that it came up empty, alongside the park entry.
   - If the target doctype allows a draft state (docstatus 0) and the step itself is a `create`, run the create without the subsequent submit/cancel/delete action. Record a `parked` entry on the job's `execution_log` (issue 16): which step, what's missing, which decision is needed.
   - Continue to the next step that does not depend on this one's output (the dependency graph is already implicit in the batch/placeholder structure — a step referencing this one's `<FROM_STEP_N>` is skipped and also parked, with a reason pointing at the original park).
   - Only call `kanban_block` when literally every remaining step in the card depends, directly or transitively, on a parked item.
2. A step that is itself an update/submit/cancel/delete on an *existing* record (no draft state possible) and hits the same condition parks as a no-op log entry (nothing written) with the same reasoning, rather than attempting a partial write.
3. Job's Outcome report (close-out, `03-spec-driven-execution.md` step 7) must list every parked item with its reason and the specific decision needed — never silently drop it.

## Depends on

16 (ledger to log parks against), 18 (so "outside the template" excludes anything placeholder-resolution would have handled).
