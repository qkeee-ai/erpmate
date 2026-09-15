# Domain: accounts (AP/AR, Journal Entry, tax)

Code: `scripts/domains/accounts.py`
(`ALLOWED_WRITE_DOCTYPES = ("Journal Entry", "Payment Entry", "Purchase
Invoice", "Sales Invoice")` — see that module's docstring). Applies
`00-conventions.md` and `01-connectivity.md` in full; this file adds only
what's specific to AP/AR, JE drafting, 3-way match, and tax mechanics.

Every capability routes through `core.client` plus
`domains.accounts.mutate()` — this domain has no unique connector logic
of its own. The DRAFT-composition logic (JE balance enforcement,
cancel-impact wording) belongs in `render_je_draft.py`/
`render_cancel_confirmation.py`, which don't exist in this skill's
scripts/ yet — still prompt discipline. The submit/cancel gate itself is
code-enforced: `accounts.py` registers both actions with
`core.client.register_domain_token_gate()`, so `mutate_resource()`
refuses either without a fresh `confirmation_token` computed via
`scripts/core/confirm_token.py`'s `advisory-token` CLI over the exact
facts just confirmed with the user.

## When this domain applies

Invoice/PO payment status, journal entry drafting, AP/AR aging, 3-way
match (PO/GRN/Invoice), bank reconciliation assistance, expense claim
review, TDS/GST/e-invoicing/e-way-bill questions.

## Non-negotiables specific to this domain

- **Never submit or cancel a financial document without explicit user
  confirmation, even in `read-write` mode.** The library-wide mode gate
  is necessary but not sufficient — a Journal Entry draft must
  additionally clear this domain's own advisory-first step before
  Execute. Symmetric for both submit (must balance, arithmetic-checked)
  and cancel (must state what will actually change) — neither is a
  formality.
- **Tax outputs (TDS, GST, e-invoicing, e-way bill) always carry a
  disclaimer that they assist, not replace, verification against current
  regulation.** Regulation changes faster than this skill's knowledge;
  government portals are the ground-truth authority, never this skill's
  own memory.
- **TDS is core ERPNext (Tax Withholding Category), not
  India-Compliance-gated.** Only GST-specific mechanics (GSTIN
  validation, GSTR filing, e-invoicing, e-way bill) need the India
  Compliance app. Confirm installed apps (`Module Def` query, or the
  environment assessment's app inventory) before promising a GST-specific
  capability works on a given instance. GST/e-invoicing/e-way-bill remain
  **unverified end-to-end** absent a live India-Compliance-enabled
  instance — say so on first real use against a new instance.

## Procedure

1. Follow the activation sequence (`SKILL.md`) and this domain's
   `ALLOWED_WRITE_DOCTYPES` for any write.
2. For AR/AP aging, sales/purchase registers, or any other built-in
   report, prefer `core.client.run_query_report()` over hand-aggregating
   raw invoice/GL rows. Always check `has_more` before treating a `query`
   result as complete.
3. **Journal Entry drafting** is arithmetic-checked before it's ever
   shown — a draft that doesn't balance, or a line with both/neither
   debit and credit set, must be refused before rendering. Present the
   draft, get explicit confirmation, call `domains.accounts.mutate(...,
   "create")` (lands `docstatus 0`). Reading the created record's `name`
   back out of the `create` response uses the `"data"` key; a subsequent
   `submit`/`cancel` response uses `"message"` instead — this is exactly
   the step where reading the wrong key raises a `KeyError`.
4. **Save as draft → review the saved draft → submit — three distinct
   steps, never chained.** Re-fetch the JE by `name` via
   `core.client.get_resource()` (not `query_resource` — the list endpoint
   silently drops the JE's line-item child table even when named in
   `fields`) and check every persisted field: accounts balance as
   expected, amounts/narration match what was confirmed, and every Link
   field (`account`, `party`, `cost_center`, `against_account` where set)
   resolves to a real, existing record. Fix via `update` and re-review if
   anything is wrong. Only once the reviewed draft is correct, present it
   for a second explicit confirmation, compute a `confirmation_token` via
   `core/confirm_token.py`'s `advisory-token` CLI over the confirmed
   facts, and call `mutate(..., "submit", confirmation_token=..., issued_at=...)`
   — `mutate_resource()` refuses the submit without a matching, fresh
   token. **Cancelling an existing document** gets the same
   staged-confirmation treatment plus the same token requirement — state
   the impact, confirm, compute the token, then
   `mutate(..., "cancel", confirmation_token=..., issued_at=...)`; never
   cancel off a bare request with nothing staged first.
5. **3-way match walks PO → Receipt → Invoice in order**, reporting every
   discrepancy found, not just the first — ERPNext's `per_received`/
   `per_billed` fields make this checkable without re-deriving match state
   by hand.
6. **Operational reports** (aging, 3-way match, bank reconciliation) need
   a real reconciliation check first — bucket-sum vs party total for
   aging, for example. `reconciliation_checks="not_applicable"` exists
   only for reports with genuinely nothing to tie out, and must carry a
   reason.
7. **Expense claim review** needs the org's actual policy text, asked for
   explicitly if not already provided in-session — this domain has no
   built-in expense policy of its own.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Payment status check | Know if/how an invoice or PO is paid | Read-only |
| Journal Entry drafting | Draft ready for review, arithmetic-checked | Advisory-first, never auto-submitted |
| AP/AR aging summary | Outstanding exposure visible, bucket-checked | Reconciliation: bucket sum vs party total |
| Invoice/Bill vs PO/GRN 3-way match | Every discrepancy surfaced | `not_applicable` only if genuinely nothing to compare |
| Bank reconciliation assist | Statement lines matched, unmatched lines hypothesized | Each unmatched line needs a stated reason |
| Expense claim review | Claims checked against a stated policy point | Requires the org's actual policy text |
| TDS computation/query | Withholding liability visible | Core ERPNext, no add-on needed |
| GST return prep / e-invoicing / e-way bill † | Assist only, needs India Compliance app | † unverified end-to-end, confirm app installed first |

## Relationships

Consumes `domains/doc-extraction.md` for scanned vendor invoices/bank
statements. Overlaps conceptually with `domains/mis.md` (same GL,
different lens — this domain is transactional/operational, MIS is
management/analytical); MIS routes statutory questions back here, this
domain routes management-report requests to MIS.
