# Migrate generic domain writes and the domain-less path; token over the prepared payload

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P2
Blocked by: 07
Blocks: 11, 12
Closes: W05, W15 (generic path)

## Problem (W05)

`execute_write.py` changes the payload **after** the user confirmed it and **before** `gated_mutate_resource()` checks the token:

- `_apply_schema_mapping()` renames keys by normalized match (`schema_mapping.py:183`) and drops unmatched keys.
- `--purchase-sourced-item` adds `is_purchase_item` and `is_sales_item`.

The token, made by `confirm_token.py advisory-token` over the original payload, can then never match. The only workaround is a token over a payload the user never saw. `test_execute_write.py` mocks `gated_mutate_resource`, so no test catches this.

## Change

1. **Register ops:**
   - `<domain>.generic` for accounts, sales, procurement, inventory, hr_payroll and mis. mis keeps its empty allowlist, so it always refuses.
   - `unscoped.generic` for the domain-less path. It has no allowlist, its policy is `token+user_code`, and it uses `credential="bot"`.
2. **Move schema mapping and the purchase-sourced defaults into `prepare`**, so they run in `prepare_only` (render time) and in `run_operation` (execute time) identically.
   - The schema-mapping warnings come out of `prepare` as structured `notes`. The CLI prints them, so they still reach the user.
   - `--staged-fields` and `--confirmed-mappings` become `prepare` inputs and are covered by the token. This is right: the confirmed mapping is part of what the user approved.
3. **procurement.generic**: the KYC gate becomes a precondition and the linked Address/Contact creates become a `post`. The atomicity work in 13 lands on top of this.
4. **Rewire `mutate_resource` and `gated_mutate_resource`** to thin shims over `run_operation`, kept only for `init_bot.py` and the tests. Better still, move `init_bot.py` to a `provisioning.*` op set and delete the shims.
5. **Delete** `register_domain_token_gate`, `DOMAIN_TOKEN_GATED_ACTIONS`, `_require_advisory_token` and `advisory_token_verified`. Token policy now lives on the op.

## Acceptance

- [x] An end-to-end test with no mocking of the token check:
  1. `prepare_only` on a payload whose keys get renamed by mapping
  2. make the token from that output
  3. `run_operation` with the original raw args
  4. the call succeeds, against a mocked `_request` only
- [x] The same test with one payload value changed between render and execute is refused.
- [x] `--purchase-sourced-item` works with a token made through `prepare_only`.
- [x] All existing procurement, KYC and allowlist tests pass, ported to the new entry points.

## Comments

**Implemented 2026-10-06.**

- **One generic operation per domain, plus `unscoped.generic`.** For
  domain ops, create/update are drafts and submit/cancel/delete need the
  user code (D5). Every unscoped action needs it.
- **Schema mapping and purchase-sourced defaults run in `enrich`,** at
  render and execute alike. The CLI test round-trips render → execute
  with a key-renaming schema and the token check NOT mocked (W05).
- **Procurement KYC mapping moved into its `enrich`.**
- **`unscoped.generic` refuses three kinds of target:**
  - any doctype in a domain allowlist, which closes a Supplier-KYC
    bypass
  - any doctype a gated op owns
  - `UNSCOPED_DENY`: privilege, code-execution, outbound-destination,
    credential, audit and manufacturing doctypes (W28)
- **init_bot moved to `provisioning.create_role`/`create_doctype`.**
  These use the admin credential, have `cli=False` and `audit=False`
  (`log_role_provisioning()` stays the record), and are restricted to the
  exact Role and DocType in `doctype_defs.py`.
- **Removed:** `register_domain_token_gate`, `DOMAIN_TOKEN_GATED_ACTIONS`,
  `_require_advisory_token`, `advisory_write_token`, and the
  `advisory_token_verified` plumbing in the shims.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
