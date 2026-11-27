# `domains/hr-payroll.md`: HR build order and rules

Status: done 2026-10-22 (erpnext-hermes 7730791, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F16

## Problem

Fourteen ERPNext/HRMS rules cost rework in this run (spec section "HR build order and rules"). None of them was in the domain reference.

## Change

- Add a section "Building an HR data set" to `erpnext-hermes/skills/qkeee-erp/qkeee-erp-associate/references/domains/hr-payroll.md`. It holds the 12-step order from the spec, with each rule on the step it affects: an instruction plus one clause of why.
- Keep each fact in one place. Remove the older lines that say the same thing (the Leave Application submit and Holiday List Assignment notes), or point from them to the new section.
- Follow `writing-for-agents`: leading words, positive instructions, no restated environment facts.

## Acceptance

- Each of the 14 rules in the spec appears once, on its step.
- No line duplicates `00-conventions.md`.
