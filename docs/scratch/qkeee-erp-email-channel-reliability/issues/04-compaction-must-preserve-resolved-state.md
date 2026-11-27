# Context compaction must carry resolved facts and errors-already-hit, not just narrative

Status: ready-for-human (implemented 2026-11-20, committed 7caaee3 erpnext-hermes develop — pivoted scope, see Comments; live round trip not yet run)
Type: task
Blocked by: none
Findings: F1

## Problem

Mid-task, a context compaction fired (new system prompt + `[Recent Summary]` injected as a user message). After it, the agent redid the entire discovery chain it had already finished (supplier lookup, item lookup, invoice-history lookup, 3× `list_ops`) at L801-1087, duplicating L473-741 exactly. It also hit the identical `erp_execute_write` ValidationError ("Paid amount + Write Off Amount can not be greater than Grand Total") at both L660 and L988, and had to relearn the same fix (create draft without payment fields, then update) both times.

The compaction summary carried enough narrative to keep the agent oriented on the task, but not enough structured state to avoid repeating finished work or a solved error.

## Change

Not specified yet — needs a look at `hermes-lcm`'s compaction-summary construction to see what's feasible: likely a structured "resolved facts" / "errors hit + fix applied" block carried forward alongside (not instead of) the narrative summary, so the post-compaction agent can check it before redoing a lookup or re-triggering a write it already has a known-working shape for.

## Acceptance

TBD once design lands. At minimum: a repeat run that hits the same compaction boundary should not re-issue identical `list_ops`/lookup calls for facts already resolved, and should not re-hit a write error it already has a documented fix for in-session.

## Comments

**Scope pivot (2026-11-20, user decision):** touching `hermes-agent` or `hermes-lcm` is strictly out of scope going forward. The "## Change" section above (look at `hermes-lcm`'s compaction-summary construction) is stale/superseded — not pursued.

**Implemented instead (erpnext-hermes develop, confined to this repo):** both symptoms addressed via a `facts` field added to the Request Registry (issues 02/03's sqlite store, gateway-side — outside the LLM's own context, so a compaction summary cannot lose what's written there regardless of what hermes-lcm itself carries forward):

- New `erp_request action=facts`: merge-only (`{"supplier_id": "SUP-001"}` adds/overwrites named keys, leaves the rest), backed by `request_registry.merge_facts()`.
- SKILL.md §9: check `facts` (already present on every `open`/`lookup`/`in_thread`/`link`/`status` result, no separate read needed) before repeating a lookup you suspect you already did.
- SKILL.md §8 (`write_failure`): merge a confirmed fix into `facts` the moment it works, not deferred to end-of-task — the specific fix for "relearned the identical `erp_execute_write` error twice."
- Gated identically in spirit to `link`/`status`: the Requester, or anyone already linked, may write facts; a stranger with a bare `request_id` cannot.

**Code review (Standards + Spec) before commit found one real issue, fixed in the same pass:** the first version of the "already linked" check for `facts` compared (platform, chat_id, thread_id) tuples — reopening issue 03's cross-conversation leak (a threadless Chat space again), and simultaneously, in closing that leak the same way `open_in_session` had to, broke every legitimate email caller (whose `thread_id` is always absent). Fixed properly: added a `sender_email` column to `request_session` (with a defensive `ALTER TABLE` for any pre-existing store file) and a new `request_registry.linked_senders()` — the "already linked" check is now identity-based (does this email appear among the request's linked senders), not room-coordinate-based, which is correct for both channels and needed no thread_id heuristic at all. Also fixed in the same pass: `merge_facts()`'s read-then-write had a lost-update race between two concurrent facts merges (plausible given issue 03's whole point is multiple participants); now wrapped in `BEGIN IMMEDIATE` so the second caller's transaction waits instead of racing.

**Known limitation (spec review, not fixed, outside this diff's control):** post-compaction, the agent still needs the bare `request_id` string itself to query `facts` — that depends on hermes-lcm's own summary preserving the id, which is out of scope here and unverified.

**Not yet done:** a live round trip against a running gateway session (same caveat as issues 02/03).
