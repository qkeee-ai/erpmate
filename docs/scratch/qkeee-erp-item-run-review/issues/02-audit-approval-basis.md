# Audit Log: record whether an approval was verified or agent-asserted

Status: resolved
Type: task
Blocked by: none
Findings: F3
Decision: D1 (spec.md)

## Problem

`erp_execute_write` takes `user_approved` from the LLM. `run_operation` logs `user_approved = verified or ctx.user_approved` (`qkeee_erp/core/operations.py`). The `Qkeee Bot Audit Log` row then says "Approved" for two different things:

- a token + user code that the gate verified;
- an agent that set `user_approved: true` on a no-token draft.

In the reviewed run the token was verified, so the row was correct. On a `<domain>.generic` create/update (no token) the row is only the agent's word, and a reviewer cannot tell which one it is.

## Change

- `run_operation` works out the approval basis: `token+code`, `token`, `agent-asserted` or `none`.
- `_send` prefixes `approval_note` with `[approval: <basis>]` after the `[<op>]` tag. The compensating delete keeps its own note.
- `erp_execute_write` tool schema: `user_approved` description says "only after the user confirmed this exact payload in a reply; logged as agent-asserted".
- `00-conventions.md` and `domains/grc-audit.md`: describe the basis tag.

No Audit Log schema change (no new Select option), so Instances need no `init-bot` rerun.

## Acceptance

- Tests: token+code op → note has `[approval: token+code]`; no-token op with `user_approved=True` → `[approval: agent-asserted]`; without it → `[approval: none]`.
- Existing tests stay green.

## Comments

- Implemented 2026-10-14: `operations._approval_basis()`; `_send(approval_basis=…)` prefixes `approval_note`; `run_operation` passes it. Tool schema describes `user_approved`. Docs: `00-conventions.md`, `grc-audit.md`, `qkeee-erp:usage`. Tests: `ApprovalBasisTests` (3) in `test_operations.py`. Compensating delete unchanged (no basis tag).
