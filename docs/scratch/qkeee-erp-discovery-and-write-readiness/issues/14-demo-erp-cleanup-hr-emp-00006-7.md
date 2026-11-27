# DEMO_ERP: decide what to do with HR-EMP-00006/7 and skill `e701c144`

Status: resolved
Type: task
Blocked by: none

## Problem

1. `HR-EMP-00006` (Demo Employee One) and `HR-EMP-00007` (Demo Employee Two) are live, `Status: Active` Employees on DEMO_ERP, not drafts. They now enter payroll, attendance and headcount on that instance.
2. Pending skill `e701c144` is the first `qkeee-erp-learned/demo_erp` promotion. It holds a partial catalog (no apps or modules).

## Decision needed

1. Employees: keep as Active, set to `Inactive` and tag `qkeee-demo`, or delete? (Delete is safe only while nothing links to them.)
2. Skill `e701c144`: approve as is, reject, or hold until issue 02 lands and `modules`/`apps` can run?

## Acceptance

- The chosen action is done on DEMO_ERP and recorded under `## Comments` here.

## Comments

**Decided 2026-10-08** (grill-with-docs).

1. **Employees: wontfix.** DEMO_ERP is the scratch Instance; HR-EMP-00006/7 were residue of a runtime test from the dev-erp Deployment. Leave them. Future demo records follow the convention in issue 12.
2. **Skill `e701c144`: reject.** A partial catalog must not be the first `qkeee-erp-learned/demo_erp` promotion. After issue 02 lands, re-run discovery and promote the full catalog.

Remaining action: reject `e701c144` on dev-erp (pending).
