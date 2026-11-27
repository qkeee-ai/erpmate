# Block bespoke-gated operations from the plain domain mutate()

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P0
Blocked by: none
Closes: W01 (interim; the structural fix is the ownership step in 07)

## Problem

`system_admin.mutate()` and `fixed_assets.mutate()` call `mutate_resource(domain=...)` directly, and neither domain registers a token gate. `execute_write.py --domain system_admin` can therefore do all of these with no token:

- create a User with `roles: [{"role": "System Manager"}]`, which bypasses `create_user`'s elevated-role token
- update a User's `enabled` or `roles`, which bypasses `destructive_mutate`
- delete a User, Role, Custom Field, Property Setter, Webhook or Workflow, which bypasses `destructive_mutate`
- create a Webhook or update a Workflow, which bypasses `gated_config_mutate`

`execute_write.py --domain fixed_assets` can submit or cancel an Asset. That skips the concurrency-checked submit and any confirmation.

## Change

Add a module-level `OWNED_ELSEWHERE` map in each of the two domains. Each `mutate()` refuses a matching call before any I/O. The error names the gated function to use instead.

| Domain | doctype | action | Refusal points to |
|---|---|---|---|
| system_admin | User | create, update, delete | `create_user` / `destructive_mutate` |
| system_admin | Role | delete | `destructive_mutate` |
| system_admin | Custom Field, Property Setter | delete | `destructive_mutate` |
| system_admin | Webhook | create, delete | `gated_config_mutate` / `destructive_mutate` |
| system_admin | Workflow | update, delete | `gated_config_mutate` / `destructive_mutate` |
| fixed_assets | Asset | submit, cancel | token-gated path (see below) |

For fixed_assets also call `core_client.register_domain_token_gate("fixed_assets", {"submit", "cancel", "delete"})`. That gives Asset, Asset Movement and Asset Repair submit, cancel and delete the generic advisory token now. Keep the Asset submit and cancel refusal in `mutate()` only if 09 lands before this issue. Otherwise the token gate is the interim control, so drop that row.

The bespoke wrappers call `mutate_resource` directly, not `mutate()`, so they are unaffected.

## Acceptance

- [x] Each row in the table raises `DoctypeNotAllowedError` from the domain `mutate()`. It is raised before `_validate_prod_requester` and before any HTTP call, and the tests prove this with a mocked `_request` that is never called.
- [x] `execute_write.py --domain system_admin --doctype User --action create` exits non-zero with the redirect message.
- [x] Role create and update, and Custom Field and Property Setter create and update, still work through `mutate()`.
- [x] Asset Movement submit through `execute_write.py --domain fixed_assets` without a token is refused.
- [x] Existing tests are green.

## Comments

**Implemented 2026-10-06.**

- `system_admin.OWNED_ELSEWHERE` covers the table above, and adds two rows the table missed:
  - Webhook update, which could repoint an existing webhook's URL
  - Workflow create
  Pairs with no gated path (Webhook update, Workflow create) are refused with "give UI guidance instead".
- `system_admin.mutate()` refuses before any I/O.
- fixed_assets now registers `{"submit", "cancel", "delete"}` with `register_domain_token_gate`, so the Asset refusal row was dropped (the interim option in this ticket).
- `mutate_resource_with_concurrency()` now accepts and passes through `confirmation_token`/`issued_at` and the channel and prompt context. Without that it would have been unusable for a submit.
- Checked via the CLI: `execute_write.py --domain system_admin --doctype User --action create` and `--domain fixed_assets --doctype Asset --action submit` (no token) both exit 1 with a clean `ERROR:`.
- Tests are in `scripts/domains/test_p0_hardening.py`. `test_allowlist_gates.py`'s system_admin probe was switched from User to Role.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Note: since W30, Role and Custom Field/Property Setter create/update through `system_admin.generic` also need the rendered confirmation. Suite: 305 passed, 861 subtests.
