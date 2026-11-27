# Read back the record after submit and cancel

Status: done 2026-10-22 (erpnext-hermes 5624960, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F8

## Problem

HRMS runs Payroll Entry submit as a background job. When the job fails, the record stays `docstatus 0` and `status Failed`, with `error_message` set. `run_batch` reported the step "succeeded" (part 2 A[74]–[81]). Only the agent's own read-back found it.

## Change

- After `frappe.client.submit` or cancel returns, read the record with the bot key (`internal=True`). Read the fields `docstatus`, `status` and `error_message`, when the doctype has them. Put this in `operations._send`, or in a `post` step shared by every resource submit and cancel.
- The expected `docstatus` is 1 after submit and 2 after cancel.
- When the record does not match:
  - the result carries `state_confirmed: false`, the live values and `error_message`;
  - the batch row is `failed` with `outcome: "not_applied"`;
  - the batch stops.
- When it matches: `state_confirmed: true`.
- When the read-back itself fails (network): `state_confirmed: null` and a warning. Nothing is undone.
- Skill text: a submit counts as done only with `state_confirmed: true`.

## Acceptance

- Tests:
  - a submit whose read-back shows `docstatus 0` and `status Failed` is a failed batch step and carries the `error_message`;
  - a normal submit gives `state_confirmed: true`;
  - a read-back error gives `null`.
