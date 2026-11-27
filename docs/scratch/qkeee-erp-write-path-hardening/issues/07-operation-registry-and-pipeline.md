# Build the operation registry and the single write pipeline

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: design + task
Phase: P2
Blocked by: none (land after P0 to avoid churn)
Blocks: 08, 09, 10, 18
Closes: W03, W07, W12, W15 (structurally, once 08–10 migrate onto it)
Decision: D3 — resolved 2026-10-06: every token-gated op uses `token+user_code`

## Goal

Add one module, `core/operations.py`. It holds the `Operation` dataclass, the registry, `operation_token()` and `run_operation()`, exactly as sketched in spec.md "Target design". This issue builds the machinery and moves **no** domain onto it yet, apart from a test-only op.

## Contents

1. **`PreparedRequest`**, a frozen dataclass with these fields:
   - `method`
   - `path`
   - `name`
   - `body`
   - `doctype` (the audit and RBAC doctype)
   - `ptype`
   - `audit_action`
   - `audit_reference`
2. **`Operation`**, a frozen dataclass with these fields:
   - `key`
   - `domain`
   - `transport`
   - `doctype`, which is None for generic ops
   - `ptype_for(action)`
   - `token_policy`
   - `credential`, `"bot"` or `"admin"`, per D1
   - `prepare`
   - `preconditions`
   - `post`
   - `owns`: the set of (doctype, action) pairs this op claims, used by the ownership check
3. **Registry:** `register_operation(op)`, `get_operation(key)` and `owned_by(doctype, action)`. Registering a second op that claims the same (doctype, action) raises at import time.
4. **`operation_token(op_key, req, requested_by, issued_at)`** is `compute_token(kind="op", op=op_key, method, path, name, body, requested_by, issued_at)`. `confirmation_code()` is unchanged.
5. **`run_operation(op_key, args, ctx)`** runs steps 1 to 13 from spec.md in that exact order.
   - `ctx` is a frozen `WriteContext` with these fields:
     - `tag`
     - `mode`
     - `requested_by`
     - `session_id`
     - `domain_code`
     - `channel`
     - `channel_metadata`
     - `prompt_summary`
     - `latest_prompt`
     - `confirmation_token`
     - `issued_at`
     - `user_confirmation_text`
     - `user_approved`
     - `approval_note`
   - One object carries the context, so no wrapper can drop part of it (W12).
6. **Token check.** One function, `_verify_token(policy, expected, ctx)`, uses the `_coerce_issued_at()` helper from 14. Its order is presence, then freshness, then match, then user code. It raises the existing exception types.
7. **RBAC.** Always `_validate_prod_requester(tag, requested_by, req.doctype, req.ptype, docname=req.name, for_write=True, ...)`, from 02. The credential selects which `cfg` is used, and the reliability probe runs per credential.
8. **Audit.** `record_audit_log_start` happens before send. Any exception leads to `record_audit_log_finish("Failure")` and a re-raise. On success, `finish` runs, then `post` hooks, which are best-effort and never fail the write. The result always gets `_audit_log_status`.
9. **`prepare_only(op_key, args, ctx_without_token)`** runs steps 1 and 2 and returns `PreparedRequest` plus a human-readable render. It is used by the token CLI in 11, so render and execute hash identical bytes.

## Non-goals

- Do not delete `mutate_resource` / `gated_mutate_resource` yet. 10 rewires them.
- No CLI changes. That is 11.

## Acceptance

- [x] Unit tests use a fake op and a mocked `_request` to prove all of these:
  - the step order, by asserting the call sequence
  - a refusal at each gate, with zero HTTP calls before RBAC
  - a body changed after the token was made is refused
  - an exception during send finishes the audit row as Failure
  - `_audit_log_status` is always set
  - a duplicate `owns` claim raises at registration
- [x] `prepare_only` and `run_operation` produce identical `PreparedRequest` bytes for the same args.
- [x] Module docstring: state the invariant "no domain module may call `_request` with a mutating method outside this pipeline". 18 enforces it with a test.

## Comments

**Implemented 2026-10-06** in `scripts/core/operations.py`.

Deviations from this ticket:

- **The pipeline gained step 6b, `enrich`.** `prepare` does no I/O.
  Schema mapping and other live shaping run in `enrich`, after mode,
  requester, allowlist and ownership, and identically at render. The
  review caught the first cut doing live reads before the cheap gates,
  so even a write MIS would refuse triggered a logged DocType read first.
- **The RBAC reliability probe is not per credential.** RBAC always
  evaluates with the bot credential; the admin credential only sends.
  The probe therefore never runs under a System Manager identity.
- **New `PreparedRequest` fields.** `audit_action`, `audit_doctype` and
  `audit_reference` are validated against the Audit Log's Select values
  (see W27).
- **New `Operation` fields:**
  - `audit` — False only for init_bot provisioning
  - `refuse` — pairs a generic op refuses outright
  - `on_failure` — reports a partial outcome
  - `render_defaults` — fills live facts at render
- **Compatibility shims kept.** `client.mutate_resource()` (now
  requiring `domain=`), `client.gated_mutate_resource()` and each
  domain's `mutate()` are shims via `call_generic()`. An invariant test
  forbids domain modules from calling them, `_do_mutate`, or a non-GET
  `_request`.
- **Test conftest.** It aliases bare and package imports to one module
  each. Before, `client` and `core.client` were two separate modules in
  the test process, so patches on one were invisible to the other.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
