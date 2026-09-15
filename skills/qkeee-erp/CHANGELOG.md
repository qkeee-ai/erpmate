# qkeee-erp skills — changelog

Repo-side record only. Never loaded into agent context — nothing in
`qkeee-erp-associate/references/` should point here or at a finding
number; this file is where that provenance moved to (2026-09-13 writing
pass, batch 1 / C1). Findings F1–F13 were logged in a companion repo's
local issue tracker (`.scratch/hermes-erp-bot-reliability/spec.md` and
its `issues/01-schema-first-attribute-mapping.md`), not part of this
repo — read there for the full incident writeup, reproduction, and code
diff discussion behind each entry below. This file maps each finding to
the rule it produced and where that rule now lives in the shipped skill.

| Finding | What happened | Rule it produced | Lives in |
| --- | --- | --- | --- |
| F1 | Hand-written per-write scripts left `session_id` blank, never built `channel_metadata`, never passed `latest_prompt` — despite the real values being in context | `execute_write.py` is the one write entry point; WARNs on stderr before firing if session/channel/prompt context is missing | `00-conventions.md` GRC baseline; `01-connectivity.md` CLI usage; worked examples in `cli-cookbook.md` |
| F2 | Supplier KYC-completeness rule was treated as optional scope; GSTIN retry targeted the wrong doctype (Supplier instead of Address) | `procurement.py` refuses Supplier `create` without `kyc={...}` or an explicit waiver; tax ID documented as an Address field, never Supplier | `domains/procurement.md` non-negotiables |
| F3 | `Item` has no owning domain and no documented India-compliance field list; HSN code was embedded in free text instead of attempted as a field | Generalized into issue 01 (schema-first attribute mapping) rather than a narrow Item patch | `domains/procurement.md` (cross-reference); issue 01 in the companion repo |
| F4 | doc-extraction's mandatory staged report (confidence ratings + `reconciliation_check`) never ran — went straight from OCR to free text | Confidence/value-key refusal is code-enforced one step downstream (`schema_mapping.match_staged_report()`); a dedicated `render_*.py` staged-report script is still owed | `domains/doc-extraction.md` step 6 "Known gap" |
| F5 | The Item write's confirmation token was self-computed and self-verified in the same turn/process | `gated_mutate_resource()` requires `user_confirmation_text` — the literal text of the user's own reply — not just a matching token | `00-conventions.md` GRC baseline; worked example in `cli-cookbook.md` |
| F6 | Purchase cost auto-saved as this org's selling price (`standard_rate` bare on Item create defaults to Standard Selling) | `item_write_helpers.py` refuses a bare `standard_rate` on a purchase-sourced item create; points at the Standard Buying alternative | worked example comment in `cli-cookbook.md` |
| F7 | Every write proceeded on a WARN, not a real per-requester RBAC check, under a privileged bot identity where `has_permission` doesn't discriminate | `_requester_has_role_permission()` — a local, RPC-free role/DocPerm check — replaces the allowlist/token fallback when the RPC is known unreliable; `False`/`None` both refuse | `00-conventions.md` GRC baseline (RBAC pre-check) |
| F8 | Schema discovery (`discover.py`) didn't exist in the shipped skill; a 403 querying `DocField` directly was misdiagnosed as expected low-privilege access | `discover.py` resurrected and ported to the current connector API; `meta`/`resolve` go through `get_resource()`, never `query DocField` | `01-connectivity.md` "Discovering a doctype's live shape" |
| F9 | `is_sales_item` set to 1 with nothing in a purchase invoice supporting it | Same fix as F6 — `item_write_helpers.py` defaults `is_purchase_item=1, is_sales_item=0` for a purchase-sourced item unless told otherwise | worked example comment in `cli-cookbook.md` |
| F10 | Agent offered to re-attribute a permission-denied read to a different `requested_by` | Refusal messages and the GRC baseline explicitly forbid rerouting to a substitute requester; only "report the gap" or "decline" | `00-conventions.md` GRC baseline (requester identity) |
| F11 | A denied requester-permission check left zero trace in the audit log — the gate raised before the guarded read/write ever ran, and the gate itself never logged | `_log_gate_decision()` writes one row per `_validate_prod_requester()` call, denial or allow | `00-conventions.md` GRC baseline; `domains/grc-audit.md` |
| F12 | Business-intent reads of `User`/`Role` were silently dropped by a doctype-keyed audit exemption built for a different purpose (stopping internal plumbing calls from logging themselves) | Read-path exemption made purpose-keyed (`internal=True`), not doctype-keyed; a business-intent `User`/`Role` read is now a real logged row | `00-conventions.md` GRC baseline; `domains/grc-audit.md` |
| F13 | Update's `field_diff` had been silently dead since it was built — the pre-image fetch never passed `requested_by`, so it was refused every time and the refusal was swallowed | Pre-image `get_resource()` call now carries the write's own `requested_by` and full session/channel/prompt context | `core/client.py` `mutate_resource()` (code fix; no doc rule needed) |

## 2026-09-13 — writing pass, batch 1

Stripped all `F<n>` / `.scratch/hermes-erp-bot-reliability` references from
`qkeee-erp-associate/references/` (11 refs across `00-conventions.md`,
`01-connectivity.md`, `domains/doc-extraction.md`, `domains/grc-audit.md`,
`domains/procurement.md`) — see this file's table above for where each
finding's rule now lives. No rule's enforceable meaning changed; only the
case-number/path provenance moved here. Also added a **Done when:**
completion criterion to every numbered step across `SKILL.md`,
`03-spec-driven-execution.md`, and every `domains/*.md` procedure, and a
new `## Report-back` contract in `00-conventions.md`. See
`agents/.scratch/mattpocock-skills-adoption/plan.md` (companion repo) for
the full analysis this pass executes against.

## 2026-09-13 — writing pass, batch 2

Moved `SKILL.md`'s Governance and Status-note sections (operator/
maintainer material) to new `qkeee-erp-associate/references/governance.md`
— router 248 → 200 lines. Split `01-connectivity.md`'s worked CLI/
`execute_write.py` invocations into new `references/cli-cookbook.md`,
latched only once a call is about to run; `01-connectivity.md` keeps the
always-needed mechanics. Normalized domain-file section templates:
`mis.md`'s stray sixth heading folded into its Non-negotiables section,
`manufacturing.md`'s per-heading status parentheticals moved into each
section's one-line caveat and its missing Relationships section added.
10 of 11 domain files now share an identical five-heading skeleton
(`grc-audit.md` deliberately kept its own second heading — its content
isn't a behavioral-rule list, so the standard name would misdescribe
it). No rule's enforceable meaning changed.

## 2026-09-13 — writing pass, batch 3

- **Dedup (C8):** `00-conventions.md`'s GRC baseline had drifted into
  restating `governance.md`'s own two sections. Replaced both restated
  bullets with one-line pointers; moved the "doc claims vs. actual
  config" content (not previously in `governance.md`) there as its own
  section. `00-conventions.md` GRC baseline: ~237 → ~205 lines.
- **Positive-target rewrite (C4):** rewrote the two most negation-led GRC
  bullets — requester-identity resolution (F10) and the RBAC pre-check
  — to lead with the positive target, retaining exactly one prohibition
  each for the hard security guardrail underneath (never substitute a
  requester; never trust `has_permission` once known unreliable without
  the local fallback). Two similarly negation-led lines in
  `01-connectivity.md` (the `qkeee-erp.env` read-into-context ban, the
  `discover.py` intro) rewritten the same way. Every other GRC-baseline
  bullet was already positive-led with a necessary retained ban — left
  as-is rather than force a rewrite that wouldn't change meaning.
- **Folds:** `grilling`'s frontier/numbered-rounds clarify method into
  `03-spec-driven-execution.md` step 1 (C10). `diagnosing-bugs`' "red"
  signal + "a WARN in the same output outranks your prior" into
  `02-environment-assessment.md` step 4 — targets F8's exact
  misdiagnosis (C11). `wayfinder`'s "refer by name" + "the board is an
  index, not a store" into the Kanban section (C17).
- **Two new shipped skills (C13/C14):** `qkeee-erp-questionnaire` (turn
  a data gap only someone else can fill — supplier KYC, an HR field, a
  doc-extraction low-confidence field — into a questionnaire document to
  hand them; fills the gap F2 hit) and `qkeee-erp-handoff` (emit a fixed
  ERP continuity block — env tag, requester-identity rule, `session_id`
  reset, active spec path — before a hand-off or context compaction;
  F1's "compaction is a new logical session" rule had nothing carrying
  state across that boundary). Both cross-referenced from
  `qkeee-erp-associate/SKILL.md`'s intro and, for the questionnaire's
  output path, `00-conventions.md`'s naming table.
- **Category router (C15):** new `skills/qkeee-erp/DESCRIPTION.md`.
- **C18 (requires_tools gating) — substituted, not implemented as
  specified.** Hermes' conditional-activation fields
  (`requires_tools`/`requires_toolsets`/`session_platforms`) gate on
  tool/toolset/platform presence; neither new skill has a real
  dependency of that kind to gate on; a "child of qkeee-erp-associate"
  gate doesn't exist as a mechanism. Substituted narrowly-scoped,
  ERPNext-specific descriptions plus `related_skills` metadata as the
  practical equivalent — controls accidental firing the way the plan
  intended, without declaring a tool dependency that isn't real.

No rule's enforceable meaning changed anywhere in this pass. 216 tests,
28 subtests still pass throughout (docs/new-skills only — `scripts/`
untouched). Closes the mattpocock-skills-adoption plan's in-scope items
(C1–C11, C13–C15, C17, C18); C12 and C16 remain deliberately deferred
until F1–F13 is reviewed and committed.
