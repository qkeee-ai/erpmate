# Align the discovery docs

Status: resolved (docs done: 9ccc0dc, in feat/discovery-write-readiness)
Type: task
Blocked by: 02, 04, 06

## Problem

- `01-connectivity.md:131-137` says `meta` and `resolve` call `GET /api/resource/DocType/<name>` and need System Manager. The code uses `getdoctype` merged meta, and `DocType` is exempt from the requester gate.
- `01-connectivity.md:138` says `modules` "works broadly". It failed on DEMO_ERP.
- `02-environment-assessment.md:38` says `modules` is a "plain REST read, always works".
- No doc says which reads need the **bot's** permission and which need the **requester's**.

## Change

1. Rewrite the `discover.py` section of `01-connectivity.md` to match the code after issues 02, 05 and 06. Add `preflight`.
2. `02-environment-assessment.md`: add step 0 (run `health`, show gaps through the issue 04 prompt). Fix step 2.
3. Add a table: capability → whose permission (bot / requester) → role → effect if missing.
4. `SKILL.md` Files section: add `preflight`.

## Acceptance

- Every claim about a `discover.py` subcommand matches the code.
- The bot/requester table exists in one place; other files link to it.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). The `discover.py` section of `01-connectivity.md` matches the CLI (apps, modules, meta, resolve, preflight). The single "Whose permission each discovery read needs" table lives in `01-connectivity.md`, and `02-environment-assessment.md` links to it. `02-environment-assessment.md` has step 0 (`health` plus the gap prompt). `SKILL.md` lists `preflight`.
