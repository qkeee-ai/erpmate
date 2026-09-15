# Domain: system-admin (Users, roles, permissions)

This is the widest-blast-radius domain here (user/role/permission changes,
destructive actions), and it carries the most business logic of any
domain module. Code: `scripts/domains/system_admin.py`
(`ALLOWED_WRITE_DOCTYPES = ("User", "Role", "Custom Field", "Property
Setter", "Webhook", "Workflow")`), which also carries
`_record_attribution_comment()`, `destructive_mutate()`,
`get_roles_and_doctypes()`, `get_permissions()`,
`call_permission_manager()`, `create_user()`, `gated_config_mutate()`, and
`get_scheduler_status()`.

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
  in code via the matching `confirmation_token`/`issued_at` gate in
  `call_permission_manager()`/`destructive_mutate()`. Creating a user with
  an elevated role (System Manager/Administrator) and the two config
  writes with real external-facing risk (Webhook create, Workflow
  `is_active` toggle) get the same token+freshness backstop via
  `create_user()`/`gated_config_mutate()`.
- **Know the token gate's limit before treating it as sufficient on its
  own.** A matching `confirmation_token` proves the call being made is
  byte-for-byte identical to what a render script last printed, and that
  it happened within 15 minutes — no more. It does **not** prove a human
  read the rendered confirmation and said yes. `issued_at`/token must
  only be used after the user's own reply affirmatively confirms that
  specific rendered action, one turn later at minimum.
- **Connections must be `https://`.** `get_env_config()` refuses a
  non-`https://` base URL unless `QKEEE_ERP_<TAG>_ALLOW_INSECURE=1` is
  explicitly set — a deliberate opt-out for local/dev, never the default.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
2. **Permission-related reads go through the dedicated Role Permission
   Manager methods** (`get_roles_and_doctypes()`, `get_permissions()`) —
   plain `query_resource("DocPerm", ...)` fails live with a
   `PermissionError`; these are the only confirmed working read path.
   Always read-only, never gated.
3. **User creation** goes through `create_user()`. Never infer roles from
   a vague request ("give them access to procurement") — resolve to exact
   role names first (`query_resource("Role", ...)` lists what actually
   exists), pass them as `existing_roles` so a typo surfaces here instead
   of as an opaque ERPNext error. If any requested role is `System
   Manager` or `Administrator`, the draft is blocked until
   `elevated_roles_acknowledged: true` is set AND a matching elevated-role
   `confirmation_token`/`issued_at` is supplied — the single
   highest-privilege action this domain can take gets the same code-level
   backstop as a permission change. Present, confirm, then
   `create_user(..., elevated_confirmation_token=..., issued_at=...)`.
   Re-fetch via `core.client.get_resource()` afterward (not
   `query_resource` — it silently drops the `roles` child table) and check
   that the `roles` table lists exactly the confirmed role names and no
   extra role slipped in. User isn't submittable — this re-fetch is the
   only checkpoint.
4. **Any permission change** goes through `call_permission_manager()` —
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
   checkpoint.
5. **Simple DocType customization** (one Custom Field, or one Property
   Setter value change) — pass `existing_fieldnames` (query `Custom
   Field` filtered by `dt`) so a fieldname collision is caught here, not
   as an opaque create failure. Anything beyond one field/one property is
   a complex case — give step-by-step guidance instead. **Verify by
   re-querying the `Custom Field`/`Property Setter` resource directly by
   its own name — NOT `DocType/<dt>` meta**: a freshly created Custom
   Field does not appear in the DocType meta's `fields` array (server-side
   cache), and cache-clear isn't callable over this REST API even as
   Administrator. Trust the direct resource query, and confirm the
   persisted `dt` Link matches what was confirmed.
6. **Email/notification settings review is read-only** —
   `query_resource("Notification", ...)`.
7. **Data import/export assist is guidance-first.** `Data Import`'s schema
   is confirmed live but actual execution needs a binary file upload —
   this connector has no upload primitive, so walk the user through the
   Data Import tool in the ERPNext UI rather than attempting to drive it.
   Reviewing existing Data Import records' status is fully supported.
8. **Integration/webhook config review** — `query_resource("Webhook",
   ...)` lists configured webhooks. Creating a new Webhook is a real
   outbound data-destination change — an attack surface, not a passive
   setting — and goes through `gated_config_mutate(kind="create_webhook")`.
   Workflow `is_active` toggling goes through the same path
   (`kind="toggle_workflow"`), since it can halt every in-flight approval
   on that document type; anything more (new states/transitions) is
   guidance only. Re-fetch and confirm the persisted fields after either
   write, same as any other domain's save-then-review discipline.
9. **System health check**: combine `get_scheduler_status()`, a
   `Scheduled Job Type` query (flag `stopped: 1` or a stale
   `last_execution`), and the most recent `Error Log` rows. **The `RQ Job`
   doctype is NOT usable via this REST API** — it 500s with a `TypeError`
   unrelated to auth/permissions. Report this gap explicitly and point to
   the fallback (Frappe desk UI's Background Jobs page, or `bench` CLI if
   the user has server access). `not_applicable` unless a specific
   numeric check is being made.
10. **Disabling/deleting a user, or deleting a Custom Field/Property
    Setter/Webhook/Workflow**, always goes through `destructive_mutate()`
    — and requires asking a second time after showing it. Require a
    stated `reason`. Prefer `disable_user` over `delete_user` unless the
    account must be gone entirely — disable is reversible; delete fails
    with `LinkExistsError` on any user who owns/created other records (a
    never-referenced user deletes cleanly). Only after both confirmations,
    call `destructive_mutate()` with the printed token.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| User creation & role assignment | New user provisioned correctly | Elevated role needs a fresh, matching confirmation_token |
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
