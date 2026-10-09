#!/usr/bin/env python3
"""
qkeee-erp-associate — procurement domain (Supplier, Address, Contact, PO, RFQ).

Writes run as operation "procurement.generic" (core/operations.py):
create/update land drafts; submit/cancel/delete are token-gated.
Cross-check ALLOWED_WRITE_DOCTYPES against references/domains/
procurement.md before expanding.

`Address`/`Contact` are included because ERPNext's India-Compliance
GSTIN field (and tax ID generally) lives on Address, linked to Supplier
via the standard Frappe Dynamic Link `links` child table — never
directly on Supplier itself.

## Supplier KYC (write-path hardening ticket 13, decision D2 = C + A)

A Supplier `create` must carry `kyc={"address": {...}, "contact": {...}}`
(contact optional) or `kyc_waiver_confirmed=True` (the user explicitly
confirmed proceeding without KYC). With KYC:

- The address must carry a tax ID — one of KYC_TAX_ID_FIELDS, or the
  field named by QKEEE_ERP_<TAG>_KYC_TAX_ID_FIELD — non-empty.
- Address/Contact are schema-mapped against THEIR live schema at prepare
  time, and checked for missing mandatory fields BEFORE the Supplier is
  created (pre-validation, "C").
- The Supplier is created, then the linked Address (and Contact). If a
  linked create still fails server-side, the just-created Supplier is
  deleted again ("A", compensating delete — audited) and
  KycLinkFailedError is raised. If that delete fails too,
  KycPartialFailureError names the Supplier left behind.
"""


from ..core import client as core_client
from ..core import operations

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

KYC_TAX_ID_FIELDS = ("gstin", "tax_id", "pan")

# Layout/table fields never carry a value a caller must supply.
_NON_VALUE_FIELDTYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Table",
                         "Table MultiSelect", "Button", "Heading", "Fold"}


class IncompleteSupplierKYCError(core_client.PreconditionFailedError):
    """A Supplier create with neither KYC (address + tax ID) nor an
    explicit waiver — refused before any write. This domain's
    non-negotiable: never create a live Supplier with incomplete KYC."""


class KycLinkFailedError(core_client.ConnectorError):
    """The linked Address/Contact create failed server-side; the Supplier
    created a moment earlier was deleted again (compensating delete).
    Nothing is left behind."""


class KycPartialFailureError(core_client.PartialOutcomeError):
    """The linked Address/Contact create failed AND the compensating
    Supplier delete failed too — the message names the Supplier (and any
    Address) left behind, for the user to fix or remove."""


def _tax_id_fields(tag: str) -> tuple:
    override = core_client._qkeee_env().get(core_client._tag_env_var(tag, "KYC_TAX_ID_FIELD"))
    return (override,) if override else KYC_TAX_ID_FIELDS


def _is_supplier_create(req) -> bool:
    return req.doctype == "Supplier" and req.action == "create"


def prepare(args: dict, ctx) -> "operations.PreparedRequest":
    args = dict(args)
    kyc = args.pop("kyc", None)
    waiver = bool(args.pop("kyc_waiver_confirmed", False))
    req = operations.prepare_generic(args, ctx)
    if (kyc or waiver) and not _is_supplier_create(req):
        raise core_client.ConnectorError(
            "kyc/kyc_waiver_confirmed only apply to a Supplier create — they'd be silently "
            "ignored otherwise.")
    if _is_supplier_create(req):
        req.bound["kyc"] = {k: dict(v) for k, v in (kyc or {}).items() if k in ("address", "contact") and v}
        req.bound["kyc_waiver_confirmed"] = waiver
    return req


def enrich(req, args, ctx) -> None:
    """Generic enrichment, plus the KYC Address/Contact mapped against
    THEIR own live schema (identically at render and execute)."""
    operations.enrich_generic(req, args, ctx)
    if _is_supplier_create(req):
        for key, sub_doctype in (("address", "Address"), ("contact", "Contact")):
            if req.bound["kyc"].get(key):
                req.bound["kyc"][key] = operations._schema_map(ctx, sub_doctype,
                                                               req.bound["kyc"][key], req.notes)


def check_kyc(req, args, ctx) -> None:
    if not _is_supplier_create(req):
        return
    kyc = req.bound.get("kyc") or {}
    waiver = req.bound.get("kyc_waiver_confirmed")
    if not kyc.get("address"):
        if waiver:
            return
        raise IncompleteSupplierKYCError(
            "Refusing to create Supplier without KYC: pass kyc={'address': {...}} (fields per "
            "discover.py meta \"Address\" for this instance, including the tax ID) or — only "
            "when the user has explicitly confirmed proceeding without it — "
            "kyc_waiver_confirmed=True. See references/domains/procurement.md.")
    from .. import schema_mapping

    def live_fields(sub_doctype):
        fields, _err = schema_mapping.get_doctype_schema(ctx.tag, sub_doctype,
                                                         requested_by=ctx.requested_by,
                                                         **ctx.audit_kwargs())
        return fields

    tax_fields = _tax_id_fields(ctx.tag)
    address_meta = live_fields("Address")
    address_has_tax_field = address_meta is None or any(
        f.get("fieldname") in tax_fields for f in address_meta)
    if address_has_tax_field:
        if not any(kyc["address"].get(f) for f in tax_fields) and not waiver:
            raise IncompleteSupplierKYCError(
                f"Refusing to create Supplier: the KYC address carries no tax ID (none of "
                f"{list(tax_fields)} is set). Capture it, or get the user's explicit waiver.")
    elif not (req.body or {}).get("tax_id") and not waiver:
        # Core ERPNext without India Compliance: Address has no tax-ID field
        # (a gstin/pan sent there is dropped by schema mapping), so the
        # Supplier's own core `tax_id` field carries it instead.
        raise IncompleteSupplierKYCError(
            f"Refusing to create Supplier: this instance's Address has no tax-ID field (none of "
            f"{list(tax_fields)}), so the tax ID goes on the Supplier's own `tax_id` field — "
            f"set payload.tax_id, or get the user's explicit waiver.")
    for key, sub_doctype in (("address", "Address"), ("contact", "Contact")):
        payload = kyc.get(key)
        if not payload:
            continue
        fields = address_meta if sub_doctype == "Address" else live_fields(sub_doctype)
        if fields is None:
            continue  # meta unavailable: the server-side check + rollback still apply
        missing = [f["fieldname"] for f in fields
                   if f.get("reqd") and f.get("fieldtype") not in _NON_VALUE_FIELDTYPES
                   and f.get("default") in (None, "") and f.get("fieldname") != "links"
                   and payload.get(f["fieldname"]) in (None, "")]
        if missing:
            raise IncompleteSupplierKYCError(
                f"Refusing to create Supplier: the KYC {sub_doctype} is missing mandatory "
                f"field(s) {missing} — nothing was created. Collect them first.")


def create_linked_kyc(result, req, args, ctx, hooks):
    if not _is_supplier_create(req) or not isinstance(result, dict):
        return result
    kyc = req.bound.get("kyc") or {}
    supplier = ((result.get("data") or {}).get("name"))
    kyc_result = {"waived": bool(req.bound.get("kyc_waiver_confirmed") and not kyc.get("address")),
                  "address": None, "contact": None}
    created = []
    for key, sub_doctype in (("address", "Address"), ("contact", "Contact")):
        payload = kyc.get(key)
        if not payload or not supplier:
            continue
        linked = dict(payload)
        linked["links"] = list(payload.get("links", [])) + [
            {"link_doctype": "Supplier", "link_name": supplier}]
        try:
            kyc_result[key] = hooks.run(f"{DOMAIN_NAME}.generic",
                                        {"doctype": sub_doctype, "action": "create", "payload": linked})
            created.append(f"{sub_doctype} {(kyc_result[key].get('data') or {}).get('name')!r}")
        except core_client.ConnectorError as e:
            try:
                hooks.compensate_create(f"KYC {sub_doctype} create failed: {e}")
            except core_client.ConnectorError as rollback_err:
                raise KycPartialFailureError(
                    f"Supplier {supplier!r} was created, but its KYC {sub_doctype} failed ({e}) "
                    f"and deleting the Supplier again also failed ({rollback_err}). Left behind: "
                    f"Supplier {supplier!r}" + (f", {', '.join(created)}" if created else "")
                    + ". Fix or remove it with the user before retrying.") from e
            raise KycLinkFailedError(
                f"KYC {sub_doctype} create failed ({e}); the Supplier {supplier!r} created a "
                f"moment earlier was deleted again, so nothing is left behind. Fix the "
                f"{sub_doctype} fields and retry the whole Supplier create.") from e
    result["_kyc"] = kyc_result
    return result


operations.generic_operation(
    DOMAIN_NAME, prepare=prepare, enrich=enrich, preconditions=(check_kyc,), post=create_linked_kyc,
    extra_args_help={
        "kyc": 'Supplier create: {"address": {...incl. tax ID}, "contact": {...}}',
        "kyc_waiver_confirmed": "Supplier create: true only when the user explicitly waived KYC",
    },
    example_args={"doctype": "Purchase Order", "action": "submit", "name": "PUR-ORD-2026-00001"},
)
