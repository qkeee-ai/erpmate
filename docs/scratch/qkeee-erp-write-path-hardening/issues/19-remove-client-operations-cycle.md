# Remove the client.py → operations.py back-reference (Option A)

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P4 follow-up
Blocked by: none
Raised by: nikhil.sharma@qkeee.ai (design review of the shims)

## Problem

`core/client.py` kept `mutate_resource()` and `gated_mutate_resource()`. These were compatibility shims that lazily imported `core/operations.py` through `_operations()`, while `operations.py` imports `client.py`. That made a runtime dependency cycle, with the lower layer calling up into the pipeline.

- The shims had zero production callers; only 23 test calls used them.
- The `_operations()` fallback import could load a second, empty copy of the operation registry.
- `get_operation()`'s auto-import of `domains.<x>` was a second back-edge, from operations to domains, that existed for the same tests.

Passing `_do_mutate` in as a lambda was considered and rejected:

- It targets the wrong edge.
- `operations.py` uses about fifteen things from `client.py`.
- A pluggable sender would let a caller bypass audit and the invariant tests.

## Change (Option A)

- **Removed from `client.py`:** `mutate_resource`, `gated_mutate_resource` and `_operations`. `client.py` no longer imports `operations.py`.
- **Removed every domain module's `mutate()` wrapper.** `core/operations.py` (`run_operation`, `prepare_only`, `call_generic`) is the only write API in Python, and `execute_write.py` the only CLI.
- **Removed `get_operation()`'s auto-import.** Dependencies run one way: domains → operations → client.
- **Tests.** The 35 test call sites were rewritten by an AST script to `operations.call_generic(...)`. Test names and docstrings were updated, and the shim-only test was removed.
- **New guards:**
  - `client.py` has no write function and does not import `operations`.
  - No domain module has a `mutate()`.
  - `PUBLIC_CALLABLES` lists no wrappers.

  Each guard was verified by injecting the regression and watching it fail.
- **Docs:**
  - `SKILL.md` names the write API and the dependency direction.
  - `cli-cookbook.md` gives the Python write API.
  - Docstrings in `client.py`, `schema_mapping.py`, `item_write_helpers.py`, `domains/__init__.py` and `mis.py` updated, plus `mis.md`.
  - `docs/qkeee-erpnext-feature-inventory.md` and `CHANGELOG.md`.

## Acceptance

- [x] `client.py` imports nothing from `operations.py`; guard test.
- [x] No `mutate_resource`/`gated_mutate_resource`/domain `mutate()` remains in code or docs; the guard tests name them on purpose.
- [x] Suite green: 305 passed, 856 subtests.
- [x] `execute_write.py --list-ops` still lists 23 operations.
