# converge_fields() replaces migrate_fields(); fold reporting into one consolidated plan

Status: done
Type: task
Blocked by: 01, 02

## Change

1. Rename `migrate_fields()` to `converge_fields()`, rebuilt on
   `_classify_fields()`: iterate every doctype in `ALL_DOCTYPES` (not
   `MIGRATABLE_FIELDS.items()`), skip ones with no changes, send the
   patched field list through the existing `provisioning.migrate_fields`
   operation (call shape unchanged), return the tagged change strings.
2. `compute_plan()`: compute `fields_to_migrate` via the same
   `_classify_fields()` helper over `ALL_DOCTYPES`, skipping doctypes
   still in `doctypes_needed` (created fresh from the current
   definition, nothing to converge).
3. `_print_bot_findings()`: add a `NOTE:` line per `fields_to_migrate`
   entry, so a real run's consolidated findings report shows the
   categorized field list too, not just a dry-run's `[dry-run] Would
   migrate field …` lines.
4. `run_real()`: call site updated to `converge_fields()`; summary dict
   key `fields_migrated` unchanged.

## Done when

- `migrate_fields` no longer exists; every call site uses
  `converge_fields`.
- An additive field on a previously-provisioned instance is actually
  sent and added through the full pipeline, not just detected.
- `_print_bot_findings()` surfaces `fields_to_migrate` on a real run.

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/init_bot.py`
  (`converge_fields()`, `compute_plan()`, `_print_bot_findings()`,
  `run_real()`).
- Tests: `tests/test_init_bot.py::AuditFieldMigrationTests::
  test_converge_fields_adds_a_missing_field_through_the_pipeline` (new —
  the exact bug scenario: a live doctype missing `domain_code` gets it
  added, every other live field rides along unchanged),
  `test_migration_patches_only_the_migratable_field_through_the_pipeline`
  (renamed call site, updated expected tagged string).
