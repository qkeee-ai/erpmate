# SKILL.md's activation sequence needs a planner-intake step, not an optional escalation

Status: done (erpnext-hermes@24b4801)

Blocked by: 01

## Problem

`SKILL.md`'s activation sequence (first-contact steps run every
session) doesn't currently name "produce a comprehensive plan, then
decompose into atomic kanban cards" as a default step for a
write/multi-step request — kanban is reachable today only via
`03-spec-driven-execution.md`'s now-retired bucketing heuristic (issue
01), which the activation sequence doesn't itself gate on.

## Proposed fix

Add a step to `SKILL.md`'s activation sequence (or point to
`03-spec-driven-execution.md`'s rewritten section from issue 01)
stating the floor from ADR 0011 explicitly: before taking any write or
multi-step action, decide single-turn-read (direct reply) vs.
everything else (plan → atomic cards). Don't duplicate the floor
definition in two places — one canonical statement, cross-referenced.
