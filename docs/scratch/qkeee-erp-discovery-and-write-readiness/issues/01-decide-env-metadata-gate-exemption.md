# Decide: environment metadata reads are exempt from the requester gate

Status: resolved
Type: grilling
Blocked by: none
Blocks: 02, 03

## Problem

`_validate_prod_requester()` checks the requester's own permission on `Module Def` before `discover.py modules` and `discover.py apps` run. On DEMO_ERP the requester (HR user) holds no Module Def read, so both calls were refused. The environment catalog stayed partial.

The requester's Module Def permission says nothing about whether they may create an Employee. `DocType` reads are already exempt from the requester read gate (`client.py:198`) for this reason.

## Options

- **A (recommended).** Treat environment metadata (`Module Def`, the `get_versions` RPC) like `DocType`: exempt from the requester read gate, still audit-logged under the requester. The bot account needs read on Module Def (issue 03).
- **B.** Keep the gate. The requester must hold System Manager (or a custom role with Module Def read) to run discovery. This gives HR users broad rights only so discovery can run.
- **C.** Keep the gate, and on refusal fall back to `apps_seen: unknown` and continue. The catalog stays partial on every instance with least-privilege users.

## Decision needed

1. A, B or C?
2. Does A fit the narrow-role bot decision in `qkeee-erp-write-path-hardening/issues/05`?
3. Record the result as an ADR (`docs/adr/`).

## Acceptance

- An ADR records the decision and the reason.
- Issues 02 and 03 are updated to match.

## Comments

**Resolved 2026-10-08** (grill-with-docs, nikhil.sharma@qkeee.ai).

1. **Option A.** Environment Metadata is exempt from the Requester Gate, still audit-logged under the Requester. The exemption is a closed list: `DocType`, `Module Def`, the `frappe.utils.change_log.get_versions` RPC. Adding to it needs a new ADR.
2. **Fits D1** (`qkeee-erp-write-path-hardening/issues/05`): Module Def read is a metadata grant on `Qkeee Bot`, not a write privilege. The Bot Account still holds no System Manager.
3. Recorded in `docs/adr/0001-environment-metadata-exempt-from-requester-gate.md`. Issues 02 and 03 updated.
