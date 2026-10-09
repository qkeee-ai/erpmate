#!/usr/bin/env python3
"""
qkeee-erp-associate — fixed-assets domain (Asset lifecycle: depreciation,
disposal, transfer).

Every write is a named operation in core/operations.py's pipeline:

| Operation                     | What it does (ERPNext v16, verified 2026-10-06)        |
|-------------------------------|---------------------------------------------------------|
| fixed_assets.generic          | Asset / Asset Movement / Asset Repair create/update (drafts); submit/cancel/delete token-gated and refused if the record changed since render |
| fixed_assets.depreciation_run | make_depreciation_entry(depr_schedule_name, date): posts a Journal Entry for every due, unbooked schedule row |
| fixed_assets.scrap            | scrap_asset(asset_name, scrap_date): depreciation up to the date + scrap JE |
| fixed_assets.restore          | restore_asset(asset_name): reverses disposal depreciation, CANCELS the scrap JE |
| fixed_assets.sell             | creates a DRAFT Sales Invoice for the asset (mapped by make_sales_invoice, which itself persists nothing) |

All four non-generic operations need a rendered token plus the user's
confirmation code (the domain's double-confirm). Their token covers the
exact RPC body and the facts shown to the user (book value, pending rows,
reason). The depreciation slice indices `sch_start_idx`/`sch_end_idx`
are never accepted (they force-post rows; W24); `scrap_date` is always
explicit (W25).

ALLOWED_WRITE_DOCTYPES: "Asset", "Asset Movement", "Asset Repair". The
named RPC operations are individually reviewed and skip the doctype
allowlist; fixed_assets.sell is checked against the accounts allowlist
(it creates a Sales Invoice).
"""

from ..core import client as core_client
from ..core import operations
from ..core.operations import PreparedRequest, require_args

DOMAIN_NAME = "fixed_assets"

ALLOWED_WRITE_DOCTYPES = (
    "Asset",
    "Asset Movement",
    "Asset Repair",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)
operations.generic_operation(DOMAIN_NAME, example_args={
    "doctype": "Asset", "action": "submit", "name": "ACC-ASS-2026-00001"})

_DEPRECIATION = "/api/method/erpnext.assets.doctype.asset.depreciation.make_depreciation_entry"
_SCRAP = "/api/method/erpnext.assets.doctype.asset.depreciation.scrap_asset"
_RESTORE = "/api/method/erpnext.assets.doctype.asset.depreciation.restore_asset"
_MAP_SALES_INVOICE = "/api/method/erpnext.assets.doctype.asset.asset.make_sales_invoice"

_READ_CTX = ("session_id", "domain_code", "channel", "channel_metadata",
             "prompt_summary", "latest_prompt")
def _get(ctx, doctype, name):
    return core_client.get_resource(ctx.tag, doctype, name, strip_noise=False,
                                    requested_by=ctx.requested_by, **ctx.audit_kwargs()).get("data") or {}


# ---------------------------------------------------------------- depreciation run

def _due_rows(schedule: dict, as_of: str) -> list:
    return [r for r in schedule.get("depreciation_schedule") or []
            if not r.get("journal_entry") and str(r.get("schedule_date") or "") <= as_of]


def _depr_render(args, ctx):
    import datetime
    args = dict(args)
    schedule = _get(ctx, "Asset Depreciation Schedule", args.get("depr_schedule_name"))
    as_of = args.get("date") or datetime.date.today().isoformat()
    due = _due_rows(schedule, as_of)
    args.setdefault("asset", schedule.get("asset"))
    args.setdefault("pending_rows", len(due))
    args.setdefault("total_depreciation", round(sum(float(r.get("depreciation_amount") or 0) for r in due), 2))
    return args


def _depr_prepare(args, ctx):
    require_args(args, ["depr_schedule_name", "asset"], ["date", "pending_rows", "total_depreciation"])
    body = {"depr_schedule_name": args["depr_schedule_name"]}
    if args.get("date"):
        body["date"] = args["date"]
    return PreparedRequest(
        transport="rpc", doctype="Asset", action="depreciation_run", rpc_path=_DEPRECIATION,
        body=body, rbac_doctype="Journal Entry", rbac_ptype="create", audit_action="Create",
        audit_doctype="Asset", audit_reference=args["asset"],
        bound={"asset": args["asset"], "pending_rows": args.get("pending_rows"),
               "total_depreciation": args.get("total_depreciation")})


def _depr_schedule_matches_asset(req, args, ctx):
    schedule = _get(ctx, "Asset Depreciation Schedule", args["depr_schedule_name"])
    if schedule.get("asset") != args["asset"]:
        raise core_client.PreconditionFailedError(
            f"Refusing depreciation run: schedule {args['depr_schedule_name']!r} belongs to asset "
            f"{schedule.get('asset')!r}, not {args['asset']!r}.")


def _depr_partial_outcome(exc, req, args, ctx):
    """make_depreciation_entry keeps posting rows after one fails, then
    re-raises (W26) — say which rows now carry a Journal Entry."""
    schedule = _get(ctx, "Asset Depreciation Schedule", args["depr_schedule_name"])
    posted = [f"{r.get('schedule_date')}: {r.get('journal_entry')}"
              for r in schedule.get("depreciation_schedule") or [] if r.get("journal_entry")]
    return ("depreciation may have PARTIALLY posted — rows now carrying a Journal Entry: "
            f"{posted or 'none'}. Review these before any retry.")


operations.register_operation(operations.Operation(
    key="fixed_assets.depreciation_run", domain=DOMAIN_NAME,
    summary="post every due, unbooked depreciation row on one Asset Depreciation Schedule",
    prepare=_depr_prepare, render_defaults=_depr_render, allowlist_domain=None,
    preconditions=(_depr_schedule_matches_asset,), on_failure=_depr_partial_outcome,
    example_args={"depr_schedule_name": "ACC-ADS-2026-00001", "date": "2026-10-31"},
    args_help={"depr_schedule_name": "Asset Depreciation Schedule name",
               "date": "post rows due on/before this date (default today)",
               "asset": "filled by render", "pending_rows": "filled by render",
               "total_depreciation": "filled by render"},
))


# ---------------------------------------------------------------- scrap / restore

def _book_value(asset_doc: dict):
    rows = asset_doc.get("finance_books") or []
    return rows[0].get("value_after_depreciation") if rows else None


def _scrap_render(args, ctx):
    args = dict(args)
    if args.get("asset") and "book_value" not in args:
        args["book_value"] = _book_value(_get(ctx, "Asset", args["asset"]))
    return args


def _scrap_prepare(args, ctx):
    require_args(args, ["asset", "scrap_date", "reason"], ["book_value"])
    return PreparedRequest(
        transport="rpc", doctype="Asset", action="scrap", rpc_path=_SCRAP,
        body={"asset_name": args["asset"], "scrap_date": args["scrap_date"]},
        rbac_ptype="write", audit_action="Update", audit_reference=args["asset"],
        bound={"book_value": args.get("book_value"), "reason": args["reason"]})


def _restore_prepare(args, ctx):
    require_args(args, ["asset", "reason"])
    return PreparedRequest(
        transport="rpc", doctype="Asset", action="restore", rpc_path=_RESTORE,
        body={"asset_name": args["asset"]}, rbac_ptype="write", audit_action="Update",
        audit_reference=args["asset"], bound={"reason": args["reason"]})


operations.register_operation(operations.Operation(
    key="fixed_assets.scrap", domain=DOMAIN_NAME,
    summary="scrap an asset: depreciation up to scrap_date, then the scrap Journal Entry",
    prepare=_scrap_prepare, render_defaults=_scrap_render, allowlist_domain=None,
    example_args={"asset": "ACC-ASS-2026-00001", "scrap_date": "2026-10-06",
                  "reason": "damaged beyond repair"},
    args_help={"asset": "Asset name", "scrap_date": "YYYY-MM-DD, required (never defaulted)",
               "reason": "stated reason", "book_value": "filled by render (finance_books[0])"},
))
operations.register_operation(operations.Operation(
    key="fixed_assets.restore", domain=DOMAIN_NAME,
    summary="restore a scrapped asset: reverses disposal depreciation and CANCELS the scrap JE",
    prepare=_restore_prepare, allowlist_domain=None,
    example_args={"asset": "ACC-ASS-2026-00001", "reason": "scrapped by mistake"},
    args_help={"asset": "Asset name", "reason": "stated reason"},
))


# ---------------------------------------------------------------- sale

# Server-generated keys stripped from a mapped (unsaved) document before it
# is sent as a create payload.
_SERVER_KEYS = {"name", "owner", "creation", "modified", "modified_by", "idx",
                "parent", "parentfield", "parenttype", "__islocal", "__unsaved", "docstatus"}


def _clean_mapped(doc):
    if isinstance(doc, dict):
        return {k: _clean_mapped(v) for k, v in doc.items() if k not in _SERVER_KEYS}
    if isinstance(doc, list):
        return [_clean_mapped(x) for x in doc]
    return doc


def map_sales_invoice(tag: str, asset: str, item_code: str, company: str, sell_qty,
                      serial_no: str = None, *, requested_by: str, **read_ctx) -> dict:
    """READ: ERPNext's make_sales_invoice mapper — returns the unsaved
    Sales Invoice it would create for this asset; persists nothing. Gated
    and logged like any read."""
    payload = {"asset": asset, "item_code": item_code, "company": company, "sell_qty": sell_qty}
    if serial_no:
        payload["serial_no"] = serial_no
    result = core_client.read_rpc(tag, "POST", _MAP_SALES_INVOICE, payload=payload,
                                  gate_doctype="Sales Invoice", requested_by=requested_by,
                                  log_doctype="Asset", log_name=asset,
                                  **{k: v for k, v in read_ctx.items() if k in _READ_CTX})
    return _clean_mapped(result.get("message") or {})


def _sell_render(args, ctx):
    args = dict(args)
    if "invoice" not in args:
        args["invoice"] = map_sales_invoice(
            ctx.tag, args.get("asset"), args.get("item_code"), args.get("company"),
            args.get("sell_qty"), args.get("serial_no"), requested_by=ctx.requested_by,
            **ctx.audit_kwargs())
    return args


def _sell_prepare(args, ctx):
    require_args(args, ["asset", "item_code", "company", "sell_qty", "customer", "invoice",
                        "reason"], ["serial_no", "sale_proceeds"])
    proceeds = args.get("sale_proceeds")
    if isinstance(proceeds, bool) or not isinstance(proceeds, (int, float)) or proceeds < 0:
        raise core_client.ConnectorError("sale_proceeds is required: the sale amount (>= 0).")
    invoice = dict(args["invoice"])
    if invoice.get("docstatus") not in (None, 0):
        raise core_client.PreconditionFailedError("fixed_assets.sell only creates a DRAFT invoice.")
    invoice.pop("docstatus", None)
    # make_sales_invoice leaves customer blank and the asset row at rate 0
    # (live, ERPNext v16): set both from what the user confirms, so the
    # draft carries the confirmed buyer and proceeds.
    invoice["customer"] = args["customer"]
    items = []
    for row in invoice.get("items") or []:
        row = dict(row)
        if row.get("asset") == args["asset"] and row.get("is_fixed_asset"):
            row["rate"] = round(args["sale_proceeds"] / float(row.get("qty") or args["sell_qty"]), 2)
        items.append(row)
    invoice["items"] = items
    return PreparedRequest(
        transport="resource", doctype="Sales Invoice", action="create", body=invoice,
        bound={"asset": args["asset"], "reason": args["reason"],
               "sale_proceeds": args.get("sale_proceeds")})


def _invoice_sells_this_asset(req, args, ctx):
    rows = (req.body or {}).get("items") or []
    if not any(r.get("asset") == args["asset"] and r.get("is_fixed_asset") for r in rows):
        raise core_client.PreconditionFailedError(
            f"Refusing fixed_assets.sell: the invoice has no fixed-asset item row for asset "
            f"{args['asset']!r}.")


operations.register_operation(operations.Operation(
    key="fixed_assets.sell", domain=DOMAIN_NAME,
    summary="create a DRAFT Sales Invoice disposing of an asset (submit it via accounts.generic)",
    prepare=_sell_prepare, render_defaults=_sell_render, allowlist_domain="accounts",
    preconditions=(_invoice_sells_this_asset,),
    example_args={"asset": "ACC-ASS-2026-00001", "item_code": "LAPTOP", "company": "DEMO LLP",
                  "sell_qty": 1, "customer": "Acme Traders", "reason": "sold to employee",
                  "sale_proceeds": 15000},
    args_help={"asset": "Asset name", "item_code": "the asset's item", "company": "company",
               "sell_qty": "quantity sold (ERPNext v16 requires it)", "serial_no": "optional",
               "customer": "buyer (Customer name) — the mapper leaves it blank",
               "reason": "stated reason",
               "sale_proceeds": "required: total sale amount, applied as the asset row's rate",
               "invoice": "filled by render from make_sales_invoice; edit rates, then re-render"},
))
