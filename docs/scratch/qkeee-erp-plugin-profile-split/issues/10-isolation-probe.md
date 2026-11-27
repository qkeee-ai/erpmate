# Isolation probe, fingerprint and exclusive terminal target

Status: done (2026-10-09)
Type: task
Blocked by: 03, 04

ADR 0006: ERPNext access requires proven Isolation.

## Change

- `qkeee_erp/isolation.py`, called by `setup` (issue 07) as its last steps:
  - **I1 probe:** through the configured terminal backend (issue 03, question 4), try to read `plugin-data/qkeee-erp/qkeee-erp.env` and the profile `.env` by absolute gateway path. Isolated only if both reads fail. `local` backend: not Isolated without running anything.
  - **Fingerprint:** backend, ssh host, user, port, key path. Store result + fingerprint + time in `plugin-data/qkeee-erp/isolation.json`.
  - **I2 exclusive target:** refuse if another ERP-enabled Profile on this machine records the same `ssh_user@ssh_host` (scan `<HERMES_HOME>/profiles/*/plugin-data/qkeee-erp/isolation.json`).
- `register()` (issue 07): Isolated only if the stored result is true and the stored fingerprint equals the live terminal config.

## Tests

Fake backend: read succeeds → not Isolated; both fail → Isolated; probe error → not Isolated. Fingerprint mismatch → not Isolated. Same target in a second profile → refused. `local` backend → not Isolated, no probe run.

## Outcome (2026-10-09)

- `plugins/qkeee-erp/qkeee_erp/isolation.py`; setup items I2 then I1 in `setup_steps.ITEMS` (I2 first: a shared target is never probed or recorded). `register()` needs no change: `unmet()` re-checks I1 (record isolated + fingerprint == live) and I2 on every load.
- Probed paths: the credentials file (required; I1 waits for S1) and the profile `.env` when it exists.
- Probe error / missing marker → recorded `isolated: false`, item pending (not failed), so the run continues.
- Hook 0185 retries setup up to `QKEEE_ERP_SETUP_ATTEMPTS` (3) times, 5 s apart: the sidecar may still be starting.
- Verified against the real Hermes factory: `local` execute + marker parsing; unreachable ssh → "not proven". Verified 2026-10-12 on the dev-erp VM (arm64, hermes-agent v0.21.3 / 2026.9.14): upgrade from origin/develop with `HERMES_PROFILE_BRANCH`; 0185 first attempt reported I1 `Isolated: the terminal hermes@terminal cannot read …/plugin-data/qkeee-erp/qkeee-erp.env, …/.env` through the real sidecar sshd.
- Review fixes: the probe-only connection forwards no env and uploads no files, so I1 also refuses (without probing) when `terminal.env_passthrough` names a `QKEEE_ERP_*` variable or `terminal.credential_files` names the credentials file or `.env`; both keys are in the fingerprint. Skill-registered passthroughs / credential files are not covered (Profile skills carry no code). The record lists the probed paths; a `.env` that appears later forces a new probe. `ssh_key` is fingerprinted as written (boot hook and gateway have different HOMEs).
- I2 deviates from the original wording: it does not check that the other Profile has the plugin enabled. A stale `isolation.json` still claims the target (fails closed; the refusal names the file to remove).
- Tests: `tests/test_plugin_isolation.py`.
