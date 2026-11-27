# Task summary email to the requester after the user confirms completion

Status: ready-for-human (code and docs done 2026-10-14; live check open)
Type: task
Blocked by: none
Raised by: nikhil.sharma@qkeee.ai, 2026-10-14

## Goal

At the end of a task (e.g. "Item created", or "Supplier, Item and Purchase Invoice created"), the agent asks whether the task is complete. After the user's own "yes", it sends the requester one well-formatted HTML email that summarizes the task.

## Decisions (user, 2026-10-14)

| # | Question | Decision |
|---|---|---|
| E1 | Channel | Hermes sends the email with the profile's email skill: `google-workspace` (`gmail send --html`), else `himalaya`. (The gateway's email platform adapter is plain-text only, so not that one.) |
| E2 | Gate | The user's completion reply only. No confirmation code. |
| E3 | HTML | A plugin template. The agent passes structured data and never writes HTML. |
| E4 | Linking | The email is logged as a Sent `Communication` on the primary record's timeline. |

## Design

New operation `notify.summary_email` (`plugins/qkeee-erp/qkeee_erp/domains/notify.py`). Its domain `notify` has `ALLOWED_WRITE_DOCTYPES = ("Communication",)`.

- **render:**
  - Re-reads every record with the requester's gated read: status (Draft / Submitted / Cancelled / Saved (live)), title, amount.
  - Takes `instance` from config and `completed_on` from the clock. Neither comes from args.
  - Builds `subject` + `content` (HTML, every value escaped, links only to the Instance).
  - Sets `recipients` to the turn's requester. No argument sets it.
- **Policy:** `token`, no user code. The token binds the exact subject and body, so the logged email is the sent email.
- **The agent sends** `request.body.content` to `request.body.recipients` only, with the email skill.
- **execute** (only after the send succeeds) creates a `Communication`:
  - `communication_medium` Email, `sent_or_received` Sent;
  - `reference_doctype` / `reference_name` = the primary record (records[0]);
  - RBAC = the requester's read on the primary doctype;
  - Audit Log row on the primary record, `[approval: token]`.
- **Registering `notify`** also makes unscoped.generic refuse Communication. `init-bot --domains notify` grants the bot `Communication` read/create/write.

Docs:
- `03-spec-driven-execution.md` step 8: ask after close-out; send only on a later "yes"; never in autonomous mode; no Cc / other recipient; no fallback channel; one Outcome line.
- `tool-cookbook.md`: worked example.
- `qkeee-erp:usage` §7a.

Tests: `tests/test_notify.py` (10), plus `notify.summary_email` added to the TAMPER matrix in `test_operations.py`. 635 plugin + 12 repo tests pass.

## Open (human, live)

1. `hermes qkeee-erp init-bot --tag demo_erp --domains notify` (Operator). Then confirm the bot can create a `Communication` linked to an Item.
2. Confirm the insert does **not** make ERPNext send a second email. The Communication is inserted with `sent_or_received: Sent`, not through `communication.email.make`.
3. `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `EMAIL_SMTP_HOST` (and port/security) reach the gateway: profile `.env` or the container environment. Set 2026-10-15 by the user; verify after the rebuild.
4. One live run: create an Item, reply "yes" to "task complete?", get the email, and see it on the Item's timeline.

## Comments

- Sample render (3 records): `summary-email-sample.html` in the 2026-10-14 session scratchpad.
- 2026-10-15, decision E1 revised (user): the agent will also converse over email, and must not use ERPNext email. The gateway sends from the agent's own mailbox (`EMAIL_*`, the same mailbox the email platform uses), so replies come back as email turns. New `qkeee_erp/mailer.py` (multipart HTML + text, exactly one recipient, password never in the sidecar). `notify.summary_email` sends in a new `Operation.pre_send` step: after the token and RBAC gates, before the Audit Log row. A failed send writes nothing. The Communication records the `message_id` and `sender`. Sidecar email skills are no longer used. Tests: `test_mailer.py` (7) and 2 more in `test_notify.py`; 649 plugin tests pass. Live check open: a send from dev-erp after the rebuild. Upstream follow-up for issue 07: HTML support in the Hermes email adapter's `send`, so `mailer.py` can be dropped.
