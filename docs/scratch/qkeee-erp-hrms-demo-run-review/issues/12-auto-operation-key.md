# `op: "auto"`: route a write to the owning operation

Status: done 2026-10-22 (erpnext-hermes 0a1a522, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F7
Decision: D5 (spec.md)

## Problem

The agent picked the wrong operation 8 times: Leave Application, Employee Onboarding and Employee Separation through `unscoped.generic`, and Interview Type through `hr_payroll.generic`. The gate refused each one correctly, but each cost a render round.

## Change

- `erp_execute_write` accepts `op: "auto"` with `args.doctype`. Resolve it at render:
  - the domain whose `DOMAIN_WRITE_ALLOWLISTS` holds the doctype gives `<domain>.generic`;
  - no domain gives `unscoped.generic`;
  - a doctype in two allowlists is refused as ambiguous.
- The render result shows `resolved_op`. The token and the batch binding (02) hold the resolved key, so execute cannot route elsewhere. An execute with `op: "auto"` re-resolves and must get the same key, else it is refused.
- Every gate runs on the resolved operation, unchanged.
- `auto` never resolves to a gated named operation (`owned_by`). Operations such as `fixed_assets.scrap` still need their own key.
- Cookbook: a short table of the HR doctypes and their owning operation.

## Acceptance

- Tests:
  - Leave Application resolves to `hr_payroll.generic`;
  - Interview Type resolves to `unscoped.generic`;
  - `User` (deny list) is refused as before;
  - a token rendered with `auto` refuses an execute that names another operation.
