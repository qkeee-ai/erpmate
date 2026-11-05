# Domain: manufacturing (BOM, Work Order, Job Card, Production Plan, Routing, Workstation, Operation)

**Code exists now** (`qkeee_erp/domains/manufacturing.py`), registering
`ALLOWED_WRITE_DOCTYPES` for all 7 doctypes above and the
`manufacturing.generic` operation — the same shape every other domain's
generic op has (create/update land drafts; submit/cancel/delete are
token-gated). Permission is provisioned the same way as every other
domain: `doctype_defs.py`'s `DOMAIN_TEMPLATE_ROLES["manufacturing"]`
feeds `init-bot --domains manufacturing`, copying a stock role's
permlevel-0 rows onto `Qkeee Bot` — no manual Role Permission Manager
step needed.

**What's still pending live confirmation** (agents
`.scratch/qkeee-erp-manufacturing-domain/`, issues 02/03/04/06): the
exact stock role name(s) in `DOMAIN_TEMPLATE_ROLES` (currently
`"Manufacturing Manager"`, `"Manufacturing User"` — standard ERPNext
role names, not yet confirmed against a live `Role` query the way every
other `DOMAIN_TEMPLATE_ROLES` entry was), whether ERPNext's own
`BOM.validate()` clears a prior default when a new one is marked
default, and whether ERPNext's own Work Order creation already blocks
on insufficient stock. The two checks below work either way — they're
self-contained, not dependent on confirming ERPNext's native behavior
— but treat anything phrased as "ERPNext does/doesn't already enforce
this" as a hypothesis until the live round trip runs.

## When this domain applies

Building or reviewing a Bill of Materials, planning production
(Production Plan / MRP), creating or tracking a Work Order, logging
shop-floor progress via Job Card, or checking raw-material availability
against a planned production run.

## Non-negotiables specific to this domain

- **A BOM create/update setting `is_default: 1` refuses if another BOM
  for the same item already has an active default** — enforced in code
  (`manufacturing.py`'s `_enrich_bom_default`), not just agent
  discipline: it queries live BOM rows at render/execute time and
  raises `PreconditionFailedError` naming the conflicting BOM(s) rather
  than silently leaving two defaults open. Still query existing BOMs
  for an item before assuming none exists
  (`query_resource("BOM", filters=[["item","=",...]])`) — the code
  check only fires on `is_default: 1`, not on every create.
- **Work Order creation resolves live Bin qty for every raw-material
  line** (`manufacturing.py`'s `_enrich_work_order_stock_check`, reusing
  `domains/inventory.md`'s `get_bin_qty()` convention) and attaches a
  shortfall note to the rendered request rather than refusing — show
  that note to the requester before staging the Work Order as ready;
  don't silently proceed past it.
- **Job Card completion percentage / status transitions likely need the
  same double-check-before-submit discipline** as Stock Entry material
  transfers — unconfirmed which Job Card actions are submittable vs.
  plain status updates on this ERPNext version family; this is the one
  claim in this file with no code-level check yet, agent discipline
  only.

## Procedure

1. Before proposing any manufacturing-specific field or workflow, run
   `erp_discover resolve "BOM"` / `"Work Order"` / `"Job Card"` and
   `erp_discover meta` for each — per `01-connectivity.md`'s
   non-negotiable 4, do not carry general ERPNext manufacturing
   knowledge into a specific org's instance without this confirmation.
   **Done when:** every field/doctype claim in the ensuing draft traces
   to a live `erp_discover` result, not general knowledge.
2. **BOM (Bill of Materials):** review/draft the item/operation/raw-
   material structure. Query existing BOMs for an item before assuming
   none exists. The default-ambiguity refusal above is a backstop, not
   a substitute for checking first. **Done when:** the existing-BOM
   check ran before any create/update that sets `is_default`.
3. **Production Plan / MRP:** raw-material requirement aggregation
   across planned Work Orders — prefer any built-in ERPNext report over
   hand-aggregating BOM explosions, per `01-connectivity.md`'s
   "built-in reports vs. hand-aggregated queries" guidance, once one is
   confirmed to exist for this purpose on a target instance. **Done
   when:** a built-in report was checked for first, and only if absent
   is a hand-aggregation attempted.
4. **Work Order:** stage a draft (item, BOM, qty, warehouses); the
   render step's stock-shortfall note (above) surfaces automatically —
   relay it to the requester before marking "ready," don't re-derive it
   by hand. Save-draft-then-review-then-submit, same as every
   write-capable domain. **Done when:** any shortfall note was relayed
   and the draft was reviewed post-save, before any submit.
5. **Job Card:** log shop-floor progress against a Work Order's
   operations. Confirm the Job Card's parent Work Order and operation
   Link fields resolve to real records before reporting progress
   recorded. **Done when:** both Link fields are confirmed resolved
   before progress is reported.

## Quick reference

| Capability | Outcome | Status |
| --- | --- | --- |
| BOM review/drafting | Structure visible or staged; default-ambiguity refused in code | Live, pending the live round trip for ERPNext's own clearing behavior |
| Production planning (MRP) | Raw-material requirements aggregated | Not built — needs a confirmed built-in report or hand-aggregation path |
| Work Order creation/tracking | Production run drafted/tracked; stock shortfall noted in code | Live, pending the live round trip for ERPNext's own stock enforcement |
| Job Card logging | Shop-floor progress recorded | Write path live (`manufacturing.generic`); submit/status-transition behavior still unconfirmed |

## Relationships

Reuses `domains/inventory.md`'s `get_bin_qty()` freshness-check
convention for Work Order raw-material availability — imported directly
from `inventory.py`, not reimplemented. Permission provisioning follows
the same `doctype_defs.py` `DOMAIN_TEMPLATE_ROLES` pattern as
`accounts`/`hr_payroll`/`inventory`/`procurement`/`sales`. Tracked at
agents `.scratch/qkeee-erp-manufacturing-domain/` — see that spec for
what's code-complete vs. still pending a live round trip against
demo_erp.
