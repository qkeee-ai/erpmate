---
name: usage
description: "Rules for calling the erp_* tools safely: requester, audit context, the confirmed-write flow, role gaps, refusals and failures."
---

# qkeee-erp: calling the erp_* tools

The `qkeee_erp` tools are the only way to reach ERPNext. They run in the
gateway, hold the ERPNext credentials, and bind every call to the person
who sent this turn's message (the **requester**). These rules apply to
every agent that has the tools.

| Tool | Use |
| --- | --- |
| `erp_query` | List records of one DocType. |
| `erp_get` | Read one record with its child tables. |
| `erp_report` | Run a built-in query report. |
| `erp_discover` | `health`, `whoami`, `roles`, `apps`, `modules`, `meta`, `resolve`, `preflight`, `promotion_plan`. |
| `erp_execute_write` | `list_ops`, `render`, `execute` (one `op`, or a `batch`). The only write path. |

## 1. Never reach ERPNext another way

- Do not read the credentials file. Do not call ERPNext from the terminal,
  `execute_code`, `curl` or a script.
- A refusal from a tool or from the gateway guard is final. Report it to
  the user with its reason. Do not retry it with other arguments, another
  tool, or a shell command.

## 2. The requester comes from the gateway

- No tool takes a requester. Do not ask the user who they are, and do not
  take an identity from memory, an earlier turn, a display name or a card.
- `erp_discover` `whoami` shows the identity this turn is bound to (no
  network call).
- If a tool refuses because the turn has no sender, tell the user, and
  stop. In a Kanban worker without a recorded requester, block the task
  with `kanban_block(kind="needs_input")` and name the gap.
- The bot account itself is never a requester.

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
- The write mode (read-only or read-write) is an operator setting. You
  cannot change it. In read-only mode every write is refused.

## 5. Show role gaps exactly

- When `health` or `preflight` returns `gaps[]`, show each
  `gaps[].prompt` exactly as returned, once per session, before any write.
  Do not paraphrase or shorten it.
- Never grant the bot account a role or a permission, and never ask the
  user to let you do it. Changes to the bot's own rights are always
  refused. An administrator makes them in the ERPNext UI.

## 6. Report a permission error as its own failure

A clean `health` proves connectivity and authentication only. A later
permission error on one DocType is a separate failure: name the DocType
and the action, and say that access, not connectivity, is the problem.

## 7. Write with confirmation

1. `erp_execute_write` `list_ops`: find the operation and copy its
   `example_args`. Keep the keys, replace the values.
2. `render` with `op` and `args`. When the operation needs confirmation,
   the result has `request`, `args`, `confirmation_token`, `issued_at` and
   `confirmation_code`.
3. Show the user `request` and the code, for example "reply `yes 3F0A9C`
   to confirm". Wait for their reply in a later turn.
4. `execute` with the same `op`, the rendered `args` unchanged,
   `confirmation_token`, `issued_at`, the user's reply verbatim as
   `user_confirmation_text`, and `latest_prompt`.

- Never write `user_confirmation_text` yourself.
- Any change after render is refused: other args, a changed record,
  another requester, or more than 15 minutes. Render again and ask again.
- An operation that needs no confirmation says so in its render result.

## 8. Handle failures honestly

- `write_failure`: ERPNext rejected the write and named the missing or
  invalid fields. Show them, ask the user for the values, and render
  again. Never fill a value the user did not give.
- `outcome_unknown`: the write may or may not have happened. Read the
  record again before any retry.
- A batch stops at the first failed step. Report each step as created,
  failed or not attempted.
- `warnings` on a successful write (for example an audit-log problem):
  tell the user. Do not report a plain success.
