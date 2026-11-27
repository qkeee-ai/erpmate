# Move the ERP library from the skill into the plugin package

Status: done
Type: task
Blocked by: 02, 03

## Change

1. `git mv skills/qkeee-erp/qkeee-erp-associate/scripts/*` into `plugins/qkeee-erp/qkeee_erp/` (tests into `plugins/qkeee-erp/tests/`). Commit the move alone, so history follows the files.
2. Rewrite imports to relative inside `qkeee_erp`; the plugin modules import `from .qkeee_erp …`. Remove `_load_skill_library`, the `scripts_dir` setting and the `core` resolution check from `__init__.py`.
3. One conftest. `python -m pytest plugins/qkeee-erp` runs everything (today: 35 plugin + 430 library tests).
4. Remove the skill-scripts loop from CI (issue 02).

## Done when

- Full suite green, same test count.
- `grep -rn "scripts_dir\|sys.path" plugins/qkeee-erp` finds only the test conftest.
- `skills/qkeee-erp/qkeee-erp-associate/scripts/` does not exist.

## Comments

2026-10-09 (erpnext-hermes `26f86ab` pure move, `0a44c39` imports):
- `python -m pytest plugins/qkeee-erp`: 467 passed (35 + 430 + 2 new `register()` contract tests). Subtests 936 vs 968: `test_domain_modules_send_no_write_outside_the_pipeline` yields one subtest per call in a domain module; the removed sys.path preambles held 32 calls.
- `grep -rn "scripts_dir\|sys.path" plugins/qkeee-erp` → only the conftest docstring.
- `register()` never raises (issue 03 finding): identity guard first, library import failure → logged, no `erp_*` tools.
- Loaded under the real Hermes loader (scratch venv): 5 `erp_*` tools, library at `hermes_plugins.qkeee_erp.qkeee_erp.*`, same after `discover_and_load(force=True)`.
- `__main__` guards removed: the library modules no longer run as scripts. Until issue 05, there is no Operator CLI for `init_bot`, `client health`, `memory_promote` etc. SKILL.md/README/references still name `scripts/...` paths (issue 06); `docker/cont-init.d/0198` comment and lock now protect only docs (issue 08).
