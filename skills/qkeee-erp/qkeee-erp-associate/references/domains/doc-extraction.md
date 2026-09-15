# Domain: doc-extraction (field extraction, no writes)

**Not a connector domain** — there is no `scripts/domains/doc_extraction.py`
and no `ALLOWED_WRITE_DOCTYPES` because this domain has no write path at
all, structurally, not by allowlist. Turns attached documents — including
scanned/photographed images — or a shared URL into structured fields
shaped for a target ERPNext doctype (Supplier, Purchase Invoice, Job
Applicant, Employee), then stops: it hands back a staged report, never a
written record.

**Scope note:** this domain's inputs are attached PDFs/DOCX/XLSX/images.
URL-based extraction is a materially different capability — fetching
arbitrary external web content — flagged here rather than treated as
equivalent to attached-file extraction.

## When this domain applies

A document is attached, or a link is shared, during HR/Procurement/
Accounts work (or any session), asking to pull structured fields out of a
PDF/resume/invoice/business card/scanned image/profile URL — ERP-related
or not.

## Non-negotiables specific to this domain

- **Never create or update an ERPNext record directly from extraction
  output.** Output always lands as a staged, human-reviewable report
  first — this holds regardless of `qkeee_erp.mode`. Not a self-imposed
  restraint on a capability this domain has and chooses not to use: this
  domain has **no ERPNext connector at all** — no `core.client` import,
  no `mutate_resource()` call, nothing that can reach ERPNext's write
  endpoints. Writing to ERPNext from here isn't refrained from, it's
  structurally impossible. This is the one domain in the library not
  gated by `qkeee_erp.mode` at all — its safety property is structural,
  not config-driven.
- **Low-confidence or not-found fields must be explicitly flagged, never
  silently guessed or filled with a placeholder.**
- **URL-based extraction never bypasses a login wall or paywall.** Fetch
  only what's publicly retrievable via the harness's normal web-fetch
  behavior. Never authenticate as the user, use stored credentials/
  cookies, or work around access controls — if a page comes back blocked,
  partial, or login-gated (LinkedIn profile pages commonly are, to
  non-logged-in fetchers), say so plainly and stage whatever was actually
  retrieved as low-confidence rather than padding it with assumptions.

## Procedure

1. **Discover harness file-reading and web-fetch tools before using
   bundled logic.** If the harness exposes a tool-search/tool-listing
   mechanism, use it to check for PDF/DOCX/XLSX/image-reading and a
   URL-fetch-capable tool, and prefer those over any bundled parsing. If
   the harness exposes no such mechanism at all, degrade gracefully:
   native multimodal reading covers PDF/DOCX/image directly in most
   Claude-based harnesses; a web-fetch tool covers URLs. **Done when:**
   the harness's own tools were checked first, before falling back to
   bundled logic.
2. **Scanned/photographed images go through the same multimodal reading
   path as clean files** — no separate OCR dependency. Treat image
   quality as a first-class confidence signal: blur, glare, skew,
   handwriting, or a cropped edge push affected fields to `medium`/`low`
   confidence. If a load-bearing field (tax ID, bank account number) is
   illegible, say so explicitly and suggest a clearer capture rather than
   guessing. **Done when:** every affected field's confidence reflects
   actual image quality, and any illegible load-bearing field is named
   explicitly.
3. **URLs are fetched, not assumed.** Extract from the returned content
   only. Any one of these signals is enough to mark every field pulled
   from a fetch `confidence: "low"` and say so in `notes` — don't wait for
   certainty, don't require all three: the fetched text is under ~500
   characters of actual content; it contains literal sign-in/paywall
   strings ("Sign in", "Join LinkedIn", "to see more", "Log in to view");
   or none of the expected section markers (name header, Experience,
   Education) are present at all. **Done when:** any one of the three
   signals present is checked and, if triggered, every field from that
   fetch is marked `confidence: "low"` with a `notes` reason.
4. **Identify the target doctype shape** — Supplier, Invoice/Bill (+ line
   items), or Job Applicant/Employee — from context. Treat any field-
   mapping reference as a starting point, not verified ground truth,
   unless confirmed against a live ERPNext instance. **Done when:** the
   target shape is named, and any field-mapping reference used is marked
   unconfirmed unless checked live.
5. **Extract, and rate every field's confidence** as `high`/`medium`/
   `low`. A field not in the document/page at all is `value: null` — not
   omitted, not guessed. A field genuinely blank in the source is
   `value: ""` — present-but-empty, a different signal from `null`. For a
   repeating line item, tag each field with a `row` identifier (e.g.
   `"items[0]"`) so it renders as one grouped line item instead of
   colliding into a flat table. **Done when:** every field carries both a
   `confidence` rating and a `value` key (`null` or `""`, never omitted).
6. **Render the staged report through a script, not reproduced inline**
   — the enforcement of the confidence/value-key requirements only holds
   if it's actually code-enforced. Refuse to render if any field is
   missing a confidence rating or a `value` key — don't work around that
   by inventing one. For an invoice's total-reconciliation self-check
   (line items + tax lines vs. `grand_total`), record the result as its
   own field (`field: "reconciliation_check"`) so it's visible in the
   report itself, not just something the agent is trusted to remember to
   mention.

   **Known gap:** no dedicated `render_*.py` staged-report script exists
   in this tree yet — this step has historically been done inline, which
   is exactly how the confidence-rating/reconciliation-check requirement
   documented here has gone unrun before. The confidence/value-key
   refusal rule IS code-enforced now, one step downstream: when this
   domain's output is handed to a write path via
   `execute_write.py --staged-fields`, `schema_mapping.match_staged_report()`
   refuses with `MalformedStagedReportError` on any field
   missing `confidence` or `value`, and additionally flags a field that's
   both `confidence: "low"` and unmatched against the live doctype schema
   as high-risk before any write can proceed. That does not replace a real
   render step for the report itself (still owed) — it means a downstream
   write can no longer silently consume a malformed staged report even
   though nothing currently code-enforces the report's own construction.
   **Done when:** an invoice's `reconciliation_check` field is populated,
   and no field is missing `confidence`/`value` in what's handed
   onward.
7. **Hand the staged report back** to the user, or to the calling domain
   if invoked mid-task (e.g. from `domains/procurement.md`'s supplier
   onboarding). The receiving domain is responsible for its own Confirm →
   Execute steps against ERPNext — this domain's job ends at the staged
   report. **Done when:** the staged report is handed back and no write
   has been attempted from inside this domain.
8. **Standalone use is fine.** Nothing here assumes an ERPNext context —
   pulling structured fields out of a PDF, scanned image, or shared URL
   for an unrelated purpose is a legitimate, unblocked use.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Supplier detail extraction | Onboarding fields + confidence, staged | Incl. scanned/photographed |
| Invoice + line-item extraction | Header + line items + confidence, staged | Self-checks totals as `reconciliation_check` |
| Resume/employee detail extraction | Candidate/employee fields + confidence, staged | From file, scan, or profile URL |
| Scanned/photographed image extraction | Same field sets, image-sourced | No OCR dependency, quality issues lower confidence |
| URL-based extraction | Structured fields from a shared link | Public/unauthenticated fetch only |

## Relationships

Consumed by `domains/hr-payroll.md` (resumes), `domains/procurement.md`
(supplier KYC docs), `domains/accounts.md` (vendor invoices, bank
statements). Each of those degrades to manual data entry if this domain
isn't reachable — no hard dependency either direction.
