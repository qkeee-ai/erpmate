# Schema-first attribute mapping for every create/update

Status: resolved (implemented 7597704, in erpnext-hermes `develop`; verified 2026-10-08)
Type: design
Raised from: F3 (`.scratch/hermes-erp-bot-reliability/spec.md`), 2026-09-11
Raised by: nikhil.sharma@qkeee.in

## The ask

Stop curating per-doctype "here are the fields that matter" reference lists in domain docs (`procurement.md`'s KYC bar, the discussion of where `gst_hsn_code` should live for Item, etc.). Instead, change the write path itself so that for **every** doctype, not just Supplier/Item:

1. Fetch the live doctype schema first — now possible for real via the resurrected `discover.py meta <DocType>` (F8).
2. Match the fields present in the extracted/user-provided data against that live schema — every attribute that has a real field on the instance gets mapped to it, not just the ones a domain doc happened to enumerate.
3. Only then fire `create`/`update`.

Goal: no critical *or* optional-but-useful attribute (HSN code, tax ID, anything else) gets left behind because a domain reference doc's hand-curated field list didn't happen to mention it. This generalizes past F3 (Item specifically) to Supplier, and to every other doctype this skill ever writes.

## Why this is its own item, not folded into F3

Explicitly scoped separately per 2026-09-11 direction: needs its own design pass before implementation — this is a change to the shared write path's shape (likely `core/client.py` and/or a new pre-write mapping step used by every domain's `mutate()`), not a one-doctype fix. F3 (Item/HSN specifically) stays open and gets closed by whatever this design produces, rather than by a narrower per-domain-doc patch.

## Open questions for the design pass

- Where does the mapping step live — inside `mutate_resource()`/`gated_mutate_resource()` itself (every write schema-checks first, unconditionally), or a separate pre-flight the domain `mutate()` wrappers call before their existing `create`/`update`?
- `discover.py meta` needs System Manager–level read on DocType (confirmed live, F8) — what's the fallback when that 403s (a correctly least-privileged bot, per F7's own recommendation)? Degrade to the current curated-doc-list behavior? Refuse the write? Warn and proceed?
- Extracted-field-to-DocType-field matching: exact fieldname/label match only, or fuzzy matching against `label`/`fieldname`/known synonyms (e.g. "HSN" vs `gst_hsn_code`, "GSTIN" vs `gstin`)? Fuzzy matching risks false positives (mapping the wrong field) — needs an explicit confidence/confirmation story, not silent best-effort.
- Interaction with F4 (doc-extraction's staged report): does the schema match happen before or after the confidence-rated staged report? Likely after extraction, before the domain's create/update — needs to be sequenced explicitly.
- Interaction with F2's proposed code-enforced KYC gate: does the schema-mapping step subsume that gate (if Address/gstin exists in schema and in extracted data, it gets mapped and the KYC gate is automatically satisfied), or do both checks run independently?
- Doctypes the bot doesn't have discover.py-level read access to at all (F7's least-privilege bot) — same fallback question as above, instance-wide rather than per-doctype.
- Performance/cost: a schema fetch before every single write adds a round trip. Worth caching per (tag, doctype) within a session? `discover.py` doesn't currently cache.

## Scope update (2026-09-11): F4 folded in

F4 (doc-extraction's mandatory staged report — per-field confidence, a visible `reconciliation_check` — never running before the task spec was written) is folded into this item rather than fixed separately. Extraction-confidence output and schema-mapping are adjacent steps in the same pipeline (extract → confidence-rate → match against live schema → write); design them together, not as two independently-enforced gates that could drift out of sync with each other. Add to the open questions above: the design must define the concrete handoff shape between the staged report and the schema-mapping step (does schema-mapping require every field's confidence to already be rated? what happens to a `low`-confidence field that also doesn't match any live schema field?).

## Related findings this would touch if implemented

- F3 (Item/HSN) — closed by this, not by a narrower fix.
- F4 (doc-extraction staged report) — folded in, see above.
- F2 (Supplier KYC/GSTIN) — likely simplified or partially subsumed; F2's code-enforced gate may become a special case of this instead of a separate mechanism. Decide during design, not before.
- F9 (is_sales_item guessed) — a schema-first approach doesn't fix a *business-logic* default like this on its own (the field exists either way); kept as its own small fix, implemented 2026-09-11 (see spec.md F9) as `item_write_helpers.apply_purchase_sourced_item_defaults()`, invoked via `execute_write.py --purchase-sourced-item`. If Item ever gets a real domain module out of this design, that module's `mutate()` should call into the same function rather than re-deriving the logic.
- F8 (discover.py) — this is the direct consumer of that resurrection; F8's code is what makes this possible at all.

## Comments

**Decided course + implementation (2026-09-11):** Landed as a new
`schema_mapping.py` module, wired into `execute_write.py` (not into
`mutate_resource()`/`gated_mutate_resource()` themselves). Answers to
each open question above:

- **Where it lives:** `execute_write.py` calls `schema_mapping.
  map_payload_for_write()` unconditionally for every `--action create`/
  `update`, before dispatching to a domain's `mutate()` or to
  `gated_mutate_resource()`. NOT inside `core/client.py` — embedding it
  in `mutate_resource()` would touch every one of `core/test_client.py`'s
  existing tests (which build already-correct payloads and mock at the
  HTTP layer) for a check that changes nothing about a payload that was
  already correct, and doctype-field-shape judgment already sits at the
  call-site tier in this codebase (`item_write_helpers.py`'s own stated
  layering). `execute_write.py` is still the one write entry point (F1),
  so "runs on every create/update" holds for every real write. Known,
  accepted gap: a caller that mutates outside `execute_write.py` —
  `procurement.py`'s own `_create_linked_kyc_record()`, which calls
  `core_client.mutate_resource()` directly for the Address/Contact half
  of a Supplier-KYC create — does not get this check. Not fixed here;
  flagged for whoever picks this up next if it turns out to matter.
- **403 fallback:** degrade to the payload as given (today's behavior),
  WARN loudly on stderr, never refuse the write over a schema-fetch
  failure. A correctly least-privileged bot (F7) can legitimately lack
  System-Manager-level DocType read; refusing every write instance-wide
  over that would be a worse regression than an occasional unmapped
  field. `status: "unavailable"` on the result surfaces this the same
  way `_audit_log_status` already does for degraded audit logging.
- **Fuzzy matching:** exact and normalized (case/space/hyphen/underscore-
  insensitive) fieldname/label matches auto-apply — this is just today's
  working behavior, now confirmed rather than assumed. A small hardcoded
  synonym table (HSN, GSTIN, PAN, TIN) only ever *suggests* a mapping
  (`suggested_mappings`); it's applied only via an explicit
  `--confirmed-mappings` entry the caller got a yes on. Never silent
  best-effort.
- **F4 (staged report) sequencing:** schema-mapping consumes the staged
  report's own shape directly (`match_staged_report()`, `--staged-fields`
  on `execute_write.py`) — extraction/confidence-rating happens first,
  schema-matching runs on its output. A field missing `confidence`/
  `value` refuses outright (`MalformedStagedReportError`) — this is the
  first place doc-extraction.md step 6's confidence/value-key requirement
  is actually code-enforced, closing F4 for real rather than just
  re-documenting it. A field that's BOTH `confidence: "low"` AND
  unmatched against the live schema is `high_risk` — never mapped, never
  even offered as a suggestion, and `execute_write.py` hard-refuses the
  write outright on it. Everything else (matched-but-low-confidence,
  high-confidence-but-unmatched) proceeds on a warning.
- **F2/KYC interaction:** kept complementary, not merged. Procurement's
  `IncompleteSupplierKYCError` gate is a policy question (is KYC data
  present, or was a waiver confirmed); schema-mapping is a mechanical one
  (do the offered fields land on real schema fields). Both run,
  independently, unchanged relative to each other.
- **Caching:** in-process only, per (tag, doctype), mirroring
  `core/client.py`'s own `_BOT_IDENTITY_CACHE` pattern. No disk/cross-
  process cache — `execute_write.py` is a fresh process per CLI
  invocation, so a persistent cache would mostly help hypothetical
  future multi-write-per-process callers, not today's actual usage
  pattern; not worth the staleness/invalidation questions it would raise
  yet.

**Known adjacent gap, not fixed by this:** doc-extraction.md step 6 says
to "render the staged report through a script" — no `render_*.py` (or
any staged-report renderer) exists anywhere in this tree
(`find . -iname 'render_*'` is empty, checked live). `match_staged_report()`
gives a future renderer something real to hand off to; it doesn't create
the renderer itself. Flagged honestly rather than assumed away, same
spirit as F8's own writeup for the previously-missing `discover.py`.

Code: `scripts/schema_mapping.py` (new — `match_fields()`,
`match_staged_report()`, `apply_confirmed_mappings()`,
`get_doctype_schema()`/`map_payload_for_write()`). `execute_write.py`:
new `_apply_schema_mapping()`, wired in for every create/update; new
`--staged-fields`/`--confirmed-mappings` flags.

Docs: `00-conventions.md` Non-negotiable 4 now cross-references this as
the code-enforcement mechanism. `references/domains/doc-extraction.md`
step 6 notes the code-enforcement point and the still-missing renderer.

Tests: `test_schema_mapping.py` new (16 tests — exact/normalized/synonym
matching, confirmed-mapping fold-in, staged-report malformed-entry
refusal, the low-confidence+unmatched high-risk case, in-process schema
caching including a cached failure, `map_payload_for_write()`'s
unavailable/ok/high_risk statuses). `test_execute_write.py`: 8 existing
create/update-dispatching tests updated to mock
`schema_mapping.map_payload_for_write()` as a passthrough (none of them
were exercising this feature, and several tests use a nonexistent tag —
without the mock they'd otherwise depend on whatever `qkeee-erp.env`
happens to be configured on the machine running them, which is not
something a test should depend on); 6 new tests added for the dispatch
wiring itself (unavailable warns, suggested/unmatched warns, high_risk
refuses before dispatch, the action-guard on `--staged-fields`, flag
forwarding). `python -m pytest scripts` — 176 passed, 28 subtests, no
regressions. CLI sanity-checked against a bad tag: clean degrade-and-warn
for the schema-fetch failure, no traceback, existing token-staleness
error still surfaces cleanly after it.

Committed `7597704` on `develop`, pushed to `origin` — pending human review,
per this repo's existing review convention for F1-F13's own not-yet-reviewed
changesets.
