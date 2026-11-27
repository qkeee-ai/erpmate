# Bind the job to the originating session's channel, so later proactive updates have somewhere to go

Status: ready-for-agent
Blocked by: 16 (writes into the fields 16 adds)
Blocks: 21, 23

## What's needed

ADR 0010 point 4: the session that first dispatches a job (renders it, gets it to the Requester for approval — a chat turn or an email turn) should have its channel identity captured as the job's monitor of record. A gateway session is per-turn and cannot stay resident as a literal running process; this issue is about durably recording *where to send updates*, not keeping a process alive.

This directly extends an existing, working pattern: `kanban_origin.py` already records a kanban task's origin (platform, user_id, chat_id) "from the gateway's per-turn session context, never from tool arguments," at `kanban_create` time. This issue applies the same capture, at the same point in the session lifecycle, to the job level instead of (or in addition to) the per-task level.

## Exact fix

In `prepare_job()` (`core/operations.py`), read the same session-context fields `kanban_origin.record_origin()` already reads (platform, chat_id, user identifiers) and write them into the new `monitor_platform`/`monitor_channel_id` fields (issue 16) on the `Qkeee Bot Job Approval` row, at render time — not asked for as a tool argument, same discipline as `kanban_origin.py`'s own docstring ("never from tool arguments"): a caller-supplied channel could be used to redirect notifications to somewhere the actual Requester never sees.

If the dispatching session has no resolvable channel context (e.g. a CLI session, where ERPNext access itself isn't available per `SKILL.md` step 3.3's existing rule) — fall back to email-only, addressed to `requested_by`, same as today's `notify.job_approval_request` default.

## Depends on

16 (the fields this writes into).
