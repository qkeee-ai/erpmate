# Migrate fixed_assets writes onto the pipeline

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P2
Blocked by: 07, 06
Closes: W01 (fixed_assets part), W06, W07, W10 (Asset part), W18, W23, W24, W25, W26 (see 06's Answer for the v16 signatures)

## Ops to register

Finalize the body keys from 06's answer.

| Op key | Transport | Doctype / ptype | Token policy | owns | Notes |
|---|---|---|---|---|---|
| `fixed_assets.depreciation_run` | rpc | Asset / submit (or Journal Entry / create, per 06) | `token+user_code` | — | `prepare` builds the body from the schedule name. The render shows pending rows and the book value from `finance_books[N]`. |
| `fixed_assets.scrap` | rpc | Asset / write | `token+user_code` | — | The render shows the current book value as the write-off |
| `fixed_assets.restore` | rpc | Asset / write | `token+user_code` | — | Now gated, and audited as a GL-reversing action (W06) |
| `fixed_assets.sell` | per 06 | per 06 | `token+user_code` | — | If `make_sales_invoice` is a mapper, the op becomes "map, then create a draft Sales Invoice". Decide whether that draft create belongs to accounts' allowlist or this op. |
| `fixed_assets.submit` / `.cancel` | resource | Asset, Asset Movement, Asset Repair / submit, cancel | `token` | those pairs | Precondition: the concurrency check (`expected_modified` must equal the live `modified`) is **mandatory**, no longer opt-in. The token covers `expected_modified`. |
| `fixed_assets.generic` | resource | from args | `none` for create/update | the rest | |

## Other changes

- The audit reference and Comment target come from `PreparedRequest.audit_reference`, which each op's `prepare` sets correctly. Nothing reads `body["asset_name"]` blindly anymore.
- Delete `call_whitelisted_method`, `_expected_token`, `depreciation_run_token`, `disposal_token`, `_compute_token` and `mutate_resource_with_concurrency`.
- `fixed_assets.mutate()` becomes `run_operation("fixed_assets.generic", ...)`.
- Drop the interim `register_domain_token_gate` from 01 once the submit and cancel ops own those pairs.

## Acceptance

- [x] The matrix in 18 covers every op.
- [x] An Asset submit where `modified` has moved since render is refused.
- [x] Restore without a token is refused.
- [x] Each audit row's `reference_name` is the Asset name, for all four RPCs.

## Comments

**Implemented 2026-10-06.**

Deviations:

- **No separate `fixed_assets.submit`/`.cancel` operations.** The
  generic operation's concurrency check from 12 covers Asset, Asset
  Movement and Asset Repair submit/cancel/delete, so the concurrency
  check now applies everywhere, not only to assets.
- **`fixed_assets.sell` creates a DRAFT Sales Invoice,** checked against
  the accounts allowlist (W23). The mapping comes from the gated read
  `map_sales_invoice()`, which strips server-generated keys.

Other changes:

- **Depreciation.** The body is `depr_schedule_name` plus `date` only;
  unknown args, including the slice indices, are refused (W24). The
  render computes the due rows and their total. A precondition checks the
  schedule's asset. `on_failure` reports which rows carry a JE (W26); a
  timeout stays a `TransportTimeoutError` with that detail.
- **Scrap.** `scrap_date` is required and sent (W25). The render fills
  `book_value` from `finance_books[0]`.
- **Removed:** `call_whitelisted_method`, `_expected_token`, the token
  constructors, `mutate_resource_with_concurrency`, and the interim
  `register_domain_token_gate`.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
