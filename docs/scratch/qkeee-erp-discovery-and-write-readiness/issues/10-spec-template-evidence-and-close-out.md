# Spec template: evidence, idempotency, rollback, deviations, close-out

Status: resolved (docs done: e9a861a + 4b0cb3c, in feat/discovery-write-readiness)
Type: task
Blocked by: 06

## Problem

From the 2026-10-07 DEMO_ERP spec:

- **No schema evidence.** It says "129 fields" and "mandatory fields confirmed from live metadata", but lists no mandatory fields, no `custom_fields_merged` value, no naming rule, no workflow check.
- **Stale risk.** Line 48 says "the user must confirm them before creation". Line 8 already records the approval. Risks were not updated after approval or at close-out.
- **No duplicate check.** A re-run would create HR-EMP-00008/9.
- **No rollback or cleanup plan.**
- **No exact payload or command.** The template asks for the payload shape. Technical step 1 gives only the operation name.
- **Post-approval fix-up.** Technical step 6 says "if a Link value does not resolve, update only that field". That changes data outside what was approved.
- **Outcome breaks the Report-back contract** (`00-conventions.md`): no records table with docstatus, no "no deviations" line, no audit row reference, no follow-ups (Module Def gap, pending skill `e701c144`).

## Change

In `03-spec-driven-execution.md`:

1. Template: add sections
   - **Schema evidence**: the `preflight` output per doctype (issue 06): meta source, `custom_fields_merged`, mandatory fields with the value set for each, conditional mandatory fields confirmed, naming rule, workflow.
   - **Side effects**: what the write triggers downstream (for example: an Active Employee enters payroll and attendance).
   - **Idempotency**: the query run before create to find an existing record, and its result.
   - **Rollback**: how to undo each write (delete, cancel, set status), and who may do it.
   - **Exact calls**: the full `execute_write.py` command and payload JSON per write.
2. Step 6: any change to a value after approval is a deviation. Log it in the spec and re-confirm before the update. Remove "fix the Link and re-fetch" as a silent step.
3. Step 7: before Outcome, reconcile Risks / open questions (close, update or carry forward each one). Outcome uses Report-back items 1–4: records table (doctype, name, action, docstatus), warnings first, "Deviations: none" or the list, audit status, open follow-ups.
4. Drop field counts like "129 fields" from the template guidance. They carry no decision.

## Acceptance

- The template has every section above.
- A re-written version of the 2026-10-07 spec, kept as an example in `references/examples/`, fills every section.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). The template in `03-spec-driven-execution.md` has Schema evidence, Side effects, Idempotency, Rollback, Teardown, Exact calls and Deviations. Step 6 treats any change after approval as a deviation and re-confirms. Step 7 reconciles Risks, and the Outcome follows Report-back. Field counts are dropped. `references/examples/spec-demo-employees.md` fills every section.
