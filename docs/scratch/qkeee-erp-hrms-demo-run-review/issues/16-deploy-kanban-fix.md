# Deploy the kanban fix (`931dfba`) on dev-erp

Status: ready-for-human
Type: task
Blocked by: none
Findings: F20

## Problem

The agent had no `kanban_*` tools, so it ran `hermes kanban` in the terminal sidecar, and its board was out of the gateway's reach. Commit `931dfba` fixes this. It is committed but not pushed or deployed.

## Change (Operator)

1. Pull on the dev-erp host (branch pushed 2026-10-22, 455089d), and update the profile so `config.yaml` arrives.
2. Run `hermes -p dev-erp qkeee-erp setup --apply-profile-fixes`. P3 must end `done`, with `kanban.orchestrator_profile` and `kanban.default_assignee` set to `dev-erp`.
3. Run `docker compose build terminal && docker compose up -d terminal`. Then `docker exec <terminal> sh -c 'command -v hermes'` must print nothing.
4. Restart the gateway.
5. Run `/jev skills off` once in a dev-erp chat (issue 14: the env default only seeds a new profile).
6. Optional: delete the orphan sidecar board `/opt/data/.hermes/kanban/boards/hrms-demo-qkeee`.

## Acceptance

- A chat request for a board creates it through `kanban_create`.
- The board shows in `hermes -p dev-erp kanban boards`, and its cards leave triage.
