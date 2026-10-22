---
name: usage
description: "Rules for calling the erp_* tools safely: Requester, audit context, the confirmed-write flow, role gaps, refusals and failures."
---

# qkeee-erp: calling the erp_* tools

The `qkeee_erp` tools are the only way to reach ERPNext. They run in the
gateway, hold the ERPNext credentials, and bind every call to the person
who sent this turn's message (the **Requester**; "the user" below). These rules apply to
every agent that has the tools.

| Tool | Use |
| --- | --- |
| `erp_query` | List records of one DocType. |
| `erp_get` | Read one record with its child tables. |
| `erp_report` | Run a built-in query report. |
| `erp_discover` | `health`, `whoami`, `roles`, `apps`, `modules`, `meta`, `resolve`, `preflight`, `promotion_plan`, `bot_permissions`. |
| `erp_execute_write` | `list_ops`, `render`, `execute` (one `op`, or a `batch`). The only write path. |

## 1. Never reach ERPNext another way

- Do not read the credentials file. Do not call ERPNext from the terminal,
  `execute_code`, `curl` or a script.
- A refusal from a tool or from the gateway guard is final. Report it to
  the user with its reason. Do not retry it with other arguments, another
  tool, or a shell command.

## 2. The Requester comes from the gateway

- No tool takes a Requester. Do not ask the user who they are, and do not
  take an identity from memory, an earlier turn, a display name or a card.
- `erp_discover` `whoami` shows the identity this turn is bound to (no
  network call).
- If a tool refuses because the turn has no sender, tell the user, and
  stop. In a Kanban worker without a recorded Requester, block the task
  with `kanban_block(kind="needs_input")` and name the gap.
- The bot account itself is never a Requester.

## 3. Pass the audit context

- `prompt_summary` on every call: one line that says what the user asked.
- `latest_prompt` on every write: the user's most recent message,
  verbatim. A write that needs confirmation is refused without it.
- The tools write both to the audit log. A paraphrase in `latest_prompt`
  makes the audit row false.

## 4. Know the Instance

- `erp_discover` `health` returns the active Instance's `tag` and
  `base_url`. State them to the user before the first read or write of a
  session, and again before a batch of writes.
- Pass `tag` only when the user names another Instance.
- The write mode (read-only or read-write) is an Operator setting (`health` returns it as `mode`). You
  cannot change it. In read-only mode every write is refused.

## 5. Show role gaps exactly

- When `health`, `preflight` or a refusal returns `gaps[]`, show each
  `gaps[].prompt` exactly as returned, once per session, before any write.
  Do not paraphrase or shorten it.
- The bot's roles are `health` `bot_roles` (`null` = could not be read,
  never "no roles"). What the bot needs is `erp_discover`
  `bot_permissions`. Answer bot-permission questions from those two.
- The Admin Account reads the roles for the permission check. When an
  `admin` gap appears, every business call is refused until an Operator
  or admin fixes it; the bot needs no read on User or DocType.
- Never grant the bot account a role or a permission, and never ask the
  user to let you do it. Changes to the bot's own rights are always
  refused. An administrator makes them in the ERPNext UI.

## 6. Report a permission error as its own failure

A clean `health` proves connectivity and authentication only. A later
permission error on one DocType is a separate failure: name the DocType
and the action, and say that access, not connectivity, is the problem.

## 7. Write with confirmation

1. `erp_execute_write` `list_ops` with `op="<key>"` (or `domain="<prefix>"`):
   copy the operation's `example_args`. Keep the keys, replace the values.
2. `render` with `op` and `args`. When the operation needs confirmation,
   the result has `request`, `args`, `confirmation_token`, `issued_at` and
   `confirmation_code`.
3. Show the user `request` and the code, for example "reply `yes 3F0A9C`
   to confirm". Wait for their reply in a later turn. Any reply that
   contains the code confirms; `3F0A9C` alone is valid.
4. `execute` with the same `op`, the rendered `args` unchanged,
   `confirmation_token`, `issued_at`, the user's reply verbatim as
   `user_confirmation_text`, and `latest_prompt`.

- **Several writes: one batch, one code.** Render the whole remaining
  card as one `batch` of `{op, args}` steps (at most 25). Show the user
  every `steps[].line` (submit, cancel and delete steps are marked) and
  the one `confirmation_code`. Execute with the rendered `batch`
  unchanged, `batch_token`, `issued_at` and the user's reply. Any change
  to the list is refused before step 1. A submit or cancel after an
  earlier step changed the same record takes
  `expected_modified: {"from_step": k}`. A step that needs another
  step's new name goes in the next batch, rendered after this one ran.
- Never write `user_confirmation_text` yourself.
- `user_approved=true` only after the user confirmed this exact payload
  in a reply. On a write with no token, the Audit Log records it as
  `[approval: agent-asserted]`; a verified token records
  `[approval: token+code]`.
- Any change after render is refused: other args, a changed record,
  another Requester, or a reply after `expires_at`. Render again and ask
  again.
- Show the expiry as "expires at HH:MM UTC", taken from the render's
  `expires_at` (`ttl_minutes` is the lifetime: 15 on production, 60 on
  non-production). Never run `date` or another clock command in the
  terminal: the render already holds the time.
- An operation that needs no confirmation says so in its render result.
- An execute result holds the record's `name`, `docstatus`, `modified`
  and the fields you set; a child table shows as a row count. Read the
  new name from it. Pass `full_result=true` only when you need the whole
  document.

## 7a. Task summary email

`notify.summary_email` builds the summary email for the requester. Use
it only after the user confirmed the task is complete. Render it, then
execute it with the rendered values. The gateway sends the email from
the agent's mailbox to the requester only and logs it on the record's
timeline. Never send it any other way (no email skill, no terminal). The
full procedure is in the domain skill's spec procedure, step 8.

`notify.share_records` emails one live query result to people in the
org's email domain (`QKEEE_ERP_EMAIL_DOMAINS`, else the requester's
domain), one email each. Pass `doctype`, `filters`, `fields`, `limit`,
`recipients`, `title` and an optional `note`; the plugin runs the query
itself as the requester. Show the user the recipients, the row count and,
when there are recipients beyond the requester, the confirmation code;
never paste the HTML. Execute after their reply. A recipient outside the
allowed domains is refused: report it, do not retry another way.

For both `notify.*` operations: a result with `outcome_unknown` that says
"The email WAS SENT" means the email reached its recipients and only the
ERPNext log failed. Tell the user it was delivered. Never render or send
it again. A refusal that says "no email was sent" means nothing went out.

## 8. Handle failures honestly

- `write_failure`: ERPNext rejected the write and named the missing or
  invalid fields. Show them, ask the user for the values, and render
  again. Never fill a value the user did not give.
- `outcome_unknown`: the write may or may not have happened. Read the
  record again before any retry.
- A submit or cancel counts as done only with `state_confirmed: true`:
  the plugin read the record back and saw the new docstatus. `false`
  (`outcome: not_applied`) means the record did not change; show
  `read_back.status` and `read_back.error_message`. `null` means the read
  failed; read the record yourself before you report it.
- A batch stops at the first failed step. Report each step as created,
  failed or not attempted.
- `warnings` on a successful write (for example an audit-log problem):
  tell the user. Do not report a plain success.
