# CI runs the plugin tests on develop

Status: done
Type: task
Blocked by: none

## Problem

`.github/workflows/ci.yml` runs only the `skills/qkeee-erp/*/scripts` tests, and triggers on push to `main`. The default branch is `develop`. `plugins/qkeee-erp/tests` never runs in CI.

## Change

- Trigger on push to `develop` and `main`, and on PRs.
- Add a step: `python -m pytest plugins/qkeee-erp` from the repo root.
- Keep the skill-scripts loop until issue 04 empties it; issue 04 removes it.

## Comments

2026-10-09 (erpnext-hermes `3da6168`): triggers on push to `develop`/`main` and on PRs; steps `python -m pytest tests` (repo tests, from issue 01; needs `pyyaml`) and `python -m pytest plugins/qkeee-erp`. Skill-scripts loop kept here, removed in issue 04 (`0a44c39`). Not yet seen running on GitHub.
