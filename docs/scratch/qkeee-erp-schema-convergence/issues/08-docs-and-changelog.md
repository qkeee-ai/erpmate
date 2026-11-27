# Docs and changelog: init_bot.py docstring, CHANGELOG.md row

Status: done
Type: task
Blocked by: 01, 02, 03, 04, 05, 06

## Change

1. `init_bot.py`'s module docstring: reword the paragraph that described
   `MIGRATABLE_FIELDS` as covering only the one `reference_name` flip —
   describe the general per-field, per-key convergence mechanism
   instead, keeping `reference_name` as the historical motivating
   example.
2. `doctype_defs.py`: fix the stale inline comment above
   `reference_name` that pointed at "MIGRATABLE_FIELDS below" (which no
   longer exists) — points at `managed_keys()` instead.
3. `skills/qkeee-erp/CHANGELOG.md`: new row "SC 2026-11-11" in the
   existing finding/rule/lives-in table format, summarizing the bug, the
   fix, and the new `env_defs.py` module.
4. `tests/test_env_defs.py`'s round-trip test (issue 04/07) is the
   enforcement going forward that `env_defs.py` and
   `qkeee-erp-associate.env.example` can't drift apart.

## Done when

- `init_bot.py` and `doctype_defs.py` contain no stale reference to
  `MIGRATABLE_FIELDS`.
- `skills/qkeee-erp/CHANGELOG.md` has the new row.

## Comments

2026-11-11:
- Implemented across `plugins/qkeee-erp/qkeee_erp/init_bot.py`,
  `doctype_defs.py`, and `skills/qkeee-erp/CHANGELOG.md`.
- `grep -rn MIGRATABLE_FIELDS plugins/qkeee-erp` confirms no stale
  references remain outside the two intentional historical mentions
  (`managed_keys()`'s docstring, a test docstring).
