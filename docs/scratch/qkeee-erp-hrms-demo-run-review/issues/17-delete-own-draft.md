# `delete_own_draft`: delete a draft the bot made in this session

Status: ready-for-human (step 1 is a live check; steps 2–6 then become ready-for-agent)
Type: task
Blocked by: none
Findings: F12
Decision: D4 (spec.md)

## Problem

A draft made by mistake (an Interview with a wrong, set-once `scheduled_on`) could not be removed, because the bot has no delete (ADR 0003). The demo kept a stray draft.

## Change

1. **Live check (human, demo_erp).**
   - In Role Permission Manager, on Interview, give `Qkeee Bot` a second permlevel-0 row: "Only If Creator" (`if_owner = 1`) with `delete` only, next to the existing row.
   - Confirm the bot still reads, creates and writes Interviews it did not create.
   - Confirm that a direct API delete with the bot key is refused for an Interview the bot did not create, and allowed for one it created.
   - **If ERPNext cannot hold both rows, stop and return to the spec owner. Do not move deletes to the Admin Account key.**
2. **`init-bot`.**
   - Grant that `if_owner = 1` delete row on every doctype where the bot may create.
   - Never on `UNSCOPED_DENY`, `admin_key_doctypes()` or the Audit Log.
   - New step `delete_own` in `provisioning.grant_bot_permission`; it is the only step that sets `if_owner = 1`.
   - The plan, the plan token and `extra_rights` include it.
3. **Operation `delete_own_draft`.** It uses the bot credential and policy token+user_code, and the agent reaches it through `erp_execute_write`. These checks run at render and again at execute:
   1. `docstatus = 0`;
   2. `owner` is the Bot Account user;
   3. `modified_by` is the Bot Account user;
   4. the Audit Log has a `Success` Create row for this doctype and name with the current `session_id`, taken from the gateway session context (or the Kanban task id), never from an argument;
   5. that row's `requested_by` is the current requester;
   6. that row is at most 8 hours old;
   7. the doctype is not in `UNSCOPED_DENY` or `admin_key_doctypes()`, and is not the Audit Log;
   8. ERPNext's link check runs: report `LinkExistsError` as is, and never unlink records.

   Also:
   - at most 10 deletes per session, counted from the Audit Log Delete rows with this `session_id`;
   - the Audit Log Delete row stores the full record before the delete and names the Create row.
4. **One path.** Refuse `action: delete` on every bot-credential `generic` operation (`<domain>.generic`, `unscoped.generic`), with a pointer to `delete_own_draft`. The `system_admin` deletes (Admin Account, its own doctypes) are unchanged.
5. **ADR 0003 amendment (2026-10-22).**
   - "Never delete" becomes "delete only own same-session drafts, through `delete_own_draft`, backed by an `if_owner` row".
   - Remaining risk: a leaked bot key can still delete bot-created cancelled records. Deleted Document and the Audit Log keep the evidence.
6. **Tests.** One refusal for each condition in step 3, plus:
   - an 11th delete in one session;
   - a delete through a `generic` operation;
   - `session_id` or requester passed as a tool argument (ignored; the gateway value wins);
   - the batch render (02) marks the step as a delete.

## Acceptance

- Spec acceptance items 9 and 10 hold on demo_erp and in tests.
