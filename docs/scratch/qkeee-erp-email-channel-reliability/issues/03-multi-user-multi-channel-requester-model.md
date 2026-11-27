# Requester-vs-input-provider model for multi-user, multi-channel tasks

Status: ready-for-human (implemented 2026-11-20, committed 4770b55 erpnext-hermes develop — Chat path only; email Message-ID index's erpnext-hermes half built 2026-11-25, inert in production pending a hermes-agent change — see Comments)
Type: research
Blocked by: 02
Findings: U4
Decision: D3 (spec.md)

## Problem

When several people discuss the same task across an email thread, a group chat, and individual chats, the agent currently has no concept of:

- who is the **original requester** (the person whose request started the task) vs. an **input-provider** (someone else contributing info/approval on the same thread);
- reply-all discipline that matches that distinction (e.g. a status update should likely go to everyone on the thread; a record created should be attributed to the original requester, not whoever last replied);
- what to do when a non-requester tries to inject a **new**, different record/request into an ongoing thread — today this risks interleaving a second task's writes into the first task's context/approval flow. Expected behavior: recognize it's a different ask and tell that person to open a new channel message/thread, rather than absorbing it into the current one.

## Dependency

Needs issue 02's cross-channel identity key and thread/participant tracking first — this issue is about roles *within* a thread's participant list, which doesn't exist as a concept until 02 gives the agent a stable notion of "this thread" and "who's on it."

## Open questions (needs-triage, not pre-decided)

- How does the agent detect "this is a new request" vs. "this is more input on the existing request," from a non-requester's message, reliably enough to redirect rather than wrongly refuse a legitimate contribution?
- Does `requested_by` (used today for RBAC/audit attribution per write) stay pinned to the original requester for the life of the thread, even when other participants are the ones answering clarifying questions?

## Change

Not specified yet — design task, write up here first.

## Acceptance

TBD once design lands.

## Comments

**Draft design note (2026-11-20, drafted by agent, pending sign-off):**

Code audit finding, more fundamental than it first looked: **Chat and email are asymmetric here, and only one of them needs new infrastructure.**

- **Google Chat already shares one session across participants.** `session_key` (`hermes-agent/gateway/delivery.py:149`) is built from `chat_id`/`thread_id` (the space/thread), not the sender — so Alice and Bob talking in the same Chat thread already land in the SAME session/LCM context. Per-turn identity is still correct: `set_session_vars()` (`hermes-agent/gateway/session_context.py:131`) is called fresh per inbound message with THAT message's own `user_id`, via per-task `ContextVar`s (not a session-wide fixed value). So on Chat, the substrate issue 03 needs — "who said this, within one shared thread" — already exists. Nothing new to build there.
- **Email has no equivalent.** `hermes-agent/plugins/platforms/email/adapter.py:869` keys `chat_id=sender_addr` — Alice and Bob replying on the identical email thread get two *different* `chat_id`s (their own addresses), hence two completely separate sessions. This is the one real gap: there is no "thread" concept on email independent of "sender," so Bob's reply can't even find Alice's request today, regardless of role.

**Answers to the two open questions:**

1. **Detecting "new request" vs. "more input" from a non-Requester: don't auto-detect, same stance as issue 02 decision 3.** Free-text classification risks wrongly refusing a legitimate contribution (the exact failure mode the question worries about) just as easily as it risks wrongly absorbing an unrelated ask. When a non-Requester's message looks like it might be a different, unrelated subject, the agent asks them directly ("this looks like a new, separate request — want me to open a new one, or is this related to `<topic>`/`<request_id>`?") instead of guessing either way.

2. **`requested_by` (RBAC/audit identity) stays exactly as today — do NOT pin it to the original Requester.** This needs to be split into two different things the issue's wording conflates:
   - **RBAC/audit `requested_by`** (security-critical: whose ERPNext permissions are checked, who the Audit Log names) stays bound to whoever's session/turn actually triggered the write, same as every other tool today. Changing this would mean one person's message can spend another person's ERPNext permissions — a privilege-confusion risk, and a reversal of "RBAC fail-closed held even against the requester's own identity" (spec.md's own "what went right"). Not proposing this change.
   - **Business-level attribution** ("a record created should be attributed to the original requester" — i.e. which human a record's own content/notes should credit) is a different, safe thing: when a write happens inside an open Request (issue 02's Request Registry), the agent notes the Request's `topic`/`request_id`/opener in the write's `latest_prompt`/audit comment or the record's own "requested by" business field (when the doctype has one) — informational only, never a permission change.

**Proposed minimal build (additive, no RBAC change):**

- A small new index, email-only: Message-ID → `request_id`, populated whenever the bot sends a reply tied to a request (reusing issue 01's generated `Message-ID`). An inbound email's `In-Reply-To`/`References` resolving to a known Message-ID resolves the Request regardless of the sender's own address — this is what lets Bob's reply find Alice's request at all.
- No new "participant" table. The role is always derivable from data issue 02 already has: the Requester is `request.sender_email` (fixed at `open`); anyone else whose message resolves to that `request_id` (via the Message-ID index on email, or via shared `chat_id`/`thread_id` on Chat — free, no new code) is an input-provider for the life of that request. Persisting a participant list ahead of a concrete need is not proposed.
- Redirect behavior (open q1) and the role note on writes (open q2) are SKILL.md guidance, not new mechanism.

**Decision needed from you:** the RBAC/audit point (2) is the one change here with real stakes — confirm "no RBAC change, business-attribution only" before anything else, or redirect.

**Implemented 2026-11-20 (erpnext-hermes develop, 4770b55, scoped down from the proposal above after an adversarial review):**

- `erp_request` gained `action=in_thread`: finds an open Request already linked to the CURRENT (platform, chat_id, thread_id) — this is the Chat-side half of the proposal. **The email-only Message-ID index proposed above was NOT built this pass** — scoped out, not forgotten; see Known limitations below.
- No new participant table, as proposed: role (`your_role`: `"requester"`/`"input_provider"`) is derived at read time from `request.sender_email`, added to every `erp_request` result.
- `request_link()` (issue 02) lost its ownership check — any sender may now link their own session to a known `request_id`, which is how an input-provider joins. `request_status()` kept it: only the Requester can change `agent_status`/`requester_confirmed`.
- RBAC point (2) above: implemented exactly as proposed — no change to `requested_by` resolution anywhere. Business attribution is SKILL.md guidance only (write it into the record's own content/comment), not a mechanism.

**Adversarial review findings fixed before commit:**
- `open_in_session`'s original SQL matched two requests that both had `thread_id=None` — in a Google Chat space with threading turned off, this would have surfaced every unrelated conversation in that space to anyone posting there. Fixed: a real `thread_id` is now required on both sides; a missing one matches nothing (closes the leak; costs email nothing, since email's `chat_id` is already the sender's own address and never shared across people anyway).
- `request_link()`'s full result (including every other participant's `linked_sessions` — their platform/chat_id/session_id) was returned to ANY caller who supplied a valid `request_id`, including one who was never in the room — and SKILL.md has the agent say `request_id` out loud, so overhearing it was the realistic exposure, not guessing a uuid4. Fixed: `linked_sessions` is now stripped from `request_link()`'s result for a non-Requester caller; `topic`/`anchor_doctype`/`anchor_name` stay visible (needed for the "yes, this is the ask I mean" confirmation that's the whole point of linking).
- `erp_request` had no audit trail at all for `open`/`link`/`status` (every ERPNext write tool logs one; this didn't). Added an append-only `request_event` table (`request_registry.log_event()`) and an optional `note` argument on those three actions, best-effort (never blocks the caller's actual action on a logging failure).
- `_role_for()`'s ownership comparison now lowercases both sides defensively, instead of trusting that the stored column is always already lowercase.

**Known limitations, not fixed this pass (adversarial review, accepted as out of scope for now):**
- ~~The email-only Message-ID → request_id index (how a second person's email reply would find the first person's request) was not built. `in_thread` on email currently returns nothing, by design — correctly documented in the tool's own description, not just a code comment.~~ **erpnext-hermes half built 2026-11-25 — see Comments below. Still returns nothing in production: the one missing piece is a hermes-agent change, filed there, not built.**
- No retention/cleanup policy exists for the Request Registry — rows accumulate indefinitely. Not addressed; no issue filed yet.
- The entire Chat-side mechanism rests on reading `hermes-agent` source (shared `session_key`, per-turn `ContextVar` identity) rather than a live integration test against a running gateway — same caveat as issue 02's "live round trip not yet run."
- The redirect-for-a-new-unrelated-ask behavior (open question 1, and the issue's own headline problem) remains 100% SKILL.md prose with no code enforcement or test that the agent actually asks at the right moments — an accepted tradeoff of the "ask, don't guess" design, not something this pass added enforcement for.

**erpnext-hermes half built 2026-11-25 (committed 62023c8, erpnext-hermes develop) — ready, inert in production:**

Scoped per 2026-11-24 discussion: build everything that can live entirely in erpnext-hermes, ship it structurally inert for email (same pattern as issue 11's `facts.pending` before its SKILL.md doc landed), and explicitly capture the one remaining hermes-agent-side piece as a named follow-up rather than silently leaving it implicit.

- Design change from the original 2026-11-20 proposal ("Message-ID → request_id, populated whenever the bot sends a reply"): indexes every INBOUND touch's own `Message-ID` instead of the bot's own generated outbound id. This needs zero new hermes-agent plumbing for the write side — `HERMES_SESSION_MESSAGE_ID` already exists and is already exposed (confirmed in `gateway/session_context.py`/`gateway/run.py::_set_session_env()`). A reply's `In-Reply-To`/`References` chain normally carries every ancestor, including the original opening message — not just the immediate parent (which may be the bot's own, never-indexed, generated id) — so matching against the FULL chain still resolves correctly without ever needing the bot's own sent Message-ID.
- New `request_message_id(message_id PK, request_id, created_at)` table in `request_registry.py`; `open_request()`/`link_session()` gained an optional `message_id` kwarg that indexes it (first-writer-wins); `resolve_request_by_message_ids(store_path, message_ids)` reads it back. `client.request_in_email_thread(sender_email, references_raw)` parses a raw header blob (`<...>` tokens) and resolves, shaped like `request_in_session()`. `erp_tools.py`'s `action=open`/`action=link` now pass `HERMES_SESSION_MESSAGE_ID`; `action=in_thread` branches to this path when `platform == "email"`.
- **Rejected an apparently-free shortcut**: reusing the existing `thread_id` field (already wired end-to-end — chat_id/thread_id-based `open_in_session()` already exists) instead of adding anything new. Checked `gateway/session.py::build_session_key()` first: `thread_id` directly feeds the session routing key. Setting a real value for email there would silently split every email sender's session history by thread, platform-wide, for every Hermes profile — not a cost worth paying for this one registry feature. Not done.
- Tests: `plugins/qkeee-erp/tests/test_request_registry.py` (`MessageIdIndexTests`), `test_client.py` (`RequestInEmailThreadFacadeTests`, plus `message_id`-threading cases on `RequestRegistryFacadeTests`), `test_plugin_erp_tools.py` (end-to-end `open`→index→`in_thread`→resolve, simulating the env var hermes-agent hasn't shipped yet).

**OPEN — the one piece this does not close, captured here for later recall:**

hermes-agent does not expose an inbound email's `In-Reply-To`/`References` header to any plugin today — confirmed by reading `plugins/platforms/email/adapter.py` (captures `in_reply_to` into `msg_data` but never surfaces it past `MessageEvent.reply_to_message_id`, which only feeds platform-specific OUTBOUND reply-anchoring, e.g. Telegram), `gateway/session_context.py`'s fixed `_SESSION_VARS` tuple (no such var), and `gateway/platforms/event.py`'s `SessionSource`/`build_source()` (no such field). Scanned hermes-agent's open issues/releases 2026-11-24: nothing in flight covers this — closest is #156006 ("a post-send plugin hook carrying the platform message ids of a sent turn", open, P3, unassigned), which would only give a plugin the BOT's own sent id, not the inbound chain this needs; #147452 (session_key to tool handlers, open, P3, patch-ready) is unrelated.

**To close:** one new, purely additive field — `SessionSource.email_references: Optional[str] = None` → a new `email_references` kwarg on `gateway/platforms/base.py::build_source()` → one new ContextVar in `gateway/session_context.py` (`HERMES_SESSION_EMAIL_REFERENCES`) → `gateway/run.py::_set_session_env()` passing `context.source.email_references`. Zero behavior change for any existing platform/caller (every other call site passes nothing, defaults to `""`/`None`); confined to ~4 small edits, none touching `build_session_key()` or anything else load-bearing. Only `plugins/platforms/email/adapter.py` needs to actually populate it: capture `msg.get("References", "")` in `_message_metadata()` (already has `in_reply_to`, needs `references` too — read fresh per-message, NOT from the racy per-sender `_thread_context` slot implicated in upstream bugs #143299/#84533) and pass it through `build_source(email_references=...)` in `_dispatch_message()`.

Once that lands: `erp_tools.py::_email_references()` (already written, currently returns `""` unconditionally since the var doesn't exist) needs no change at all — it already reads `HERMES_SESSION_EMAIL_REFERENCES`. The feature activates with zero further erpnext-hermes work.

Not filed as a numbered issue yet (still inside issue 03's own limitations). Promote to its own ticket — in this repo's tracker, and/or as a hermes-agent upstream issue alongside #156006/#147452 — before picking it up for real.
