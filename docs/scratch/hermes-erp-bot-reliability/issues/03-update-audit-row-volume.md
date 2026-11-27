# Update writes now log 4 audit rows per call, not the documented 2 — undocumented, untested

Status: resolved (committed 9ec68e9, in erpnext-hermes `develop`; verified 2026-10-08)
Raised from: 2026-09-13 verification review of F11/F12/F13 (`.scratch/hermes-erp-bot-reliability/spec.md`)
Raised by: nikhil.sharma@qkeee.ai

## The gap

F13's fix (spec.md, "Comments (F13)") makes `mutate_resource()`'s Update pre-image fetch (`get_resource(..., requested_by=requested_by, ...)`, deliberately NOT `internal=True`) a real, gated, non-internal read — correctly so, since it's a legitimate requester-attributed access, not connector plumbing.

But that means an Update call now produces:

1. a gate-decision row for the pre-image read (`_validate_prod_requester(perm_type="read")` → `_log_gate_decision()`)
2. a real Read row for the pre-image fetch itself (`_log_read()`)
3. a gate-decision row for the write itself (`_validate_prod_requester(perm_type="write")`)
4. the Attempted→Success/Failure write row

That's 4 Qkeee Bot Audit Log rows for one Update. The doc's own "Comments (F13)" section states "A denial produces exactly one row... an allow produces exactly two" — true for a plain read or create, not true for Update, where an allow now produces four. Nothing in `core/test_client.py` exercises the full unmocked path to confirm/lock in this row count — `UpdatePreImageAttributionTests` mocks `_validate_prod_requester` away entirely, so it verifies the call shape (requested_by present, internal absent) but not the resulting row count.

## Why this matters

This isn't a bug — it's the correct, honest consequence of F13 fixing a previously-silently-broken feature (the pre-image read was refused on every call before, since it never passed `requested_by`). But it's a real, unflagged shift in audit-log volume/cost for every Update, on every domain, going forward, and the doc that would tell a future reader to expect it currently says the opposite. Worth a deliberate human decision — accept the volume, or special-case the pre-image read's gate-decision logging — rather than leaving it to be rediscovered live the way F13 itself was discovered.

## Open questions for whoever picks this up

- Is 4 rows/Update an acceptable cost, or does it warrant folding the pre-image read's gate-decision row into the write's own gate-decision row (one combined row covering both permission checks for one logical Update)?
- Should a test assert the row count directly (an integration-shaped test against a mocked HTTP layer that counts Qkeee Bot Audit Log inserts for one Update call), so a future change to this path can't silently change the count again without a test failing?
- Once a decision is made either way, update spec.md's "Comments (F13)" claim ("an allow produces exactly two [rows]") to explicitly carve out Update as a 4-row case.

## Related findings

- F13 (dead field_diff fix) — the change that introduced this.
- F11/F12 (gate-decision logging, purpose-keyed read exemption) — the logging mechanisms whose composition produces the 4-row count.

## Comments

**2026-09-15 (decided + implemented):** decision — accept the 4-row volume, don't fold the pre-image read's gate-decision row into the write's own. They answer genuinely different questions (does `requested_by` hold *read* on this doctype vs. *write*), and a requester can legitimately hold one without the other (uncommon, but real — DocPerm rows aren't required to be symmetric). Folding them would hide that distinction from a GRC reviewer for a marginal row-count saving; correctness/completeness of the audit trail wins over volume, consistent with every other best-effort-but-never-silently-wrong posture this connector already takes (F1, F11, F12). Answers the open questions above:

- **4 rows/Update accepted as-is** — no code change to the logging path itself.
- **Row count now locked in by a test**, closing the "untested" half of this ticket's own title: `core/test_client.py`'s new `UpdateAuditRowVolumeTests.test_allowed_update_writes_four_audit_rows` drives a real (only-HTTP-mocked, not `_validate_prod_requester()`-mocked) `mutate_resource()` Update call and asserts exactly 4 `_audit_insert()` calls, in order (`["Update", "Read", "Read", "Update"]` by `action`, gate-decision rows flagged via `"gate_check" in response_payload` at positions 0/1 only), and that exactly one row (the write's own) ever makes the `Attempted` → `Success` transition. A future change to this path that silently changes the count now fails a test instead of being rediscovered live.
- **spec.md's "Comments (F13)" claim corrected**, not left standing: it originally said "an allow produces exactly two [rows]" without qualifying Update as the 4-row exception — see that file's own update below.

Docs: `.scratch/hermes-erp-bot-reliability/spec.md`'s "Comments (F13)" section corrected to carve out Update explicitly.

Tests: `core/test_client.py` — new `UpdateAuditRowVolumeTests` (1 test, described above). `python -m pytest scripts` — 220 passed, 28 subtests, no regressions (up from 216, alongside issue 02's own new tests in the same pass).

Not committed — pending human review, same convention as F1-F13's own changesets.
