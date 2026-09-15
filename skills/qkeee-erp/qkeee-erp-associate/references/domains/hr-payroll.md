# Domain: hr-payroll (HR, leave, payroll batch)

Code: `scripts/domains/hr_payroll.py`
(`ALLOWED_WRITE_DOCTYPES = ("Employee", "Employee Onboarding", "Employee
Separation", "Job Offer", "Leave Application")` — see that module's
docstring). Applies `00-conventions.md` and `01-connectivity.md` in full;
this file adds what's specific to HR/talent-acquisition work.

This domain has no unique connector logic of its own — the PII-flagging
and advisory-only enforcement described below belongs in
`render_employee_draft.py`/`render_advisory_draft.py`, which don't exist
in this skill's scripts/ yet. This reference states the target procedure.

## When this domain applies

Onboarding or updating an employee, leave application/balance, attendance
review, exit checklist, job opening/applicant management, interview
scheduling, offer letter drafting, salary slip batch creation, HR reports
(headcount, birthdays/anniversaries, probation-ending).

## Non-negotiables specific to this domain

- **Never surface or write sensitive employee PII (compensation, ID
  documents, personal contact details) outside the scope of the current
  authorized task.** A scope discipline, not a blanket lock — HR work is
  inherently PII-heavy. Every draft touching PII-sensitive fields (bank
  details, passport number, health details, emergency contacts, and
  similar) should flag them explicitly so a reviewer notices data present
  for no reason the current task explains.
- **Offer Letter (Job Offer) and Employee Onboarding never auto-commit,
  regardless of `qkeee_erp.mode`.** Compensation sensitivity (Job Offer)
  and irreversible-in-practice organizational commitment (both) put these
  above this domain's other read-write-capable capabilities — advisory-
  only, full stop, no "ready" state to chain into a write. The only
  legitimate path to an actual create is a human doing it themselves in
  ERPNext, or this skill executing it as its own separate, deliberately-
  confirmed step outside the advisory renderer.
- **Always confirm before any write touching an employee's record** —
  never infer license to touch fields the user didn't actually ask about.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype is confirmed inside the tuple above
   before any write is proposed.
2. **New employee onboarding and Employee updates** stage a draft that
   enforces ERPNext's mandatory fields and the live-discovered
   `status: "Left"` → `relieving_date` requirement, and flags PII fields.
   Present, confirm, then `domains.hr_payroll.mutate(..., "create"/
   "update")`. Re-fetch the Employee by `name` afterward
   (`query_resource` with explicit `fields` is sufficient — none of the
   reviewed fields live in a child table) and check every persisted
   field, especially that Link fields (`department`, `designation`,
   `reports_to`, `company`, `holiday_list`) resolve to real records.
   Employee has no submit workflow, so this post-save review is the only
   checkpoint — fix via a further `update` and re-review if anything is
   wrong. **Done when:** every Link field on the re-fetched record
   resolves to a real one, and any PII field present is flagged.
3. **Offer Letter and Employee Onboarding stop at the advisory draft.**
   Do not continue into `create`/`submit` as part of this domain's own
   logic — if the user wants the write performed, that's a separate,
   explicitly-confirmed step they direct. **Done when:** the draft is
   presented and no `create`/`submit` call has been made from inside this
   step.
4. **Leave Application submission needs two live-discovered
   preconditions**, not just declared-mandatory fields: `status` must be
   `Approved` or `Rejected` before submit (a fresh application defaults to
   `Open`), and a resolvable Holiday List must exist (on the Employee or
   the Company) — check this with a real query before ever promising
   submission will work, don't rely on remembering it as a mental note.
   **Save-draft-then-review-then-submit:** `create` lands it `Open`/
   `docstatus 0`; re-fetch, set `Approved`/`Rejected` via `update` if
   needed, re-review, only then `submit` as its own distinct step. **Done
   when:** both preconditions are confirmed live, not assumed, before
   `submit` fires.
5. **Approving/submitting a Leave Application auto-creates an Attendance
   record for the covered dates; cancelling auto-cancels it too.** Explain
   this as expected system behavior. Correct an Attendance discrepancy
   that traces back to a Leave Application via the Leave Application, not
   by editing the derived Attendance record directly. **Done when:** any
   correction routes through the Leave Application, never a direct edit
   to the derived Attendance record.
6. **Job Applicant is autonamed by `email_id`**, not a generated series —
   query/reference by email, and check for an existing record with that
   email before creating a new one. **Done when:** the email-based
   existence check has run before `create` fires.
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

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| New employee onboarding | Employee created, checklist tracked | May source from doc-extraction |
| Update employee details | Employee updated | PII fields flagged if present |
| Leave application / balance check | Applied or reported | Submission needs Approved/Rejected + resolvable Holiday List |
| Attendance query / regularization | Visibility or correction | Route corrections through the originating Leave Application |
| Employee separation / exit checklist | Clean, complete exit | `status: "Left"` requires `relieving_date` |
| Job Opening management | Open role tracked | Closed openings can't take new applicants |
| Job Applicant / resume intake | Candidate captured | Autonamed by email; may source from doc-extraction |
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
