# Domain: system-admin (Users, roles, permissions)

This is the widest-blast-radius domain here (user/role/permission changes,
destructive actions), and it carries the most business logic of any
domain module. Code: `scripts/domains/system_admin.py`
(`ALLOWED_WRITE_DOCTYPES = ("User", "Role", "Custom Field", "Property
Setter", "Webhook", "Workflow")`).

Every write is a named operation through `execute_write.py --op`, sent
with the tag's separate ADMIN credential
(`QKEEE_ERP_<TAG>_ADMIN_API_KEY`/`_ADMIN_API_SECRET`, a System Manager
account). The everyday bot credential never holds System Manager. Every
operation in this domain, `system_admin.generic` included, always needs
the confirmation — anything sent with the admin key does: render, show
the user the rendered request and code, execute with their reply
(`cli-cookbook.md`).

| Operation | Write | Args |
|---|---|---|
| `system_admin.create_user` | User create; every role must exist; elevated roles are flagged in the render | `email`, `first_name`, `roles` (exact names), optional `last_name`, `send_welcome_email` |
| `system_admin.set_user_roles` | replace a User's role list; render shows before/after; refused if roles changed since render | `name`, `roles` (complete new list), `reason` |
| `system_admin.disable_user` | User update, exactly `{"enabled": 0}` | `name`, `reason` |
| `system_admin.delete` | delete a User/Role/Custom Field/Property Setter/Webhook/Workflow | `doctype`, `name`, `reason` |
| `system_admin.create_webhook` | Webhook create; `request_url` must be https on a public host | `payload`, `reason` |
| `system_admin.toggle_workflow` | Workflow update, exactly `{"is_active": 0|1}` | `name`, `is_active`, `reason` |
| `system_admin.permission_add`/`_update`/`_remove`/`_reset` | Role Permission Manager changes; `_update`'s render shows the current value | `doctype`, `role`, `permlevel`, `ptype`/`value` (update), `reason` |
| `system_admin.generic` | Role create/update, Custom Field/Property Setter create/update — confirmed like every other operation here | `doctype`, `action`, `payload`, `name` |

`system_admin.generic` refuses everything a gated operation owns.
Webhook update and Workflow create have no write path at all, so give
UI guidance instead. Every write here runs the requester gate: the
requester must hold the permission, which in practice means System
Manager. Every write is in the Qkeee Bot Audit Log.

## When this domain applies

Provisioning or deactivating an ERPNext user, reviewing or changing who
can do what, adding a simple custom field, reviewing notifications/
integrations, checking instance health.

## Non-negotiables specific to this domain

- **Any permission/role change or destructive action must be scoped
  explicitly and confirmed — never broad or implicit.** "Give them admin
  access" must resolve to specific role names before this domain acts,
  never a blanket grant. Permission changes and destructive actions get a
  DOUBLE confirm (state the exact before/after, then ask again) — enforced
  in code: every write operation in this domain needs a rendered token
  plus the user's reply with its code. User creation (elevated roles are
  flagged in the render), role changes, and the two config writes with
  real external-facing risk (Webhook create, Workflow `is_active`
  toggle) all get the same backstop.
- **Know the token gate's limit before treating it as sufficient on its
  own.** A matching `confirmation_token` proves the call being made is
  byte-for-byte identical to what `confirm_token.py render` last printed, and that
  it happened within 15 minutes — no more. It does **not** prove a human
  read the rendered confirmation and said yes. `issued_at`/token must
  only be used after the user's own reply affirmatively confirms that
  specific rendered action, one turn later at minimum.
- **Connections must be `https://`.** `get_env_config()` refuses a
  non-`https://` base URL unless `QKEEE_ERP_<TAG>_ALLOW_INSECURE=1` is
  explicitly set — a deliberate opt-out for local/dev, never the default.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
   **Done when:** the target doctype/action is confirmed inside this
   domain's scope before any write is proposed.
2. **Permission-related reads go through the dedicated Role Permission
   Manager methods** (`get_roles_and_doctypes()`, `get_permissions()`) —
   plain `query_resource("DocPerm", ...)` fails live with a
   `PermissionError`; these are the only confirmed working read path.
   Always read-only, never gated. **Done when:** the read used
   `get_roles_and_doctypes()`/`get_permissions()`, never a raw
   `DocPerm` query.
3. **User creation** is `system_admin.create_user`. Never infer roles from
   a vague request ("give them access to procurement") — resolve to exact
   role names first (`query_resource("Role", ...)` lists what actually
   exists) and check every requested role is in that list before
   rendering (the operation also refuses a role that doesn't exist). If
   any requested role is `System Manager` or `Administrator`, the render
   lists it under `elevated_roles` — say so explicitly to the user; the
   single highest-privilege grant this domain can make. Render, confirm,
   execute. Changing an existing user's roles later is
   `system_admin.set_user_roles`, whose render shows the exact before/
   after.
   Re-fetch via `core.client.get_resource()` afterward (not
   `query_resource` — it silently drops the `roles` child table) and check
   that the `roles` table lists exactly the confirmed role names and no
   extra role slipped in. User isn't submittable — this re-fetch is the
   only checkpoint. **Done when:** the re-fetched `roles` table matches
   the confirmed role names exactly, no extra role present.
4. **Any permission change** is `system_admin.permission_add`/`_update`/
   `_remove`/`_reset` —
   and requires asking a second time after showing it. Four actions, all
   token-gated: `add` (bare new row, every right off — grants nothing by
   itself), `update` (flips ONE right on an existing row — fetch the
   row's real current value via `get_permissions()` first so the rendered
   before/after is real, not assumed), `remove` (deletes the CUSTOM
   OVERRIDE row only — **this may not fully revoke access:** if ERPNext's
   shipped/standard row for this doctype+role already grants the same
   right independent of the override, the role keeps it after remove;
   re-run `get_permissions()` after applying and verify, never assume
   "removed" means "revoked"), `reset` (wipes ALL custom overrides for the
   doctype, every role — the single most blast-radius call in this
   domain, always token-gated regardless of invocation; confirm the user
   actually means the whole doctype, not one role). Re-run
   `get_permissions()` after every action (not just `remove`) and confirm
   the resulting matrix matches the stated before/after — permission rows
   have no separate submit step, so this post-write re-fetch is the only
   checkpoint. **Done when:** the re-fetched matrix matches the stated
   before/after, for every action including `remove`.
5. **Simple DocType customization** (one Custom Field, or one Property
   Setter value change) — pass `existing_fieldnames` (query `Custom
   Field` filtered by `dt`) so a fieldname collision is caught here, not
   as an opaque create failure. Anything beyond one field/one property is
   a complex case — give step-by-step guidance instead. **Verify by
   re-querying the `Custom Field`/`Property Setter` resource directly by
   its own name — NOT `DocType/<dt>` meta**: confirmed live, a freshly
   created Custom Field does not appear in the DocType meta's `fields`
   array (server-side cache), and cache-clear isn't callable over this
   REST API even as Administrator. Trust the direct resource query, and
   confirm the persisted `dt` Link matches what was confirmed. **Done
   when:** the verification query hit the `Custom Field`/`Property
   Setter` resource directly, never `DocType/<dt>` meta.
6. **Email/notification settings review is read-only** —
   `query_resource("Notification", ...)`. **Done when:** no write call
   is attempted from inside this step.
7. **Data import/export assist is guidance-first.** `Data Import`'s schema
   is confirmed live but actual execution needs a binary file upload —
   this connector has no upload primitive, so walk the user through the
   Data Import tool in the ERPNext UI rather than attempting to drive it.
   Reviewing existing Data Import records' status is fully supported.
   **Done when:** the user has UI-level guidance in hand, or the status
   review is complete — never an attempt to drive the import itself.
8. **Integration/webhook config review** — `query_resource("Webhook",
   ...)` lists configured webhooks. Creating a new Webhook is a real
   outbound data-destination change — an attack surface, not a passive
   setting — and is `system_admin.create_webhook` (https, public host;
   private, loopback and internal host names are refused). Workflow
   `is_active` toggling is `system_admin.toggle_workflow` (body exactly
   `{"is_active": 0|1}`), since it can halt every in-flight approval on
   that document type; anything more (new states/transitions) is
   guidance only. Re-fetch and confirm the persisted fields after either
   write, same as any other domain's save-then-review discipline. **Done
   when:** the re-fetched fields are confirmed persisted, for either
   write path.
9. **System health check**: combine `get_scheduler_status()`, a
   `Scheduled Job Type` query (flag `stopped: 1` or a stale
   `last_execution`), and the most recent `Error Log` rows. **The `RQ Job`
   doctype is NOT usable via this REST API** — confirmed live 500 with a
   `TypeError` unrelated to auth/permissions. Report this gap explicitly
   and point to the fallback (Frappe desk UI's Background Jobs page, or
   `bench` CLI if the user has server access). `not_applicable` unless a
   specific numeric check is being made. **Done when:** all three signals
   are combined in the report, or the specific gap (e.g. `RQ Job`) is
   named with its fallback rather than silently omitted.
10. **Disabling/deleting a user, or deleting a Custom Field/Property
    Setter/Webhook/Workflow**, is `system_admin.disable_user` or
    `system_admin.delete`
    — and requires asking a second time after showing it. Require a
    stated `reason`. Prefer `disable_user` over `delete_user` unless the
    account must be gone entirely — disable is reversible; delete is
    confirmed to fail with `LinkExistsError` on any user who owns/created
    other records (a never-referenced user deletes cleanly). Only after
    both confirmations, execute with the printed args and token. A
    failed delete leaves no Comment behind; the audit row records it.
    **Done when:** a stated reason and both confirmations are in place
    before the operation runs.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| User creation & role assignment | New user provisioned correctly | Always confirmed; elevated roles flagged in the render |
| Permission/role matrix review | Current access visibility | Read-only, never gated |
| Role/permission change | Access grant/revoke applied | DOUBLE confirm; states exact before/after |
| Workflow configuration assist | `is_active` toggle applied, rest is guidance | Token-gated |
| DocType customization guidance | Field/property applied for simple cases | Complex cases get guidance instead |
| Email/notification settings review | Notification setup understood | Read-only |
| Data import/export assist | Guided, not executed | No file-upload primitive in this connector |
| System health check | Background jobs / error log status known | RQ Job queue depth not readable via this API — stated fallback given |
| Integration/webhook config review | Integration surface understood | Creating a webhook is token-gated |
| Destructive action | Access/config removed deliberately | DOUBLE confirm |

## Relationships

Provisioning of the audit-trail schema itself (`Qkeee Bot Audit Log`) is
`scripts/init_bot.py`'s job, run once per target environment before any
domain's writes against that tag — see `00-conventions.md`'s GRC baseline
and `scripts/init_bot.py`'s own docstring for what it provisions (the
`Qkeee Bot` Role and Audit Log DocType) and what it doesn't (bot-user
provisioning).
