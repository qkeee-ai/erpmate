# Isolate the terminal backend from the gateway

Status: implemented (erpnext-hermes `feat/discovery-write-readiness` 925003d + review fix a0e8261, 2026-10-08); not built or run yet (no Docker on the dev machine): dev-erp rollout + retest pending
Type: task
Blocked by: none

## Problem

The `local` terminal backend runs in the same container, as the same user, as the gateway. The agent can read any file the gateway can read, including secrets that issues 04 and 05 move out of `qkeee-erp.env`.

## Change

Run the dev-erp terminal on a separate backend (`docker` or `ssh` terminal backend in Hermes) with no mount of `$HERMES_HOME` secrets. Keep only the working directory (`/opt/cwd`) and the skill scripts.

## Decision needed

Which backend fits the dev-erp and PROD deployment (`erpnext-hermes/docker-compose.yaml`)? What does it cost in latency and setup?

## Comments

**Decided 2026-10-08** (grill-with-docs). See `docs/adr/0002-terminal-on-ssh-sidecar.md`. Use the Hermes `ssh` backend to a sibling compose service that mounts `/opt/cwd` and the skill scripts, never `/opt/data`. Rejected: the `docker` backend, because it needs `/var/run/docker.sock` in the gateway (root on the host). `execute_code` follows the terminal backend, so it moves too. Config: `terminal.backend: ssh`, `ssh_host`, `ssh_user`, `ssh_key` (BatchMode, key auth only).

**Implemented 2026-10-08.** Compose `terminal` service (`docker/terminal/`: hermes base image + key-only sshd, no forwarding, no AcceptEnv), mounts only `/opt/cwd` + own volumes. `config.yaml` `terminal.backend: ssh`. `0157-terminal-ssh` makes the gateway client key in `/opt/data/.terminal-ssh/` and publishes the public key via the `terminal-auth` volume.

Review found: Hermes `sync_back` copies changed skill files from the sidecar into the gateway `skills/`, and the qkeee-erp plugin imports `skills/qkeee-erp/.../scripts` in the gateway. Fix: `0198-erp-skills-readonly` makes that tree root-owned; `0158-erp-skills-unlock` gives it back before `016-profile-install`.

Still open: skill credential files (Google Workspace tokens) and `cache/` (documents, spillover from all sessions) are synced to the sidecar. Not a regression vs `local`.

Rollout on dev-erp: rebuild, hand-copy the `terminal:` block into the profile `config.yaml` (preserved on update). Check: `terminal` tool `cat /opt/data/profiles/*/plugin-data/qkeee-erp/qkeee-erp.env` fails (no such file), `pwd` = `/opt/cwd`, a skill script runs, a file in `/opt/cwd` reaches Google Chat.
