# erpnext-hermes: fixes from the prod-erp email-channel run (18 Nov)

Status: 01, 05, 06, 07, 08, 09 actioned 2026-11-19, all ready-for-human with a live check open:
issue 01 committed d7c25e0efd (hermes-agent); issue 05 no code change needed; issue 06 committed
0f6fa41+bdd15ba (erpnext-hermes); issue 07 committed dcc02f0f62 (hermes-agent, scoped to
daytona/vercel_sandbox — see its Comments); issue 08 committed 3d1a21b34e+53c9ee4c4b (hermes-agent);
issue 09 committed 18c5199+827c88c (erpnext-hermes). 02, 03, 04, 10 still needs-triage, not touched
this session. 2026-11-20: issue 02 design-drafted (Request Registry, see its Comments) and implemented, committed
5f7be1f (erpnext-hermes, develop), ready-for-human, live round trip not yet run; issue 11 (weekly
tasks-received-vs-completed report) filed, blocked by 02. Issue 03 design-drafted and implemented, committed 4770b55
(Chat path only; email Message-ID index deferred — see issue 03 Comments), ready-for-human, after
an adversarial review that found and fixed a cross-conversation leak risk, a session-metadata leak
via `link`, and a missing audit trail. Issue 04 scope-pivoted (hermes-agent/hermes-lcm ruled
strictly out of scope by the user) to a `facts` field on the same Request Registry, implemented
and committed 7caaee3 (erpnext-hermes develop) — code review caught and fixed an
identity-vs-room-coordinate bug in the "already linked" check and a lost-update race in the facts
merge. Remaining open: 10 (prompt-log truncation), 11 (weekly report, unblocked, design + sign-off locked 2026-11-23 — see its Comments;
adds pending/dependencies summary, token/cost metrics via hermes-agent state.db cross-checked
against OpenRouter's own usage API (§8a found hermes-agent receives but discards OpenRouter's
authoritative per-call cost — filed as a hermes-agent follow-up, not blocking), and an
admin-gated on-request monthly pull; no code yet).
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (email adapter, qkeee_erp skill/connector), `hermes-lcm` (context/compaction engine), `hermes-agent` (shared tool runtime, file tools, logging)
Evidence: `erpnext-hermes/logs/prod-erp-18-Nov/prompts.log` (openrouter prompt chain, two runs separated by one compaction event) + code audit of `erpnext-hermes`/`hermes-lcm`/`hermes-agent`

## Goal

The email-channel path for erpnext-hermes should:

- reply in-thread, to the full original To/Cc list, not a fresh mail back to the sender only;
- know who is the original requester vs. an input-provider when several people across several channels (email threads, group chats, DMs) discuss the same task, and redirect a non-requester's new-record ask to a new thread instead of interleaving it;
- survive a mid-task context compaction without silently re-doing finished work or re-hitting the same already-solved error;
- not drift on things that are already hard runtime rules (tool-call batching) just because a compaction boundary crossed.

## What went right (keep)

- RBAC fail-closed held even against the requester's own identity — no silent bypass.
- OCR/attachment extraction and `erp_execute_write` draft-then-verify sequencing both worked once the agent found the right path.
- No prompt-injection in the forwarded email body that the agent handled.

## Findings → issues

| # | Finding | Source | Issue |
|---|---|---|---|
| U1 | Reply to a user over an email chain doesn't preserve the original To/Cc list — reverts to sender-only. | User-reported | 01 |
| U2 | Reply is sent as a fresh email, not threaded (quoted trail) under the original. | User-reported | 01 |
| U3 | One user discussing the same request across email and chat isn't stitched into one context — agent treats channels as unrelated. | User-reported | 02 |
| U4 | Multiple users across multiple channels on the same task: agent has no requester-vs-input-provider model, no reply-all discipline, and no redirect when a non-requester injects a new record/request mid-thread. | User-reported | 03 |
| F1 | Mid-task context compaction made the agent redo an entire finished discovery chain (supplier/item/invoice lookups, 3× `list_ops`) and re-hit the identical `erp_execute_write` ValidationError twice, relearning the same fix both times. | Log audit (L473-741 vs L801-1087; error at L660 and L988) | 04 |
| F2 | "One entry per tool_call, no mixed/multi-local batches" — a documented hard runtime limit (`00-conventions.md`) — was violated twice in the same session, once before and once after the compaction boundary. | Log audit (L461, L789) | 05 |
| F3 | No write op exists to attach a file (e.g. source PDF) to a record. Agent burned 6 `list_ops` calls across scopes hunting for an op that was never going to exist. | Log audit (L644-649) | 06 |
| F4 | `~` expands to the wrong home dir on first `read_file` call, causing a cascade of dead-end `pip3`/`sudo`/`apt-get` attempts before the absolute path (which worked) was tried. | Log audit (L74/79, L184-259, L269-291) | 07 |
| F5 | Memory pointed at skill `qkeee-erp-learned/default`, which doesn't exist in this profile. Agent self-flagged the violation of its own "procedures belong in skills, not memory" rule and stuffed a pitfall paragraph into flat memory anyway. | Log audit (L1190, L1201) | 08 |
| F6 | RBAC fail-closed correctly blocked `nikhil.sharma@qkeee.in` from reading `Payment Entry Reference` — but that blocked the agent from verifying its own write-back to the very requester who started the task. | Log audit (L1170) | 09 |
| F7 | `prompts.log` cuts off right after the last tool result; the actual final email reply sent to the requester is not captured anywhere, so what the user actually received can't be audited from this file. | Log audit (L1212, EOF) | 10 |

## Code audit findings (bear on U3/U4 root cause)

Checked whether email-channel is under-wired for LCM/skills vs. chat-channel (`config.yaml:259-289,304-305,328-346`; `hermes-agent`/`hermes-lcm` for `platform==email` conditionals). **Result: no.** `context.engine: lcm`, `plugins.enabled`, and `platform_toolsets.email` vs. `.google_chat` are identical/global — F1/F2/F5 are generic LCM/runtime weaknesses, not an email-specific wiring gap.

The one confirmed email-specific gap: session identity is keyed only on the `From:` address (`erpnext-hermes/plugins/platforms/email/adapter.py:809-810`), with no `Message-ID`/`References`/thread-id disambiguation. This is the mechanism underlying U1-U4 — there is currently no concept of "this thread" independent of "this sender," so the adapter can't distinguish a reply-in-thread from a fresh mail, can't tell two participants on the same thread apart, and can't recognize the same human arriving via a different channel.

## Decisions

| # | Question | Proposed decision |
|---|---|---|
| D1 | Scope of issue 01 | Send-side header hygiene only (reply headers: To/Cc/In-Reply-To/References, quoted trail body). Smallest fix that closes U1/U2; does not require the thread-model work in issue 02. |
| D2 | Cross-channel identity stitching (U3) | Needs design, not a quick patch — extend the existing per-channel `requested_by` resolution (see `qkeee-erp-requester-identity-binding` memory/ADR) to a channel-independent identity key (e.g. resolve email address ↔ chat handle to one person record) before session/thread state can be shared across channels. Filed as needs-triage, not pre-decided here. |
| D3 | Requester-vs-input-provider model (U4) | Depends on D2's identity key plus a thread-participant list keyed off issue 02's `Message-ID`/thread-id fix. Filed as needs-triage — blocked by issue 02. |
| D4 | Issue 09 (RBAC blocks requester's own verification read) | **Decided 2026-11-19 (nikhil.sharma@qkeee.ai):** design fix, narrowly scoped — the bot verifies its own just-written record using its own (bot-identity) read, ONLY as an immediate verification-read right after the bot's own write to that same record. Not a general permission bypass, not a requester-role grant (doesn't scale per D4's original framing), and not deferred to F7's broader "bot writes using its own login" design in `hermes-erp-bot-reliability` — treated as a narrower, separately-scoped case than F7. |
| D5 | Issue 06 (no File-attach write op) | **Decided 2026-11-19 (nikhil.sharma@qkeee.ai):** build the write op (not documentation-only). Scope/shape TBD at implementation time — see issue 06 for research findings once gathered. |

## Implementation order (once actioned)

1. Issue 01 — email reply fidelity (To/Cc + threading). Smallest, highest-visibility, no dependencies.
2. Issue 05 — enforce tool-call batching client-side. Zero design risk, stops a documented rule from silently failing.
3. Issue 07 — fix `~` expansion. Contained path-resolution bug.
4. Issue 08 — auto-bootstrap `qkeee-erp-learned/<env-tag>` on first miss. Contained.
5. Issue 04 — compaction must carry resolved facts/errors-already-hit, not just narrative. Larger, touches `hermes-lcm` core; do after the contained fixes above so it isn't competing with in-flight changes.
6. Issue 02 — cross-channel identity key (design). Unblocks issue 03.
7. Issue 03 — requester-vs-input-provider + redirect-new-request behavior. Blocked by issue 02.
8. Issue 06 — file-attach write op (design: scope/gating for `File` doctype writes).
9. Issue 09 — RBAC-blocks-self-verification: needs a real decision, not urgent to land first.
10. Issue 10 — logging gap (capture full final turn). Observability only, no behavior risk either way.
