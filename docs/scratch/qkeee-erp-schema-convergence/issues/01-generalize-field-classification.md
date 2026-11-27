# Generalize field classification: managed_keys() replaces MIGRATABLE_FIELDS

Status: done
Type: task
Blocked by: none

## Change

1. `doctype_defs.py`: add `managed_keys(field_def) -> set` — every key a
   field's own definition specifies, except `fieldname`. Remove
   `MIGRATABLE_FIELDS` and its doc comment; fix the stale comment above
   `reference_name` that referenced it.
2. `init_bot.py`: add `_classify_fields(tag, name) -> (patched_fields,
   change_strings)` — for every field `doctype_defs.py` defines for
   `name`: missing live → additive (`+ <type> (new field)`); present on
   both sides with a `managed_keys()` diff → type-changed (if
   `fieldtype` differs) or property-changed (otherwise, listing the
   differing keys); no diff → skipped. Live-only fields are never
   touched or reported here.
3. Shared by both `compute_plan()` (read-only report) and the function
   that replaces `migrate_fields()` (issue 03), so dry-run and real-run
   can never disagree about what needs converging.

## Done when

- `managed_keys()` exists in `doctype_defs.py`; `MIGRATABLE_FIELDS` does not.
- `_classify_fields()` correctly tags additive / type-changed /
  property-changed / (silently) live-only, across every doctype in
  `ALL_DOCTYPES`, not just the Audit Log.

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/doctype_defs.py`
  (`managed_keys()`) and `plugins/qkeee-erp/qkeee_erp/init_bot.py`
  (`_classify_fields()`).
- `grep -rn MIGRATABLE_FIELDS plugins/qkeee-erp` → no matches outside one
  explanatory reference in `managed_keys()`'s own docstring and a test
  docstring, both describing history.
- Tests: `tests/test_init_bot.py::AuditFieldMigrationTests::
  test_plan_detects_an_additive_field`,
  `test_plan_detects_a_property_only_change`,
  `test_plan_detects_a_dynamic_link_instance` (type-changed, unchanged
  from before but now routed through the shared classifier).
