# Table-driven per-op gate test matrix + "no write outside the pipeline" invariant

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P4 (starts with 07; grows with each migration; final sweep after 11)
Blocked by: 07
Closes: W21

## Matrix

One parametrized test module, `core/test_operations_matrix.py`, iterates the **live registry**, so a new op is covered automatically. For each op it supplies a minimal valid `args` fixture, declared next to the op as `example_args`, and asserts each refusal case with **zero** HTTP calls before the refusing gate:

| Case | Expected |
|---|---|
| mode `read-only` | `ReadOnlyModeError` |
| no `requested_by` | `MissingRequesterError` |
| doctype outside the domain allowlist (generic ops) | `DoctypeNotAllowedError` |
| (doctype, action) owned by another op (generic ops) | `DoctypeNotAllowedError` naming the owner |
| token policy ≠ none: missing token, stale `issued_at`, future `issued_at`, non-int `issued_at`, wrong token, body mutated after render, wrong or missing user code | the matching refusal |
| RBAC denies | `UnvalidatedProdRequesterError` |
| transport raises (`HTTPError`, `TimeoutError`) | `ConnectorError`, audit finished as Failure |
| happy path | result has `_audit_log_status`, and the audit start carries every ctx field |

## Invariants

- **AST/grep test:** no module under `domains/` calls `_request` with `"POST"`, `"PUT"` or `"DELETE"`, nor calls `mutate_resource` directly.
- **Every public function in `domains/*.py`** is either a read helper routed through the read gate (15), or a thin call into `run_operation`.
- **`execute_write.py --list-ops`** matches `operations.REGISTRY` exactly.

## Acceptance

- [x] The matrix runs for every registered op and is green.
- [x] A deliberately added rogue `_request(cfg, "POST", ...)` in a domain module fails the invariant test. Verify locally, then revert.
- [x] `python -m pytest scripts` is green.

## Comments

**Implemented 2026-10-06** (pulled forward from P4).

- `domains/test_operations.py` drives every CLI operation through these
  cases:
  - happy path
  - read-only mode
  - missing requester
  - no, stale, future, malformed or tampered token
  - missing or wrong user code
  - token for another requester
  - RBAC denial
  - transport failure
- The same file covers pipeline order, registry rules, ownership,
  unscoped deny, per-operation rules, gated reads, audit masking, and
  the invariant AST test.
- `test_every_cli_operation_has_an_example` fails if a new operation has
  no example.
- **Mutation check:** five injected bugs were each caught, failing 3 to
  156 tests:
  - token ignores the body
  - ownership check skipped
  - RBAC before token
  - User audit-exempt again
  - user code not checked
- **Suite:** 291 passed, 702 subtests (up from 256 after P0).

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
