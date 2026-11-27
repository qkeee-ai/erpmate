# Work Order stock-availability pre-check before staging "ready"

Status: partially done (erpnext-hermes@eb99887) — code + unit tests shipped as a note (not a refusal), the live-check-first step against demo_erp still open

Blocked by: 01

## Problem

`references/domains/manufacturing.md` procedure step 4 hypothesizes
that Work Order creation should resolve real-time stock availability
for every raw-material line before staging as "ready," reusing
`domains/inventory.md`'s `get_bin_qty()`-style freshness check — but
it's unconfirmed whether ERPNext's own Work Order creation already
does this server-side, or silently trusts whatever's passed (like
Stock Reconciliation's `current_qty`).

## Proposed fix

1. Live-check first: create a Work Order against known-insufficient
   stock on demo_erp and observe whether ERPNext itself refuses/warns.
   If it already enforces this server-side, the check here only needs
   to surface that result clearly — don't duplicate server-side
   validation client-side for no reason.
2. If ERPNext trusts the input (likely, per the Stock Reconciliation
   precedent cited in `manufacturing.md`), add a `render_defaults`/
   `enrich` hook on the Work Order create path that resolves each raw
   material line's available qty via the same `get_bin_qty()`
   convention `inventory.py` already uses, and flags (doesn't silently
   proceed past) any line below the Work Order's required quantity.
3. Update `manufacturing.md`'s step 4 from "proposed, not yet wired"
   to the confirmed, live-tested behavior either way.
