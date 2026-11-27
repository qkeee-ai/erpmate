# Job-level approval token + durable cross-task storage

Status: ready-for-human (code done: erpnext-hermes@0896f3f, ADR 0009; live init-bot run + ERPNext round trip open)

## What this builds (ADR 0008, accepted 2026-10-23)

A `job_token()` analogous to the existing `batch_token()`
(`plugins/qkeee-erp/qkeee_erp/core/operations.py` lines ~925-944), but
binding an ordered list of **batches** (each batch itself a list of
`{op, args}` steps, same shape `prepare_batch()`/`_batch_requests()`
already produce) instead of one flat list of steps. Fields to hash, per
ADR 0008: every batch's every step's operation key + full prepared
request, in order, plus `requested_by` (the Requester), a job id, the
total step count, and `issued_at`.

## The open decision this issue exists to force before coding starts

`batch_token`/`WriteContext.batch_approval` work because render and
execute happen inside the same turn, same process, same `WriteContext`
(`operations.py` line ~1061: `dataclasses.replace(ctx.without_confirmation(),
batch_approval=label, ...)`). A job-level approval does not: the plan is
rendered once by the orchestrating session, approved by an email reply
that arrives as its own turn (possibly its own process), and then
consumed by N separate kanban child-task workers, each a different
process with its own fresh `WriteContext`. There is currently no durable
store the job's token, plan, and approval state can live in that a later,
unrelated process can read.

Three candidate stores, not decided here:

1. **A new ERPNext doctype** (e.g. `Qkeee Bot Job Approval`), written
   through the Admin Account the same way `Qkeee Bot Audit Log` is
   (`doctype_defs.py`, `init_bot.py` pattern). Pro: same audit/GRC
   posture as everything else this connector writes; every write is
   already going through ERPNext. Con: another doctype `init-bot` has to
   provision read/write for, another thing that can be missing on a
   fresh instance.
2. **Hermes-side kanban card body/metadata** (the orchestrating card
   holds the plan + token; child cards reference it). Pro: no new
   ERPNext doctype. Con: this is the same board whose auto-decomposer is
   issue 01 in this directory — leaning on it further for something
   load-bearing (the approval state itself) compounds that risk rather
   than avoiding it.
3. **A local store on the gateway** (sqlite, similar to what hermes-lcm
   already keeps for session state). Pro: no ERPNext round trip to check
   approval. Con: a kanban worker is a separate process/container from
   the gateway (per `00-conventions.md`'s "Isolation" distinction between
   the gateway and a sidecar); needs its own RPC path for a worker to
   read it, which doesn't exist today either.

Pick one before writing `job_token()`'s storage side — the hash function
itself is a small, mechanical extension of `batch_token()`; the storage
decision is the actual design work here.

## Scope once storage is decided

- `job_token(batches: list, requested_by: str, job_id: str, issued_at) -> str`
  in `core/operations.py`, next to `batch_token()`.
- A `prepare_job(batches: list, ctx: WriteContext, issued_at=None) -> dict`
  analogous to `prepare_batch()`, returning the full plan + token + code
  for the email (not for chat display — see issue 04).
- Read/write to whichever store is chosen, gated the same way every other
  write in this connector is (mode check, requester gate, audit row).

## Depends on

Nothing blocks starting the design decision above. Issues 04-06 in this
directory depend on this one's storage decision.

## Comments

2026-10-23: storage decision made — option 1, a new ERPNext doctype
(`Qkeee Bot Job Approval`), approved by nikhil.sharma@qkeee.ai. See ADR
0009 for the full reasoning and the rejected alternatives. Implemented:
`job_token()`, `prepare_job()`, `job_approval.create`,
`job_approval.mark_approved` (core/operations.py), the doctype
(doctype_defs.py), and two gate-exemption-set additions in core/client.py
(WRITE_GATE_EXEMPT_DOCTYPES/PROD_GATE_EXEMPT_DOCTYPES — needed for
correctness, since the Requester never holds direct ERPNext permission on
this bookkeeping doctype). Unit tests pass offline
(tests/test_job_approval.py); no live ERPNext round trip or init-bot
provisioning run against a real instance has happened yet — that's the
open item before 04-06 build on top of this.

2026-10-24: this issue's own scope (the token + storage primitive) is
fine. Live testing found a separate gap one layer up — `prepare_job()` is
called nowhere outside test code, so nothing actually starts a job from
a live session. Tracked as issue 07; it blocks 04/05/06 from being
exercised, not 03 itself.
