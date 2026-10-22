# Domain: hr-payroll (HR, leave, payroll batch)

Code: `qkeee_erp/domains/hr_payroll.py`
(`ALLOWED_WRITE_DOCTYPES = ("Employee", "Employee Onboarding", "Employee
Separation", "Job Offer", "Leave Application")` — see that module's
docstring). Applies `00-conventions.md` and `01-connectivity.md` in full;
this file adds what's specific to HR/talent-acquisition work.

This domain has no unique connector logic of its own. Writes are
operation `hr_payroll.generic` through `erp_execute_write` (`op="hr_payroll.generic"`); submit/cancel/delete need a rendered confirmation
(`tool-cookbook.md`). The PII-flagging and advisory-only rules below are
prompt discipline; no script composes these drafts. Audit payloads mask
bank/identity fields (`core.client.AUDIT_MASK_FIELDS`).

## When this domain applies

Onboarding or updating an employee, leave application/balance, attendance
review, exit checklist, job opening/applicant management, interview
scheduling, offer letter drafting, salary slip batch creation, HR reports
(headcount, birthdays/anniversaries, probation-ending).

## Non-negotiables specific to this domain

- **Never surface or write sensitive employee PII (compensation, ID
  documents, personal contact details) outside the scope of the current
  authorized task.** A scope discipline, not a blanket lock — HR work is
  inherently PII-heavy. Every proposed write touching PII-sensitive fields (bank
  details, passport number, health details, emergency contacts, and
  similar) should flag them explicitly so a reviewer notices data present
  for no reason the current task explains.
- **Offer Letter (Job Offer) and Employee Onboarding never auto-commit,
  regardless of the write mode.** Compensation sensitivity (Job Offer)
  and irreversible-in-practice organizational commitment (both) put these
  above this domain's other read-write-capable capabilities — advisory-
  only, full stop, no "ready" state to chain into a write. The only
  legitimate path to an actual create is a human doing it themselves in
  ERPNext, or this skill executing it as its own separate, deliberately-
  confirmed step outside the advisory renderer.
- **Always confirm before any write touching an employee's record** —
  never infer license to touch fields the user didn't actually ask about.

- **Demo or test employees follow `00-conventions.md`'s Demo and test
  data section** — `Demo ` name prefix, `qkeee-demo` tag, `Inactive`,
  synthetic values, a teardown query in the spec.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype is confirmed inside the tuple above
   before any write is proposed.
2. **New employee onboarding and Employee updates.** Employee is **not
   submittable**: a save creates a live record, not a draft. Say "saved"
   or "created", and say it is live (`00-conventions.md` term rule).
   - **State the side effects of `status: "Active"`** before confirming:
     an Active Employee enters payroll runs, attendance, leave allocation
     and headcount reports, and its `date_of_birth` triggers birthday
     reminders when HR Settings enables them. Ask whether `Inactive` fits
     the task better (it always does for demo or test data — see
     `00-conventions.md`'s Demo and test data section).
   - **Run `erp_discover(action="preflight", doctype="Employee", payload={...})`.** It
     covers the mandatory fields, the live-discovered conditional
     `status: "Left"` → `relieving_date` rule, and the first check below.
     Then confirm the other two:
     1. HR Settings `emp_created_by` (Naming Series / Employee Number /
        Full Name). "Employee Number" makes `employee_number` mandatory
        and the record's name; preflight checks it.
     2. `date_of_birth` before `date_of_joining`, and the instance's
        minimum-age rule.
     3. The Company's holiday list ("Building an HR data set", step 2).
   - Flag PII fields. Present the exact payload, confirm, then
     `hr_payroll.generic` `create`/`update`. Re-fetch the Employee by
     `name` afterward (`query_resource` with explicit `fields` is
     sufficient — none of the reviewed fields live in a child table) and
     check every persisted field, especially that Link fields
     (`department`, `designation`, `reports_to`, `company`,
     `holiday_list`) resolve to real records. This post-save review is the
     only checkpoint. A wrong value after save is a deviation: log it in
     the spec and re-confirm before any fixing `update`
     (`03-spec-driven-execution.md` step 6).
   **Done when:** the side effects were stated, preflight is `ready:
   true`, every Link field on the re-fetched record resolves to a real
   one, and any PII field present is flagged.
3. **Offer Letter and Employee Onboarding stop at the advisory draft.**
   Do not continue into `create`/`submit` as part of this domain's own
   logic — if the user wants the write performed, that's a separate,
   explicitly-confirmed step they direct. **Done when:** the draft is
   presented and no `create`/`submit` call has been made from inside this
   step.
4. **Leave Application submit** follows "Building an HR data set", step
   6, and needs a submitted Holiday List Assignment (step 2); check both
   with a live query before you promise the submit will work. HR Settings
   can also make `leave_approver` mandatory at create.
   **Save-draft-then-review-then-submit:** `create` lands it `Open`/
   `docstatus 0`; re-fetch and re-review before the step 6 update and
   submit. In standard HRMS only
   System Manager may delete an Employee Onboarding or Employee
   Separation; the RBAC gate refuses an HR Manager requester, correctly.
   **Done when:** both preconditions are confirmed live, not assumed,
   before `submit` fires.
5. **Approving/submitting a Leave Application auto-creates an Attendance
   record for the covered dates; cancelling auto-cancels it too.** Explain
   this as expected system behavior. Correct an Attendance discrepancy
   that traces back to a Leave Application via the Leave Application, not
   by editing the derived Attendance record directly. **Done when:** any
   correction routes through the Leave Application, never a direct edit
   to the derived Attendance record.
6. **Before a Job Applicant create**, query by `email_id` for an
   existing record ("Building an HR data set", step 11). **Done when:**
   the email-based existence check has run before `create` fires.
7. **Interview Feedback should only be attributed to interviewers
   actually assigned to that Interview Round** — ERPNext enforces this
   server-side; don't work around it. **Done when:** the attributed
   interviewer is confirmed assigned to the round.
8. **HR reports** need a real reconciliation check first (department
   headcounts summing to total headcount, for example);
   `not_applicable` is only for reports with genuinely nothing to tie out
   (birthday/anniversary list, probation-ending list) and needs a stated
   reason. **Done when:** a reconciliation check ran, or `not_applicable`
   carries a stated reason.
9. **Warn before delete on any HR record beyond a fresh, never-referenced
   one.** Once a record has any downstream auto-generated link (Leave
   Application → Attendance), delete stays blocked even after every
   record in the chain is cancelled. Prefer cancel over delete, and say so
   upfront for anything past a bare create. **Done when:** the
   fresh-vs-referenced check ran, and the warning (or the cancel
   alternative) was stated before delete is attempted.

## Building an HR data set

Build a company's HR data (a demo set, an onboarding, a payroll run) in
this order. Each rule sits on the step it affects. Write through
`op="auto"` (`tool-cookbook.md`, "Picking the operation"), render each
step's batch after the previous one ran, and take every created name from
its result.

1. **Company.** Creating it also creates 13 departments named
   `<department> - <abbr>`: query Department before you create one. With
   India Compliance installed, the bot needs read on GST Settings, which
   the app reads while it validates the Company; preflight reports it.
2. **Holiday List, then Holiday List Assignment (submit it).** HRMS 16
   resolves holidays only through a submitted assignment; a Holiday List
   on the Employee alone fails later leave and attendance.
3. **Masters:** Designation, Branch, Employee Grade, Shift Type, Leave
   Type, Leave Period, Leave Policy, Salary Component.
   - Employee Grade, Shift Type, Salary Structure, Payroll Period, Income
     Tax Slab and Staffing Plan are named by the user: pass the name as
     the create's `name` argument, because it is not a payload field.
   - Query each Designation the data refers to; a spec's "exists" is a
     claim until a query shows it.
4. **Employees.** Run `preflight` first; `date_of_birth` is mandatory.
5. **Leave Policy Assignment (submit).** It creates the Leave
   Allocations, pro-rated from the joining date; say so in the report,
   because the balances are smaller than the policy.
6. **Leave Applications, before any Attendance.** A leave over a date
   that has Attendance fails with `AttendanceAlreadyMarkedError`. Set
   `status = Approved` (a permlevel-1 field), then submit: ERPNext submits
   only Approved or Rejected applications.
7. **Attendance Request (submit).** It skips approved leave and
   holidays, so it fills only the working days left.
8. **Salary Structure (submit), then Salary Structure Assignment
   (submit).** A draft assignment does not apply to payroll.
9. **Payroll Entry.**
   - Set `employees` (child `Payroll Employee Detail`, `employee` only)
     and `cost_center` yourself: through the API nothing fills them, and
     the default cost centre can belong to another company.
   - The payroll payable account needs `account_type = Payable`.
   - Submit runs as a background job: count it done only with
     `state_confirmed: true`, and show `read_back.status` and
     `error_message` otherwise.
10. **Salary Slips (submit).**
11. **Recruitment.**
    - Create the Skill first: an Interview Type needs at least one
      `expected_skill_set` row.
    - Job Applicant is named by its email.
    - Interview `scheduled_on` is set once, at create: pick a past date
      when feedback is submitted now, because Interview Feedback cannot
      be submitted before the scheduled date.
    - Interview submit needs `status` Cleared or Rejected.
12. **Onboarding and exit.**
    - An onboarding template without activities never creates the
      Employee or sets its status: create the Employee directly, and for
      an exit set `status = Left` and `relieving_date` yourself.
    - Exit Interview needs `relieving_date` on the Employee.
    - Full and Final Statement submit needs settled payables and
      receivables; leave it a draft and say why.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| New employee onboarding | Employee created (live on save), checklist tracked | Preflight first; state Active side effects; demo data: `00-conventions.md` |
| Update employee details | Employee updated | PII fields flagged if present |
| Leave application / balance check | Applied or reported | "Building an HR data set", steps 2 and 6 |
| Attendance query / regularization | Visibility or correction | Route corrections through the originating Leave Application |
| Employee separation / exit checklist | Clean, complete exit | `status: "Left"` requires `relieving_date` |
| Job Opening management | Open role tracked | Closed openings can't take new applicants |
| Job Applicant / resume intake | Candidate captured | Check the email first; may source from doc-extraction |
| Interview scheduling / feedback | Interview loop tracked | Feedback restricted to assigned interviewers |
| Offer Letter drafting | Ready for a human to extend | Advisory-only, any mode, no exceptions |
| HR reports | Headcount, birthdays/anniversaries, probation-ending | Reconciliation-checked |
| Payroll — batch salary slips | Draft/submit Salary Slips across employees/periods | Dedup detection, per-payslip status report |

## Relationships

Consumes `domains/doc-extraction.md` for resumes and offer-adjacent
documents; degrades to "ask user to paste resume text" if that domain
isn't reachable. Compensation-tax mechanics downstream of an Employee's
data (TDS on salary, PF/ESI) belong to `domains/accounts.md`, not
duplicated here.
