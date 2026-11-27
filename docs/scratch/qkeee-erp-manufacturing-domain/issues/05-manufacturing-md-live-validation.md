# Rewrite references/domains/manufacturing.md from hypothesis to live-validated reference

Status: partially done (erpnext-hermes@eb99887) — rewritten to reflect code-complete-pending-live-round-trip; a further pass is needed once issue 06 actually runs against demo_erp, to flip the remaining hedged language to confirmed

Blocked by: 01, 02, 03, 04

## Problem

`manufacturing.md` currently carries the header "NEW, unvalidated" and
caveats every claim as "authored from general ERPNext/Frappe product
knowledge... with none of the live-instance confirmation every other
domain file's specifics carry." Once issues 01-04 ship and are
live-tested against demo_erp, this is no longer true and the doc
should stop saying it is.

## Proposed fix

Same bar every other domain file was held to: replace "proposed/
unconfirmed" language throughout with the actual confirmed behavior
(mandatory fields, submit/cancel behavior, the BOM-default and
Work-Order-stock-check findings from issues 03/04, the real
`DOMAIN_TEMPLATE_ROLES` role names from issue 02). Update the Quick
Reference table's "Status" column from "Not built" to the real state.
Update `SKILL.md`'s domain table row (currently "unvalidated, no write
path yet") and `docs/qkeee-erpnext-feature-inventory.md`'s Manufacturing
row (currently "*(none yet — no code)*... Planned") to match. Do this
last, against the final code, not an intermediate draft.
