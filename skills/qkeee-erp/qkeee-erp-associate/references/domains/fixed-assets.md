# Domain: fixed-assets (Asset lifecycle)

Code: `scripts/domains/fixed_assets.py`
(`ALLOWED_WRITE_DOCTYPES = ("Asset", "Asset Movement", "Asset Repair")`),
which also carries this domain's genuine connector logic:
`mutate_resource_with_concurrency()`, `call_whitelisted_method()` (see
that module's docstring — `call_whitelisted_method()` also writes to
`Qkeee Bot Audit Log`, not just the usual ERPNext Comment).

## When this domain applies

Capitalizing a new asset, reviewing or running depreciation, transferring/
relocating an asset, scheduling or logging maintenance/repair, disposing
of or scrapping an asset, running a physical asset verification.

## Non-negotiables specific to this domain

- **Depreciation runs and disposals never execute without explicit
  confirmation, and get a DOUBLE confirm** — state the financial impact
  in plain terms, then ask again — these are financially irreversible-
  in-spirit even though technically cancelable in ERPNext. Every relevant
  financial fact must be stated before rendering (pending-period count and
  total for a depreciation run; method, book value, and — for a sale —
  proceeds and gain/loss for a disposal); asking again after showing the
  rendered confirmation is required, one "yes" never covers both the
  concept and the specifics.
- **A single depreciation-run call can post more than one period at
  once.** ERPNext's `make_depreciation_entry` posts every currently-
  overdue period on a schedule in one call, not one period at a time.
  Always show exactly how many periods and how much total depreciation
  are about to post before calling it.
- **A transfer's stated source location must be verified against the
  asset's actual current location before it's staged as ready.** ERPNext
  does not cross-check `Asset Movement.source_location` against the
  asset's real `Asset.location` at create/submit time, so a fabricated or
  stale source would be silently accepted. Fetch the asset's real current
  `location` immediately before rendering (not a cached value); refuse
  the draft if the declared `source_location` doesn't match, or if the
  location snapshot is older than 300 seconds.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
2. **Route the four disposal/depreciation RPCs**
   (`make_depreciation_entry`, `scrap_asset`, `restore_asset`,
   `make_sales_invoice`) through
   `domains.fixed_assets.call_whitelisted_method()` — never a raw request.
   It enforces `read-write` mode in code, and for the three double-confirm
   methods (`make_depreciation_entry`, `scrap_asset`, `make_sales_invoice`)
   additionally requires a `confirmation_token` matching what a render
   script computed — the call is refused without it. `restore_asset` is
   mode-gated but not token-gated (a recovery action, not a write-off).
3. **Asset capitalization**: a draft is only "ready" when cost basis is
   present and nonzero (or a stated reason for zero), the source is
   unambiguous (a linked purchase document, or `is_existing_asset`
   stated), `asset_category` is set, and — if `calculate_depreciation` is
   set — the finance book (method, total periods, frequency, start date)
   is complete. Present, confirm, `mutate(..., "create")` (lands
   `docstatus 0`). **Save-draft-then-review-then-submit:** if capitalizing
   immediately, re-fetch via `core.client.get_resource()` (needed for the
   `finance_books` child table, and to keep `modified` unstripped for the
   next step) and call `domains.fixed_assets.mutate_resource_with_concurrency()`
   for the `submit` — never call plain `mutate()` for an Asset submit, or
   the TOCTOU concurrency check is silently skipped. Submitting an Asset
   also submits its auto-created Asset Depreciation Schedule in the same
   call — the review must cover the schedule config too.
4. **Depreciation runs**: fetch every `Depreciation Schedule` row with
   `schedule_date <= today` and an empty `journal_entry` (the "pending"
   rows) first — never guess at what's due. Use the asset's current book
   value from `Asset.finance_books[N].value_after_depreciation`, NOT the
   top-level `Asset.value_after_depreciation` field, which does NOT
   update after a run (a stale-field trap). Only after both the render
   and the second confirmation, call `call_whitelisted_method()` with
   `"make_depreciation_entry"` and the printed `confirmation_token`.
5. **Asset transfer**: see the non-negotiable above for the location
   freshness check. Receipt items are exempt (no prior location to
   check). Present, confirm, `create` (lands `docstatus 0`).
   **Save-draft-then-review-then-submit:** re-fetch via `get_resource()`
   (needed for the per-row child table) and check `asset`,
   `source_location`, `target_location`, `to_employee`/`from_employee`
   Link fields resolve to real records before `submit`.
6. **Disposal (scrap or sale)**: require a stated `reason` (never accept a
   bare "dispose it"). For scrap, the entire current book value (from
   `finance_books[]`, not the stale top-level field) is the write-off
   amount. For sale, require `sale_proceeds` and state the resulting
   estimated gain/loss explicitly — and be clear that the drafted Sales
   Invoice is NOT submitted by this domain; gain/loss is only realized
   when someone submits that invoice separately. Only after both
   confirmations, call `call_whitelisted_method()` with `"scrap_asset"`/
   `"make_sales_invoice"` and the printed token. **The sale path
   (`make_sales_invoice` through eventual submission) is not confirmed
   live-tested end to end** — treat its exact field defaults/error modes
   as unconfirmed until it is.
7. **Asset maintenance scheduling and Asset Repair** are moderate-risk,
   single-confirm (not double) — they don't carry the same book-value/
   write-off stakes. Stage a normal draft, confirm, `create`/`update` via
   `mutate()`, submit via `mutate_resource_with_concurrency()`. If
   `capitalize_repair_cost` is set on a repair, say so explicitly since it
   changes the asset's book value going forward.
8. **Asset audit / physical verification checklists**: no single figure
   to tie out — declare `not_applicable` with the reason in `notes`. A
   depreciation-schedule-review report DOES have a tie-out (sum of
   scheduled depreciation amounts vs. depreciable base) — use it, don't
   hand-check it.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Asset capitalization | New Asset from a purchase | Refuses "ready" if cost basis/source/category/depreciation config incomplete |
| Depreciation schedule review | Schedule visibility | Reconciliation: scheduled sum vs. depreciable base |
| Depreciation run | Depreciation JE(s) posted | DOUBLE confirm; states every period about to post |
| Asset transfer | Location/custodian updated | Refuses "ready" if declared source doesn't match real current location |
| Asset maintenance scheduling | Maintenance tracked | Single-confirm |
| Asset repair | Repair logged, optionally capitalized | Single-confirm |
| Asset disposal/scrap | Asset retired correctly | DOUBLE confirm; sale path not fully live-tested |
| Asset audit / physical verification | Verification-ready checklist | `not_applicable` — no single figure to tie out |

## Relationships

Consumes `domains/procurement.md` (capitalizing from a Purchase Receipt/
Invoice) and feeds `domains/accounts.md` (depreciation JEs, disposal
sales invoices). Conceptually adjacent to `domains/inventory.md` but a
distinct doctype universe.
