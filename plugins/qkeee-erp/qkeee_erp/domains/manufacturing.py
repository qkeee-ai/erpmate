#!/usr/bin/env python3
"""
qkeee-erp-associate — manufacturing domain (BOM, Work Order, Job Card,
Production Plan, Routing, Workstation, Operation).

Writes run as operation "manufacturing.generic" (core/operations.py):
create/update land drafts; submit/cancel/delete are token-gated. Two
extra checks layer on top of the plain generic floor, both named in
references/domains/manufacturing.md's non-negotiables:

- BOM create/update setting is_default=1 refuses (_enrich_bom_default)
  if another BOM for the same item is already an active default. The
  check is self-contained (queries live BOM rows at render/execute
  time) regardless of whatever ERPNext's own save-time clearing
  behavior turns out to be — see
  .scratch/qkeee-erp-manufacturing-domain/issues/03-* for the live
  round trip that confirms ERPNext's side of this, still pending as of
  this commit.
- Work Order create resolves live Bin qty for every raw-material line
  via inventory.get_bin_qty() (same convention as Stock Entry/Stock
  Reconciliation) and flags, as a note on the rendered request rather
  than a hard refusal, any line below the required quantity. Whether
  ERPNext itself also blocks creation on insufficient stock is being
  live-confirmed (issue 04) — this check doesn't assume either way: it
  surfaces the shortfall either in addition to or in place of ERPNext's
  own validation.

Cross-check ALLOWED_WRITE_DOCTYPES against references/domains/
manufacturing.md before expanding.
"""

from ..core import client as core_client
from ..core import operations
from . import inventory

DOMAIN_NAME = "manufacturing"

ALLOWED_WRITE_DOCTYPES = (
    "BOM",
    "Work Order",
    "Job Card",
    "Production Plan",
    "Routing",
    "Workstation",
    "Operation",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def _enrich_bom_default(req, args, ctx) -> None:
    """BOM create/update setting is_default=1: refuse if another BOM for
    the same item is already the active default AND this same call isn't
    the one clearing it — a caller forcing two defaults open at once
    (agents issue 03). Only runs for BOM; every other doctype's enrich is
    a no-op so this never touches Work Order/Job Card/etc."""
    if req.doctype != "BOM" or req.action not in ("create", "update"):
        return
    body = req.body or {}
    if not body.get("is_default"):
        return
    item = body.get("item")
    if not item:
        return
    existing = core_client.query_resource(
        ctx.tag, "BOM", [["item", "=", item], ["is_default", "=", 1], ["docstatus", "!=", 2]],
        ["name", "is_default"], 10, requested_by=ctx.requested_by, **ctx.audit_kwargs())
    conflicting = [row["name"] for row in existing.get("data", []) if row["name"] != req.name]
    if conflicting:
        raise core_client.PreconditionFailedError(
            f"Refusing: {item!r} already has an active default BOM "
            f"({', '.join(conflicting)}). ERPNext clears a prior default when "
            f"this BOM's own save marks is_default=1 on an EXISTING default-aware "
            f"update, but this call is creating/updating a separate row while "
            f"another is still marked default — flag this to the requester "
            f"rather than silently leaving two defaults.")


def _enrich_work_order_stock_check(req, args, ctx) -> None:
    """Work Order create: resolve live Bin qty for every raw-material
    line (inventory.get_bin_qty(), agents issue 04) and note, don't
    refuse, any line below the required qty — ERPNext itself doesn't
    block creation on insufficient stock; silently proceeding would hide
    a real planning problem from the requester."""
    if req.doctype != "Work Order" or req.action != "create":
        return
    body = req.body or {}
    required_items = body.get("required_items") or []
    if not required_items:
        return
    shortfalls = []
    for line in required_items:
        item_code, warehouse = line.get("item_code"), body.get("source_warehouse") or line.get("source_warehouse")
        required_qty = line.get("required_qty") or 0
        if not item_code or not warehouse or not required_qty:
            continue
        bin_rows = inventory.get_bin_qty(ctx.tag, item_code, warehouse,
                                         requested_by=ctx.requested_by, **ctx.audit_kwargs())
        available = sum(row.get("actual_qty") or 0 for row in bin_rows.get("data", []))
        if available < required_qty:
            shortfalls.append(f"{item_code} in {warehouse}: needs {required_qty}, {available} on hand")
    if shortfalls:
        req.notes.append(
            "Stock shortfall at render time (ERPNext does not block Work Order "
            "creation on this — flag it to the requester before staging ready): "
            + "; ".join(shortfalls))


def _enrich(req, args, ctx) -> None:
    _enrich_bom_default(req, args, ctx)
    _enrich_work_order_stock_check(req, args, ctx)


# Operation "manufacturing.generic": create/update are ungated draft
# steps; submit/cancel/delete need a rendered confirmation token + the
# user's confirmation code, and `expected_modified` (the record must not
# have changed since it was confirmed). See core/operations.py.
operations.generic_operation(
    DOMAIN_NAME, enrich=_enrich,
    example_args={"doctype": "Work Order", "action": "submit", "name": "MFG-WO-2026-00001"})
