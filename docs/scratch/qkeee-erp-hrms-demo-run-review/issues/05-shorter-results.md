# Shorter write and read results

Status: done 2026-10-22 (erpnext-hermes f104010, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F6

## Problem

A create or update returns the full ERPNext document. The Holiday List create returned all 59 child rows, 26 KB (part 1 [8]). Large list results fill the context window, and no step used them in full.

## Change

- Write results (`run_operation`, resource transport) return `name`, `doctype`, `docstatus`, `modified` and the top-level fields the payload set. A child table appears only as a row count, for example `holidays: 59 rows`.
- A new optional `full_result: true` on `erp_execute_write` returns the full document (today's behaviour).
- The Audit Log keeps the full `payload_after` (unchanged).
- An `erp_query` result above 10 KB carries `_note`: "large result — use `count_only` or fewer fields".

## Acceptance

- Tests:
  - a create with a 59-row child table returns less than 2 KB, with the row count;
  - `full_result` returns the full document;
  - the Audit Log row still holds the full document.
