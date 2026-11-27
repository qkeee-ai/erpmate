# Test coverage for the generalized convergence (fields, grants, env vars)

Status: done
Type: task
Blocked by: 01, 02, 03, 04, 05, 06

## Change

1. `tests/test_init_bot.py` (`AuditFieldMigrationTests`): additive-field
   detection and full-pipeline convergence, property-only change
   detection, doctype-acceptance generalized to `ALL_DOCTYPES`, missing-
   shipped-field refusal, unmanaged-key-drift refusal, broadened live-
   only full-dict refusal. New `_live_doctype_side_effect()` test helper
   — `compute_plan()`/`converge_fields()` now iterate every doctype in
   `ALL_DOCTYPES`, so a blanket `_live_doctype` mock (one fixture
   regardless of doctype name) would misreport every other doctype's
   fields as missing.
2. `tests/test_init_bot_provisioning.py`: new `EnvVarManifestTests` —
   gap detection in `compute_plan()`, exclusion from `_needs_action()`
   and the confirm-token hash, the `_print_bot_findings()` WARN line.
   `RunTests.PLAN` and other fixture plans gained `"env_var_gaps": []`.
3. New `tests/test_env_defs.py`: manifest shape, `missing_required_keys()`
   fixtures, and the `.env.example`↔manifest round-trip.
4. Three existing `run_real()` tests (`test_init_bot.py::
   RunRealCallsLogRoleProvisioningAfterDoctypesTests::
   test_log_role_provisioning_called_after_doctype_loop`, `BotReadGrantTests::
   test_run_real_grants_after_the_doctypes_exist`,
   `test_init_bot_provisioning.py::RunTests::
   test_run_real_order_reads_then_role_then_business`) needed a
   `log_migration_summary` patch added — the new unconditional call in
   `run_real()` otherwise hit the real `core_client.get_env_config()`
   with no credentials configured.

## Done when

- `python -m pytest plugins/qkeee-erp/tests/test_init_bot.py
  tests/test_init_bot_provisioning.py tests/test_env_defs.py` is green.
- `python -m pytest plugins/qkeee-erp/tests` (full plugin suite) shows
  no regression.

## Comments

2026-11-11:
- `python -m pytest tests/test_init_bot.py tests/test_init_bot_provisioning.py
  tests/test_env_defs.py -q` → 72 passed, 57 subtests passed.
- `python -m pytest tests/ -q` (full plugin suite) → 908 passed, 1562
  subtests passed, no regressions.
