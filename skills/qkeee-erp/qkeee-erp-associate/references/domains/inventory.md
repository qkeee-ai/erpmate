# Domain: inventory (Stock, transfers, reconciliation)

Code: `scripts/domains/inventory.py`
(`ALLOWED_WRITE_DOCTYPES = ("Stock Entry", "Material Request", "Stock
Reconciliation")`), which also carries this domain's genuine connector
logic: `get_bin_qty()`, `get_stock_reconciliation_items()`,
`bin_rows_to_actual_source_qty()`.

## When this domain applies

Checking stock levels, transferring/moving stock between warehouses,
reconciling a physical count against system stock, triggering a reorder/
purchase requisition, tracing a batch/serial number's history.

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

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
2. **Stock level queries**: fetch `Bin` rows via
   `domains.inventory.get_bin_qty()` for every warehouse in scope, sum
   them, and state the reconciliation (sum across queried warehouses vs.
   stated total) explicitly. Stock level is always item + warehouse; a
   "total" figure only means an explicit sum across a named warehouse set.
3. **Stock transfer** (Issue/Receipt/Transfer/etc.): fetch a fresh
   `get_bin_qty()` balance for every `(item_code, s_warehouse)` pair among
   the drafted lines, convert with `bin_rows_to_actual_source_qty()`, and
   use it as the freshness check above requires. Present, confirm, then
   `domains.inventory.mutate(..., "create")` (lands `docstatus 0`).
   **Save-draft-then-review-then-submit:** re-fetch via
   `core.client.get_resource()` (the list endpoint silently drops the
   line-items child table) and review every line — quantities and every
   Link field (`item_code`, `s_warehouse`, `t_warehouse`) resolve to real
   records and match what was confirmed — before `submit` as its own
   step.
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
   an unresolved line, given the batch-inflation risk above.
5. **Reorder / Material Request triggers**: single-confirm, not double —
   a Material Request is a request, not a commitment; the buy/make
   decision belongs downstream. Recommend (don't require) a target
   `warehouse` for a Purchase-type request. A drafted, unsubmitted
   Material Request is a complete, valid outcome on its own.
6. **Batch/serial trace queries**: query `Stock Ledger Entry` filtered to
   the item/batch/serial, chronological order, feed the rows straight
   into the running-balance check — don't hand-reconstruct it. If the
   running balance comes back inconsistent, say so explicitly before
   presenting the trace as complete; usually a query-limit truncation
   (check `has_more`) or a genuine gap worth investigating, not something
   to paper over.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Stock level query | Current stock known | Reconciliation: sum vs. stated total |
| Stock transfer | Goods moved in-system | Refuses "ready" if outgoing qty exceeds fresh balance |
| Stock reconciliation | System matches physical count | Refuses "ready" unless current_qty (and batch_no) came from ERPNext's own resolver |
| Reorder / Material Request | Low stock actioned | Single-confirm |
| Batch/serial trace | Trace a batch/serial | Running-balance-consistency checked |

## Relationships

Feeds `domains/procurement.md` (reorder triggers) and
`domains/accounts.md` (stock valuation touches GL). Conceptually adjacent
to `domains/fixed-assets.md` (both track physical things) but a distinct
doctype universe — no shared write path.
