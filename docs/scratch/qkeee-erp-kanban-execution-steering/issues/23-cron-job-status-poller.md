# Scheduled poller is what actually makes the originating channel a "monitor" across a long-running job

Status: ready-for-agent
Blocked by: 21, 22
Blocks: nothing further

## What's needed

ADR 0010 point 4's "monitor" role has to be realized without a resident process: nothing drives a proactive update on its own once the dispatching turn ends. `erpnext-hermes/cron/jobs.json` already exists as an empty Hermes cron-job registry — the plumbing for a scheduled job is present in this repo, just unused for this purpose.

## Exact fix

Add a cron job entry (`cron/jobs.json`) running on a short interval (e.g. every 5 minutes) that:
1. Queries `Qkeee Bot Job Approval` for rows with `status in (Pending, Approved)` whose linked kanban card(s) have changed state (new `execution_log` entries, a new park, a card now `blocked`, or all cards `done`) since the last poll (track a `last_notified_log_index` field alongside the ones issue 16 adds, or reuse `execution_log`'s own length as the watermark).
2. For each changed job, collects **every currently-open park and block for that job** (not just what changed this pass — the full open set) into one `items` list and makes **one** `notify.job_status_update` (issue 21) call for it, addressed to that job's bound monitor channel (issue 22). Never one call per individual event — see ADR 0010 point 3 (revised 2026-10-30).
3. On a job reaching full completion (every batch's steps either executed or explicitly parked, no card left `blocked`), the same single-call shape applies: one final report summarizing steps executed, items parked (with their reasons), and what each parked item still needs.

This is deliberately a thin polling loop over durable state (the doctype), not a new long-lived process — consistent with ADR 0010's "no resident monitor process" consequence. The poller's own interval is what naturally batches events into one consolidated report per pass; it does not need its own separate de-duplication logic beyond the watermark in step 1.

## Depends on

21 (the op it calls), 22 (the channel it addresses).
