# Enforce the one-tool-call-per-batch rule client-side, don't rely on the model remembering it

Status: ready-for-human — already enforced, no code change; live check open
Type: task
Blocked by: none
Findings: F2

## Problem

"Local tools require one entry per tool_call; mixed and multi-local batches are not supported" is documented as a hard runtime limit in `00-conventions.md`. The agent violated it twice in the same session — once before and once after the compaction boundary (L461, L789) — because it's enforced only by the model remembering a loaded doc, which a compaction event (or just a long session) can wash out.

## Change

In the tool-call dispatch path (`hermes-agent`, wherever local-tool batches are split/validated before execution), add a client-side check: if an incoming tool-call batch mixes local tools with anything else, or contains more than one local-tool entry, split it or reject with a clear retry-with-one-call-per-batch error before dispatch — don't let it reach the point where the runtime itself errors.

## Acceptance

- A test that sends a 2-local-tool batch and asserts it's split/rejected before any tool executes, not after a runtime error surfaces to the model.
- Live: confirm the existing "mixed and multi-local batches are not supported" error no longer appears in a fresh log.

## Comments

2026-11-19: Investigated in `hermes-agent`. This is already fully implemented, pre-existing:
`tools/tool_search.py::resolve_underlying_call` (line ~604) rejects any batch with >1 local-tool
entry (or a local+connector mix) via `tool_search_validation.local_batch_error`, BEFORE any
dispatch — this is the single choke point `model_tools.handle_function_call` calls to resolve a
`tool_call`. `tools/connectors/dispatch.py::dispatch_connector_batch` has a second, independent
check for the connector-batch wire shape. `agent/tool_call_batches.py::expand_local_tool_batches`
pre-splits an all-local multi-entry batch into separate turns before persistence (the "split" half;
`resolve_underlying_call`'s reject is the backstop for anything that reaches dispatch unsplit).

Acceptance criterion 1 (test asserting rejection before dispatch) is already covered by
`tests/tools/test_connector_bridge_wiring.py::test_resolve_multi_local_batch_echoes_first_entry_as_the_retry_shape`
— confirmed passing (`pytest tests/tools/test_connector_bridge_wiring.py tests/tools/test_connector_local_batches.py tests/tools/test_tool_search.py` → 89 passed).

No code change made — nothing to fix. Acceptance criterion 2 (confirm the error no longer appears
in a fresh prod log) is a live check, not something verifiable from a static checkout; left open as
`ready-for-human`.
