# Research: verify the fixed-asset RPC signatures and side effects on DEMO_ERP

Status: resolved
Type: research
Phase: P1
Blocked by: none
Blocks: 09
Closes: W18 (findings only)

## Why

`fixed_assets.call_whitelisted_method()` reads `body["asset_name"]` for both the audit reference and the Comment target. The review suspects that this key, and the assumed behaviour, are wrong for two of the four RPCs. Before 09 encodes the RPCs in the registry, confirm them against the real instance version.

## Questions

For each method under `/api/method/erpnext.assets.doctype.asset...`, find the exact parameters, what persists, and what it returns:

1. `depreciation.make_depreciation_entry`
   - Does it take `asset_depr_schedule_name` (+`date`) rather than `asset_name`?
   - Does it create and submit a Journal Entry?
2. `depreciation.scrap_asset(asset_name)`
   - Does it create and submit a JE, and set Asset status to Scrapped?
3. `depreciation.restore_asset(asset_name)`
   - Does it cancel the scrap JE (a GL reversal)?
   - What precondition errors does it raise?
4. `asset.make_sales_invoice(asset, item_code, company, serial_no?)`
   - Does it only **return an unsaved Sales Invoice doc** (a mapper), or does it insert one?
   - If it is a mapper, the disposal-by-sale flow needs a follow-up `Sales Invoice` create. That doctype is in the accounts domain, not fixed_assets, so 09 must design a cross-domain op.

## Method

- Read the source for the instance's ERPNext version, from GitHub at the matching tag. Get the version from `discover.py` or `/api/method/frappe.utils.change_log.get_versions`.
- Where cheap and safe, run on a throwaway Asset on DEMO_ERP. See the memory note on demo access. Never run on a real tenant.
- Record for each method:
  - the signature
  - the persisted side effects
  - the return shape
  - the key holding the Asset name

## Answer

Resolved 2026-10-06 from ERPNext **version-16** source (`erpnext/assets/doctype/asset/depreciation.py` and `asset.py` on the `version-16` branch). DEMO_ERP runs v16, per the 2026-09-01 memory note. Nothing was run on the live instance.

| Method | Signature (v16) | Persisted side effects | Returns | Asset key in body |
|---|---|---|---|---|
| `make_depreciation_entry` | `(depr_schedule_name, date=None, sch_start_idx=None, sch_end_idx=None, accounting_dimensions=None)` | One Journal Entry per schedule row. Each row is posted when it has no JE yet and `schedule_date <= date`. If **both** `sch_start_idx` and `sch_end_idx` are given, **every row in that slice is posted with no booked-or-due check**. Sets the Asset status and `depr_entry_posting_status`. | The Asset Depreciation Schedule doc | **None.** The body names the schedule, not the asset. |
| `scrap_asset` | `(asset_name, scrap_date=None)` | Sets `disposal_date`. Reschedules depreciation, posts a depreciation entry up to disposal, then creates and submits a scrap JE (unless a JE workflow exists). Refuses an unsubmitted, Sold, Scrapped, Cancelled or Capitalized asset, a future date, or a date before the last booked depreciation. | Nothing | `asset_name` |
| `restore_asset` | `(asset_name)` | Reverses the depreciation JE posted on disposal (when applicable). Resets the schedule. **Cancels the scrap JE.** Clears `disposal_date`. | Nothing | `asset_name` |
| `make_sales_invoice` | `(asset, item_code, company, sell_qty, serial_no=None)` | **Nothing.** It builds a Sales Invoice with `frappe.new_doc` and calls `set_missing_values()`, but never inserts it. | The unsaved Sales Invoice dict | `asset` |

Server-side, each method calls `frappe.has_permission` for the **calling session**, which is the bot: Journal Entry for depreciation, Asset write for scrap and restore. That checks the bot, not the requester, so the requester gate in 09 is still needed.

### Consequences for the current code and for ticket 09

1. **W18 confirmed.**
   - `make_depreciation_entry`: the body has no asset key, so the audit `reference_name` is blank and no Comment is posted. The current code's `asset_depr_schedule` fact name doesn't match the real parameter either. The real key is `depr_schedule_name`.
   - `make_sales_invoice`: since P0, the code reads `asset` as well as `asset_name`, so the reference now resolves.
2. **New finding W23: the sale path persists nothing.**
   - Today `call_whitelisted_method("make_sales_invoice")` is token-gated and audited as a successful disposal, and posts an "applied" Comment. No record is created.
   - Also, v16 requires `sell_qty`, which the docs never mention.
   - Ticket 09: make `fixed_assets.sell` a two-step op. First map with this RPC, which is a read and needs no token. Then create a **draft** Sales Invoice from the mapped doc, which is the token-gated write. The draft create counts as an accounts-domain write: let the op declare `Sales Invoice` and run the accounts allowlist and RBAC.
3. **New finding W24: the depreciation slice indices force posting.**
   - If the body carries both `sch_start_idx` and `sch_end_idx`, rows are posted whether or not they are already booked or due. That risks duplicate or premature depreciation.
   - Ticket 09: refuse both keys in the body, and allow only `depr_schedule_name` and `date`.
4. **New finding W25: the scrap date is not bound to the body.**
   - The token fact `disposal_date` is shown to the user, but the RPC uses `body.scrap_date` and defaults to today.
   - Ticket 09: require `body.scrap_date` and require it to equal the shown date. The fixed-assets docs should say so too.
5. **New finding W26: partial depreciation posting is reported as a plain Failure.**
   - `make_depreciation_entry` catches each row's error, keeps posting the other rows, then re-raises the last error. Some JEs may therefore have posted even though the audit row says Failure.
   - Ticket 09: on failure, re-read the schedule and record which rows have JEs, in `error_detail` and in the error raised to the agent.

## Comments
