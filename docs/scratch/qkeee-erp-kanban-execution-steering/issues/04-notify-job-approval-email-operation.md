# notify.job_approval_request — the plan email itself

Status: ready-for-human (code done: erpnext-hermes@500287e; live send + ERPNext round trip open)
Blocked by: 03

## What this builds (ADR 0008)

A new `notify.*` operation, modeled directly on
`notify.summary_email`/`notify.share_records`
(`plugins/qkeee-erp/qkeee_erp/domains/notify.py`) — same render-then-send
shape, same `mailer.send_html()` call, same Communication-logging
pattern (`_require_bot_can_log`, `_sent_but_not_logged`). Call it
`notify.job_approval_request`.

- **Render**: given the job's batches (from issue 03's `prepare_job()`),
  build one HTML + plain-text email listing every batch's every step in
  plain words, each submit/cancel/delete step marked — i.e. the same
  rendered-request shape a chat batch render already shows, just all of
  it in one document instead of paginated per batch. A new
  `render_job_approval()` pure template function, same discipline as
  `render_summary()`/`render_share()`: every value escaped, links only to
  the instance.
- **Recipients**: `To` the Requester (`ctx.requested_by`), `Cc` the ERP
  admin. The admin address needs a config source — likely a new
  `QKEEE_ERP_<TAG>_ADMIN_EMAIL` setting (env, Operator-maintained, same
  place `qkeee-erp.env` already lives) rather than inferred from the
  Admin Account's ERPNext user record, to avoid a new live lookup on
  every render. Confirm this against `01-connectivity.md`'s env
  resolution before picking the setting name.
- **Send**: through the agent's own mailbox (`mailer.send_html`), same as
  the existing two `notify.*` ops. The job token and code (from issue 03)
  go in the body, not as a separate attachment.
- **Logging**: a Communication on... which primary record? Unlike
  `notify.summary_email`, a job approval request has no single ERPNext
  record to anchor the timeline entry to — it's a plan for records that
  don't exist yet. Needs its own anchor: logging it against the job
  approval store from issue 03 (if that store is an ERPNext doctype) is
  the natural fit; if issue 03 picks a non-ERPNext store, this operation
  needs a different logging anchor decided alongside it.

## Depends on

Issue 03's storage decision (the Communication-logging anchor question
above is downstream of it).

## Comments

2026-10-23: implemented as `notify.job_approval_request` (erpnext-hermes
`qkeee_erp/domains/notify.py`), same render-then-send shape as
`notify.summary_email`/`notify.share_records`. The operation's only
argument is `job_id` — render re-fetches the stored plan via a new
`operations.render_stored_job()`/`get_job_approval()` (never trusting an
agent's own batches/token), so the email can't show a plan that differs
from what `job_token()` actually bound. Recipients: `To` the Requester,
`Cc` the ERP admin (`mailer.send_html()` gained an optional `cc` param).
Admin address: `QKEEE_ERP_<TAG>_ADMIN_EMAIL`, read per-tag via
`get_env_config()` (new optional `admin_email` key), same file as the
existing ADMIN_API_KEY/SECRET — confirmed against `_tag_env_var()`'s
existing resolution, not a new scheme. Logging anchor: resolved by ADR
0009 — the Communication's `reference_doctype`/`reference_name` and the
audit row both point at the "Qkeee Bot Job Approval" row, not a business
record. A live precondition re-checks the row is still Pending at send
time (not just at render), mirroring `job_approval.mark_approved`'s own
check. Unit tests pass offline (`tests/test_notify.py`,
`tests/test_operations.py`); no live SMTP send or ERPNext round trip
against a real instance yet — same open item issue 03 left, now also
covering this operation.

2026-10-24: live sales-dataset test confirmed this operation is also
currently unreachable in practice — it needs a `job_id` from
`prepare_job()`, and nothing produces one from a live session (issue 07).
The operation itself is unaffected; it's waiting on 07's entry point.
