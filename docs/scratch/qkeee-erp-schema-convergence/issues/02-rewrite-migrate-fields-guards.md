# Rewrite the migrate_fields write-gate for the generalized managed-keys check

Status: done
Type: task
Blocked by: 01

## Change

1. `_provision_migrate_prepare` (pure, no I/O): drop the `name in
   MIGRATABLE_FIELDS` check for `name in {d["name"] for d in
   ALL_DOCTYPES}`. For every field `doctype_defs.py` defines for that
   doctype, require the sent body's value at every `managed_keys()` key
   to equal the definition's value exactly — generalized from the old
   fieldtype-only check. This is what lets an additive field through:
   it's just another managed field, pinned to the definition.
2. `_check_live_only_fields_unchanged` (precondition, I/O): keep the
   membership invariant (nothing added/dropped outside shipped ∪ live).
   Broaden the live-only-field check from `(fieldtype, options)` to full-
   dict equality. Add the missing half: for a field present on both
   sides, every key the field's own definition does NOT manage (idx,
   permlevel, Frappe's internal `name`, …) must also equal its live
   value — this is what keeps "only managed_keys() may ever move" true
   end to end, not just on the pure-prepare side.

## Done when

- A body missing a shipped field is still refused (additive fields must
  be *sent*, not merely tolerated as absent).
- A body that changes an unmanaged key on a shipped field is refused —
  new expressive power the old guard couldn't check (it only ever
  compared `fieldtype`/`options`).
- A live-only field with any property drifted (not just type) is still
  refused.
- `Qkeee Bot Job Approval` is now a valid `doctype_name`, not just the
  Audit Log; an unrelated doctype (e.g. `User`) still refuses.

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/init_bot.py`
  (`_provision_migrate_prepare`, `_check_live_only_fields_unchanged`).
- Tests: `tests/test_init_bot.py::AuditFieldMigrationTests::
  test_migrate_operation_refuses_anything_but_the_definition` (missing
  field, wrong type, wrong doctype — unchanged behavior, now routed
  through the generalized check),
  `test_migrate_operation_accepts_any_all_doctypes_member` (Job Approval
  now accepted),
  `test_migration_refuses_an_unmanaged_key_changed_on_a_shipped_field`
  (new — `idx` tampering on `session` refused),
  `test_migration_keeps_a_live_only_legacy_field_unchanged` (extended
  with a "relabeled legacy" subtest — a non-type property change on a
  live-only field is refused by the broadened full-dict check).
