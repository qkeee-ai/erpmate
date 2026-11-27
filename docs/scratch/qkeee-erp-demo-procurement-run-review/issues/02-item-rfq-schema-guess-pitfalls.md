# Two avoidable schema-guess failures: Item buying/selling rate field, RFQ row UOM

Status: done

## Problem

Two render-fail-fix-rerender cycles in the log, both avoidable with a schema check before rendering instead of after a rejection:

1. **Item create rejected `standard_rate`.** The agent set `standard_rate` on a purchase-sourced Item; that field is the selling-price field, not the buying price. ERPNext rejected it.
2. **RFQ item row rejected for missing `uom`/`conversion_factor`.** The agent omitted both on a Request for Quotation item row; ERPNext requires them.

Each cost a full render → rejection → fix → re-render round trip (and, where the render was a confirmation-gated batch, a wasted confirmation code).

## Proposed fix

Add both as named pitfalls to the procurement/inventory reference docs (`procurement.md` and/or `inventory.md` under `qkeee-erp-associate/references/`):

- Item: buying-side price goes on a different field than `standard_rate` (selling); check live schema for which rate field the create path actually needs before rendering, don't assume.
- RFQ item row: `uom` and `conversion_factor` are required on the row, not inherited from the Item automatically — set both explicitly.

## Comments

2026-10-23: Checked `inventory.md` first — the Item/`standard_rate` pitfall was **already documented** ("Price. Do not put `standard_rate` on the create..."), so that half needed no edit; the run either predated that doc fix or the agent didn't apply it, not a doc gap. The RFQ `uom`/`conversion_factor` pitfall was genuinely missing and is now added to `procurement.md` (Procedure step 4). `CHANGELOG.md` row `DP 2026-10-23`.
