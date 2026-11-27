# Bind the bespoke tokens to the request actually sent (incl. restore_asset)

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P0
Blocked by: none
Closes: W03, W06 (interim token part; the RBAC part lands in 09)

## Problem

Each token below is checked over facts that do not fully describe what gets sent:

| Function | Token covers | Sent but not covered |
|---|---|---|
| `system_admin.destructive_mutate` | action, doctype, name, reason | `payload`. A "disable user" token can carry `{"roles": [...System Manager]}`. |
| `system_admin.gated_config_mutate` | kind, doctype, identifier, reason | `payload`, `name`. `kind` is not tied to `doctype`, so a `toggle_workflow` token can update a User. `identifier` is never compared with the payload's `request_url` or `document_type`. |
| `fixed_assets.call_whitelisted_method` | `token_facts` | `body`. A user can be shown asset A while asset B is scrapped. |
| `fixed_assets` `restore_asset` | nothing | everything. It is not in `TOKEN_REQUIRED_METHODS`, yet it cancels the scrap Journal Entry, which changes the General Ledger. |

## Change

Keep this as a minimal patch on the current functions. 07 replaces all of it with `operation_token()`.

1. **destructive_mutate**
   - Fold `payload or {}` into `destructive_action_token`.
   - For `update`, reject any payload other than exactly `{"enabled": 0}`. "Disable" is the only update this function exists for.
2. **gated_config_mutate**
   - Add `KIND_DOCTYPE = {"create_webhook": ("Webhook", "create"), "toggle_workflow": ("Workflow", "update")}` and refuse any mismatch.
   - For `toggle_workflow`, the payload must be exactly `{"is_active": 0|1}`.
   - For `create_webhook`, require `payload["request_url"] == identifier`.
   - Fold `name` and `payload` into `config_change_token`.
3. **call_whitelisted_method**
   - Derive the identifying facts from `body`, not from caller-supplied `token_facts`.
   - Keep only the display-only facts in `token_facts`: amounts and dates the user saw. Fold the whole `body` into the token as well.
   - Refuse when `token_facts["asset"]` is present and differs from the asset key in `body`.
4. **restore_asset**
   - Add it to `TOKEN_REQUIRED_METHODS` with a new `restore_token(asset, issued_at)` that also covers `body`.
   - Log `user_approved=True` only when a token was verified.
5. **Shared coercion.** Guard `int(issued_at)` everywhere: a non-integer raises `ConnectorError`, not `ValueError`.

## Acceptance

- [x] Tests: for each function, a token made for request R is refused for R with one changed field, covering payload, name, body asset and kind/doctype.
- [x] A `destructive_mutate` update with any payload other than `{"enabled": 0}` is refused before I/O.
- [x] `restore_asset` without a token is refused.
- [x] A malformed `issued_at` gives a clean `ConnectorError`.
- [x] `references/domains/system-admin.md` and `fixed-assets.md` are updated: token inputs, restore_asset now gated, and the "recovery action" wording removed.

## Comments

**Implemented 2026-10-06.**

- `destructive_action_token(..., payload=)`. The disable payload is fixed to `{"enabled": 0}`, which is also the default when omitted.
- `config_change_token(..., name=, payload=)` and `KIND_DOCTYPE_ACTION`. The webhook URL must equal the identifier, and the workflow payload must be exactly `{"is_active": 0|1}` with `name` required.
- Every fixed-asset token constructor takes `body=` and folds it in. `restore_token()` was added and `restore_asset` is now in `TOKEN_REQUIRED_METHODS`.
- A shown `asset` that differs from the asset in the body is refused. The body asset is read from `asset_name` or `asset`.
- In `call_whitelisted_method`, freshness is now checked before the match, consistent with the other gates.
- All `int(issued_at)` calls go through `core.client.coerce_issued_at()`.
- **Added after 06:** interim body rules in `call_whitelisted_method` for W24 and W25:
  - refuse `sch_start_idx`/`sch_end_idx` on `make_depreciation_entry`
  - require `scrap_date`, and require it to equal the shown `disposal_date`, on `scrap_asset`
- Docs updated: `references/domains/system-admin.md` and `fixed-assets.md`.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests; note: superseded — the functions were replaced by operations in 07–09; covered by domains/test_operations.py's matrix (tampered-args, kind/doctype, body, restore token). Suite: 305 passed, 861 subtests.
