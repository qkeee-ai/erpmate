# Domain: sales (Customer, Quotation, Sales Order, Delivery Note)

Code: `scripts/domains/sales.py` (`ALLOWED_WRITE_DOCTYPES = ("Customer",
"Quotation", "Sales Order", "Delivery Note")`). Deliberately scoped to
ERPNext's Selling module, not a full CRM replacement.

This domain has no unique connector logic of its own — the domain logic
below belongs in `render_customer_draft.py`/`render_quotation_draft.py`/
`render_report.py`, which don't exist in this skill's scripts/ yet.

## When this domain applies

Customer onboarding, drafting a quotation, checking where a Sales Order
or Delivery Note stands, a lightweight sales pipeline view.

## Non-negotiables specific to this domain

- **A Quotation is drafted, never auto-submitted as a formal customer
  commitment, without explicit confirmation.** Creating one (`docstatus`
  0, `status: "Draft"`) is cheap and reversible; submitting it
  (`docstatus` 0→1, `status: "Draft"→"Open"`) is the point the business is
  on record having quoted a customer — always a separate, explicitly-
  confirmed step, no authority-override exception (unlike procurement's
  Purchase Order).
- **Customer onboarding requires a reachable primary contact, not just a
  name.** ERPNext's own hard-mandatory Customer fields are only
  `customer_name` + `customer_type`. This domain's bar is stricter:
  `customer_group`, `territory`, and at least one of `contact_email`/
  `contact_mobile` are required before a draft is marked ready.
  Incomplete extractions must be flagged, never silently filled with a
  placeholder.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype is confirmed inside the tuple above
   before any write is proposed.
2. **Customer onboarding's 3-step execute order must be followed exactly,
   never parallelized or reordered:**
   1. `mutate Customer create`.
   2. `mutate Contact create`, with `links[0].link_name` set to the
      Customer's **real** name from step 1 (naming-series suffixes or
      collisions can change it from the requested `customer_name`).
   3. `mutate Customer update` on the same Customer, setting
      `customer_primary_contact` to the Contact's real (autonamed) name
      from step 2 — **not optional**: `Customer.mobile_no`/`email_id`
      stay empty without it (creating the Contact and linking it via its
      own `links` table does NOT auto-populate this field).
   Present the full staged draft (all pending payloads) and get one
   explicit confirmation before starting step 1. After step 3, re-fetch
   the Customer via `core.client.get_resource()` (needed to check the
   Contact linkage — `query_resource` can't) and confirm
   `customer_group`/`territory`/`customer_primary_contact` resolve to real
   records and `mobile_no`/`email_id` are actually populated. Neither
   Customer nor Contact is submittable — this post-save re-fetch is the
   only checkpoint. **Done when:** all three steps ran in order and the
   post-save re-fetch confirms `mobile_no`/`email_id` are populated.
3. **Quotation drafting**: resolve `Item.is_sales_item` for every line's
   `item_code` first (query `Item` directly) — never assume "sales-
   enabled" by default; a non-sales-enabled item line fails live with a
   specific `ValidationError` if skipped. `party_name` (the customer link)
   is required by this domain even though ERPNext's own schema doesn't
   flag it — a Quotation created without it is silently accepted as a
   "quotation to nobody." Present, confirm, `mutate(..., "create")`
   (lands `docstatus 0`). **Save-draft-then-review-then-submit:** re-fetch
   via `get_resource()` (the line-item child table check needs it) and
   confirm every Link field resolves to a real record before ever
   offering to submit — submission is a second, distinctly-confirmed call,
   never bundled with create. **Done when:** every Link field on the
   re-fetched Quotation resolves to a real record, before submission is
   even offered.
4. **Sales Order status and Delivery Note tracking** use `query_resource`
   with explicit `fields` (`name`, `status`, `delivery_status`,
   `per_delivered`, `billing_status`, `per_billed`, `customer`) — no
   child-table data needed, far cheaper than a full-doc GET. Always report
   delivery fulfilment (`delivery_status`/`per_delivered`) and billing
   fulfilment (`billing_status`/`per_billed`) as two separate figures,
   never collapsed into one "status." When investigating a fulfilment
   mismatch specifically, also query `Delivery Note Item` (`parent`,
   `against_sales_order`, `so_detail`) — a missing `so_detail` is the
   live-confirmed likely cause of a `per_delivered` mismatch. **Done
   when:** delivery and billing fulfilment are reported as two separate
   figures, never collapsed into one.
5. **Sales pipeline-lite reporting**: query `Quotation` grouped/counted by
   `status` for the quotation-stage side (no dedicated built-in report
   confirmed for this lens — hand-aggregate, and pass the true total row
   count so the reconciliation check can catch a status-value or
   filter bug). For the Sales Order side, prefer the built-in **"Sales
   Order Analysis"** report over hand-reconstructing delay/pending-amount
   math. Never present a failed reconciliation without the specific
   issues explaining why. **Done when:** the reconciliation check ran and
   any failure names the specific issues, not just "doesn't reconcile."

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Customer onboarding | New, reachable customer | Refuses "ready" if group/territory/contact channel missing |
| Quotation drafting | Ready to send | Create-as-draft-only, always; submission is separate |
| Sales Order status/query | Fulfilment status, both axes | Delivery and billing reported separately |
| Delivery Note tracking | Delivery status + linkage health | Flags missing `so_detail` as a likely mismatch cause |
| Sales pipeline lite reporting | Quotation-stage counts + open SO exposure | Reconciled against total queried |

## Relationships

Conceptual counterpart to `domains/procurement.md` on the outbound side.
No current consumption of `domains/doc-extraction.md` (customer onboarding
here is conversation-driven, not document-driven) — a future capability
could route business-card/onboarding-form extraction through it the same
way procurement does for supplier KYC.
