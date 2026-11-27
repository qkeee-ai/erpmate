# log_migration_summary(): one durable audit row per init-bot run

Status: done
Type: task
Blocked by: 03, 05

## Change

1. `init_bot.py`: `log_migration_summary(tag, requested_by,
   approval_note, plan, summary)`, beside `log_role_provisioning()`,
   reusing `_audit_insert()`/`_audit_submit()` the same direct way and
   for the same reason (this records the tool's own bootstrap action,
   not a business write).
2. Always one `Read` row (`reference_doctype="DocType"`,
   `reference_name="init-bot convergence check"`) with `field_diff`
   carrying the full categorized plan (`fields_to_migrate`,
   `bot_read_grants`, `business_grants`, `env_var_gaps`) — what this
   run found, whether or not anything changed.
3. Only when something actually changed (any of `role_created`,
   `doctypes_created`, `fields_migrated`, `bot_read_grants`,
   `bot_role_change`, `business_grants` in the run's summary), a second
   `Update` row with `payload_after` carrying the full summary.
4. `run_real()`: call it right after the Summary dict is built (which is
   itself right after `log_role_provisioning()`), so the Audit Log
   DocType is guaranteed to exist first.

## Done when

- Every `init-bot` real run writes at least one audit row describing
  what it found.
- A run that converges nothing still logs the check; a run that
  converges something also logs what was applied.
- No persisted state/version file was added — this row IS the durable
  trail (ADR 0012 decision 5).

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/init_bot.py`
  (`log_migration_summary`, called from `run_real()`).
- Tests: `tests/test_init_bot.py::
  RunRealCallsLogRoleProvisioningAfterDoctypesTests::
  test_log_role_provisioning_called_after_doctype_loop` extended to also
  assert `log_migration_summary` runs last, after
  `log_role_provisioning`.
