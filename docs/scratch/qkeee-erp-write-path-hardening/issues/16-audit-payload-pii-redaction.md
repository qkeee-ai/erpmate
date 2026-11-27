# Redact PII in audit payload_before / payload_after / field_diff

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P3
Blocked by: none
Closes: W19

## Problem

`record_audit_log_start` and `record_audit_log_finish` (`core/client.py:1610`, `:1647`) apply `json.dumps` to `payload_before`, `payload_after` and `field_diff` **without** `_redact_pii_deep`. Other fields are redacted: `approval_note`, the prompts and `channel_metadata`. For Employee, Supplier bank and User writes, the audit log then permanently stores SSN or card-like values in clear text. These are exactly the values the Comment path already redacts.

## Change

- Pass all three through `_redact_pii_deep` before `json.dumps`.
- Add a field-name denylist that is masked regardless of value pattern. Make it overridable per env:
  - `bank_ac_no`
  - `iban`
  - `pan`
  - `aadhaar`
  - `passport_number`
  - `api_key`
  - `api_secret`
  - `new_password`
- Keep `field_diff` usable: show `"***changed***"` for a masked field rather than dropping it.

## Acceptance

- [x] Tests: an Employee update with `bank_ac_no` and an SSN-shaped string stores neither in any audit field.
- [x] `field_diff` still lists the masked field as changed.

## Comments

**Implemented 2026-10-06.**

- `AUDIT_MASK_FIELDS` can be extended with `QKEEE_ERP_AUDIT_MASK_FIELDS`.
- `_redact_audit_payload()` handles `payload_before`/`payload_after`.
- `_redact_field_diff()` shows a changed masked field as
  `***changed***`.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
