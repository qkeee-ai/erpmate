# One confirmation code per batch

Status: done 2026-10-22 (erpnext-hermes a2ed26d, review fixes e3807f6; pushed 2026-10-22). ADR 0007 approved 2026-10-22.
Type: task
Blocked by: 01
Findings: F2, F3
Decision: D2 (spec.md)

## Problem

Each token-gated step gets its own code. In this run the user typed up to 9 codes per reply. `erp_execute_write` accepts `batch` only at `phase=execute` (`erp_tools.py`, `_execute_write`), and each step carries its own `confirmation_token`, so render is still one call per step. A batch that changes a record and then submits it fails, because the submit was rendered with the record's old `modified` (F3).

## Change

- **ADR** `agents/docs/adr/0007-one-confirmation-per-batch.md`. It covers:
  - why one code over a listed batch still meets "confirm-first human review" (SOUL);
  - the binding;
  - the cap;
  - what the Audit Log records.
- **Render a batch:** `phase=render` with `batch: [{op, args}, ...]`, at most 25 steps.
  - Each step is prepared like `prepare_only`.
  - One token over the ordered list of (operation key, doctype, action, name, body, confirmed facts, requester), plus the batch length and `issued_at`.
  - Returns `batch_token`, `confirmation_code`, `issued_at`, `expires_at` and one plain-word line per step.
  - Submit, cancel and delete steps are marked.
  - A step whose policy is `none` is listed as "no code needed".
- **Execute a batch:** `phase=execute` with the same `batch`, `batch_token`, `issued_at` and `user_confirmation_text`.
  - `run_batch` recomputes the binding before step 1 and refuses any difference: a step added, removed, moved or changed.
  - It verifies the code once.
  - Each step then runs through `run_operation` with the verified approval.
  - A per-step `confirmation_token` stays supported for single renders.
- **Chained `expected_modified` (F3):**
  - A step may set `expected_modified: {"from_step": k}`.
  - The binding stores the reference, not a timestamp.
  - At execute, the value is step k's result `modified` for the same doctype and name.
  - Refuse when step k wrote a different record.
- **Audit:** each step's Audit Log row carries the batch code and its step number in `approval_note`.
- **Stops:** execution stops at the first failure (unchanged). Steps after it are `not_attempted`; the agent renders a new batch for them.
- **Skill text:** render the whole remaining card as one batch; show the step list and the one code.

## Acceptance

- Tests:
  - one code executes 5 mixed steps;
  - any change to a rendered batch is refused before step 1;
  - a 26-step batch is refused;
  - an expired batch is refused;
  - `from_step` resolves to step k's `modified` and refuses a different record;
  - every Audit Log row names the batch code.
- ADR approved by nikhil.sharma@qkeee.ai.
