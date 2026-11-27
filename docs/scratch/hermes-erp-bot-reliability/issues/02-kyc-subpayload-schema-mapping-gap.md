# Supplier-KYC sub-payload (Address/Contact) bypasses schema-first mapping entirely

Status: resolved (committed 9ec68e9, in erpnext-hermes `develop`; verified 2026-10-08)
Raised from: 2026-09-13 verification review of F1-F13 (`.scratch/hermes-erp-bot-reliability/spec.md`), cross-checking issue 01's "known accepted gap" note against actual code
Raised by: nikhil.sharma@qkeee.ai

## The gap

Issue 01 (schema-first attribute mapping) flags one known, accepted gap: `procurement.py`'s `_create_linked_kyc_record()` (F2's Supplier→Address→Contact KYC write) calls `core_client.mutate_resource()` directly instead of routing through `execute_write.py`, so it never gets `_apply_schema_mapping()`. Issue 01's writeup frames this as a wrong-entry-point problem — implying that if it *did* go through `execute_write.py`, it would be covered.

2026-09-13 review traced this further and found the gap is structurally deeper: even routed through `execute_write.py`, the `kyc` dict (the Address/Contact sub-payload) is forwarded as `domain_kwargs`, not as the top-level `--payload`. `execute_write.py`'s `_apply_schema_mapping()` only ever operates on the top-level payload. So schema-mapping was never wired for the KYC sub-payload's shape at all, regardless of entry point.

Practical consequence: a typo'd or misnamed field inside `kyc.address`/`kyc.contact` (e.g. a fieldname that doesn't exist on Address, or one that's a near-miss of the real one) gets no schema-match/unmatched-field warning — silently dropped by ERPNext on write, unfilterable/unreportable. This is exactly the class of bug F3 (Item/HSN silently landing in free text) was.

Not a bypass of audit-context or requester-attribution — `_create_linked_kyc_record()` correctly threads `session_id`/`channel_metadata`/`requested_by`/etc through to `mutate_resource()`, and gets full gate-decision + audit logging (F1/F11/F13's mechanisms live in `core/client.py`, not `execute_write.py`). This is specifically a schema-mapping coverage gap, nothing else.

## Why this matters

The KYC flow is the highest-blast-radius write path in this skill (the one place a single call fans out into a create→linked-record chain), and it's specifically the doctype (Address, carrying GSTIN/tax_id) F2 was written to protect. A schema-mapping gap here undermines F2's own guarantee more directly than it would on any other doctype.

## Open questions for whoever picks this up

- Should `_apply_schema_mapping()` run over `kyc.address`/`kyc.contact` as their own doctype-shaped sub-payloads (Address, Contact) before `_create_linked_kyc_record()` fires, mirroring how the top-level Supplier payload is treated?
- Does this need a second call into `schema_mapping.map_payload_for_write()` per sub-record inside `execute_write.py`'s `--kyc` handling, or does the mapping step move into `procurement.py`'s own `mutate()` so it covers both the top-level and linked writes in one place?
- Any other domain-module helper that builds a linked/child sub-payload outside the top-level `--payload` (worth a repo-wide grep for other `_create_linked_*` patterns) likely has the same gap — decide whether to scope this fix as "the KYC case" or "every linked-record helper" before implementing.

## Related findings

- Issue 01 (schema-first attribute mapping) — this is a gap in that design's actual coverage, not a new mechanism.
- F2 (Supplier KYC gate) — the write path this gap sits inside.
- F3 (Item/HSN) — the original bug this pattern (a field silently dropped because nothing schema-checked it) is a repeat of, one layer deeper.

## Comments

**2026-09-15 (implemented):** `execute_write.py`'s `_apply_schema_mapping()` now takes an optional `doctype` param (defaults to `args.doctype`, matching the original top-level behavior unchanged). New `_apply_kyc_schema_mapping()` calls it once for `kyc["address"]` (doctype `"Address"`) and once for `kyc["contact"]` (doctype `"Contact"`) when either is present, wired in right after the top-level `--payload` mapping, inside the same `create`/`update` block — so `kyc` arrives at `procurement.mutate()` already schema-mapped by the time `_create_linked_kyc_record()` fires. Answers to the open questions above:

- **Where it lives:** `execute_write.py`, not `procurement.py`'s `mutate()` — same call-site-tier placement issue 01 already established, applied consistently rather than reached into the domain module or the connector.
- **Second `map_payload_for_write()` call, not a merge:** Address and Contact are mapped as two separate calls against their own live schemas — never folded into the Supplier call, since they're different doctypes with different field shapes.
- **Scope: "the KYC case," not "every linked-record helper."** Repo-wide grep for `_create_linked_` confirmed `_create_linked_kyc_record()` is the only such pattern anywhere in `scripts/` — no other domain module builds a sub-payload this way today, so there's nothing else to generalize to yet. Revisit if/when a second one shows up.
- **No `--staged-fields`/`--confirmed-mappings` for the KYC sub-payload** — deliberately, per the design note added to `_apply_kyc_schema_mapping()`'s own docstring: no staged-report shape exists for KYC data, and confirming a fuzzy match through one shared top-level flag for two different doctypes risked a wrong-doctype mixup. Only exact/normalized matching applies; `unmatched`/`suggested_mappings` still warn on stderr.

Docs: `execute_write.py`'s own module docstring and `schema_mapping.py`'s "known, deliberate gap" bullet both updated to describe the fix instead of the gap.

Tests: `scripts/test_execute_write.py` — new `KycSchemaMappingDispatchTests` (3 tests: address+contact each mapped against their own doctype with an unmatched field dropped and warned on, `--kyc-waiver-confirmed`-only path never calls the KYC mapping at all, a contact-only `--kyc` never calls Address mapping). `python -m pytest scripts` — 220 passed, 28 subtests, no regressions (up from 216). CLI sanity-checked: `execute_write.py --help` and a plain import both still work.

Not committed — pending human review, same convention as F1-F13's own changesets.
