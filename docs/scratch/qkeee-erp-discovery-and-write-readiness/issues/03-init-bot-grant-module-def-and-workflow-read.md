# `init_bot.py`: grant the bot role read on Module Def and Workflow

Status: ready-for-human (code done: 1684999; live checks open)
Type: task
Blocked by: none (01 resolved: option A, ADR 0001)

## Problem

After issue 02 the bot account itself reads Module Def. A correctly least-privileged bot (no System Manager) has no read on Module Def, and no read on Workflow, which issue 06 needs to detect an active workflow. Today an admin must find this out from a failed call.

## Change

1. `doctype_defs.py`: add Custom DocPerm definitions for role `Qkeee Bot`, permlevel 0, read only:
   - `Module Def`
   - `Workflow`
2. `init_bot.py`: provision them after the role exists. Skip a row that already exists. Show them in `--dry-run`.
3. Never grant write, create or delete through these rows.
4. These are DocPerms on the dedicated `Qkeee Bot` role, per `docs/adr/0003-service-accounts-dedicated-roles-no-self-escalation.md`. Never grant a stock role instead.

## Acceptance

- Dry-run lists both rows.
- A real run on a test instance creates them once; a second run skips them.
- A bot user with only `Qkeee Bot` can list Module Def and Workflow.
- Check first, live: does a Custom DocPerm row on Module Def replace its standard DocPerm rows? (Frappe uses Custom DocPerm in place of DocPerm once any exist for a doctype.) If yes, copy the standard rows too, so System Manager does not lose access.

## Comments

**Implemented 2026-10-08** (/implement verification).

Code done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`), commit 1684999. `doctype_defs.BOT_READ_GRANTS` = Module Def, Workflow (read only). `init_bot.py` grants them through the Permission Manager `add`/`update` RPCs. Dry-run, skip-existing and read-only refusal are covered in `test_init_bot.py`.

On the standard-DocPerm question: the Permission Manager RPCs call Frappe's `setup_custom_perms()`, which copies the standard DocPerm rows into Custom DocPerm before it adds one. So System Manager does not lose access. This comes from the Frappe code. It is not yet confirmed live.

Open, live on a test instance: (1) a real run creates both rows and a second run skips them; (2) a user with only `Qkeee Bot` can list Module Def and Workflow; (3) System Manager still holds its Module Def and Workflow rows after the grant.
