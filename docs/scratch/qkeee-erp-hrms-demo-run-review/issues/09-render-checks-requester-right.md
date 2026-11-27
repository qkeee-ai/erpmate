# Render checks the requester's right for the action

Status: done 2026-10-22 (erpnext-hermes 53e0a31, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F13

## Problem

The requester had no submit right on Exit Interview. The requester gate refused it at execute, after the user had already confirmed a code for it.

## Change

- `operations.prepare_only` (and the batch render from 02) run the requester check for the rendered action: `_validate_prod_requester` with the same arguments as pipeline step 9, read-only. Use the existing helper that writes no Audit Log gate row, or add a `dry=True` flag to it.
- A failed check refuses the render, with the gate's message and its gap, so no code is shown for a step that cannot run.
- Execute keeps its own check (unchanged), because the right can change between render and execute.

## Acceptance

- Tests:
  - a submit render for a requester without submit is refused, with the gap;
  - with the right, the render succeeds;
  - execute still runs its own check.
