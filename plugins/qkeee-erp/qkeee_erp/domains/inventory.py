#!/usr/bin/env python3
"""
qkeee-erp-associate — inventory domain (Stock, transfers, reconciliation).

Writes run as operation "inventory.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete are token-gated.

Unlike most other domain modules, this one carries genuine business logic
beyond the shared core connector: get_stock_reconciliation_items(),
bin_rows_to_actual_source_qty(), and get_bin_qty() below (see their
docstrings) exist to prevent a batch-tracked-item Stock Reconciliation
footgun. Both reads are gated and audit-logged like every other read
(write-path hardening W13) — pass `requested_by` and the usual context.

ALLOWED_WRITE_DOCTYPES: cross-check against references/domains/
inventory.md before expanding.
"""


from ..core import client as core_client
from ..core import operations

DOMAIN_NAME = "inventory"

ALLOWED_WRITE_DOCTYPES = (
    "Stock Entry",
    "Material Request",
    "Stock Reconciliation",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Operation "inventory.generic": create/update are ungated draft steps;
# submit/cancel/delete need a rendered confirmation token + the user's
# confirmation code, and `expected_modified` (the record must not have
# changed since it was confirmed). See core/operations.py.
operations.generic_operation(DOMAIN_NAME, example_args={"doctype": "Stock Entry", "action": "submit", "name": "MAT-STE-2026-00001"})
# Read context keywords accepted by the read helpers below.
_READ_CTX = ("session_id", "domain_code", "channel", "channel_metadata",
             "prompt_summary", "latest_prompt")


def get_stock_reconciliation_items(tag: str, warehouse: str, company: str,
                                    posting_date: str, item_code: str = None,
                                    posting_time: str = "23:59:59", *, requested_by: str,
                                    **read_ctx) -> dict:
    """Resolve authoritative current qty/valuation (and, for batch-tracked
    items, per-batch rows) for a warehouse/item via ERPNext's own
    get_items whitelisted method — NEVER guess or hand-supply current_qty
    for a Stock Reconciliation line.

    The Stock Reconciliation Item's `current_qty` field is not resolved
    server-side from a caller-supplied value at create time (it is reset
    to 0 in the create response regardless of what is passed in), and
    Bin.actual_qty is not authoritative for a batch-tracked item —
    get_items() is the only correct source, and for a batch-tracked item
    it returns one row PER EXISTING BATCH (not a single item-level
    total), each with its own batch_no + current_qty. A caller that
    reconciles a batch-tracked item without resolving per-batch rows
    first, and instead submits a single line with an unresolved/zero
    current_qty, risks ERPNext creating a brand-new batch for the delta
    instead of correcting existing batches, because the reconcile adds
    the unresolved delta to the existing balance rather than setting it.

    `item_code` is optional: omitting it returns every item with a
    nonzero/tracked balance in `warehouse`, not just one — useful for
    reconciling a whole warehouse's physical count in one resolver call
    instead of one per item.
    """
    payload = {
        "warehouse": warehouse,
        "posting_date": posting_date,
        "posting_time": posting_time,
        "company": company,
    }
    if item_code:
        payload["item_code"] = item_code
    # get_items computes and persists nothing — a read, gated as one.
    result = core_client.read_rpc(
        tag, "POST",
        "/api/method/erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_items",
        payload=payload, gate_doctype="Stock Reconciliation", requested_by=requested_by,
        **{k: v for k, v in read_ctx.items() if k in _READ_CTX},
    )
    rows = result.get("message", [])
    return {"item_code": item_code, "warehouse": warehouse, "rows": rows, "batch_tracked": any(r.get("batch_no") for r in rows)}


def bin_rows_to_actual_source_qty(bin_rows: list) -> dict:
    """Convert get_bin_qty()'s ``{"data": [{"item_code", "warehouse",
    "actual_qty"}, ...]}`` row shape into the ``{(item_code, warehouse):
    qty}`` mapping a stock-entry draft renderer expects as
    `actual_source_qty`.

    Passing get_bin_qty()'s raw ``data`` list straight into
    actual_source_qty fails every availability check with "no
    actual_source_qty entry provided" — not because stock is actually
    unavailable, but because the shapes don't match. Use this converter
    instead.
    """
    return {(row["item_code"], row["warehouse"]): row["actual_qty"] for row in bin_rows}


def get_bin_qty(tag: str, item_code: str, warehouse: str = None, *, requested_by: str,
                **read_ctx) -> dict:
    """Read the live Bin (actual on-hand qty) for an item, optionally
    scoped to one warehouse. Authoritative for non-batch/non-serial items;
    for batch-tracked items prefer get_stock_reconciliation_items() to
    also see the per-batch breakdown before staging a reconciliation.
    """
    filters = [["item_code", "=", item_code]]
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    result = core_client.query_resource(
        tag, "Bin", filters, ["item_code", "warehouse", "actual_qty"], limit=100,
        requested_by=requested_by, **{k: v for k, v in read_ctx.items() if k in _READ_CTX})
    return {"data": result.get("data", []), "has_more": result.get("has_more", False)}
