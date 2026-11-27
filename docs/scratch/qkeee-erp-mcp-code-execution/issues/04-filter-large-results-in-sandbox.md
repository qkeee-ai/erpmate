# Filter/aggregate large ERPNext result sets inside the sandbox

Status: needs-triage
Type: task
Blocked by: 02

## Problem

Item run and HRMS demo runs pull row sets (Items, GL entries, employee records) that today pass through the agent loop in full — hundreds of rows copied into model context just to extract a few, or to count/aggregate. This is the token cost the code-execution pattern is meant to cut.

## Change

- For wrapper calls that can return large collections, let generated code filter, aggregate, or slice the result before anything is logged or returned to the model (e.g. `rows.filter(...)`, a count, `rows.slice(0, 5)`).
- Confirm the audit log still records what the wrapper call actually returned (the full result), not just what the sandbox script chose to surface to the model — see the audit trace risk in spec.md.

## Done when

A real Item run or HRMS run that today copies N rows through context instead shows the model a count and a small sample, with the audit log still showing the full N rows were read.

## Comments
