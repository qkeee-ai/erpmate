# Domain: inventory (Stock, transfers, reconciliation)

Code: `qkeee_erp/domains/inventory.py`
(`ALLOWED_WRITE_DOCTYPES = ("Stock Entry", "Material Request", "Stock
Reconciliation")`), which also carries this domain's genuine connector
logic (both reads are gated and audit-logged — pass `requested_by=` and
the session context): `get_bin_qty()`, `get_stock_reconciliation_items()`,
`bin_rows_to_actual_source_qty()`.

## When this domain applies

Checking stock levels, transferring/moving stock between warehouses,
reconciling a physical count against system stock, triggering a reorder/
purchase requisition, tracing a batch/serial number's history, creating or
changing an Item (see "Item master" below: Item is not in the tuple above).

## Non-negotiables specific to this domain

Two distinct live findings drive these — enforced in code, not just
prompt:

- **Transfers:** ERPNext accepts an outgoing (`s_warehouse`) line with
  more qty than is actually on hand at Draft creation, and only rejects it
  at Submit. Never stage a transfer as "ready" without checking the
  source line's qty against a **freshly-fetched** Bin balance
  (`domains.inventory.get_bin_qty()`) — a balance read earlier in the
  conversation can go stale. Check cumulatively across every line in the
  same draft sharing an item_code/s_warehouse pair — two lines each
  individually under balance can still overdraw it combined.
- **Reconciliations:** a Stock Reconciliation line's `current_qty` is NOT
  resolved from the real Bin balance by ERPNext at create time — it
  silently echoes back whatever the caller passed (or 0). Harmless for a
  non-batch item; **not harmless for a batch-tracked item**: ERPNext
  reconciles per batch, and an unresolved current_qty/batch_no risks
  creating a brand-new batch and ADDING to the existing balance instead of
  correcting it — this exact mistake has inflated a real 6-unit balance
  to 14 units. Always resolve `current_qty` via
  `domains.inventory.get_stock_reconciliation_items()` first — never a
  bare Bin read, never a guess. For a batch-tracked item, that function
  returns one row PER EXISTING BATCH — resolve and pass through each
  batch's own `batch_no`/`current_qty` individually.
- **A cancelled document that posted to the ledger usually can't be
  deleted.** Frappe v16 keeps the cancelled GL Entry (and Stock Ledger
  Entry) rows, so a delete fails with `LinkExistsError` ("linked with GL
  Entry") unless Accounts Settings `delete_linked_ledger_entries` is on.
  Live-confirmed on DEMO_ERP 2026-10-06 for Journal Entry and Stock Entry.
  The failure is clean (exit 1, nothing deleted, Failure audit row). Treat
  cancel as the end state; don't retry, and don't suggest turning the
  setting on without an accounts owner's say-so.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype is confirmed inside the tuple above
   before any write is proposed, or it is `Item` and "Item master" below
   applies.
2. **Stock level queries**: fetch `Bin` rows via
   `domains.inventory.get_bin_qty()` for every warehouse in scope, sum
   them, and state the reconciliation (sum across queried warehouses vs.
   stated total) explicitly. Stock level is always item + warehouse; a
   "total" figure only means an explicit sum across a named warehouse set.
   **Done when:** the sum-vs-stated-total reconciliation is stated in the
   reply.
3. **Stock transfer** (Issue/Receipt/Transfer/etc.): fetch a fresh
   `get_bin_qty()` balance for every `(item_code, s_warehouse)` pair among
   the drafted lines, convert with `bin_rows_to_actual_source_qty()`, and
   use it as the freshness check above requires. Present, confirm, then
   `inventory.generic` `create` (lands `docstatus 0`; submit needs a
   rendered confirmation — `tool-cookbook.md`).
   **Save-draft-then-review-then-submit:** re-fetch via
   `core.client.get_resource()` (the list endpoint silently drops the
   line-items child table) and review every line — quantities and every
   Link field (`item_code`, `s_warehouse`, `t_warehouse`) resolve to real
   records and match what was confirmed — before `submit` as its own
   step. **Done when:** every drafted line's Link fields resolve to real
   records and quantities match the confirmed draft, before `submit`.
4. **Stock reconciliation**: resolve `Item.has_batch_no`/
   `Item.has_serial_no` for every item being reconciled first
   (`query_resource("Item", ...)`). Pass `current_valuation_rate` whenever
   `valuation_rate` is nonzero — without it, the value delta would compare
   against an implied current rate of 0. State the qty and value delta
   plainly before asking for confirmation — a reconciliation restates the
   baseline everything after it is measured against; treat it with that
   weight. **Save-draft-then-review-then-submit:** re-fetch via
   `get_resource()` and check `item_code`/`warehouse`/(for batch-tracked
   items) `batch_no` resolve to real records, and `qty`/`valuation_rate`
   match what was resolved via `get_stock_reconciliation_items()` — this
   review is not optional even when the pre-flight check already refused
   an unresolved line, given the batch-inflation risk above. **Done
   when:** every line's `current_qty`/`batch_no` came from
   `get_stock_reconciliation_items()`, and the post-save re-fetch matched,
   before the reconciliation is reported complete.
5. **Reorder / Material Request triggers**: single-confirm, not double —
   a Material Request is a request, not a commitment; the buy/make
   decision belongs downstream. Recommend (don't require) a target
   `warehouse` for a Purchase-type request. A drafted, unsubmitted
   Material Request is a complete, valid outcome on its own. **Done
   when:** one confirmation is given before `create`.
6. **Batch/serial trace queries**: query `Stock Ledger Entry` filtered to
   the item/batch/serial, chronological order, feed the rows straight
   into the running-balance check — don't hand-reconstruct it. If the
   running balance comes back inconsistent, say so explicitly before
   presenting the trace as complete; usually a query-limit truncation
   (check `has_more`) or a genuine gap worth investigating, not something
   to paper over. **Done when:** `has_more` is checked, and any
   running-balance inconsistency is named rather than silently presented
   as clean.

## Item master

`Item` is not in `ALLOWED_WRITE_DOCTYPES`. Write it with
`unscoped.generic`; every action needs a rendered confirmation. `Item` is
not submittable: a create is live on save. Say so before approval.

**Clarify checklist.** Ask the whole set in one round
(`03-spec-driven-execution.md` step 1), each with your recommended value.
A missing account or tax field does not fail the save, so ERPNext will
not warn; it fails later, on a purchase or sales document.

| Field | Ask when | Why |
| --- | --- | --- |
| `item_group`, `stock_uom` | always | Mandatory. Offer live values from `Item Group` (`is_group = 0`) and `UOM` (`enabled = 1`). |
| `is_stock_item`, `is_purchase_item`, `is_sales_item` | always | Set all three explicitly. A service is `is_stock_item = 0`. |
| `item_defaults[].expense_account` (per company) | non-stock purchase item (services, consumables expensed on receipt) | Without it, a Purchase Invoice line uses the company `default_expense_account`, often Cost of Goods Sold. Marketing, rent or professional fees then book to the wrong account. |
| `item_defaults[].buying_cost_center` | the company tracks spend by cost center | Purchase lines otherwise take the company default cost center. |
| `item_defaults[].income_account`, `selling_cost_center` | sales item | Same fallback on Sales Invoice lines. |
| `gst_hsn_code` (HSN for goods, SAC for services) | Indian company (`india_compliance` installed) | Validation requires it only for a sales item when GST Settings `validate_hsn_code` is on. Purchase lines still carry it to GSTR-2B / ITC reconciliation, so ask for purchase items too. |
| `taxes[]` (Item Tax Template), `is_ineligible_for_itc` | Indian company, item with a non-standard rate or blocked ITC | Otherwise the document's default tax template applies. |
| `item_defaults[].default_supplier` | one regular supplier | Pre-fills purchase documents. |
| `has_batch_no`, `has_serial_no`, `valuation_method` | stock item | Cannot change freely after stock transactions exist. |
| `is_fixed_asset`, `asset_category` | the purchase is a fixed asset | Routes to `fixed-assets.md`; `asset_category` becomes mandatory. |

Fetch account and cost-center candidates live (`Account` with
`root_type = "Expense"`, `is_group = 0`, `company = <company>`; `Cost
Center` with `is_group = 0`). Never guess an account name.

**Item code.** `autoname` is usually `field:item_code`, so the code is the
record name and cannot change later without a rename. Check the existing
codes' convention (`erp_query Item fields=["item_code","item_name"]
limit=20`) and follow it, or ask.

**Idempotency.** Query the exact `item_code`, then `item_name like` the
full name, then `item_name like` each main word of the name. Show any near
match ("Marketing Services", "Digital Marketing") and ask before you
create.

**Instance defaults on save.** ERPNext adds one `Item Default` row per
company (with the company's default warehouse, even for a non-stock item)
and one `UOM Conversion Detail` row for `stock_uom` (factor 1). Name them
in the spec's Side effects before the write, not as a surprise after it.

**`purchase_sourced_item`.** Only for an Item whose sole evidence is a
purchase document (doc-extraction of a PO, purchase invoice or GRN). It
fills `is_stock_item = 1`, `is_purchase_item = 1`, `is_sales_item = 0`
where the payload is silent and refuses a bare `standard_rate`. Never use
it for a typed request.

**Price.** Do not put `standard_rate` on the create: ERPNext turns it into
a Standard **Selling** Item Price. Create a buying price as its own
confirmed `Item Price` write (`buying = 1`, `selling = 0`).

**Done when:** every checklist row that applies has a user-confirmed
value or an explicit "not needed", and the spec's Side effects name the
instance defaults.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Stock level query | Current stock known | Reconciliation: sum vs. stated total |
| Stock transfer | Goods moved in-system | Refuses "ready" if outgoing qty exceeds fresh balance |
| Stock reconciliation | System matches physical count | Refuses "ready" unless current_qty (and batch_no) came from ERPNext's own resolver |
| Reorder / Material Request | Low stock actioned | Single-confirm |
| Batch/serial trace | Trace a batch/serial | Running-balance-consistency checked |
| Item master create | Item usable on documents | `unscoped.generic`; accounts, SAC and flags asked first |

## Relationships

Feeds `domains/procurement.md` (reorder triggers) and
`domains/accounts.md` (stock valuation touches GL). Conceptually adjacent
to `domains/fixed-assets.md` (both track physical things) but a distinct
doctype universe — no shared write path.
