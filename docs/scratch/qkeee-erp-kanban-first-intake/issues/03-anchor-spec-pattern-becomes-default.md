# Anchor-spec + per-card-spec pattern becomes the default planning artifact, not an option for multi-card objectives

Status: done (erpnext-hermes@24b4801, folded into issue 01's edit)

Blocked by: 01

## Problem

`03-spec-driven-execution.md` already documents the anchor-spec
(`<slug>-<YYYYMMDD-HHMM>.md`) + per-card-spec
(`<slug>-card-<NN>-<YYYYMMDD-HHMM>.md`) pattern for a multi-card kanban
objective (shipped for `qkeee-erp-kanban-execution-steering` issue 15).
Under ADR 0011 this becomes the default planning artifact for *every*
write/multi-step request that gets decomposed into atomic cards, not
just ones that happen to already be multi-card kanban objectives.

## Proposed fix

Cross-reference from the rewritten section (issue 01): the planner's
"comprehensive plan" output *is* the anchor spec; each atomic task's
own scope is its per-card spec. No new mechanism needed — just make
explicit that this pattern is now load-bearing for the default intake
path, not an opt-in used only when a human happened to pick Kanban.
