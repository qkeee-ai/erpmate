# Narrow distribution_owned so updates keep learned skills

Status: done
Type: task
Blocked by: none

## Problem

`distribution.yaml` lists `skills/` as owned. `profile update` replaces an owned path with `rmtree` + `copytree` (`hermes-agent/hermes_cli/profile_distribution.py`, `_copy_dist_payload`). So every update deletes `skills/qkeee-erp-learned/<env-tag>` (the agent's instance notes) and every other skill that is not in the repo. The comment "other installed skills stay" is wrong.

## Change

- Replace `skills/` with one entry per shipped skill directory (`skills/qkeee-erp`, `skills/productivity/<name>`, `skills/research/<name>`, `skills/email/<name>`), plus the category `DESCRIPTION.md` files.
- Fix the comment.
- Add a repo test: every directory under `skills/` that holds a `SKILL.md` is covered by a `distribution_owned` entry, so a new skill cannot ship unowned.

## Check first

On dev-erp: does `skills/qkeee-erp-learned/` exist? If an earlier update deleted it, record that here.

## Done when

An update keeps a `skills/qkeee-erp-learned/x/SKILL.md` that the profile has and the repo does not (checked by the Distribution test job in issue 07, or by hand with hermes-agent's `update_distribution`).

## Comments

2026-10-09 (erpnext-hermes `fc575a9`): `distribution_owned` lists `skills/qkeee-erp`, each `skills/{productivity,research,email}/<skill>` and the three category `DESCRIPTION.md` files. `tests/test_distribution.py` fails if a shipped skill or category file is unowned, or if `skills/` or `skills/qkeee-erp-learned/…` becomes owned. Checked by hand with hermes-agent's real `_copy_dist_payload` against a fake profile: `skills/qkeee-erp-learned/x/SKILL.md` kept, stale `skills/qkeee-erp/qkeee-erp-associate/scripts/old.py` removed, shipped skills present.

**Check first — not done:** dev-erp was not reachable from this session. Still open: check whether `skills/qkeee-erp-learned/` exists on dev-erp before its next update (spec Risks).
