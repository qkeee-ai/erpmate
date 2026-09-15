# Domain: procurement (Supplier, Address, Contact, PO, RFQ)

Code: `scripts/domains/procurement.py`
(`ALLOWED_WRITE_DOCTYPES = ("Supplier", "Address", "Contact", "Purchase
Order", "Request for Quotation", "Supplier Quotation")`).

This domain has no unique connector logic of its own — the domain logic
below belongs in `render_supplier_draft.py`/`render_po_draft.py`/
`render_report.py`, which don't exist in this skill's scripts/ yet.

## When this domain applies

Onboarding a supplier, creating or checking a Purchase Order, comparing
quotations from an RFQ, reconciling a goods receipt against a PO,
checking a supplier's performance.

## Non-negotiables specific to this domain

- **Never create a live Supplier record with incomplete mandatory KYC/
  bank fields — code-enforced, not just this document.** This domain's
  KYC bar is stricter than ERPNext's own (confirmed live: ERPNext's
  hard-mandatory Supplier fields are only `supplier_name` +
  `supplier_type`) — the fuller bar (identity/classification, tax ID,
  bank/payable details) must be enforced before a draft is marked
  "ready." Incomplete extractions must be flagged, never silently filled
  with a placeholder. `procurement.mutate(..., "Supplier", "create")`
  refuses outright (`IncompleteSupplierKYCError`) unless the call carries
  either `kyc={"address": {...}}` (see "Supplier KYC write order" below)
  or an explicit `kyc_waiver_confirmed=True` — a live session let this
  slip once already by treating it as optional scope; this is the
  backstop for that, matching how the other non-negotiables in
  `00-conventions.md` are
  code-enforced rather than left to prompt discipline.
- **Tax ID lives on Address, never on Supplier — do not retry a
  `gstin`/`tax_id` field write against Supplier itself.** ERPNext's
  India-Compliance GSTIN field (and tax ID generally, per country) is a
  field on the **Address** doctype, linked back to Supplier via Frappe's
  standard Dynamic Link `links` child table — not a field on Supplier.
  Live-confirmed the wrong way once: a `gstin` write against
  Supplier was silently dropped (Frappe ignores unrecognized fields
  rather than rejecting them), and the session concluded — incorrectly —
  that GSTIN "would need a custom field on Supplier." It doesn't; it
  needs an Address record, created and linked the normal way. Confirm the
  live field name for this instance via `discover.py meta "Address"`
  (`gstin`, `tax_id`, `pan`, or an instance-specific custom field — never
  assumed) before building the `kyc.address` payload below.
- **Draft-only is the hard default for Purchase Order submission absent
  confirmed submission authority — not just "when unsure."** Where no
  Workflow is configured for Purchase Order, role membership (Purchase
  User vs. Purchase Manager/Purchase Master Manager) is the only
  API-visible signal, and it's a heuristic, not a guarantee. Only
  recommend "create-then-submit-on-confirm" when the caller explicitly
  passes confirmed submission authority; otherwise every PO is
  create-as-draft-only, full stop.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype is confirmed inside the tuple above
   before any write is proposed.
2. **Supplier onboarding — Supplier → Address → Contact, one `mutate()`
   call.** Present the drafted, KYC-complete record (Supplier fields plus
   the Address the tax ID/registered address will carry, and Contact if
   captured) and get explicit confirmation. Then a single
   `domains.procurement.mutate(tag, "Supplier", "create", payload={...
   supplier_name, supplier_type, supplier_group, country,
   default_currency, ...}, kyc={"address": {... address_line1, city,
   state, country, pincode, and whichever field this instance's live
   `discover.py meta "Address"` confirmed carries the tax ID — gstin,
   tax_id, pan, ... }, "contact": {... first_name, email_id, phone
   ...} (optional)}, ...)` call creates the Supplier, then the linked
   Address (and Contact, if given) — the Dynamic Link back to the new
   Supplier name is wired automatically, don't build it by hand. Only
   when the user has explicitly confirmed proceeding without KYC (they
   declined to provide it, a jurisdiction with no applicable tax ID),
   pass `kyc_waiver_confirmed=True` instead — say so plainly in the
   report-back, don't let a waived KYC read the same as a captured one.
   Neither given raises `IncompleteSupplierKYCError` before anything is
   written (see the non-negotiable above). Backfilling KYC onto an
   already-onboarded Supplier later is a plain `mutate(tag, "Address",
   "create", payload={..., "links": [{"link_doctype": "Supplier",
   "link_name": "<existing supplier name>"}]})` call — Address is a
   regular allowlisted doctype in this domain, not reachable only through
   the Supplier-create convenience path above.

   Re-fetch the Supplier via `query_resource` with explicit `fields`
   (none of the checked fields live in a child table) and confirm
   `supplier_group`, `country`, `default_currency`, and any bank/payable
   Link fields resolve to real records. Supplier isn't submittable — this
   post-save re-fetch is the only checkpoint. Re-fetch the Address the
   same way if KYC was captured, and confirm the tax-ID field actually
   persisted (don't assume — a field that doesn't exist on this
   instance's Address doctype is silently dropped the same way it is on
   Supplier). **Done when:** the re-fetched Supplier's Link fields
   resolve to real records, and (if KYC was captured) the tax-ID field is
   confirmed actually persisted on the re-fetched Address.
3. **Purchase Order drafting**: check the practical warehouse requirement
   for stock-tracked lines first (not visible in the DocType's `reqd`
   flags). Before treating submission authority as confirmed, check for a
   real Workflow on Purchase Order (`query_resource("Workflow",
   filters=[["document_type","=","Purchase Order"]])`); fall back to
   `core.client.get_user_roles()` only if none exists, and treat that as a
   heuristic the user should corroborate, not a determination made
   silently on their behalf — an empty roles list with a non-empty
   warning is ambiguous (no role vs. a failed lookup), not confirmed "no
   authority." Resolve `stock_items` explicitly (query `Item.is_stock_item`
   for every line) rather than relying on an "assume stock" default.
   **Save-draft-then-review-then-submit:** `create` always lands at
   `docstatus 0` regardless of confirmed authority; before ever
   submitting, re-fetch via `core.client.get_resource()` (the list
   endpoint silently drops the line-items child table) and review every
   Link field (`supplier`, each line's `item_code`, `warehouse`,
   `cost_center`). Never chain create straight into submit even when
   authority is confirmed. **Done when:** every Link field on the
   re-fetched PO resolves to a real record, before `submit`.
4. **RFQ/Supplier Quotation comparison and GRN matching** need a real
   quotation-coverage check (per supplier: was every invited item quoted,
   name exactly what's missing, state that before ranking on price) and a
   real GRN-match check (walk every PO line, return quantity and
   rejected-quantity discrepancies as separate issues on the same line
   where both apply) — don't hand-aggregate coverage or discrepancies
   inline without those two checks. `not_applicable` is only for reports
   with nothing to tie out (a bare PO status lookup) and needs a stated
   reason. **Done when:** both the coverage check and the GRN-match
   check have run, or `not_applicable` carries a stated reason.
5. **Supplier Scorecard queries are documentation-grounded, not
   universally live-tested** — say so on first real use against a new
   instance, and treat that first real query as the effective validation.
   **Done when:** the documentation-grounded caveat is stated on first
   real use against a new instance.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Supplier onboarding | KYC-complete Supplier + linked Address (+ Contact) | Code-refuses (`IncompleteSupplierKYCError`) without `kyc={"address": {...}}` or an explicit `kyc_waiver_confirmed=True` |
| PO creation | Purchase Order drafted/placed | Draft-only unless submission authority is confirmed |
| PO status query | Where a PO stands | `status`, `per_received`, `per_billed` |
| RFQ / Supplier Quotation comparison | Best-value supplier, coverage-checked | Flags incomplete coverage |
| GRN matching | Goods receipt reconciled to PO | Quantity + rejected-quantity flagged separately |
| Supplier scorecard/performance | Reliability visibility | Cites underlying counts, not just the score |

## Relationships

Consumes `domains/doc-extraction.md` for supplier KYC docs. Conceptual
counterpart to `domains/sales.md` on the inbound side. Feeds
`domains/accounts.md` for downstream Purchase Invoice matching.
