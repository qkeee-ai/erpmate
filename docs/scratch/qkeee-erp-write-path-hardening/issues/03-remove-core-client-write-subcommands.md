# Remove the write subcommands from core/client.py's CLI

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P0
Blocked by: none
Closes: W04
Decision: D4 — resolved 2026-10-06: remove

## Problem

`core/client.py mutate <doctype> <action>` without `--domain` calls `mutate_resource(domain=None)`. That path has:

- no allowlist
- no token
- no user confirmation code

It writes to any doctype with any action, gated only by the requester check. The help text calls it "debug". It also sets `user_approved=bool(args.confirmation_token)`, which records "Approved" in the audit log from a value nobody verified. `gated-mutate` duplicates `execute_write.py`'s domain-less path, but skips its schema mapping and context warnings.

## Change

- Delete the `mutate` and `gated-mutate` subparsers and their dispatch branches from `core/client.py _cli()`. Keep `health`, `list-envs`, `query`, `get`, `report` and `roles`.
- Update the module and `_cli` docstrings to say every write goes through `execute_write.py`.
- If triage picks the keep-behind-a-flag option instead, do this:
  - require the env var `QKEEE_ERP_DEBUG_WRITES=1`
  - require `--domain`
  - import every domain module in the dispatch branch
  - never set `user_approved` from token presence
- Update `references/cli-cookbook.md` and `references/01-connectivity.md`, which mention these subcommands.

## Acceptance

- [x] `python core/client.py --tag X mutate Item create` fails with argparse "invalid choice".
- [x] `grep -rn "client.py.*mutate" references/` has no remaining instruction to use it.
- [x] All `test_client.py` tests are green. Delete or rewrite any test that exercised the CLI write branch.

## Comments

**Implemented 2026-10-06.**

- Removed the `mutate` and `gated-mutate` subparsers and dispatch, plus the now-unused `--mode`/`--approval-note` flags. The `_cli` docstring was rewritten.
- Docs updated: `cli-cookbook.md`, the `execute_write.py` module docstring, the `test_execute_write.py` docstrings, `docs/qkeee-erpnext-feature-inventory.md`, and the stale `erp_client.py mutate` comment in `config.yaml`.
- `python core/client.py --tag X mutate Item create` now fails with argparse "invalid choice".

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
