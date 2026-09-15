# Domain: manufacturing (BOM, Work Order, Job Card) — NEW, unvalidated

**This domain has no write path yet.** Everything below is authored from
general ERPNext/Frappe product knowledge, the same way `references/domains/
doc-extraction.md` and every other domain file cite ERPNext mechanics —
**but with none of the live-instance confirmation every other domain
file's specifics carry** (no `<erp-instance>` round trip, no confirmed
field-mandatory list, no confirmed submit/cancel behavior, no confirmed
Workflow-vs-role-heuristic finding for this module). Treat every claim
below as a starting hypothesis to verify against a real target instance
(`discover.py resolve`/`meta`, per `01-connectivity.md`) before acting on
it, not as ground truth the way `domains/inventory.md`'s batch-tracking
finding is.

**No code exists yet either.** There is no `scripts/domains/
manufacturing.py`. This reference document describes the target shape a
future `manufacturing.py` should take; until that module exists and
registers an `ALLOWED_WRITE_DOCTYPES` allowlist via
`core.client.register_domain_allowlist()`,
`core.client.mutate_resource(..., domain="manufacturing")` will raise
`DoctypeNotAllowedError` for every doctype — there is no write path for
this domain in the shipped skill yet, full stop. Anything below framed as
"drafting/creating" a manufacturing doctype describes a future build
target, not a live capability.

## When this domain applies

Building or reviewing a Bill of Materials, planning production (Production
Plan / MRP), creating or tracking a Work Order, logging shop-floor
progress via Job Card, or checking raw-material availability against a
planned production run — once built; see the intro above.

## Non-negotiables specific to this domain

Proposed, unvalidated — confirm each against a live instance before
trusting it, same caveat as the intro above.

- **A BOM change on an already-consumed-in-production item needs the same
  save-draft-then-review-then-submit discipline as any other domain** —
  hypothesized, not confirmed: whether ERPNext blocks/warns on editing an
  active BOM referenced by open Work Orders needs a live check before this
  is stated as a guarantee.
- **Work Order creation should resolve real-time stock availability for
  every raw-material line before staging as "ready"** — analogous to
  `domains/inventory.md`'s stock-transfer freshness check
  (`get_bin_qty()`), reused here rather than reinvented, but not
  confirmed whether ERPNext's own Work Order creation already does this
  server-side or whether (like Stock Reconciliation's `current_qty`) it
  silently trusts whatever's passed.
- **Job Card completion percentage / status transitions likely need the
  same double-check-before-submit discipline** as Stock Entry material
  transfers — unconfirmed which Job Card actions are submittable vs.
  plain status updates on this ERPNext version family.

## Procedure

Draft, needs live confirmation — same caveat as the intro above.

1. Before proposing any manufacturing-specific field or workflow, run
   `discover.py resolve "BOM"` / `"Work Order"` / `"Job Card"` and
   `discover.py meta` for each — per `01-connectivity.md`'s non-negotiable
   4, do not carry general ERPNext manufacturing knowledge into a specific
   org's instance without this confirmation, especially here where no
   prior build has ever done it. **Done when:** every field/doctype
   claim in the ensuing draft traces to a live `discover.py` result, not
   general knowledge.
2. **BOM (Bill of Materials):** review/draft the item/operation/raw-
   material structure. Query existing BOMs for an item before assuming
   none exists (`query_resource("BOM", filters=[["item","=",...]])`).
   Flag (don't silently accept) a BOM with no default marked, or more than
   one active default for the same item — a likely-inconsistent-state
   signal worth surfacing, not silently picking one. **Done when:** the
   existing-BOM check ran and any default-marking ambiguity is flagged,
   not picked silently.
3. **Production Plan / MRP:** raw-material requirement aggregation across
   planned Work Orders — prefer any built-in ERPNext report over hand-
   aggregating BOM explosions, per `01-connectivity.md`'s "built-in
   reports vs. hand-aggregated queries" guidance, once one is confirmed to
   exist for this purpose on a target instance. **Done when:** a built-in
   report was checked for first, and only if absent is a hand-aggregation
   attempted.
4. **Work Order:** stage a draft (item, BOM, qty, warehouses) and check
   raw-material availability via `domains/inventory.md`'s
   `get_bin_qty()`-style freshness check before marking "ready" — reused
   convention, not yet wired into any manufacturing-specific renderer.
   Save-draft-then-review-then-submit, same as every write-capable domain
   — this is a Non-negotiable in `00-conventions.md` regardless of domain,
   not something manufacturing gets to skip for being new. **Done when:**
   the freshness check ran and the draft was reviewed post-save, before
   any submit.
5. **Job Card:** log shop-floor progress against a Work Order's
   operations. Confirm the Job Card's parent Work Order and operation Link
   fields resolve to real records before reporting progress recorded.
   **Done when:** both Link fields are confirmed resolved before progress
   is reported.

## Quick reference

Proposed, not yet built — see the intro above.

| Capability | Outcome | Status |
| --- | --- | --- |
| BOM review/drafting | Structure visible or staged | Read likely works today via generic `query_resource`/`get_resource`; drafting needs `manufacturing.py` |
| Production planning (MRP) | Raw-material requirements aggregated | Not built — needs a confirmed built-in report or hand-aggregation path |
| Work Order creation/tracking | Production run drafted/tracked | Not built — needs stock-availability check design, analogous to inventory's |
| Job Card logging | Shop-floor progress recorded | Not built — submit/status-transition behavior unconfirmed |

## Relationships

Reuses `domains/inventory.md`'s `get_bin_qty()` freshness-check
convention for Work Order raw-material availability (proposed step 4
above) rather than reinventing it — the one concrete cross-domain link
this file has today. **What this domain deliberately doesn't try to do
yet:** no write capability ships. Read-only exploration (via the generic
`core.client.query_resource()`/`get_resource()`/`run_query_report()`,
with `domain=` omitted since there's no allowlist yet to gate against) is
usable today for a user who just wants to look at BOM/Work Order/Job Card
data — but say explicitly that manufacturing writes aren't a capability of
this skill yet, rather than attempting to route them through
`gated_mutate_resource()` as a workaround. When a real build happens, it
should write `scripts/domains/manufacturing.py`, declare its
`ALLOWED_WRITE_DOCTYPES`, and replace every "proposed"/"unconfirmed" claim
in this file with a live-verified one — the same bar every other domain
file here is held to.
