# Token-gate delete everywhere; bind submit/cancel tokens to document content

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P3
Blocked by: 10
Closes: W09, W10
Decision: D5 — resolved 2026-10-06: gate delete in every domain

## Problem

- **W09:** accounts, sales, procurement, inventory and hr_payroll gate only `{"submit", "cancel"}`. ERPNext allows deleting **cancelled** submittable documents, so a cancelled Purchase Invoice or Journal Entry can be deleted with no token. That erases the business record, though the GL reversal stays.
- **W10:** for submit and cancel the payload is empty, so the token covers only (action, doctype, name). If someone edits the draft between the user's confirmation and the submit, the edited version is submitted under the old approval.

## Change

1. In every `<domain>.generic` op, set the policy to `token+user_code` for `submit`, `cancel` and `delete`. Keep `none` for create/update, which are the draft steps reviewed before submit.
2. Make the submit/cancel/delete `prepare` include `expected_modified` in the hashed request, read live at render time. Add a mandatory precondition:
   - re-read `modified` at execute time
   - refuse on mismatch with "changed since you confirmed it — re-render"
   This generalizes the old fixed_assets-only concurrency check (09 uses the same helper).
3. Optionally also hash a content digest of the doc's money and quantity fields, so the render shows what is being submitted. Decide in implementation: `modified` alone is enough to detect a change, and the digest only improves the render.

## Acceptance

- [x] Delete without a token is refused in each writer domain.
- [x] Submit after a concurrent edit is refused. The test mocks the GET returning a newer `modified`.
- [x] `references/00-conventions.md` Non-negotiable 5 says create → update → submit/cancel/delete, with the last step token-gated in every domain.

## Comments

**Implemented 2026-10-06** inside the generic operation.

- **Concurrency binding.** `render_expected_modified` binds `modified` at
  render. The precondition `check_not_modified_since_render` refuses
  every finalizing action whose record changed or lacks
  `expected_modified`.
- **Delete is gated in every domain.**
- **The optional content digest was not done;** `modified` detects the
  change.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
