# `erp_query`: filter shapes, `order_by`, `count_only`

Status: done 2026-10-22 (erpnext-hermes a2d9842, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F5, F6

## Problem

- `client.query_resource` sends `filters` to ERPNext as given. The tool schema says array, but a dict (`{"company": "X"}`, `{"employee": ["in", [...]]}`) passes the `tool_call` wrapper, and ERPNext v16 returns HTTP 500 `KeyError: 1`. This happened 6 times in part 2 B.
- There is no `order_by`; the agent tried one (part 2 A[49]).
- There is no count. To count 102 Attendance rows, the agent pulled 27.8 KB, twice.

## Change

- `query_resource` normalises `filters`:
  - a dict `{field: value}` becomes `[[field, "=", value]]`;
  - a dict `{field: [op, value]}` becomes `[[field, op, value]]`;
  - a list of lists is kept;
  - any other shape is refused with a message that shows the list form.
- `order_by`: an optional string `"<field> asc|desc"`. The field must match `^[a-z0-9_]+$`; refuse anything else, so no SQL passes through it.
- `count_only: true`: return `{"count": n}` through `frappe.client.get_count`, with the same filters, the same requester gate and an Audit Log Read row as for a list.
- Schema in `erp_tools.py`: `filters` accepts an array or an object (documented), plus `order_by` and `count_only`.

## Acceptance

- Tests:
  - both dict forms become list form;
  - a bad shape is refused before any HTTP call;
  - `order_by` refuses `name; drop`;
  - `count_only` calls `get_count` and logs a Read row.
