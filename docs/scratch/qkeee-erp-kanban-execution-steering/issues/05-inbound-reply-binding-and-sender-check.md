# Binding the Requester's reply: code + sender-address match

Status: ready-for-human (code done: erpnext-hermes@500287e; live email-reply round trip open)
Blocked by: 03

## What this builds (ADR 0008's two-factor proof)

ADR 0008 requires BOTH: (1) the reply contains the job's code, (2) the
reply's resolved sender email equals the Requester's registered email.
Good news found while scoping this: check (2) needs no new plumbing.
`notify.py`'s own docstring already says a reply to an agent-sent email
"reaches the agent as an email turn" — the gateway already resolves and
authenticates the inbound sender for every channel, email included
(`00-conventions.md` GRC baseline, "Resolve requester identity from the
channel's own authenticated sender field"; `erp_discover(action="whoami")`
→ `resolved_sender_email`, same mechanism the activation sequence already
uses for chat). So check (2) is: on the reply turn, call
`erp_discover(action="whoami")`, compare `resolved_sender_email` to the
job's recorded `requested_by` (from issue 03's stored plan). No new
sender-verification code — just a comparison against an existing field.

What's actually new:

- **Recognizing that a given turn is a reply to a job-approval email**,
  as opposed to any other message. The agent (or a gateway-side hook)
  needs to detect "this inbound message is a reply in the thread of a
  pending job approval" — likely by matching an email thread/
  `In-Reply-To` header to the Message-ID the original
  `notify.job_approval_request` send returned (same `message_id` the
  existing `notify.*` ops already capture and log), or by the job code
  appearing in the body regardless of threading. Decide which before
  building — thread-matching is more robust but needs the Message-ID
  persisted (issue 03's store should already hold it, since it's
  produced by issue 04's send step); body-only matching is simpler but
  depends entirely on the user not stripping the code.
- **Binding**: once both checks pass, mark the job approved in issue 03's
  store (code consumed, sender matched, timestamp) so a later kanban
  child task's lookup (issue 06) finds it approved. Both checks failing,
  or either alone, must refuse and say which one failed — ADR 0008 is
  explicit that a code without a matching sender, or a matching sender
  without the code, is not an approval; the refusal message should say
  so plainly (not just "not approved"), same report-back discipline as
  everywhere else in this skill.

## Depends on

Issue 03 (needs the stored plan to compare against and to update).

## Comments

2026-10-23: implemented as `operations.verify_job_reply(job_id, reply_text,
ctx)` (erpnext-hermes `core/operations.py`), a plain function (not a
registered Operation — same "internal plumbing" posture as `prepare_job()`
itself), so it can only call the already-`cli=False` `job_approval.
mark_approved`, never give an agent a path to fabricate an approval.
Sender check confirmed as scoped: `core/client.py`'s `session_identity()`
(the function `erp_discover(action="whoami")` already calls) reports
`resolved_sender_email`; compared case-insensitively against the stored
row's `requested_by` — no new sender-verification code written. Both
checks read the one authoritative stored row (`operations.
get_job_approval()`, shared with issue 04) and also enforce ADR 0008's
fixed 24h window at reply time (not just issue 06's later per-task check).
Either check failing, or both, raises with a message naming exactly which
failed (test coverage for all three combinations).

Open design decision resolved: **body-only code+sender matching**, not
email-thread/Message-ID matching. The "Qkeee Bot Job Approval" doctype
(issue 03) stores no Message-ID field, and adding one now would be a
schema change to an already-shipped, not-yet-live-tested doctype — out of
scope here. Detecting *which* `job_id` a given inbound reply is about is
left to the caller/skill layer: the job id is plainly visible in the
approval email's subject line (`notify.job_approval_request`), so no
email-thread plumbing is needed to find it.

Unit tests pass offline (`tests/test_job_approval.py`): code+sender both
match, either alone, neither, already-approved, expired window, unknown
job id. No live email-reply round trip against a real mailbox/ERPNext
instance yet, and no gateway/skill wiring exists yet to route an inbound
reply turn into this function — same open item issue 03/04 left.

2026-10-24: the missing wiring flagged above is now its own issue (07) —
confirmed live: no job can reach the "awaiting reply" state in the first
place yet, since nothing calls `prepare_job()` from a live session.
