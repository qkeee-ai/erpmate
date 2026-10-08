# Spec: Create two demo Employee records

> **Worked example, not a live record.** The 2026-10-07 DEMO_ERP spec
> (`logs/demo-employees-20261007-0851.md`), rewritten to the template in
> `03-spec-driven-execution.md` and the Demo and test data convention in
> `00-conventions.md`. Values that were not captured live on 2026-10-07
> (preflight output, query results, record names) are illustrative and
> marked so. Status is `Inactive` and names carry the `Demo ` prefix —
> what the convention asks for, not what the original run did.

- Env tag: DEMO_ERP            Base URL: https://demo.qkeee.in
- Mode: read-write
- Domain(s): hr-payroll
- Requested by: nikhil.sharma@qkeee.in
- Approval: approved 2026-10-07, reply `CONFIRM DEMO EMPLOYEES`

## Objective

Create two demo Employee records on the scratch instance for a walkthrough.
Synthetic values only; easy to find and remove afterwards.

## Plan

1. Preflight Employee with the exact payload; resolve every blocker.
2. Check for existing demo Employees (idempotency).
3. Create both records as one batch; stop at the first failure.
4. Re-fetch both and check every mandatory and Link value.
5. Close out: reconcile risks, record outcome, list follow-ups.

## Functional steps

- Doctype: Employee — **not submittable**. Each create saves a **live**
  record (docstatus 0 is final). There is no draft step.
- Two records: `Demo Employee One`, `Demo Employee Two` (`Demo ` prefix).
- Company `DEMO LLP`, naming series `HR-EMP-`, gender `Male`,
  date_of_birth `1990-01-01`, date_of_joining `2026-10-01`,
  **status `Inactive`** (demo data: least-live status).
- No department, designation, contact, bank, identity or salary fields.
- All values synthetic.

## Schema evidence

`discover.py preflight Employee --payload '<payload below>'`
(illustrative output):

- Meta source: `getdoctype`; custom_fields_merged: `true`
- Owning app: `erpnext`; custom apps installed: `hrms` (its validation
  runs on save; preflight cannot see it)
- Mandatory fields and the value set:

  | Field | Value | Note |
  | --- | --- | --- |
  | naming_series | `HR-EMP-` | also the field default |
  | first_name | `Demo Employee One` / `Demo Employee Two` | |
  | gender | `Male` | Link → Gender, exists |
  | date_of_birth | `1990-01-01` | before date_of_joining; meets minimum age |
  | date_of_joining | `2026-10-01` | |
  | company | `DEMO LLP` | Link → Company, exists |
  | status | `Inactive` | |

- Conditional mandatory: `relieving_date` when `status == 'Left'` — not
  applicable (status `Inactive`), confirmed with the user.
- Naming: `autoname: naming_series:`, series `HR-EMP-`; HR Settings
  `emp_created_by = Naming Series` (employee_number not needed).
- Workflow: none active on Employee.
- Company default holiday list: `DEMO Holidays 2026`, Holiday List
  Assignment submitted (illustrative).
- `ready: true`, `blockers: []`, `gaps: []`.

## Side effects

`Inactive` keeps both records out of payroll runs, attendance, leave
allocation and headcount reports. Birthday reminders do not fire for
Inactive employees. (Had they been `Active`, all of those would apply.)

## Idempotency

Run before create:

```
python ${HERMES_SKILL_DIR}/scripts/core/client.py --tag DEMO_ERP query Employee \
  --filters '[["first_name","like","Demo %"]]' --fields '["name","first_name","status"]'
```

Result (illustrative): `[]` — no demo Employees exist. A re-run of this
spec runs the same query first and stops if it finds them.

## Rollback

Delete each record while nothing links to it (confirmed write, an HR
Manager or System Manager requester):

```
python ${HERMES_SKILL_DIR}/scripts/core/confirm_token.py render --op hr_payroll.generic \
  --args '{"doctype": "Employee", "action": "delete", "name": "<HR-EMP-nnnnn>"}'
```

then `execute_write.py --op hr_payroll.generic --args '<printed args>'` with
the printed token and the user's reply. If anything links to it, set
`status: "Left"` with a `relieving_date` instead.

## Teardown (demo/test data only)

- Find: the Idempotency query above (prefix `Demo `), plus
  `[["_user_tags","like","%qkeee-demo%"]]` once the tag is set.
- Remove: the Rollback calls, per record found.
- Follow-up for the user: add tag `qkeee-demo` to both records in the
  ERPNext UI (this skill has no tag write yet).

## Exact calls

One batch; stops at the first failure:

```
python ${HERMES_SKILL_DIR}/scripts/execute_write.py --tag DEMO_ERP --mode read-write \
  --requested-by nikhil.sharma@qkeee.in --session-id <session> --channel "Google Chat" \
  --channel-metadata '{"space": "<space>", "thread": "<thread>"}' \
  --latest-prompt "<the user's literal request>" \
  --batch '[
    {"op": "hr_payroll.generic", "user_approved": true,
     "approval_note": "CONFIRM DEMO EMPLOYEES",
     "args": {"doctype": "Employee", "action": "create",
      "payload": {"naming_series": "HR-EMP-", "first_name": "Demo Employee One",
                  "gender": "Male", "date_of_birth": "1990-01-01",
                  "date_of_joining": "2026-10-01", "company": "DEMO LLP",
                  "status": "Inactive"}}},
    {"op": "hr_payroll.generic", "user_approved": true,
     "approval_note": "CONFIRM DEMO EMPLOYEES",
     "args": {"doctype": "Employee", "action": "create",
      "payload": {"naming_series": "HR-EMP-", "first_name": "Demo Employee Two",
                  "gender": "Male", "date_of_birth": "1990-01-01",
                  "date_of_joining": "2026-10-01", "company": "DEMO LLP",
                  "status": "Inactive"}}}
  ]'
```

`user_approved` is set per step: only for a write whose payload the user
confirmed. A batch has no batch-level approval flag.

Then, per returned name:
`python ${HERMES_SKILL_DIR}/scripts/core/client.py --tag DEMO_ERP get Employee <name>`.

## Risks / open questions

- ~~The user must confirm the synthetic values before creation.~~ Closed:
  confirmed 2026-10-07 (`CONFIRM DEMO EMPLOYEES`).
- ~~`discover.py modules`/`apps` fail for the requester (no Module Def
  read).~~ Closed by issue 02 (Environment Metadata exemption, ADR 0001).
- Carried forward: tag `qkeee-demo` not yet set (no tag write in this
  skill). Owner: the user, in the ERPNext UI.

## Deviations

None.

## Outcome (filled in at close-out)

Two demo Employees created on DEMO_ERP (read-write), both live and
`Inactive` (illustrative names).

Warnings: none. Tag `qkeee-demo` still to be added (follow-up below).

| Doctype | Name | Action | Docstatus |
| --- | --- | --- | --- |
| Employee | HR-EMP-00008 | create | 0 (non-submittable, live) |
| Employee | HR-EMP-00009 | create | 0 (non-submittable, live) |

- Verified: both re-fetched; every mandatory field matches the table
  above; `company` and `gender` Links resolve.
- Deviations: none.
- Audit: `_audit_log_status: ok` for both writes.
- Follow-ups: add tag `qkeee-demo` (user, ERPNext UI); remove both with
  the Teardown calls when the walkthrough ends.
