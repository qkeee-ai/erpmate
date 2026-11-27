# Mark partial environment catalogs in `memory_promote.py`

Status: resolved (code done: da809dc, in feat/discovery-write-readiness)
Type: task
Blocked by: 04

## Problem

On 2026-10-07 the agent staged the first `qkeee-erp-learned/demo_erp` promotion (pending skill `e701c144`) while `modules` and `apps` had both failed. The catalog has no app list. Activation step 2 latches a learned skill and skips assessment, so later sessions trust a catalog that is missing data. The staleness signals in `02-environment-assessment.md` do not cover "never complete".

## Change

1. `memory_promote.py build_promotion_plan()`: accept `gaps[]` (shape from issue 04). Write `catalog_complete: true|false` and the gap list into `environment.md`.
2. `02-environment-assessment.md`: add a staleness signal. `catalog_complete: false` → re-run the failed steps on every session until they pass, then update the skill.
3. The example in `references/examples/qkeee-erp-learned-example/` shows a partial catalog too.

## Acceptance

- Test: findings with a gap produce `catalog_complete: false` and list the gap.
- Test: findings without gaps produce `catalog_complete: true`.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). `memory_promote.py` takes `gaps` and writes `catalog_complete` plus the gap list into `environment.md`. The breadcrumb says the catalog is partial. `02-environment-assessment.md` has the staleness signal. The example has `environment-partial.md`. Tests are in `core/test_memory_promote.py`.
