# Refuse bot self-escalation through system-admin writes

Status: resolved (code done: b202dbd + 4b0cb3c, in feat/discovery-write-readiness)
Type: grilling
Blocked by: none

## Problem

Issue 04 tells the user which role the bot needs. The obvious next request is "go ahead and grant it". The `system_admin` domain allows writes to `User` and `Role` (`system_admin.py:43`). A System Manager requester could ask the agent to add a role to the bot's own User record. `UNSCOPED_DENY` blocks `Custom DocPerm` only for the unscoped operation.

The bot would then change its own permissions. The audit row would show the requester, but the change would come from the actor whose rights it widens.

## Proposed change

In the operation pipeline, refuse:

- any write to the `User` record of the bot account (`_bot_identity()`)
- any write to a `Role` the bot holds
- any `Has Role` / `Custom DocPerm` row that names the bot's user or roles

The refusal text tells the user to make the change in the ERPNext UI as an admin.

## Decision needed

1. Refuse always, or allow with a double-confirm from a System Manager?
2. Does this overlap with `qkeee-erp-write-path-hardening/issues/02`? Extend that gate or add a new one?

## Acceptance

- Tests: each refused case above, red first. A write to another user's roles still works.

## Comments

**Decided 2026-10-08** (grill-with-docs). Recorded in `docs/adr/0003-service-accounts-dedicated-roles-no-self-escalation.md`.

1. **Refuse always.** No double-confirm: the confirmation would travel through the channel the agent mediates. Refusal text points to the ERPNext UI and links ADR 0003.
2. **Scope: every Service Account** (Bot Account and Admin Account), whichever credential makes the write. Refuse:
   - a write to a Service Account's User record, including `system_admin.set_user_roles` on it;
   - a write to a Role any Service Account holds;
   - a Custom DocPerm add/update/remove/reset whose `role` a Service Account holds (`system_admin.py:254-292` Permission Manager RPCs).
3. **Placement: a new target-rule step in the operation pipeline** (`core/operations.py`), run for every `credential="admin"` op and the `UNSCOPED_DENY` doctypes. Do not extend write-path 02: that is the Requester Gate ("may this person?"); this rule is "may anyone, through the agent?".
4. **Invariant: Service Accounts hold only dedicated roles.** Extend `verify_rbac_precheck_reliable()` / `_bot_identity()` to fail closed when the Bot Account holds any stock role. Then the rule in 2 never blocks normal admin edits on stock roles.

Extra acceptance tests (red first): `set_user_roles` on the Bot Account refused; the same via the Admin Account refused; Custom DocPerm add on `Qkeee Bot` refused; Custom DocPerm add on `HR User` allowed; Bot Account holding `HR User` -> connector refuses to run.

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`), per ADR 0003. A target rule in `core/operations.py` refuses writes to a Service Account's User record, writes to roles a Service Account holds, and Custom DocPerm or Permission Manager changes on those roles. The connector fails closed when the Bot Account holds a stock role. Every acceptance case in the decision comment is covered in `domains/test_self_escalation.py`.
