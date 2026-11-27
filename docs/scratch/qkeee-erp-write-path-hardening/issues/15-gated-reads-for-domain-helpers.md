# Route domain read helpers through the read gate and read audit log

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P3
Blocked by: none (fits better after 07 for its ctx shape)
Closes: W13

## Problem

These helpers call `_request` directly. They skip `_validate_prod_requester` (RBAC) and `_log_read`, so a requester without permission can read through them and no trace is left:

- `inventory.get_stock_reconciliation_items` (a POST RPC, but a read)
- `inventory.get_bin_qty` (GET `/api/resource/Bin`)
- `system_admin.get_roles_and_doctypes`
- `system_admin.get_permissions`
- `system_admin.get_scheduler_status`

## Change

- `get_bin_qty`: use `core_client.query_resource(tag, "Bin", ...)` with `requested_by` and the context.
- The RPC reads: add a `core_client.read_rpc(tag, path, params|payload, *, gate_doctype, ptype="read", ctx)` helper. It runs `_validate_prod_requester(gate_doctype, "read")`, sends, then calls `_log_read`. Use these gate doctypes:

| Helper | Gate doctype |
|---|---|
| Stock Reconciliation get_items | `Stock Reconciliation` |
| Role Permission Manager | `DocType` |
| Scheduler status | `Scheduled Job Type` |

- Every helper gains a required `requested_by` and an optional context.
- Update the call sites in the docs (`references/domains/inventory.md`, `system-admin.md`).

## Acceptance

- [x] Each helper raises `UnvalidatedProdRequesterError` for a requester without the gate permission. Tests mock the gate.
- [x] Each helper writes one read audit row on success.
- [x] A grep test finds no direct `_request(` call in `domains/*.py`. This joins the invariant test in 18.

## Comments

**Implemented 2026-10-06.**

- New helper `core.client.read_rpc()` runs the gate, sends, then logs
  the read. An optional `credential` sends with the admin key while the
  gate and audit stay on the bot.
- Inventory reads go through `query_resource()` and `read_rpc()`.
- System-admin reads use `read_rpc(credential="admin")`.
- New `fixed_assets.map_sales_invoice()` uses `read_rpc`.
- Every helper now needs `requested_by`.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
