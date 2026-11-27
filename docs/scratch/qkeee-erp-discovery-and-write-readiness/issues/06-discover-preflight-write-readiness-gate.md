# `discover.py preflight`: write-readiness gate

Status: ready-for-human (code done: f5db8f2 + review fix 4b0cb3c; live DEMO_ERP check open)
Type: task
Blocked by: 04, 05

## Problem

On a customised instance a create can fail because of:

- a custom mandatory field, or a Property Setter that makes a field mandatory
- a conditional mandatory field
- the naming rule (for Employee: HR Settings `emp_created_by` set to "Employee Number" makes `employee_number` mandatory)
- an active Workflow on the doctype
- validation added by a custom app

The agent has no single call that checks these before a create. Today it builds the payload from stock knowledge plus `reqd`.

## Change

Add `discover.py preflight "<DocType>" [--payload <json>]`. It returns:

```json
{
  "doctype": "Employee",
  "meta_source": "getdoctype | bare_doctype",
  "custom_fields_merged": true,
  "owning_app": "erpnext",
  "custom_apps_installed": ["..."],
  "mandatory": [{"fieldname": "...", "in_payload": true}],
  "conditional_mandatory": [{"fieldname": "...", "expression": "..."}],
  "naming": {"autoname": "...", "series_options": ["..."], "settings_rule": "..."},
  "workflow_active": false,
  "ready": true,
  "blockers": ["..."],
  "gaps": []
}
```

Rules:

1. `custom_fields_merged: false` → `ready: false`. The agent must not create until the user explicitly overrides. Record the override in the spec.
2. A mandatory field missing from `--payload` → blocker.
3. `conditional_mandatory` entries are listed for the user to confirm; the agent does not evaluate the expressions.
4. `workflow_active: true` → blocker with the workflow name. The create may land in a state the user did not expect.
5. A domain can add a settings check. Start with Employee: read HR Settings `emp_created_by`.
6. Permission gaps reuse the `gaps[]` shape from issue 04.

Wire it in: `03-spec-driven-execution.md` step 2 runs `preflight` for every doctype the spec writes, and pastes the result into the spec (issue 10).

## Acceptance

- Tests: each rule above, red first.
- Live: `preflight Employee` on DEMO_ERP with the payload from the 2026-10-07 spec returns `ready: true`, or names what is missing.

## Comments

**Implemented 2026-10-08** (/implement verification).

Code done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). `discover.py preflight "<DocType>" [--payload]` implements rules 1–6 (`PreflightTests` in `test_discover.py`, one test or more per rule). Active workflows come from merged meta `__workflow_docs`. Review fix 4b0cb3c drops Workflow State docs from that list. An unknown workflow state is a blocker. Step 2 of `03-spec-driven-execution.md` runs `preflight` and pastes the result into Schema evidence.

Open: live check. Run `preflight Employee` on DEMO_ERP with the 2026-10-07 payload.
