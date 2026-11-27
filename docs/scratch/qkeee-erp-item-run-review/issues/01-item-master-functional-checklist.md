# Item master: functional checklist and correct use of `purchase_sourced_item`

Status: resolved
Type: task
Blocked by: none
Findings: F1, F2

## Problem

The agent created a purchase service Item with only `item_group`, `stock_uom` and the three flags. It did not ask for:

- `item_defaults[].expense_account`: without it, a Purchase Invoice line falls back to the company `default_expense_account` (often Cost of Goods Sold). Marketing spend books to the wrong account.
- `gst_hsn_code` (SAC for a service): india_compliance purchase lines and GSTR-2B / ITC reconciliation use it, even when the Item validation does not require it (`validate_hsn_code && is_sales_item`).
- Item Tax Template / ITC eligibility (`is_ineligible_for_itc`).
- `item_defaults[].buying_cost_center`, default supplier.

The agent also set `purchase_sourced_item: true` for a typed request. `item_write_helpers.apply_purchase_sourced_item_defaults()` is "for an Item whose only evidence is a purchase document". Its default `is_stock_item: 1` is wrong for a service.

`domains/inventory.md` has no Item master section. The domain table routes Item to inventory, so the agent latched a file with no guidance for it.

## Change

1. `references/domains/inventory.md`: add an "Item master" section:
   - Item has no domain write path: `unscoped.generic`, every action token-gated.
   - The clarify checklist, by Item kind (stock good, non-stock service, fixed asset), with the reason for each field.
   - Instance defaults ERPNext adds on save (`Item Default` row with the company default warehouse, `UOM Conversion Detail`), to state in Side effects before the write.
   - `purchase_sourced_item` only when the Item comes from a purchase document (doc-extraction). For a typed request, set the flags explicitly.
   - Idempotency: also search on the main words of the name, not only the full name.
2. `references/tool-cookbook.md`: the `purchase_sourced_item` comment says when to use it and when not.

## Acceptance

- `inventory.md` has the Item master section with the checklist above.
- The cookbook comment names the purchase-document case only.
- No code change.

## Comments

- Implemented 2026-10-14 in erpnext-hermes (uncommitted on `feat/discovery-write-readiness`): `domains/inventory.md` gains "Item master" (checklist table, item code convention, near-duplicate search, instance defaults, `purchase_sourced_item` scope, price rule), procedure step 1 and Quick reference updated; `tool-cookbook.md` Item example no longer sets `purchase_sourced_item` and says when to use it.
