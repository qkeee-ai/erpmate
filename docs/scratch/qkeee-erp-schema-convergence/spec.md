# qkeee-erp: init-bot field/grant/env-var convergence

Status: done — issues 01–08 implemented and tested 2026-11-11, uncommitted in erpnext-hermes
Raised: 2026-11-11, after `init-bot` refused to re-provision an already-provisioned instance
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (`plugins/qkeee-erp/qkeee_erp/init_bot.py`, `doctype_defs.py`, `env_defs.py`)
Decisions: `docs/adr/0012-init-bot-field-convergence-and-env-manifest.md`
Terms: `CONTEXT.md` (Plugin, Instance, Operator)

## Goal

`init-bot` converges any previously-provisioned ERPNext instance onto
the current `doctype_defs.py`, however old that instance is — adding a
field it defines but the live doctype doesn't have yet, converging any
type/property drift on a field both sides already share, and never
touching a field or key doctype_defs.py doesn't manage. The same run
reports (never acts on) permission-grant drift and required-env-var
gaps, and records one durable audit row per run.

## The bug that raised this

```
ERROR: refused, nothing was sent: provisioning.migrate_fields: the fields sent for
'Qkeee Bot Audit Log' must match doctype_defs.py exactly (names and types).
```

`MIGRATABLE_FIELDS` named exactly one field (`reference_name`'s Dynamic
Link → Data flip). Every additive field shipped to the Audit Log since
(`domain_code`, `channel`/`channel_metadata`,
`response_payload`/`prompt_summary`/`latest_user_prompt`) broke
re-provisioning on any instance older than that field: the write-gate
correctly refused a body missing those fields, but nothing in
`migrate_fields()` could ever send them.

## Decisions

| # | Question | Decision |
|---|---|---|
| D1 | What may converge on a shipped field | Every key that field's own `doctype_defs.py` definition specifies (`managed_keys()`) — type, options, reqd, in_list_view, label, description, default, whatever that field's dict names. Not just `fieldtype`. |
| D2 | Additive fields | Converge the same way as type/property changes — not a special case, not silently skipped. |
| D3 | Narrowing (a live-only field, or a grant doctype_defs.py no longer wants) | Report-only, admin-decided. Never auto-dropped, never auto-revoked. |
| D4 | `MIGRATABLE_FIELDS` | Removed. The per-field managed-keys check subsumes it; keeping it would be a second stale allowlist. |
| D5 | Migration state | No persisted state/version file. `compute_plan()` keeps live-diffing the target fresh every run. Durable record = one Qkeee Bot Audit Log row per run. |
| D6 | Env var drift | In scope. `env_defs.py` manifest (required/optional per-tag, optional-global), presence-only check (never reads/writes a value), report-only, excluded from the confirm-token hash. |
| D7 | Deliverable shape | ADR 0012 + this spec/issues, alongside the code. |

## Issues

1. `issues/01-generalize-field-classification.md` — shared managed-keys diff/classify helper; remove `MIGRATABLE_FIELDS`. **Done.**
2. `issues/02-rewrite-migrate-fields-guards.md` — rewritten `_provision_migrate_prepare`/`_check_live_only_fields_unchanged`. **Done.**
3. `issues/03-converge-fields-and-plan-reporting.md` — `converge_fields()` replacing `migrate_fields()`; tagged-string reporting into `compute_plan()`/`_print_bot_findings()`. **Done.**
4. `issues/04-env-var-manifest-module.md` — new `env_defs.py`. **Done.**
5. `issues/05-env-var-preflight-check.md` — `missing_required_keys()` wired into `compute_plan()`, `_print_bot_findings()`, `core.client.health_check()`. **Done.**
6. `issues/06-generalize-audit-record.md` — `log_migration_summary()`. **Done.**
7. `issues/07-test-coverage.md` — new/extended tests across `test_init_bot.py`, `test_init_bot_provisioning.py`, new `test_env_defs.py`. **Done** (72 tests + 57 subtests passing in the two init_bot files + env_defs; 908 tests + 1562 subtests passing across the full plugin suite).
8. `issues/08-docs-and-changelog.md` — `init_bot.py` docstring update, `skills/qkeee-erp/CHANGELOG.md` row "SC 2026-11-11", `.env.example`↔manifest round-trip test. **Done.**

## Testing

| Layer | What | Where |
|---|---|---|
| Unit | field classification (additive/type/property/live-only), write-gate (`_provision_migrate_prepare`/`_check_live_only_fields_unchanged`), `converge_fields()`, env var manifest | `plugins/qkeee-erp/tests/test_init_bot.py`, `test_init_bot_provisioning.py`, `test_env_defs.py` |
| Round-trip | every `env_defs.py` key appears in `qkeee-erp-associate.env.example` and vice versa | `test_env_defs.py::EnvExampleRoundTripTests` |
| Full suite | no regression elsewhere in the plugin | `python -m pytest plugins/qkeee-erp/tests` (908 passed, 1562 subtests passed) |
| Live acceptance | not yet run | re-run `init-bot --dry-run` against the actual instance that raised this, confirm the plan now lists the missing fields as additive and a real run converges them |

## Known gaps (not in scope)

- Live round trip against a real pre-2026-08 ERPNext instance — only unit-tested with fixtures so far.
- No CI job added for this specifically; relies on the existing `python -m pytest plugins/qkeee-erp` suite.
