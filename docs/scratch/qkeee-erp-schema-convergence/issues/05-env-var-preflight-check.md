# Wire env_defs.missing_required_keys() into compute_plan(), findings, and health_check()

Status: done
Type: task
Blocked by: 04

## Change

1. `init_bot.py`: `_env_var_gaps(tag)` builds the present-suffix set via
   `core_client._qkeee_env()` + `core_client._tag_env_var(tag, suffix)`,
   calls `env_defs.missing_required_keys()`. `compute_plan()` gains
   `env_var_gaps`. Excluded from `_init_plan_token`'s hashed fields and
   from `_needs_action()` — observational only, same reasoning as
   `extra_rights`/`skipped_privileged`.
2. `_print_bot_findings()`: one `WARN:` line per gap, naming the full
   `QKEEE_ERP_<TAG>_<suffix>` var and its purpose, ending "this tooling
   never writes secret values."
3. `core/client.py`'s `health_check()`: lazy-imports `env_defs` (`from ..
   import env_defs` inside the function — matches `operations.py`'s
   existing lazy-import convention for parent-package imports from
   within `core/`, avoiding a module-load-order assumption), adds
   `env_var_gaps` to its output dict the same way.

## Done when

- `compute_plan()["env_var_gaps"]` and `health_check()["env_var_gaps"]`
  agree for the same tag/env.
- A non-empty `env_var_gaps` never blocks a run and never changes the
  confirm token.
- `erp_discover health` (which calls `health_check()`) surfaces the
  same gaps `init-bot --dry-run` does.

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/init_bot.py`
  (`_env_var_gaps`, `compute_plan`, `_print_bot_findings`) and
  `plugins/qkeee-erp/qkeee_erp/core/client.py` (`health_check`).
- Confirmed the lazy `from .. import env_defs` resolves correctly at
  runtime (`python -c "import qkeee_erp.core.client"` succeeds) — a
  static linter flagged the two-dot relative import as unresolvable,
  which is a known limitation of that linter's fallback-path heuristic
  for this package layout, not a real import error.
- Tests: `tests/test_init_bot_provisioning.py::EnvVarManifestTests` (new
  — gap detection, `_needs_action()` exclusion, confirm-token exclusion,
  the WARN line's exact text).
