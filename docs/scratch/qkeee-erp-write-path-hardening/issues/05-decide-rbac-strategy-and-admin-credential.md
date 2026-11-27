# Decide: RBAC strategy and the system-admin credential

Status: resolved
Type: grilling
Phase: P1
Blocked by: none
Blocks: 08
Closes: W22 (decision only)
Decision: D1

## The tension

1. Creating a User, assigning roles and changing DocPerms in stock ERPNext need **System Manager** on the calling account, which is the bot.
2. The connector's own rule (`_BOT_FORBIDDEN_ROLES`, `verify_rbac_precheck_reliable()`) is that the bot must **not** hold System Manager. If it does, the per-requester pre-check is marked unreliable on every tag, for every domain.
3. Memory from 2026-09-01, confirmed live on DEMO_ERP v16: `frappe.client.has_permission` has **no `user=` parameter at all**. It always answers for the calling session. So the "reliable" branch of `_validate_prod_requester()` never truly checks the requester; it checks the bot.
4. The current code's unreliable-branch fallback is `_requester_has_role_permission()`. It mirrors the requester's roles against DocPerm rows on the client. The 2026-09-01 decision ("Option D") rejected exactly that client-side mirroring, and allowed domain-scoped writes to proceed on a warning instead. The code has since moved to "fail closed for everything unless the local mirror confirms." Code and recorded decision disagree.

Net effect today: either system_admin cannot work, because the bot lacks System Manager, or the bot holds System Manager and the pre-check is degraded for all domains.

## Options

| Option | Summary | Cost |
|---|---|---|
| A. Separate admin credential (recommended) | A second key pair, `QKEEE_ERP_<TAG>_ADMIN_API_KEY/SECRET`, held by a System Manager account. Only system_admin ops in the registry (07) can select it. The business bot stays narrow. The pre-check reliability probe runs per credential. | Small: env vars and a `credential="admin"` field on `Operation`. Two accounts to rotate. |
| B. Single privileged bot | Accept that System Manager is on the bot. Rely only on the local role/DocPerm mirror for every domain. | Weakens every domain. Contradicts the memory decision. |
| C. Custom server method | A whitelisted method on each instance that runs `frappe.set_user(requested_by)` + `frappe.has_permission()`. | Real per-requester RBAC. A per-instance deploy surface, previously rejected. |
| D. Per-user ERPNext auth | Each requester's own token. | Structurally correct. Too large for now. |

## Questions to settle

1. Option A, B, C or D?
2. Keep the local role/DocPerm mirror as the fallback, or drop it in favour of Option D's "domain allowlist + token proceeds with a warning"? Update the memory file either way. One of them is stale.
3. Should the admin credential require the user confirmation code (D3) on **every** use, including non-elevated User creation?
4. Should the system_admin ops need the requester to hold System Manager themselves? This is checked by the mirror or by Option C.

## Answer

Resolved 2026-10-06 by nikhil.sharma@qkeee.ai, who accepted the recommended options.

1. **Option A: a separate admin credential.**
   - The env vars are `QKEEE_ERP_<TAG>_ADMIN_API_KEY` and `QKEEE_ERP_<TAG>_ADMIN_API_SECRET`. The base URL is shared with the tag.
   - Only ops declared with `credential="admin"` can select it. Those are the system_admin ops in 08.
   - The business bot keeps no System Manager.
   - `verify_rbac_precheck_reliable()` is cached per (tag, credential).
   - Build: in 07 (the `credential` field on `Operation`, and `get_env_config(tag, credential=...)`) and in 08.
2. **Fallback: keep the current fail-closed local role/DocPerm check.**
   - This default was applied because the question had no explicit recommendation. Override it if you disagree.
   - Reason: `has_permission` ignores `user=`, so without the local check nothing verifies the requester at all.
   - Option D's "proceed on a warning" is not restored.
   - The memory note has been updated to mark Option D as superseded.
3. **Yes.** D3 makes `token+user_code` mandatory for every token-gated op, including non-elevated User creation.
4. **Yes.** After 02, User and Role writes run the requester check. A system_admin write therefore needs the requester to hold the matching permission, which in practice means System Manager. The check is the local role/DocPerm fallback, because the admin credential holds System Manager and so is never "reliable".

## Comments
