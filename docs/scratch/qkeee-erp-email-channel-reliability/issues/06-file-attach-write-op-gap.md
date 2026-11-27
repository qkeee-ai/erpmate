# No write op exists to attach a file to a record

Status: ready-for-human — implemented, tested, committed in erpnext-hermes (0f6fa41, bdd15ba); live check open
Type: task
Blocked by: none
Findings: F3
Decision: D5 (spec.md)

## Problem

User asked to attach the source PDF to the Purchase Invoice it was extracted from. The agent discovered (confirmed twice, L644-649) that the `File` doctype's upload fields are read-only via the current API surface — no write op covers attaching a file to a record. It burned 6 `list_ops` calls across scopes (accounts/unscoped/all-domain) hunting for an op that was never going to exist.

## Decided 2026-11-19 (D5, nikhil.sharma@qkeee.ai)

Build the write op (not documentation-only). Write shape, domain ownership, and gating TBD at
implementation time — see Comments for research findings once gathered.

## Change

Add a `File`-attach write op matching the existing write-op pattern (same confirmation-token/draft
discipline as other writes), in the domain scope research turns up as the right owner (likely
"unscoped"/general, pending confirmation). Also fix the discoverability gap that burned 6
`list_ops` calls: once the op exists, make sure it surfaces in whichever scope an agent would
naturally search when asked to attach a file, so this isn't rediscovered the hard way again.

## Acceptance

- `list_ops` in the relevant domain/scope surfaces the new File-attach op without a multi-scope hunt.
- The op attaches a file to an existing record (e.g. a Purchase Invoice) and the attachment is
  visible via the normal UI/API read path.
- The op goes through the same confirmation-token gating as other writes — no un-gated write path.

## Comments

2026-11-19: Implemented in `erpnext-hermes` as `files.attach`, a new bespoke operation in
`plugins/qkeee-erp/qkeee_erp/domains/attachments.py`, wrapping Frappe's `frappe.client.attach_file`
RPC (plain JSON body, base64 `filedata` — `core/client.py`'s transport has no multipart support,
and adding it for this one op would be materially bigger than the ticket asked for; the prior
`/implement` research confirmed this is the right RPC and that there's no existing multipart
client to reuse).

"files" is deliberately NOT added to `00-conventions.md`'s fixed 11-domain enum — it's a
non-business, bespoke-RPC pseudo-domain, the same shape as the existing `notify` module (confirmed
by reading `execute_write.py`'s own comment: "notify is not a business domain"). RBAC/audit are
gated on the TARGET record's own doctype (dynamic in `prepare()`, matching
`system_admin.permission_update`'s pattern), never on a synthetic "File" allowlist. Default
`POLICY_TOKEN_USER_CODE` confirmation (no override) — same discipline as every other write.
Documented in `tool-cookbook.md` next to the `notify.*` examples, satisfying
`test_execute_write.py`'s every-op-key-is-documented check and closing the discoverability half of
the ticket (acceptance criterion 1).

Added `TAMPER["files.attach"] = "filename"` to `test_operations.py`'s matrix; the full matrix
(render→execute, read-only refusal, missing-requester, token tamper/stale/missing-user-code) now
runs against this op automatically. Full `plugins/qkeee-erp` test suite: 907 passed, 1588 subtests
passed, 1 pre-existing unrelated failure (`ShippedValuesTests::test_match_the_shipped_config` —
confirmed identical on the pre-change base; a `google_chat` toolset list mismatch, nothing to do
with this change).

Committed `0f6fa41` on `develop` (not pushed).

**Honest gap on acceptance criterion 2** ("attaches a file... visible via the normal read path"):
the test matrix mocks `_request`, so it proves the op's shape goes through the pipeline correctly
(RBAC, audit, token gating) but does NOT prove the real Frappe RPC call succeeds against a live
instance — `frappe.client.attach_file`'s exact param names/behavior need verification against the
target ERPNext version before this is trusted live. Flagged as a live check, not silently assumed
proven.

Live checks still open: (1) verify `frappe.client.attach_file` is still whitelisted on the target
ERPNext version; (2) a real render→execute→read-back round trip attaching a real file to a real
record.

**`/code-review` follow-up, 2026-11-19:** a review pass caught a real correctness bug in commit
`0f6fa41` — the RPC body used `dt`/`dn`/`fname`/`fcontent` (Frappe's internal File-document field
names), not `frappe.client.attach_file`'s actual RPC parameters. Confirmed against the real
function source (`frappe/frappe` `develop` branch `client.py`, fetched live): the real signature is
`attach_file(filename, filedata, doctype, docname, folder, decode_base64, is_private, docfield)`.
Fixed in `bdd15ba`. The mocked test matrix passed both before and after this fix — it proves the
pipeline (token/RBAC/audit) wiring, never the wire-shape against a real endpoint, which is exactly
why live check (2) above matters and isn't redundant with the test suite being green.
