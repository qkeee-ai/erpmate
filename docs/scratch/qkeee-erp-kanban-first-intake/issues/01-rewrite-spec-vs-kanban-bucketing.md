# Rewrite the spec-file-vs-kanban complexity heuristic into ADR 0011's uniform floor

Status: done (erpnext-hermes@24b4801, branch feature/kanban-first-intake)

## Problem

`erpnext-hermes/skills/qkeee-erp/qkeee-erp-associate/references/
03-spec-driven-execution.md`'s "Kanban instead of a spec file" section
currently buckets work by complexity ("A spec file suits a
single-session, single-actor task. When the work instead crosses agent
boundaries, needs to survive a restart, needs human input mid-task, or
needs to stay discoverable later... propose Hermes' Kanban feature").
ADR 0011 replaces this heuristic: the floor is single-turn-read-only
(direct reply, no plan/card) vs. everything else (full planner intake,
always kanban).

## Proposed fix

Rewrite the section's opening framing to state the floor directly: a
single-turn read gets a direct reply; anything involving a write or
spanning multiple steps gets a comprehensive plan (anchor spec) then
atomic kanban cards, always — not "propose Kanban" as one option among
several, and not gated on crossing-agent-boundaries/surviving-a-restart
framing, which was a symptom-based heuristic ADR 0011 retires in favor
of a flat rule. Keep the file-format guidance for the anchor/per-card
spec pattern (issue 03 below) — only the gating heuristic changes.
