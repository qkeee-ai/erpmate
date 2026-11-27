# `erp_execute_write list_ops`: filter by operation

Status: resolved
Type: task
Blocked by: none
Findings: F8

## Problem

`phase=list_ops` returns every registered operation (about 14k chars). The agent needs one: the op it is about to render.

## Change

- `execute_write.list_ops(op=None, domain=None)`: with `op`, return only that operation (unknown key → `InvalidArgumentsError` that lists the registered keys); with `domain`, only that domain's operations.
- `erp_execute_write` with `phase=list_ops` passes `op` and a new optional `domain` argument through. The tool description says so.
- No change without arguments.

## Acceptance

- Tests: `list_ops(op="unscoped.generic")` returns one row; `domain="inventory"` returns only inventory ops; unknown op refuses; the tool passes both through.

## Comments

- Implemented 2026-10-14: `execute_write.list_ops(op=None, domain=None)` (domain = key prefix, so `unscoped` works); unknown filter raises `InvalidArgumentsError` listing the keys; `erp_execute_write` gains `domain` and passes `op`/`domain` on list_ops. Tests: 3 in `test_operations.py`, 1 in `test_plugin_erp_tools.py`.
