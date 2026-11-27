# hr-payroll: non-submittable ≠ draft; Employee pre-create checks

Status: resolved (docs done: e9a861a, in feat/discovery-write-readiness)
Type: task
Blocked by: none
Blocks: 12

## Problem

1. **Wrong term.** The 2026-10-07 spec called both Employees "drafts" (lines 4, 12, 59) and also said "Employee is not submittable". On a non-submittable doctype, docstatus 0 is the final state. A save creates a live record. The user reads "draft" as reversible staging. This is the most severe finding in the review.
2. **Side effects not stated.** Both records are `Status: Active`. An Active Employee enters payroll runs, attendance, leave allocation and headcount reports. DOB 1990-01-01 also triggers birthday reminders when HR Settings enables them.
3. **Employee checks are not listed.** These can fail a create on a customised instance or produce a bad record:
   - HR Settings `emp_created_by` (Naming Series / Employee Number / Full Name)
   - `date_of_birth` before `date_of_joining`, and the minimum-age rule
   - Company default holiday list (HRMS 16: Holiday List Assignment)

## Change

1. `00-conventions.md` (term rule, every domain): "draft" means docstatus 0 on a **submittable** doctype only. For a non-submittable doctype say "saved" or "created", and say it is live.
2. `hr-payroll.md` procedure step 2:
   - state that an Employee is live on save
   - list the side effects of `Active`, and ask whether `Inactive` fits the task better
   - run the three checks above (through `preflight`, issue 06, once it exists)
3. `domains/*.md`: grep for "draft" on other non-submittable doctypes (Customer, Supplier, Item, Asset Category) and fix the wording.

## Acceptance

- No domain file calls a non-submittable record a draft.
- `hr-payroll.md` names the three checks and the `Active` side effects.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). The term rule is in `00-conventions.md`. `hr-payroll.md` step 2 says Employee is live on save, lists the side effects of Active (with Inactive as the option), and names the 3 checks (emp_created_by, DOB/joining and minimum age, holiday list). A grep of `domains/*.md` found "draft" only on submittable doctypes (JE, Asset, SI, Stock Entry, Salary Slip, MR, Quotation, PO) and on advisory text drafts.
