# Kanban-first task intake: planner → atomic cards → ready/todo, by default

Status: needs-triage

## Context

ADR 0011 (`docs/adr/0011-kanban-first-task-intake.md`, accepted
2026-11-05) decides the shape: every write/multi-step request is
planned up front, decomposed into atomic kanban cards by the planner
itself (never the board's auto-decomposer), each created with `triage`
omitted (defaults `ready`/`todo`). A single-turn read-only exchange
stays a direct reply — no plan, no card. The job-email chain
(ADR 0007/0008/0009/0010) is unchanged; this only governs intake.

This directory tracks the implementation: rewriting the skill docs
that currently encode the old complexity-bucketing heuristic, and
retiring the now-incorrect "cards land in triage by default" framing
everywhere it appears.

The triage-default half of this (erpnext-hermes repo, branch
`feature/kanban-first-intake`) is already shipped:
`03-spec-driven-execution.md`'s kanban section now states the real
`initial_task_state()` behavior (`triage` defaults `false` → `ready`/
`todo`) and corrects the `initial_status` red herring. What's left is
the bucketing-heuristic rewrite and everywhere else the old framing is
assumed.

## Issues

- `issues/01-rewrite-spec-vs-kanban-bucketing.md` — **done** (`24b4801`)
- `issues/02-skill-activation-sequence-planner-step.md` — **done** (`24b4801`)
- `issues/03-anchor-spec-pattern-becomes-default.md` — **done** (`24b4801`, folded into 01)
- `issues/04-conventions-erpnext-execution-carveout-retire.md` — **done** (`24b4801`)
- `issues/05-tool-cookbook-worked-example-refresh.md` — **done, no change needed** (checked, nothing contradicted the rewrite)

2026-11-05: all 5 issues closed on `erpnext-hermes@24b4801` (branch
`feature/kanban-first-intake`, on top of the triage-default fix
`e835aba`), pushed. 883 tests pass, no regressions (doc-only changes
— this effort touches skill reference docs, not plugin code). ADR
0011 is now fully reflected in `03-spec-driven-execution.md`,
`00-conventions.md`, and `SKILL.md`.

2026-11-06: merged into `develop` (`580b5a6`), after tagging develop's
pre-merge state as `v2.1` (`78bfd89`) and merging
`feature/manufacturing-domain` first. `SKILL.md` auto-merged cleanly
against that branch's own edit to the same file (different sections).
889 tests pass on merged `develop`. Not yet deployed to dev-erp.

## Decisions so far

- Floor: single-turn read-only stays a direct reply; any write or
  multi-step request goes through the full planner→atomic-cards→kanban
  intake (2026-11-05 user decision, ADR 0011 point 1).
- Planner does the decomposition itself; the board's own auto-
  decomposer is never used — keeps issue 01 of
  `qkeee-erp-kanban-execution-steering` mitigated, now universally
  instead of only for ERPNext execution (ADR 0011 point 2).
- This ADR/effort does not touch confirmation or execution mechanics —
  ADR 0007/0008/0009/0010 stand unchanged (ADR 0011 point 4).

Related: [[qkeee-erp-kanban-execution-steering]] (issue 01 dependency,
issue 12 — unrelated, already-in-triage dead end, not this effort's
triage-default fix).
