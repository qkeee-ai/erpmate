# Convention for demo and test data

Status: resolved (docs done: e9a861a, in feat/discovery-write-readiness)
Type: task
Blocked by: 11

## Problem

The 2026-10-07 task created demo data with no marker other than the name "Demo Employee One/Two", no teardown plan, and live-status values. The skill has no rule for synthetic records, so each run invents its own.

## Change

Add a "Demo and test data" section to `00-conventions.md`:

1. Never on a tag marked PROD.
2. Mark every record two ways: a name prefix (`Demo `) and a tag (`qkeee-demo`, via `_user_tags` or a Comment).
3. Pick the least-live status the doctype allows (for example, Employee `Inactive`) unless the task needs it live. Say why in the spec.
4. Use no real personal data. Synthetic values only, flagged as synthetic in the spec.
5. The spec has a teardown section: the query that finds the records by tag, and the action that removes them.
6. Check for existing demo records with the same marker before create.

## Acceptance

- The section exists and `hr-payroll.md` points to it.
- The example spec from issue 10 follows it.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). `00-conventions.md` has a "Demo and test data" section with all 6 rules. `hr-payroll.md` points to it, and the example spec follows it.

Deviation from rule 2: the skill has no tag or Comment write, so the convention lists "add tag `qkeee-demo`" as a follow-up for the user in the ERPNext UI. The `Demo ` name prefix alone is enough for the teardown query. If you want an agent-side marker, a follow-up ticket could add a Comment write. Comment is already exempt from the write gate.
