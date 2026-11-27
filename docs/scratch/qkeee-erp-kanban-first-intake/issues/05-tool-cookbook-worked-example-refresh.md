# tool-cookbook.md worked examples: refresh for the new default intake path

Status: done, no change needed (checked 2026-11-05 against erpnext-hermes@24b4801)

## Resolution

Read end to end after issues 01-04 landed. Every kanban mention in
this file was already written at the mechanics level (how to call
`erp_execute_write`/`kanban_*`) — the stale framing lived entirely in
the policy-level docs (`03-spec-driven-execution.md`,
`00-conventions.md`), both already fixed. No example here contradicts
the rewrite; nothing to change.

Blocked by: 01, 02, 03, 04

## Problem

`tool-cookbook.md`'s worked examples for kanban/job-email calls were
written against the old "ERPNext execution avoids kanban, everything
else is case-by-case" model. Once issues 01-04 land, the examples
should show the new default shape: a plan decomposed into atomic
cards, each created with `triage` omitted, a card whose work needs
ERPNext writes using the batches/job_id path from inside its own
dispatched worker — as the normal case, not a special path.

## Proposed fix

Same treatment as `qkeee-erp-kanban-execution-steering` issue 24 (doc
refresh, last in that wave): once 01-04 are live, re-read
`tool-cookbook.md` end to end and update any example whose framing now
contradicts the rewritten docs. Do this last so it reflects the final
wording, not an intermediate draft.
