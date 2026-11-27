# No agent-facing entry point starts a job (`prepare_job()`) — exact fix locked 2026-10-24

Status: ready-for-human (code done: erpnext-hermes, pending commit; live round trip open)
Blocked by: 03 (done), blocks real use of 04/05/06

## What happened

Live test (demo sales dataset prompt, 2026-10-24): the agent correctly
reported "`job_approval.create` exists in the gate rules but hasn't been
registered for use yet — a development gap" and fell back to the
existing per-batch chat-code flow (7 batches, 7 codes) instead of the
job-level email path. It was right to refuse rather than guess.

Confirmed by code (not the agent's self-report alone):
`grep -rn "prepare_job("` across `plugins/qkeee-erp/` finds the
definition in `core/operations.py` and call sites only in
`tests/test_job_approval.py`/`tests/test_batch_confirmation.py`. Nowhere
in `erp_tools.py` or `notify.py` does anything call it. `erp_tools.py`
exposes exactly 3 `erp_execute_write` phases — `list_ops`, `render`,
`execute` — and `render` only handles one op or a flat `batch` list,
never a list of batches. `job_id`/`job_batch_index` were wired into
**execute** (issue 06) so a later step can consume an already-approved
job, but nothing produces that job in the first place from a live
session. Issues 03-06 each built their own slice correctly; nobody wired
the actual front door.

## Exact fix (decided 2026-10-24, re-grounded in `erp_tools.py`/`operations.py` as they actually stand)

Re-read `erp_tools.py`'s `_execute_write()` (lines ~361-434) and
`operations.prepare_job()` (lines ~1215-1258) directly rather than
re-guessing. Locked design, no options left open:

1. **New `erp_execute_write` arg `batches`** (plural — list of batches,
   each a list of `{op, args}`), distinct from the existing singular
   `batch`. Add to the tool schema next to `batch` (erp_tools.py line
   ~124), mutually exclusive with `batch`/`op` the same way `batch`
   already is (line ~368).
2. **In `_execute_write()`, when `phase == "render"` and `args.get("batches")`
   is given:** generate a `job_id` server-side —
   `f"JOB-{uuid.uuid4().hex[:10].upper()}"` (needs `import uuid`, not
   currently imported in `erp_tools.py`). Never accept an agent-supplied
   `job_id` here: the doctype's `job_id` field is `unique: 1`
   (`doctype_defs.py` `JOB_APPROVAL`), so a caller-chosen id risks a
   collision `job_approval.create` would reject as a hard failure deep
   inside `prepare_job()`, instead of a clean validation error at the
   tool boundary.
3. **Call `operations.prepare_job(batches, ctx, job_id)`** and return its
   result (`job_id`, `job_token`, `confirmation_code`, `expires_at`,
   `batches` in plain-words form) with a `_note` that explicitly tells
   the agent **not** to show the code/token in chat — unlike every other
   render path, this one must say: "Do not paste this code into chat.
   Call `erp_execute_write(phase=\"render\")` then `execute` with
   `op=\"notify.job_approval_request\"`, `args={\"job_id\": \"<job_id>\"}`
   to send it by email instead." `notify.job_approval_request` is
   already a normal, agent-callable operation (`cli` defaults `True`,
   never set to `False` on it) — no new wiring needed there, only the
   instruction to use it.
4. **No separate execute step for starting the job itself** —
   `prepare_job()` already writes `job_approval.create` synchronously
   during its own render (`operations.py` line ~1255), so step 2 above
   is a complete, one-shot action. Don't build a `phase="execute"`
   counterpart for `batches`; there isn't one.
5. **Skill guidance** (`qkeee-erp-associate`'s `SKILL.md`,
   `tool-cookbook.md`, `03-spec-driven-execution.md`): still needed,
   unchanged from the original finding — nothing today tells the agent
   this path exists or when to prefer it over per-batch chat codes. Add
   a worked example to `tool-cookbook.md` once (1)-(4) are coded, and a
   line in `03-spec-driven-execution.md` saying a multi-batch spec with a
   kanban job in play should default to the job-level email path over
   per-batch codes.

See issue 09 for the other missing half (binding the Requester's reply
back) — found during this same re-grounding pass, not in the original
write-up of this issue.

## Comments

2026-10-24: implemented exactly as decided above —
`erp_tools.py`'s `_execute_write()` gains a `batches` arg (plural),
mutually exclusive with `batch`/`op`, valid only with `phase="render"`
(an explicit error names this if `phase="execute"` is tried with
`batches`, since `prepare_job()` already stores the plan on render — no
execute step exists for it). Generates `job_id =
f"JOB-{uuid.uuid4().hex[:10].upper()}"` server-side per the decision
(never agent-supplied, avoids the `unique:1` collision risk). The
response's `_note` explicitly tells the agent not to show
`confirmation_code`/`job_token` in chat and names the exact next call
(`notify.job_approval_request`).

Also fixed, found while wiring the skill-guidance item (point 5 above):
`03-spec-driven-execution.md`'s "Kanban instead of a spec file" section
still told the agent to reach for `kanban_create` for the exact
multi-batch/multi-day ERPNext work this job-level path now covers —
directly contradicting the `00-conventions.md` rule already shipped for
issue 01. Narrowed that section to exclude ERPNext execution work and
point at the job-level email path instead.

Worked examples added to `tool-cookbook.md` (the actual point 5 ask).
New tests in `test_plugin_erp_tools.py` (`JobToolTests`): job_id
generation, mutual exclusivity, execute-phase rejection, no-confirmation
note variant. Full suite: 828 passed, 1401 subtests, no regressions.
**Not yet done:** no live round trip against a real ERPNext instance —
same open item as 03-06.

## Why this wasn't caught before the live test

Issues 03-06 were scoped and built bottom-up (storage → email → reply
→ consumption) without a scoping pass that asked "what does the agent
actually type to start one of these." Worth remembering for the next
multi-issue build: scope the entry point first, or at minimum as its own
explicit issue, not as an assumed side effect of the pieces around it.

## Depends on

Issue 03 (done, the store this would write into). Blocks 04/05/06 from
ever being exercised by a live agent turn, even though all three are
individually code-complete and unit-tested.
