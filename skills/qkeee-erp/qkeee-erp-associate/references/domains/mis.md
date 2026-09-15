# Domain: mis (GL / MIS reporting, read-only)

Code lives in `scripts/domains/mis.py`
(`ALLOWED_WRITE_DOCTYPES = ()` — deliberately empty, see the
non-negotiable below).

## When this domain applies

A financial or management report, drilling into what's behind a GL
figure, a variance/budget-vs-actual explanation, or an ad hoc reporting
question over ERPNext's accounts data.

## Non-negotiables specific to this domain

- **Read-only, always — enforced in code, not by omission.**
  `core.client.mutate_resource()` is one shared function used by every
  domain, so this domain's read-only guarantee is a **runtime
  write-allowlist gate**, not a missing write path: `domains/mis.py`
  registers an empty `ALLOWED_WRITE_DOCTYPES` tuple, so
  `core.client.mutate_resource(..., domain="mis")` refuses every doctype,
  unconditionally, via `DoctypeNotAllowedError`. Treat any proposal to
  add a doctype to this domain's allowlist as a decision that
  contradicts this domain's entire purpose, not a routine capability
  expansion. `domains.mis.mutate()` exists only for interface symmetry
  with every other domain module; calling it always fails.
- **Numbers must tie out before they're presented.** Every report
  self-checks a reconciliation (debits vs credits, assets vs
  liabilities+equity, segment-sum vs company-total, drill-down-sum vs
  parent figure) and states the result plainly — including when it
  *doesn't* tie out. A mismatch is never hidden or guessed past; it
  renders as a prominent anomaly, reconciliation-obsessed rather than
  reconciliation-decorative.
- **Route statutory/compliance questions to `domains/accounts.md`.**
  GST/TDS/e-invoicing mechanics are that domain's territory, not this
  one's analytical lens — if a report surfaces a statutory question,
  point the user there.

## Procedure

1. Follow the activation sequence. This domain has no `ALLOWED_WRITE_DOCTYPES`
   to check against — there is nothing to write, full stop. **Done
   when:** no `mutate`/`create`/`update`/`submit` call has been attempted
   from inside this domain.
2. For any of ERPNext's standard reports (General Ledger, Trial Balance,
   P&L, Balance Sheet, Cash Flow Statement, AR/AP, Budget Variance,
   Financial Ratios), prefer `core.client.run_query_report()` over
   hand-aggregating raw `GL Entry` rows — it runs ERPNext's own tested
   report logic, handling the Finance Book filter and multi-currency
   conversion correctly. Fall back to `query_resource("GL Entry", ...)`
   only for a genuinely custom cut no built-in report covers. Always
   check `has_more` before treating a result as complete — a truncated
   pull is the easiest way to produce a report that looks right but
   doesn't reconcile. **Done when:** a built-in report was checked for
   first, and `has_more` is confirmed false or the truncation is stated.
3. **Before declaring a reconciliation mismatch, rule out a scope
   mismatch first.** Confirm both figures being compared used the same
   Finance Book filter and the same currency basis — comparing across
   either is a known false-anomaly source, not a real discrepancy. **Done
   when:** both filters are confirmed identical before a mismatch is
   reported as real.
4. Every report needs at least one well-formed reconciliation check, or
   an explicit `not_applicable` with a one-line stated reason — never
   presented without one. **Done when:** a reconciliation check ran, or
   `not_applicable` carries a stated reason, before the report is
   presented.

## Quick reference

| Capability | Outcome | Reconciliation |
| --- | --- | --- |
| Trial balance / P&L / Balance Sheet | Standard statement | Debit/credit or assets vs liab+equity |
| GL drill-down | Transaction-level detail behind a figure | Entries sum to the figure |
| Cost-center/dimension reporting | Segment-level view | Segment-sum vs company total, unallocated bucket surfaced |
| Variance analysis | Budget vs actual / period-over-period | Named or flagged-unexplained deltas |
| Custom report/query | Ad hoc reporting need met | Still self-checked, or `not_applicable` with reason |
| Cash flow statement | Opening reconciled to closing cash | Opening + net change = closing |

## Relationships

Overlaps conceptually with `domains/accounts.md`'s reporting (same GL,
different lens: this domain is management/analytical, accounts is
transactional/operational). No direct hand-off mechanism — the user
carries context between the two, as they would between ERPNext modules.
