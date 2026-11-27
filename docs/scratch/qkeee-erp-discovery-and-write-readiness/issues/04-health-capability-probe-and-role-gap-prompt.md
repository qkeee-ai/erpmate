# `health` capability probe and role-gap prompt

Status: ready-for-human (code done: 286b6a8; live DEMO_ERP check open)
Type: task
Blocked by: 02, 03

## Problem

A missing role shows up only in the middle of a task, as a refused call. The agent then writes its own explanation. On DEMO_ERP it reported "the requester has no read permission on Module Def". That told the user nothing about what to grant, to whom, or where.

## Change

1. `client.py health`: add a `capabilities` block. Probe, as the bot:
   - Module Def list (limit 1)
   - `frappe.utils.change_log.get_versions`
   - `getdoctype` on a stock doctype (`User`)
   - Workflow list (limit 1)
2. Return `gaps[]`. Each gap is:
   `{capability, who: "bot"|"requester", user, role, doctype, perm, effect, grant_steps}`
3. Add a role-gap prompt to `00-conventions.md` (or `01-connectivity.md`). The agent renders it from a gap and does not paraphrase:

   ```
   ⚠ Environment catalog incomplete on <TAG> (<base URL>).
   Missing: <perm> on "<doctype>" for <who> <user> (role "<role>").
   Effect: <effect>.
   Fix (System Manager): Role Permission Manager → Document Type "<doctype>"
   → Add rule → Role "<role>", Level 0 → tick <perm> → Save.
   Then reply RECHECK ENV.
   ```

4. Activation step 1 (`SKILL.md`): when `health` returns gaps, show the prompt once per session, before any write.
5. The agent never grants the role itself (see issue 08).

## Acceptance

- Tests: each probe failure maps to one gap with all keys set.
- A clean bot returns `gaps: []`.
- Live: on DEMO_ERP before issue 03, `health` shows the Module Def gap with the prompt filled in.

## Comments

**Implemented 2026-10-08** (/implement verification).

Code done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`), commit 286b6a8. `client.py health` returns `capabilities` and `gaps[]` from 4 bot probes: Module Def list, get_versions, getdoctype User, Workflow list. The gap shape has every key in this ticket. The role-gap prompt is in `00-conventions.md`, and `SKILL.md` activation step 1 shows it once per session, before any write. Tests in `core/test_client.py`: clean bot gives `gaps: []`, each probe failure gives one complete gap, the Module Def gap renders the prompt.

Open: live check. Run `health` on DEMO_ERP. Issue 03 was never run there, so the Module Def gap should show with the prompt filled in.
