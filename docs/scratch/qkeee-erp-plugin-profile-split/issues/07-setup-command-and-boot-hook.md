# `hermes qkeee-erp setup`: steps, prerequisite checks, fail-closed load, Docker hook

Status: done
Type: task
Blocked by: 04, 05, 11

## Change

- `qkeee_erp/setup.py`: steps S1–S3 and prerequisites P1–P2 from the spec. Each idempotent. State in `plugin-data/qkeee-erp/setup.json` (done steps, time, version). `config.yaml.bak-<UTC>` before the first write of a run. Writes through `hermes_cli.config`. A failed step stops the run.
- Prerequisites only report unless `--apply-profile-fixes`; with it, they write the erpmate values.
- CLI: `setup [--dry-run] [--apply-profile-fixes]`, `setup status` (done / pending / failed + reason per item). Calls issue 10's Isolation steps last.
- `register()`: read setup state and the Isolation record (issue 10). If any required item is unmet, register no `erp_*` tools and log the `setup status` summary. Hooks always register.
- Remove the shell credentials move from `018-qkeee-erp-plugin` (now S1).
- `docker/cont-init.d/0185-qkeee-erp-setup`: runs `setup --apply-profile-fixes` as hermes for the installed profile. Non-fatal. `QKEEE_ERP_SETUP=0` skips. Dockerfile: the CRLF/chmod loop's globs do not match `0185-*` (`01[6-9]-*` needs `-` as the 4th character); add it. Update the hook lists in the Dockerfile and README.

## Tests

- Config fixtures: fresh shipped config, dev-erp-like (local backend, `code_execution` on google_chat, `skills.config.qkeee_erp` set), half-done, comment-heavy, a non-erpmate agent config.
- Each step: applies once, second run changes nothing, no new backup.
- Prerequisites: reported without the flag, written with it; nothing written for the non-erpmate fixture without the flag.
- Failing step: later steps not applied, state not advanced.
- `register()` with a fake ctx: tools absent while anything is unmet, present when all done; hooks present in both cases.
- Distribution job in CI: install then update a profile with hermes-agent pinned to the base-image version; Instance Notes survive (issue 01); old `scripts/` gone.

## Comments

2026-10-09 (erpnext-hermes `5d3dc5c`, review fixes `41b00d3`):
- `plugins/qkeee-erp/setup_steps.py` (not `qkeee_erp/setup.py`: setup is plugin plumbing, not ERP library). Items S1, S2, P1, P2, S3 in that order (spec table updated). Tests: `tests/test_plugin_setup.py` (fixtures fresh, dev-erp-like, half-done, comment-heavy, non-erpmate; idempotency, backups, failure stop, dry run, host vs container defaults, register() through the real checks).
- CLI `qkeee-erp setup [status] [--dry-run] [--apply-profile-fixes]`; `status` shows done / pending / failed (failure from the last run's `setup.json`); exit 0 only when complete.
- register(): no `erp_*` tools unless every item is done now **and** `setup.json` records a complete run for this plugin version (`plugin.yaml` bumped to 0.2.0, so every existing install needs one setup run). Isolation (I1/I2) not yet in `unmet()`: issue 10 adds it.
- Decisions: S1 without any credentials = pending (run continues); S1 with both old and new file = pending until an Operator deletes the old one (setup never deletes a secret). P1: google_chat must list `qkeee_erp`; every chat platform but cli that serves ERP loses `code_execution`. P2 fix values: container (`QKEEE_ERP_IN_CONTAINER=1`, set in the Dockerfile; `/.dockerenv` fallback) = shipped sidecar values; host = `localhost`, `hermes-terminal`, `<profile>/.terminal-ssh/id_ed25519`. The Operator CLI's ERP commands still gate only on the legacy section (the CLI is the Operator's shell).
- Docker: `0185-qkeee-erp-setup` (non-fatal, `QKEEE_ERP_SETUP=0` skips); `018` no longer moves credentials; Dockerfile glob + hook lists + README updated.
- CI `distribution` job: hermes-agent pinned `v2026.9.7`; install → write Instance Notes → update → notes survive, no `scripts/`, `setup status` loads. **Open:** the base image is `nousresearch/hermes-agent:latest`, so the pin is not provably "the base-image version". Pin the `FROM` tag to the same release, or derive the CI ref from it.
- Verified with real Hermes (local install): `setup --apply-profile-fixes` on a dev-erp-like config → all done, backup, key, `setup status` exit 0; distribution smoke passes locally.
- Not verified: a Docker build/boot (no Docker here).

