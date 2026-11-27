# New `notify.job_status_update` op: every park/block event reaches email and Google Chat, not just a kanban comment

Status: ready-for-agent
Blocked by: 22 (needs to know which channel/chat_id to address — reads the monitor-channel fields 22 binds)
Blocks: 23 (the poller calls this op)

## What happened

Both live runs posted every block only via `kanban_comment` (28-Oct and 29-Oct alike; 29-Oct `prompt.log` ~line 2844, ~3723-3724). No `notify.*` email, no Google Chat message, accompanied any block. A human not actively watching the dashboard has no way to learn a job stalled.

## Exact fix

New operation, `notify.job_status_update` (`qkeee_erp/domains/notify.py`, alongside `job_approval_request`/`summary_email`), `cli=True` but intended as the call issue 23's poller makes once per pass, not once per event. Args: `job_id`, `items` (a list — every currently-open park and block for this job as of this pass, each `{step, reason, decision_needed}`), `summary` (counts: parked, blocked, completed since last report). **One call reports everything open for the job in that pass — never one call per individual park/block** (ADR 0010 point 3, revised 2026-10-30: a stream of per-event pings buries the one thing the Requester needs, a consolidated view of what's open).

Behavior: render reads the job's `monitor_platform`/`monitor_channel_id` (issue 16/22) and the Requester's `requested_by`. Execute sends **both**: an email to the Requester (same template family as `job_approval_request`, logged as a `Communication` on the Job Approval row per ADR 0009's existing pattern) and a message to the stored Google Chat channel/thread if `monitor_platform == "google_chat"`. If the monitor channel is email-only, the chat send is skipped, not substituted — don't invent a chat channel that wasn't the original one. This runs alongside `kanban_comment` (which still logs each event individually on the card for audit), never instead of it.

## Depends on

22 (channel to address). Logically pairs with 16 (schema the channel fields live on) but doesn't need to be sequenced after 16 directly if 22 lands first.
