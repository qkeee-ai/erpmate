# Spec rules: live checks, no predicted names, render is not proof

Status: done 2026-10-22 (erpnext-hermes 9a0fe12, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F9, F14, F15

## Problem

- The agent's spec said "Creates need no token" and "Designation HR Executive exists". Both were wrong.
- It predicted document names to link later steps, and the real names differed.
- It took a render as proof that a fix worked.

## Change

In `references/03-spec-driven-execution.md` (the single source), with a pointer from `tool-cookbook.md`:

- Every "exists", "needs no code" or "is named X" fact in a spec cites the live call that showed it (a query, `list_ops` or `preflight`).
- Read a created record's name from its result. A later step links to that name, never to a predicted one. When steps need each other's names, split the batch at that point.
- A render shows the request. A write is proven by execute plus read-back (`state_confirmed`, issue 03).

## Acceptance

- The three rules appear once, as positive instructions, each with one clause of why.
