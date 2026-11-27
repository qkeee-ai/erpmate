# `Qkeee Bot Job Approval` needs a per-step ledger and a monitor-channel record

Status: ready-for-agent (schema + plumbing), pending ADR 0010 acceptance
Blocked by: ADR 0010 acceptance (human sign-off — see `docs/adr/0010-job-template-approval-draft-park-dual-channel-monitor.md`, currently `proposed`). Not blocked by any other issue in this set.
Blocks: 17, 18, 19 (via 18), 21 (via 22), 22

## What's needed

ADR 0009 gave `Qkeee Bot Job Approval` one row per job: `job_id`, `requested_by`, `token`, `issued_at`, `step_count`, `status`, `plan` (JSON). ADR 0010 needs two more pieces of durable state on the same doctype, because a kanban child-task worker is a separate process per dispatch and nothing today survives a respawn except what's in this row:

1. **Per-step execution ledger.** As each step actually completes, append its result (`batch_index`, `step_index`, resulting `name`, `modified`, outcome) to a `execution_log` field (JSON list) on the row. This is what lets a later batch resolve `<FROM_BATCH_N_STEP_M>` from a durable store instead of needing it in the same process's memory — the confirmed-broken case from the 29-Oct run (`prompt.log` ~line 160-176).
2. **Monitor-channel record.** At job creation (`prepare_job()`), capture the dispatching session's own channel identity — platform (`google_chat`/`email`), `chat_id`/thread or equivalent — into new fields (`monitor_platform`, `monitor_channel_id`) on the row. This is the durable anchor issue 22 and 23 read from; without it, a later proactive update has nothing to address itself to.

## Exact fix

- `doctype_defs.py`: add `execution_log` (JSON/Long Text, default `[]`), `monitor_platform`, `monitor_channel_id` fields to `JOB_APPROVAL`.
- `core/operations.py`: `prepare_job()` populates `monitor_platform`/`monitor_channel_id` from the session context at render time (same source `kanban_origin.py` already reads for per-task origin — extend that read, don't invent a second one). A new small helper, `append_execution_log(job_id, batch_index, step_index, result)`, called from wherever a step's write actually completes (the `execute` phase's per-step loop), appends one entry and saves.
- No change needed to `WRITE_GATE_EXEMPT_DOCTYPES`/`PROD_GATE_EXEMPT_DOCTYPES`/`UNSCOPED_DENY` — this doctype is already on all three per ADR 0009; a new field doesn't change its gate membership.

## Depends on

ADR 0010 sign-off. No code dependency on any other issue in this set — this is the foundation issue 17/18/19/21/22 all read or write against.
