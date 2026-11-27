# Migrate system_admin writes onto the pipeline

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P2
Blocked by: 07, 05
Closes: W01 (system_admin part), W07, W16, W17, W20 (code side)

## Ops to register

The credential depends on D1. "admin" assumes option A.

| Op key | Transport | Doctype / ptype | Token policy | owns | Notes |
|---|---|---|---|---|---|
| `system_admin.create_user` | resource POST | User / create | `token+user_code` always (D3); elevated roles add a `elevated=true` fact to the render | (User, create) | Validate every role exists (live Role query) as a precondition. This replaces the docs-only `existing_roles`. |
| `system_admin.disable_user` | resource PUT | User / write | `token+user_code` | (User, update) | Body is fixed to `{"enabled": 0}`; `reason` required |
| `system_admin.set_user_roles` | resource PUT | User / write | `token+user_code` | — (shares (User, update) through a sub-key; see below) | New op. Role changes were previously only reachable through the bypass. Decide in triage whether to ship it or refuse role edits entirely. |
| `system_admin.delete` | resource DELETE | target doctype / delete | `token+user_code` | (User, Role, Custom Field, Property Setter, Webhook, Workflow) × delete | `reason` required |
| `system_admin.create_webhook` | resource POST | Webhook / create | `token+user_code` | (Webhook, create) | Precondition: `request_url` is https and not a private or loopback host (an SSRF guard) |
| `system_admin.toggle_workflow` | resource PUT | Workflow / write | `token+user_code` | (Workflow, update) | Body fixed to `{"is_active": 0|1}` |
| `system_admin.permission_add/update/remove/reset` | rpc POST | Custom DocPerm / write (decide in triage; `DocType` is the fallback) | `token+user_code` | — | `get_*` are no longer accepted here (W17) |
| `system_admin.generic` | resource | from args | `none` for create/update | everything not owned above | Role and Custom Field/Property Setter create and update |

On a shared (doctype, action) pair, such as User update for both disable and set-roles, `owns` claims the pair once, for `disable_user`. `set_user_roles` registers as a distinct key with an explicit `owns_override`. Alternatively, fold it into one `update_user` op whose body is validated to be exactly `{"enabled":0}` or exactly `{"roles":[...]}`. Pick one approach in implementation and document it.

## Other changes

- The attribution Comment for deletes moves to `post` and is posted **after** a successful delete. That is not possible on the record itself once deleted, so post it on the audit log row instead, or skip it, since the audit row is the record (W16). On a failed delete, no "deleted" Comment is posted.
- Read helpers (`get_roles_and_doctypes`, `get_permissions`, `get_scheduler_status`) are handled in 15.
- Delete `destructive_mutate`, `create_user`, `gated_config_mutate`, `call_permission_manager`, `_compute_token` and every `*_token()` constructor. Their logic now lives in op `prepare`/`preconditions`. Keep thin deprecated shims only if something outside `scripts/` imports them. Grep the whole erpnext-hermes repo for importers first.
- `system_admin.mutate()` becomes `run_operation("system_admin.generic", ...)`, so the ownership check replaces 01's interim `OWNED_ELSEWHERE`.

## Acceptance

- [x] 18's table-driven matrix covers every op above.
- [x] `execute_write.py --op system_admin.generic --doctype User --action create` is refused with "owned by system_admin.create_user".
- [x] A webhook with an `http://` or `http://10.x` URL is refused.
- [x] A failed User delete leaves no "deleted" Comment.

## Comments

**Implemented 2026-10-06.** The operations are listed in
`system_admin.py`'s docstring and in `references/domains/system-admin.md`.

- **`set_user_roles` shipped** as its own operation, gated like
  `create_user`. Its render shows roles before/after and flags any added
  elevated roles, and it is refused if the roles changed since render.
  It does not claim `(User, update)`, which `disable_user` owns.
- **`system_admin.generic` also uses the admin credential.** Role and
  Custom Field writes need System Manager. Found in review.
- **Role existence is checked with the admin credential**, because Role
  is System-Manager-readable only.
- **Reads go through `read_rpc` with `credential="admin"`.** These are
  `get_roles_and_doctypes`, `get_permissions` and `get_scheduler_status`.
- **Permission operations audit against `reference_doctype=DocType`,
  `reference_name=<target doctype>`.** That is a real record, so the
  Dynamic Link validates. The old synthetic "role@permlevel" name, and
  the action values "Permission Add" etc., would have made the audit
  insert fail silently (W27).
- **Delete posts no Comment** (W16), and `get_*` are not operations (W17).
- **Removed:** `destructive_mutate`, `create_user`, `gated_config_mutate`,
  `call_permission_manager`, all token constructors, and
  `OWNED_ELSEWHERE`. Nothing outside `scripts/` imported them.

**Follow-up 2026-10-06 (W30, from the admin-credential review):**
`system_admin.generic` now requires the rendered confirmation and the
user's reply code for EVERY action, drafts included. It sends with the
admin key, and it was the only admin-key path without a token.

- New tests: every admin-credential CLI operation must need confirmation
  for every action, and an unconfirmed Role create is refused before RBAC
  and before any send.
- `--list-ops` now derives each operation's confirmation rule from its
  policy.
- Updated `system-admin.md` and `cli-cookbook.md`.
- Suite: 293 passed, 766 subtests.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
