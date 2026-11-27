# One anchor spec per objective, one per-task spec per kanban card

Status: ready-for-agent
Blocked by: none

## What happened

28-Oct live run: only one spec file existed for the whole objective (`demo-sales-dataset-20261028.md`), patched in place as the plan changed. No per-card spec existed — batch detail lived only in kanban card bodies/comments. The chat-shown digest said "10 batches"; the job actually rendered as 4 batches. This divergence happened silently at render time and was only caught because the user noticed and asked why — nothing in either the anchor spec or any card-level record would have surfaced it on its own.

## Fix

Add to `03-spec-driven-execution.md`'s "Kanban instead of a spec file" section: when a kanban board is in play for a multi-card objective, write **one anchor spec** (the existing `./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md` convention: objective, global Deviations log, the planned batch/card count) plus **one per-card spec** per kanban card (`./qkeee-erp-specs/<slug>-card-<NN>-<YYYYMMDD-HHMM>.md`: that card's own scope, its actual batch boundary, its confirmation/token state). Each card's `kanban_comment` links to its own spec file path rather than restating the detail inline. A divergence between the anchor's planned count and a card's actual batch boundary becomes a diffable fact instead of something only a human reading the chat transcript would catch.

This also gives the job-resume work (issue 17) and the per-step ledger (issue 16) a stable, human-readable mirror of what each card is doing — not a replacement for the doctype-backed ledger, but a parallel record a human can read without querying ERPNext.

## Depends on

Nothing.
