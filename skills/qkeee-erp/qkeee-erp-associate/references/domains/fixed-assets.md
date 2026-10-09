# Domain: fixed-assets (Asset lifecycle)

Code: `qkeee_erp/domains/fixed_assets.py`
(`ALLOWED_WRITE_DOCTYPES = ("Asset", "Asset Movement", "Asset Repair")`).
Every write is a named operation through `erp_execute_write`
(`tool-cookbook.md` has the render → confirm → execute flow). ERPNext v16
signatures were verified on 2026-10-06.

| Operation | Does | Args |
|---|---|---|
| `fixed_assets.generic` | Asset / Asset Movement / Asset Repair `create`/`update` (drafts) and `submit`/`cancel`/`delete` (confirmed; refused if the record changed since render) | `doctype`, `action`, `payload`, `name` |
| `fixed_assets.depreciation_run` | `make_depreciation_entry`: posts a Journal Entry for every due, unbooked row of one schedule | `depr_schedule_name`, optional `date`; render fills `asset`, `pending_rows`, `total_depreciation` |
| `fixed_assets.scrap` | `scrap_asset`: depreciation up to `scrap_date`, then the scrap JE | `asset`, `scrap_date` (required, never defaulted), `reason`; render fills `book_value` |
| `fixed_assets.restore` | `restore_asset`: reverses disposal depreciation and CANCELS the scrap JE (a GL change) | `asset`, `reason` |
| `fixed_assets.sell` | creates a DRAFT Sales Invoice for the asset (`make_sales_invoice` only maps one, it persists nothing; v16 needs `sell_qty`) | `asset`, `item_code`, `company`, `sell_qty`, `customer`, `sale_proceeds`, `reason`, optional `serial_no`; render fills `invoice`; the op sets `customer` and the asset row's rate (`sale_proceeds / qty`), which the mapper leaves blank/0 |

All four non-generic operations always need the confirmation: render,
show the user the rendered request (it includes the live facts above)
and its code, then execute with their reply. Their token covers the
exact RPC body and the facts shown. `sch_start_idx`/`sch_end_idx` are
never accepted. With both set, ERPNext posts every row in the slice even
if it is already booked or not yet due.

**Partial depreciation.** If a run fails part-way, ERPNext keeps posting
the other rows. The error then lists which rows now carry a Journal
Entry, and execute_write exits with code 4. Review those rows with the
user before any retry.

**Sale.** `fixed_assets.sell` creates the invoice as a draft. The selling
price is `sale_proceeds` (applied as the asset row's rate) and the buyer is
`customer` — change those args and re-render, not the rendered `invoice`'s
rate, which the op overwrites. Submitting
it is a separate `accounts.generic` submit — gain/loss is realized only
then.

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
   **Done when:** the target doctype/RPC is confirmed inside this
   domain's scope before any write is proposed.
2. **Depreciation, scrap, restore and sale are the operations
   `fixed_assets.depreciation_run`/`scrap`/`restore`/`sell`** — never a
   raw request. Each needs a rendered confirmation and the user's reply
   with its code. A render for one asset cannot run against another.
   **Done when:** the operation ran through `erp_execute_write` with
   the render's args, token and the user's reply.
3. **Asset capitalization**: a draft is only "ready" when cost basis is
   present and nonzero (or a stated reason for zero), the source is
   unambiguous (a linked purchase document, or `is_existing_asset`
   stated), `asset_category` is set, and — if `calculate_depreciation` is
   set — the finance book (method, total periods, frequency, start date)
   is complete. Present, confirm, `fixed_assets.generic` `create` (lands
   `docstatus 0`). **Save-draft-then-review-then-submit:** if capitalizing
   immediately, re-fetch via `core.client.get_resource()` (needed for the
   `finance_books` child table, and to keep `modified` unstripped for the
   next step) and render the `fixed_assets.generic` `submit`: the render
   binds the record's current `modified`, and the submit is refused if
   anyone changes the record before it runs. Submitting an Asset
   also submits its auto-created Asset Depreciation Schedule in the same
   call — the review must cover the schedule config too. **Done when:**
   cost basis, source, and (if applicable) the finance book are all
   confirmed present, and the schedule config was reviewed before
   submit.
4. **Depreciation runs**: fetch every `Depreciation Schedule` row with
   `schedule_date <= today` and an empty `journal_entry` (the "pending"
   rows) first — never guess at what's due. Use the asset's current book
   value from `Asset.finance_books[N].value_after_depreciation`, NOT the
   top-level `Asset.value_after_depreciation` field, which is confirmed
   live to NOT update after a run (a stale-field trap). Only after both
   the render and the second confirmation, execute
   `fixed_assets.depreciation_run` with the rendered args and token. Its
   render already shows the due rows and their total — check them
   against what you computed. **Done when:** the pending-rows fetch,
   the current-book-value read from `finance_books[N]` (never the stale
   top-level field), and both confirmations all happened before the RPC
   fired.
5. **Asset transfer**: see the non-negotiable above for the location
   freshness check. Receipt items are exempt (no prior location to
   check). Present, confirm, `create` (lands `docstatus 0`).
   **Save-draft-then-review-then-submit:** re-fetch via `get_resource()`
   (needed for the per-row child table) and check `asset`,
   `source_location`, `target_location`, `to_employee`/`from_employee`
   Link fields resolve to real records before `submit`. **Done when:**
   every Link field on the re-fetched record resolves to a real record,
   before `submit`.
6. **Disposal (scrap or sale)**: require a stated `reason` (never accept a
   bare "dispose it"). For scrap, the entire current book value (from
   `finance_books[]`, not the stale top-level field) is the write-off
   amount. For sale, require `sale_proceeds` and state the resulting
   estimated gain/loss explicitly — and be clear that the drafted Sales
   Invoice is NOT submitted by this domain; gain/loss is only realized
   when someone submits that invoice separately. Only after both
   confirmations, execute `fixed_assets.scrap` (with an explicit
   `scrap_date`) or `fixed_assets.sell` with the rendered args and token.
   **The sale path (draft invoice through eventual submission) is not
   confirmed live-tested end to end** — treat its exact field defaults/
   error modes as unconfirmed until it is. **Done when:** a stated reason, the
   correct book-value/proceeds figure, and both confirmations are in
   place before the RPC fires.
7. **Asset maintenance scheduling and Asset Repair** are moderate-risk,
   single-confirm (not double) — they don't carry the same book-value/
   write-off stakes. Stage a normal draft, confirm, `create`/`update` via
   `fixed_assets.generic`, submit via a rendered `fixed_assets.generic` submit. If
   `capitalize_repair_cost` is set on a repair, say so explicitly since it
   changes the asset's book value going forward. **Done when:** one
   confirmation is given and, if `capitalize_repair_cost` is set, that
   impact is stated.
8. **Asset audit / physical verification checklists**: no single figure
   to tie out — declare `not_applicable` with the reason in `notes`. A
   depreciation-schedule-review report DOES have a tie-out (sum of
   scheduled depreciation amounts vs. depreciable base) — use it, don't
   hand-check it. **Done when:** a real tie-out ran where one exists, or
   `not_applicable` carries a stated reason.

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
