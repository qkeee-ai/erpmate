# Make Supplier+KYC creation atomic and require a tax ID

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P1 (decision) → P3 (implementation)
Blocked by: none (decision); 07 (if implemented as an op post-hook)
Closes: W11
Decision: D2

## Problem

`procurement.mutate()` (`domains/procurement.py:127`) creates the Supplier first, then the linked Address, then the Contact, as separate writes.

- If the Address create fails, for example on a validation error, the Supplier exists with no KYC and the exception propagates.
- The successful Supplier result is lost to the caller, so the agent typically retries and creates a **duplicate Supplier**.
- The KYC gate only checks that `kyc["address"]` is non-empty. A bare `{"address_line1": "x"}` satisfies it, although the domain's own non-negotiable needs a tax ID.

## Options for D2

| Option | Behaviour |
|---|---|
| A. Compensating delete (recommended) | On an Address or Contact failure, delete the just-created Supplier, which is safe because a fresh Supplier has no transactions. Audit it as `Delete` with the note "KYC rollback". Raise a `KycLinkFailedError` that names the field error. If the compensating delete itself fails, raise `KycPartialFailureError` carrying the Supplier name and instructions to backfill or delete it. |
| B. Partial result | Return `{"data": supplier, "_kyc": {"address_error": ...}}` without raising. The agent must surface it. This relies on agent discipline. |
| C. Pre-validate first | Before the Supplier create, schema-map the Address and Contact and check their mandatory fields against live meta. Still combine with A for server-side validation failures. |

Recommendation: C + A.

## Tax-ID requirement

The tax ID field differs per instance (`gstin`, `tax_id`, `pan`). Proposal:

- Add `KYC_TAX_ID_FIELDS = ("gstin", "tax_id", "pan")` plus an env override `QKEEE_ERP_<TAG>_KYC_TAX_ID_FIELD`.
- The gate passes only when the address payload has at least one of these with a non-empty value, or when `kyc_waiver_confirmed=True`.

## Acceptance (after decision)

- [x] Address create fails: the Supplier is deleted and a clean error is raised. The test asserts the delete call and its audit rows.
- [x] Compensating delete fails: the error carries the Supplier name.
- [x] Address without a tax-ID field: refused before any write.
- [x] `references/domains/procurement.md` describes the rollback and the tax-ID rule.

## Answer

Resolved 2026-10-06 by nikhil.sharma@qkeee.ai: **C + A**, plus the tax-ID requirement as proposed.

- Pre-validate the Address and Contact against live meta before the Supplier create.
- On a server-side failure, delete the Supplier, then raise `KycLinkFailedError`.
- If that delete fails, raise `KycPartialFailureError` with the Supplier name.
- A tax-ID field is required: one of `KYC_TAX_ID_FIELDS`, or the field named by `QKEEE_ERP_<TAG>_KYC_TAX_ID_FIELD`. The only way around it is `kyc_waiver_confirmed=True`.

## Comments

**Implemented 2026-10-06.**

- Pre-validation runs as a precondition: tax ID, then mandatory
  Address/Contact fields from live meta.
- `create_linked_kyc` posts the linked records. On failure,
  `PostHooks.compensate_create()` deletes the Supplier; it can delete
  only the record this operation just created, and runs RBAC and audit.
- New errors: `KycLinkFailedError`, and `KycPartialFailureError` (a
  `PartialOutcomeError`, CLI exit 4).
- Tests: `domains/test_procurement.py`, 18 of them.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
