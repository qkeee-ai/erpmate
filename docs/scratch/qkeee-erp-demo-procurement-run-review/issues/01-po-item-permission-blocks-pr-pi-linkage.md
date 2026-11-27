# Missing Purchase Order Item read permission silently drops the requested PO↔PR/PI linkage scenario

Status: ready-for-agent (doc fix shipped; demo_erp role grant + live retest still needed)

## Problem

`erp_query` on `Purchase Order Item` was refused (no role for the child doctype). Instead of surfacing this as a blocker, the agent fell back to standalone Purchase Receipts and Purchase Invoices with no `against_purchase_order` link.

The user's original request explicitly asked for "scenarios should span part receipts or one to many po to pi and pr combinations" — one-to-many PO-to-PI/PR linkage. Without the row-level `against_purchase_order` link, the resulting records are cosmetically similar (matching quantities and suppliers) but not structurally linked in ERPNext — a Purchase Receipt/Invoice list view or report that filters by source PO would not find them. The deliverable looks like it satisfies the ask; it doesn't.

## Impact

Any demo/test data task that depends on PO→PR/PI linkage silently degrades to unlinked documents whenever the Qkeee Bot lacks `Purchase Order Item` read — and nothing in the conversation flags that the scenario wasn't actually built as asked.

## Proposed fix

`qkeee-erp-associate` (or `procurement.md`) should state: creating a Purchase Receipt or Purchase Invoice "against" a Purchase Order requires read on the child doctype `Purchase Order Item`, not just `Purchase Order`. When that read is missing, the agent must say so and ask (role gap → Role Permission Manager fix, same pattern as other role-gap findings) rather than silently substituting standalone documents for a named linkage scenario.

## Comments

2026-10-23: Doc fix shipped — `procurement.md` ("Linking a Purchase Receipt or Purchase Invoice to its Purchase Order needs read on `Purchase Order Item`...") and `CHANGELOG.md` row `DP 2026-10-23`. The actual demo_erp role grant (Role Permission Manager row for `Purchase Order Item` read) is an Operator/admin action in the ERPNext UI, not something this skill does itself — left `ready-for-agent` for that grant + a live retest of a linked PR/PI scenario, not `done`.
