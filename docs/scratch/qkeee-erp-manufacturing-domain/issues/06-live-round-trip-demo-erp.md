# Live round trip: full create/submit cycle for BOM → Work Order → Job Card on demo_erp

Status: ready-for-human — needs an explicit go-ahead to write real records on demo_erp

## Blocked on, 2026-11-05

Code for issues 01-04 is done (erpnext-hermes@eb99887). A render-only
read against demo_erp confirmed no BOM exists yet for any Demo item
(clean slate, good test conditions) and surfaced Work Order's real
mandatory fields (`bom_no` required, not inferred). The actual
create+submit attempt was correctly refused by the auto-mode
classifier ("Modify Shared Resources") — writing real records to a
shared instance needs the user's explicit go-ahead, not autonomous
execution from a planning/build session. Needs: the user either runs
this round trip themselves, or explicitly authorizes a live-write
session to do it.

Blocked by: 01, 02, 03, 04

## Problem

Every other domain's `ALLOWED_WRITE_DOCTYPES` and non-negotiables were
confirmed against a real instance before being documented as ground
truth (per `manufacturing.md`'s own stated bar). None of issues 01-04
have been exercised against demo_erp as of this writing.

## Proposed fix

After 01-04 land: run `init-bot --domains manufacturing` on demo_erp,
then a full create→submit round trip (one BOM for an existing item,
one Work Order against it, one Job Card logging progress against the
Work Order's operations) through the normal confirmation-gated
`manufacturing.generic` path. Confirm: the permission grant from issue
02 was sufficient (no manual UI step needed), the BOM default-ambiguity
check (issue 03) and Work Order stock check (issue 04) both fire on a
deliberately-triggered bad case before the happy-path round trip, and
the Audit Log records every write correctly. This is the evidence
issue 05's doc rewrite should cite.
