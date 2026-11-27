# Session restart replays an identical stale compaction + re-issues an already-answered tool_call_id

Status: needs-triage

## Problem

Evidence: `erpnext-hermes/logs/demo-procurement-data.log`, sessions starting at lines 2369, 2611, 3047.

Three separate session boundaries each open with a byte-for-byte identical ~26,000-character compaction summary, and each then re-issues the *identical* tool call (a Purchase Receipt submit, "R2") with the same `tool_call_id` and the same batch token — preceded in two of the three by dozens of lines of near-content-free reasoning (log lines ~2398, ~2640: "Hmm. Let me write the message. OK. Writing. Going. OK, output..."). The third replay happened under a different model (`z-ai/glm-5.3-flash` vs `deepseek/deepseek-v4.1-flash` for the first two) and still reproduced the same text, which points to a session crash-and-restart loop replaying from the same stored checkpoint rather than fresh generation each time.

## Impact

- Token cost: roughly 78,000 tokens burned across the three replays, minimum.
- Possible duplicate write risk: if `erp_execute_write` for the R2 step was actually sent 3 times against demo_erp rather than deduplicated client-side, this depends on server-side idempotency (`expected_modified` concurrency check per ADR 0007) to avoid a triple-submitted Purchase Receipt. Not confirmed from the log alone whether the duplicate calls actually reached the ERPNext API or were caught before the network call.

Likely a one-off (tied to whatever caused the session crash/restart in that run) rather than something every kanban job will hit, but worth confirming before dismissing — three recurrences in one log is not reassuring.

## Proposed fix

1. **Confirm the blast radius first.** Check demo_erp's Purchase Receipt list / Audit Log for the record(s) R2's batch would have created, to see whether the write landed once, three times, or was rejected on retries 2 and 3 by the concurrency check.
2. **hermes-lcm / hermes-agent session-restart path.** Investigate what triggers a restart that replays the same compaction checkpoint without progress, and add a guard so a restarted session doesn't re-render an already-answered tool_call_id or re-send a batch token that was already consumed.

## Second occurrence, 2026-11-05 (manufacturing demo run)

Evidence: `erpnext-hermes/logs/demo-manu-4-Nov/prompt.log`, lines 1007-1519
replayed byte-for-byte identically at lines 3104-3616 — the exact same 4
`kanban_create` calls, same resulting `task_id`s (`t_ab77c419`,
`t_161ca310`, `t_8c28270a`, `t_d342ef11`), same triage-promotion
sequence, same reasoning strings. Different symptom this time: no
duplicate-write risk, because the replayed job's own re-render of
`notify.job_approval_request` was correctly refused server-side
("job 'JOB-3D2FE5FF13' is not Pending (status='Approved'); refusing to
send an approval email for it", line 3018) — ADR 0008's pending-check
precondition caught it. So this run's replay cost tokens only, not a
write risk, but it's the same underlying mechanism as the original
finding (not tied to one model or one run) — raises confidence this is
a real, recurring session-restart bug, not a one-off.
