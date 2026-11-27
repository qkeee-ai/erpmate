# Apply requester validation to User and Role writes

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P0
Blocked by: none
Closes: W02

## Problem

`PROD_GATE_EXEMPT_DOCTYPES` in `core/client.py:208` contains `User` and `Role`. `_validate_prod_requester()` returns at once for these doctypes (`:755`). As a result, a write to User or Role checks:

- no permission
- not even whether `requested_by` is a real ERPNext User

Any non-empty string passes. The exemption exists for two reasons. One is to avoid recursion, because the requester-existence check reads User. The other is that init_bot and system_admin were assumed to have their own controls. Assumption two is false (see 01).

## Change

1. Split the exemption by intent:
   - `_READ_RECURSION_EXEMPT` keeps `User`, `DocType`, `Role`, the audit log and `Comment`. It applies only to reads, and only when `internal=True` or the call is the existence check itself.
   - Writes have no exemption except `AUDIT_LOG_DOCTYPE` and `Comment`, which are the connector's own bookkeeping.
2. Give `_validate_prod_requester()` a `for_write: bool` argument and pass it from `mutate_resource()`. When `for_write=True`, skip the exemption for User and Role.
3. Check that the `resource_exists(tag, "User", requested_by)` read path does not re-enter the gate. It must stay on the internal/exempt read route.
4. Handle `init_bot.py`. It calls `mutate_resource(tag, "Role", "create", ...)` and `mutate_resource(tag, "DocType", "create", ...)` with no domain (`init_bot.py:140`, `:154`). After this change, its requester must hold `create` on Role, which is correct for a provisioning run by an admin.
   - DocType stays exempt for now, because it is only written by init_bot. Record that exemption explicitly as `{"DocType"}` in a provisioning-only set, with a comment.
   - Update the `init_bot.py` docstring and tests: the provisioning requester must be a System Manager user.

## Acceptance

- [x] `mutate_resource(tag, "User", "create", ..., requested_by="not-a-user@x")` raises `UnvalidatedProdRequesterError`.
- [x] A User create by a real requester who lacks `create` on User, either by the RPC or by the local fallback, is refused.
- [x] A User read during the requester-existence check does not recurse. A test asserts the call count.
- [x] `test_init_bot.py` is green, or the provisioning path is updated and tested.
- [x] Note for 05: with this fix, User writes need a requester holding System Manager. That is correct, and it is why D1 matters.

## Comments

**Implemented 2026-10-06.**

- `WRITE_GATE_EXEMPT_DOCTYPES = {AUDIT_LOG_DOCTYPE, "Comment"}`.
- `_validate_prod_requester(..., for_write=False)` picks between that set and the read set. `mutate_resource()` passes `for_write=True`.
- **Deviation from step 4:** DocType is *not* kept exempt for writes. Keeping it would have let a domain-less `gated_mutate_resource` DocType write skip the permission check. init_bot's provisioning requester holds System Manager, which passes, so nothing breaks.
- The `init_bot.py` docstring now states the requirement. Its tests mock `ensure_role`/`ensure_doctype` and pass unchanged.
- One existing test assertion in `core/test_client.py` was updated for the new `for_write=True` argument.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
