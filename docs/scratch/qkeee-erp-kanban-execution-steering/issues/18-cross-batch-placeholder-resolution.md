# `<FROM_BATCH_N_STEP_M>` is documented as working across batches; live testing found it does not resolve

Status: ready-for-agent
Blocked by: 16 (reads the per-step execution ledger 16 adds)
Blocks: 19 (draft-park needs this to know what's already resolved before deciding what can't be)

## What happened

`tool-cookbook.md` (`qkeee-erp-associate/references/tool-cookbook.md` ~line 250ish, the "Starting a kanban job" section) says: "A cross-batch payload placeholder `<FROM_BATCH_N_STEP_M>` is accepted and stored literally by the render but its execute-time resolution is unverified — probe it with a minimal 2-batch job." The 29-Oct live run did exactly that probe, for real: render refused because the named record did not exist yet (`logs/sales-demo-29-Oct/prompt.log` ~line 160-176: "placeholders don't resolve at render either — the engine checks the record exists"). This forced every batch into single-create-then-separate-submit waves, multiplying confirmation rounds — and is the specific reason the 29-Oct run rejected the ADR 0008 job-email path mid-run (a job that can't pre-resolve its own cross-batch names can't be fully rendered upfront either).

## Exact fix

At execute time (not render time — render only needs to store the placeholder literally, per the existing design), when a step's payload contains `<FROM_BATCH_N_STEP_M>`, resolve it by reading batch N's entry from the job's `execution_log` (issue 16) and substituting that step's recorded `name`. This requires batch N to have already executed and appended its result — which is already the natural execution order (batches run in sequence) — so no new ordering constraint, just an actual lookup where today there is none. Refuse clearly (not a silent skip) if batch N's log entry is missing or that step didn't produce a `name` (e.g. it was a submit/cancel, not a create).

Add a regression test: a 2-batch job where batch 2's payload references batch 1's step via `<FROM_BATCH_1_STEP_1>`, executed as two separate `execute` calls (simulating two separate worker dispatches), asserting batch 2 resolves correctly from the ledger alone — no shared process state.

## Depends on

16 (the execution ledger this reads).
