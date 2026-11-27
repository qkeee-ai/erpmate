# Gateway write-safe root must include the terminal cwd

Status: ready-for-human
Type: task
Blocked by: none
Findings: F4
Decision: D3 (spec.md)

## Problem

The base image sets `ENV HERMES_WRITE_SAFE_ROOT=/opt/data` (`hermes-agent/Dockerfile`). The gateway checks every `write_file`/`patch` path against it (`agent/file_safety.py`), also when the file tools run in the ssh sidecar. `config.yaml` sets `terminal.cwd: /opt/cwd`. So:

- `./qkeee-erp-specs/<slug>.md` resolves to `/opt/cwd/...` and is denied.
- The agent falls back to `<profile>/workspace/qkeee-erp/<tag>/specs/`. In the sidecar that is `/opt/data` = the `terminal-home` named volume. The host Operator does not see it, and the Google Chat user had to ask for the content.

The fallback that `03-spec-driven-execution.md` calls "the exception" happens on every gateway session.

## Change

- `docker-compose.yaml` gateway `environment`: `HERMES_WRITE_SAFE_ROOT=/opt/data:${HERMES_CWD:-/opt/cwd}`, with a comment that says why.
- `env.example`: note it next to `HERMES_CWD`.
- `03-spec-driven-execution.md` step 3: for a chat (gateway) session, after the spec is written, post a short digest of it in the chat (objective, values, side effects, rollback). The user cannot open a server path.

## Acceptance

- Compose sets the variable; comment explains the base-image default.
- Live (human): recreate the gateway on dev-erp, ask for an Item create, and see the spec at `<HERMES_CWD_DIR>/qkeee-erp-specs/` on the host.

## Comments

- Code done 2026-10-14: `docker-compose.yaml` gateway env `HERMES_WRITE_SAFE_ROOT=/opt/data:${HERMES_CWD:-/opt/cwd}` with reason; `env.example` note; `03` step 3 gateway bullet + digest rule + fallback recorded under Deviations. Open: live check on dev-erp (recreate gateway, run an Item create, see the spec under `<HERMES_CWD_DIR>/qkeee-erp-specs/`).
