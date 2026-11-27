# Workspace state directory for resumable Kanban wave runs

Status: needs-triage
Type: task
Blocked by: 01, 02

## Problem

Kanban execution steering (ADR 0010) runs multi-wave jobs where status polling currently round-trips tool-call by tool-call through the model. A sandbox with filesystem access could poll in a loop (same pattern as the article's Slack-polling example) and persist progress across waves without re-deriving state from scratch each turn.

## Change

- Give the sandbox a per-run workspace directory (e.g. `./workspace/<job-id>/`) for state written by one execution and read by a later one.
- Move in-sandbox status polling for a wave to a loop (poll, sleep, check) instead of returning to the model between each poll.
- Decide retention/cleanup: a workspace file is not an audit record: it must not become the only copy of something the audit log should also hold.

## Done when

A Kanban wave run polls status without a model round-trip per poll, and resumes from workspace state if the run is interrupted between waves.

## Comments
