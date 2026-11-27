# The other missing half: nothing calls `verify_job_reply()` either

Status: ready-for-human (code done: erpnext-hermes, pending commit; live round trip open)
Blocked by: 03 (done); same root cause as issue 07, found during the same re-grounding pass

## What's missing

`grep -rn "verify_job_reply"` across `plugins/qkeee-erp/` finds the
function in `core/operations.py` (line ~1349) and call sites only in
`tests/test_job_approval.py`. Same shape as issue 07's finding, but for
the *inbound* side: once the Requester's email reply arrives as its own
turn, nothing exposes a way to actually call
`operations.verify_job_reply(job_id, reply_text, ctx)`. `erp_discover`'s
action list (`whoami`, `health`, `bot_permissions`, `promotion_plan`,
`roles`, `apps`, `modules`, `meta`, `resolve`, `preflight`) has nothing
for it, and neither does `erp_execute_write`'s phase list
(`list_ops`/`render`/`execute`). This issue didn't make it into the
original issue 07 write-up — it's a real omission in that scoping pass,
not a duplicate.

Issue 07 shipping alone would be actively confusing without this: the
agent could start a job and send the approval email, but would have no
way to act on the Requester's reply when it arrived — worse than today's
state, where the agent at least knows to fall back to per-batch codes.
**Ship 07 and 09 together, not one without the other.**

## Exact fix (decided 2026-10-24)

1. **New `erp_execute_write` phase, `"confirm_job"`.** Belongs on
   `erp_execute_write`, not `erp_discover`: it performs a real write
   (`job_approval.mark_approved`, via `verify_job_reply()`), and
   `erp_discover`'s actions are read/discovery-only by design — don't
   blur that line for convenience.
2. **New args**: `job_id` (string) and `reply_text` (string — the
   Requester's reply, verbatim, same discipline `user_confirmation_text`
   already has elsewhere in this connector: never agent-constructed,
   never paraphrased).
3. **In `_execute_write()`**, a branch parallel to the existing
   `phase == "list_ops"` early-return:
   ```
   if phase == "confirm_job":
       self._need(args, "job_id", "reply_text")
       requester = self._requester()
       ctx = self._context(args, requester)
       return _ok(operations.verify_job_reply(args["job_id"], args["reply_text"], ctx))
   ```
   `self._requester()` is exactly right here with no changes: it already
   resolves from the gateway session's authenticated sender
   (`client.resolve_requested_by("")`), which on the reply turn is the
   reply email's own `From` header — this is ADR 0008's sender-match
   half of the two-factor proof, already correct, already wired, just
   needs this call site to exist.
4. **Finding which `job_id` a reply is about**: the approval email's
   subject is `"[<tag>] Approval needed: job <job_id> (<n> step(s))"`
   (`notify.py` `render_job_approval()`, confirmed by reading it). A
   reply normally keeps the original subject (with a client-added
   "Re:"), so the agent extracts `job_id` by pattern-matching `job
   (\S+)` in the reply turn's subject — no new plumbing, just an
   instruction. Document this in the skill (same place as issue 07's
   skill-guidance item), not just left to be rediscovered per session.
5. **After a successful `confirm_job`**, the skill should tell the agent
   to proceed straight to executing each batch via
   `erp_execute_write(phase="execute", batch=<batch i>, job_id=<job_id>,
   job_batch_index=i)` — issue 06's already-wired consumption path. State
   this explicitly; don't leave the agent to rediscover the connection
   between "job now Approved" and "how do I actually run the batches."

## Depends on

Issue 03 (done). Ship alongside issue 07 — neither is useful alone.

## Comments

2026-10-24: implemented exactly as decided above — `erp_tools.py`'s
`_execute_write()` gains `phase="confirm_job"` as an early branch
(before the `list_ops`/`render`/`execute` enum check, same position
`list_ops` already has), needing `job_id` + `reply_text`, calling
`operations.verify_job_reply(job_id, reply_text, ctx)` directly.
`self._requester()` needed no changes — confirmed by test
(`test_confirm_job_binds_the_session_sender`) that the reply turn's
resolved sender flows through unchanged. Point 4 (finding `job_id` from
the reply's subject) and point 5 (proceeding to issue 06's execute path)
both landed as worked-example prose in `tool-cookbook.md` rather than
code — there's no code decision left to make for either, both are pure
agent instruction.

New tests in `test_plugin_erp_tools.py` (`JobToolTests`): sender
binding, missing-args refusal, and the no-live-chat-signal case (ADR
0008's actual point — `confirm_job` needs no `latest_prompt`/batch
context at all). Full suite: 828 passed, 1401 subtests. **Not yet
done:** no live email-reply round trip against a real mailbox — same
open item as 03-07.
