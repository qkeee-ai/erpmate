# Retire the "ERPNext execution is special-cased away from kanban" framing in 00-conventions.md

Status: done (erpnext-hermes@24b4801)

Blocked by: 01

## Problem

`00-conventions.md` and `03-spec-driven-execution.md` both carry
language carved specifically around ERPNext execution work needing to
avoid plain `kanban_create` (historically because of issue 01's
auto-decomposer problem, worked around by routing ERPNext execution
through the ADR 0008 batches path *instead of* kanban). Under ADR 0011,
ERPNext execution is no longer special — it's one atomic card like any
other write/multi-step request; its worker just happens to use the
batches path for its own confirmation-gated writes. The carve-out
framing (as if ERPNext execution is the one domain that needs special
kanban handling) is now misleading.

## Proposed fix

Search `00-conventions.md` and `03-spec-driven-execution.md` for every
place ERPNext execution is named specifically in relation to kanban
avoidance, and reframe: issue 01's mitigation (never let the board's
own decomposer fan out a card) is universal, not ERPNext-specific: it
applies to the planner's atomic-card creation for *any* domain. ERPNext
execution's own distinguishing fact is just that its worker uses the
batches/job-email path — state that once, not as a kanban-avoidance
rule.
