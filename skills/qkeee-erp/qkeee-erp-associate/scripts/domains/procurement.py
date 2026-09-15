#!/usr/bin/env python3
"""
qkeee-erp-associate — procurement domain (Supplier, Address, Contact, PO, RFQ).

ALLOWED_WRITE_DOCTYPES covers render_po_draft.py/render_supplier_draft.py's
target doctypes plus Request for Quotation/Supplier Quotation. Cross-check
against references/domains/procurement.md before expanding.

`Address`/`Contact` are included because ERPNext's India-Compliance
GSTIN field (and tax ID generally) lives on Address, linked to Supplier
via the standard Frappe Dynamic Link `links` child table — never
directly on Supplier itself. See mutate()'s KYC handling below and
procurement.md's write order.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "procurement"

ALLOWED_WRITE_DOCTYPES = (
    "Supplier",
    "Address",
    "Contact",
    "Purchase Order",
    "Request for Quotation",
    "Supplier Quotation",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Submit/cancel require a fresh confirmation_token from
# core/confirm_token.py's advisory-token CLI, verified in mutate_resource()
# — the code-level backstop.
core_client.register_domain_token_gate(DOMAIN_NAME, {"submit", "cancel"})

# kwargs forwarded from a Supplier 'create' call into its linked Address/
# Contact 'create' calls — write-context/audit fields only, never the
# Supplier's own payload/name/confirmation_token (Address/Contact have
# their own payload, and aren't domain-token-gated the way submit/cancel
# are).
_KYC_LINK_CONTEXT_KWARGS = (
    "mode", "requested_by", "session_id", "domain_code", "channel",
    "channel_metadata", "prompt_summary", "latest_prompt", "approval_note",
    "user_approved",
)


class IncompleteSupplierKYCError(core_client.ConnectorError):
    """Raised by mutate() when a Supplier 'create' call carries neither
    `kyc={'address': {...}}` nor `kyc_waiver_confirmed=True`.

    This domain's non-negotiable rule (references/domains/
    procurement.md): never create a live Supplier record with incomplete
    mandatory KYC/bank fields — a tax ID must be captured, or explicitly
    waived, before a Supplier record is created. This is the code-level
    backstop for that rule, matching how every other domain
    non-negotiable in 00-conventions.md is enforced."""


def _create_linked_kyc_record(tag: str, doctype: str, payload: dict, supplier_name: str,
                               **write_kwargs) -> dict:
    """Address/Contact link back to a Supplier via Frappe's standard
    Dynamic Link `links` child table, not a direct Link field on Supplier
    — see this module's docstring. Injects that link automatically so a
    caller's `kyc` payload only needs to carry the Address/Contact
    doctype's own fields (address_line1, gstin/tax_id/whichever field
    this instance actually uses for the tax ID, ...) — never a hardcoded
    field name here (Non-negotiable 4, 00-conventions.md: field shape
    comes from discover.py meta against the live instance, not assumed by
    this connector)."""
    linked_payload = dict(payload)
    linked_payload["links"] = list(payload.get("links", [])) + [
        {"link_doctype": "Supplier", "link_name": supplier_name}
    ]
    return core_client.mutate_resource(tag, doctype, "create", domain=DOMAIN_NAME,
                                        payload=linked_payload, **write_kwargs)


def mutate(tag: str, doctype: str, action: str, *, kyc: dict = None,
           kyc_waiver_confirmed: bool = False, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="procurement"), with one
    doctype-specific rule layered on top.

    Supplier 'create' additionally requires either:
      - `kyc={"address": {...fields per `discover.py meta "Address"` for
        this instance, including whichever field actually carries the
        tax ID here — gstin, tax_id, pan, ...}, "contact":
        {...} (optional)}` — creates the Supplier, then its linked
        Address (and Contact, if given) as one call, Dynamic Link wired
        automatically; or
      - `kyc_waiver_confirmed=True`, only when the user has explicitly
        confirmed proceeding without KYC (they declined to provide it, a
        jurisdiction with no applicable tax ID, etc.) — this is logged on
        the result (`result["_kyc"]["waived"]`) so it's visible on
        review, not silently indistinguishable from KYC actually having
        been captured.

    Neither given raises IncompleteSupplierKYCError before ANY write
    fires — no Supplier record, incomplete or otherwise, is created.

    Supplier 'update' is deliberately NOT gated this way: KYC
    completeness is an onboarding-time bar (this domain's non-negotiable
    talks about *creating* a live Supplier record), not a block on every
    later patch to an already-onboarded supplier. Backfilling KYC onto an
    existing Supplier is a plain `mutate(tag, "Address", "create", ...)`
    call — Address is a regular allowlisted doctype in this domain, not
    something only reachable through this Supplier-create path.
    """
    if doctype == "Supplier" and action == "create":
        if not (kyc and kyc.get("address")) and not kyc_waiver_confirmed:
            raise IncompleteSupplierKYCError(
                "Refusing to create Supplier without KYC: pass kyc={'address': {...}} "
                "(fields per discover.py meta \"Address\" for this instance "
                "— this domain's own non-negotiable requires a tax ID, not just a registered "
                "address) or, only when the user has explicitly confirmed proceeding without "
                "it, kyc_waiver_confirmed=True. See references/domains/procurement.md."
            )

    result = core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)

    if doctype == "Supplier" and action == "create" and isinstance(result, dict):
        data = result.get("data") if isinstance(result.get("data"), dict) else None
        supplier_name = (data or {}).get("name")
        kyc_result = {"waived": bool(kyc_waiver_confirmed and not kyc), "address": None, "contact": None}
        if kyc and supplier_name:
            link_kwargs = {k: v for k, v in kwargs.items() if k in _KYC_LINK_CONTEXT_KWARGS}
            if kyc.get("address"):
                kyc_result["address"] = _create_linked_kyc_record(
                    tag, "Address", kyc["address"], supplier_name, **link_kwargs)
            if kyc.get("contact"):
                kyc_result["contact"] = _create_linked_kyc_record(
                    tag, "Contact", kyc["contact"], supplier_name, **link_kwargs)
        result["_kyc"] = kyc_result

    return result
