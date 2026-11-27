# Remove the 0158/0198 skills lock hooks

Status: ready-for-agent
Type: task
Blocked by: 09 (rolled out to every Deployment), plus one release

## Why later

After issue 04 no code the gateway imports lives under `skills/`. But a Deployment that last booted with 0198 still has a root-owned `skills/qkeee-erp` tree. `profile update` runs as hermes and must delete that tree, so 0158 must still run on that first update.

## Change

Delete both hooks, their Dockerfile list entries and glob ranges, the README rows and the sync-back paragraph, and the ADR 0002 consequence line about the lock (point it at ADR 0005).
