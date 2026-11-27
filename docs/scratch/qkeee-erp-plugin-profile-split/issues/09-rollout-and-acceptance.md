# Rollout to dev-erp and demo + acceptance

Status: ready-for-human
Type: task
Blocked by: 01, 07, 10

## Runbook (per Docker Deployment)

1. Back up `HERMES_DATA_DIR` (the whole volume).
2. Check that `skills/qkeee-erp-learned/` exists; note its contents (issue 01).
3. Pull, `docker compose build`, boot once with `HERMES_PROFILE_AUTO_UPDATE=1`.
4. Logs: 016 update OK, 018 plugin enabled, 0157 key published, 0185 setup summary.
5. `hermes -p <profile> qkeee-erp setup status`: every item done, Isolation proven. Diff `config.yaml` against the newest `config.yaml.bak-*`.
6. `hermes -p <profile> qkeee-erp health --requested-by <your email>`.
7. Set `HERMES_PROFILE_AUTO_UPDATE` back to the Deployment's normal value.

## Acceptance (Google Chat, DEMO_ERP)

- tools happy path;
- identity binding with two Senders;
- identity guard blocks;
- terminal isolation, including the write-back attempt;
- Kanban origin;
- fail closed: change `terminal.ssh_user` in config, restart → no `erp_*` tools, log says run setup; restore → setup → tools back;
- regression after a second reboot (Instance Notes still present).
