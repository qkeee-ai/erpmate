# RBAC fail-closed blocks the requester from verifying their own write-back

Status: ready-for-human — implemented, tested, committed in erpnext-hermes (18c5199, 827c88c); live check open
Type: task
Blocked by: none
Findings: F6
Decision: D4 (spec.md)

## Problem

`nikhil.sharma@qkeee.in holds no role with 'read' on 'Payment Entry Reference'` (L1170) — correct fail-closed behavior per the existing RBAC-reliability design, but it blocked the agent from verifying its own write back to the very requester who initiated the task. The agent fell back to invoice-level proof instead, which worked, but the pattern (requester can't read the record type their own request wrote to) will recur.

This is the same tension documented as F7 in `.scratch/hermes-erp-bot-reliability/spec.md`: should the bot verify a write using the requester's own permissions (safe, but blocks a legitimate requester with a narrow role) or using its own bot identity for verification-only reads (unblocks this, but needs its own gating so it isn't a backdoor)?

## Open questions (needs-triage, not pre-decided — this is a repeat pattern, worth a real decision not a one-off role grant)

- Is the fix "provision requester roles more broadly" (ops action per-instance, doesn't scale) or "let the bot verify its own write with its own read, independent of requester role" (design change, needs its own safeguard so verification-reads can't be abused as a general permission bypass)?
- If the latter: does this fold into F7's still-open "bot writes using its own login, gated on requester role" design in `hermes-erp-bot-reliability`, or is verification-only read a narrower, separately-scoped case?

## Change

**Decided 2026-11-19 (D4, nikhil.sharma@qkeee.ai):** the bot verifies its own write using its own
(bot-identity) read — narrowly scoped to an immediate verification-read right after the bot's own
write to that exact record, not a general permission bypass. Not a requester-role grant, not folded
into F7's broader "bot writes using its own login" design in `hermes-erp-bot-reliability` — this is
a narrower, separately-scoped case.

Implementation: in the write-path code that does draft-then-verify (`erp_execute_write` or
equivalent in `erpnext-hermes`), when the verification read targets a record the SAME write just
touched, issue that read under the bot's own credentials rather than gating it on the requester's
role. Needs a guard that this only fires for a read immediately following the bot's own write to
that record/doctype combination (by write result / record id), not a general "bot can read anything
for verification" escape hatch — otherwise this becomes exactly the backdoor the ticket warned
against.

## Acceptance

- A write to a doctype the requester cannot read (e.g. `Payment Entry Reference`) still completes
  with a successful verification step, using the bot's own read, not blocked by the requester's RBAC.
- A verification-read attempt for a record the bot did NOT just write is still gated on requester
  role as before (no backdoor).

## Comments

2026-11-19: User chose the "bundle into the write itself" implementation shape over two
alternatives (agent-requested execute-time flag; a short-lived "bot just wrote X" session-scoped
allowlist matching the incident's exact later-separate-call shape more closely but with its own new
abuse-surface). Trade-off accepted: this doesn't literally replay the original incident (that
verification read was a separate, later tool call), but closes the gap for any future write to the
same domain without creating any new agent-invokable bypass tool.

Implemented in `erpnext-hermes`: `core/operations.py` gets `_verify_own_write_read()` (bot-
credentialed GET, skips `_validate_prod_requester` entirely, mirrors the existing `_read_back_state`
technique already used for submit/cancel confirmation) and `PostHooks.verify_write()` (zero
arguments — always targets exactly the operation's own `req`/`created`, never caller-supplied).
`generic_operation()` gets a new `auto_verify_write` flag, flipped on only for `accounts.py` (Payment
Entry's own domain, matching the actual incident) — every other domain is unaffected by default.
A create/update attaches `result["_verified_record"]`; a failed verification read never fails the
write. Gets its OWN labelled Audit Log Read row (unlike the silent internal docstatus check this
mirrors), since it surfaces real data to the agent, not just a boolean.

10 new tests in `tests/test_operations.py::WriteVerificationTests`. Full plugin suite: 917 passed,
1588 subtests passed, 1 pre-existing unrelated failure. Committed `18c5199`.

**Security review (this is an RBAC-bypass-adjacent change), 2026-11-19:** traced end to end —
`op.post` only ever receives the real `req`/`ctx` from the write that just executed (`args` never
reaches `_verify_own_write_read`); for create, `created` comes from the server's own response, not
agent-supplied args; for update, `req.name` already passed the write's own RBAC gate before the
post hook runs. No path to read an arbitrary record under elevated privilege — the bot reading back
what the bot itself just wrote is not a privilege expansion. Audit trail confirmed NOT swallowed by
`AUDIT_EXEMPT_DOCTYPES`/`_LOG_READ_RECURSION_EXEMPT_DOCTYPES` (neither contains any accounts
doctype). No bugs found. Two doc-clarity nits fixed in `827c88c`: the comment undersold the blast
radius (it's every doctype in the accounts allowlist, not just Payment Entry), and
`_verify_own_write_read`'s docstring now states explicitly that it trusts its caller completely and
has no action-scoping of its own — a future caller wiring it into submit/cancel/delete must bring
its own guard.

Spec note: acceptance criterion 1 is satisfied only for the `accounts` domain, deliberately — other
domains with the same child-doctype-permission shape (hr_payroll, procurement, ...) are NOT covered
and would need their own `auto_verify_write=True` flip if this pattern recurs there. Matches D4's
"narrowly scoped" framing; not treated as a gap.

Live check still open: a real write to a doctype the requester can't read back, confirming
`result["_verified_record"]` actually appears and the agent can use it instead of falling back.
