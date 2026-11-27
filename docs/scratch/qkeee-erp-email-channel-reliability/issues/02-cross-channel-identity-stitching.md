# Same user across email and chat must resolve to one identity/context

Status: ready-for-human (implemented 2026-11-20, committed 5f7be1f erpnext-hermes develop — live round trip not yet run)
Type: research
Blocked by: none
Findings: U3
Decision: D2 (spec.md)

## Problem

One person can raise or discuss the same request over email and over chat (Google Chat etc.). Today each channel's session is independent, keyed on a channel-local identifier only (email: `From:` address, `adapter.py:809-810`; chat: presumably a chat handle/space id — not yet audited). The agent has no way to recognize "this is the same human continuing the same task from a different channel," so it can't answer a chat question with context from the email thread or vice versa.

This sits next to the existing per-write `requested_by` resolution work (see `qkeee-erp-requester-identity-binding` memory/ADR) — that work resolves a channel-local sender to an ERPNext user for permission/audit purposes per write. This issue is about resolving a channel-local sender to one **person** across channels, for context/session purposes, which that work doesn't currently do.

## Open questions (needs-triage, not pre-decided)

- What's the identity key? Email address and chat handle are different namespaces — does a person record (ERPNext User, or a new mapping table) carry both, and who maintains that mapping?
- When two channel sessions turn out to be the same person on the same task, do they merge into one LCM session, or stay separate sessions that cross-reference each other's resolved facts?
- How does the agent decide "same task" vs. "a new, unrelated task from the same person"? (Needed before issue 03's requester/participant model can build on this.)

## Change

Not specified yet — this is a design task. Write a short design note here (under Comments) before touching code.

## Acceptance

TBD once design lands.

## Comments

**Draft design note (2026-11-20, nikhil.sharma@qkeee.ai asked for design-first; drafted by agent, pending sign-off):**

Path correction: the session-keying code is in `hermes-agent`, not `erpnext-hermes` (spec.md L49 cites the wrong repo). Email: `hermes-agent/plugins/platforms/email/adapter.py:869` builds `user_id=sender_addr` (the `From:` address). Google Chat: `hermes-agent/plugins/platforms/google_chat/adapter.py:992` builds `user_id=(sender_email or sender_name)` — and per `qkeee-erp-requester-identity-binding` memory, Chat already delivers the real sender email on dev-erp (verified via Directory API), so `sender_email` is the normal case, not the fallback.

Key finding: **for the two channels actually in use (email, google_chat), the identity namespaces already collapse to the same thing — a lowercased email address** — whenever Chat resolves a real sender email (the confirmed normal case). The stated "different namespaces" problem is narrower than the open question assumed.

The real gap is upstream of identity: session keying. `hermes-agent/gateway/delivery.py:149` builds `session_key` as `"<platform>:<chat_id>[:<thread_id>]"`. Even when both sessions' `user_id` is the identical email address, `email:<addr>` and `google_chat:<space_name>` are different session_keys with no cross-reference — that's the actual mechanism behind U3, not a missing identity mapping.

Answers to the three open questions:

1. **Identity key:** the email address itself, lowercased, used as-is — no new person record or mapping table. Rationale: both adapters already surface it as `user_id` when available (confirmed above); inventing a mapping table nobody populates is YAGNI. Chat platforms that can't deliver a real email (no Directory API, or other non-google_chat chat platforms later) are **out of scope** — flag as a tracked gap, don't solve speculatively (matches the "no feature flags/validation for scenarios that can't happen" convention).

2. **Merge vs. cross-reference:** **cross-reference, not merge.** `session_key` is load-bearing across delivery/obligation tracking (`delivery_ledger.py`), dead-letter tracking, and reply threading (issue 01's In-Reply-To/References work) for all ~27 platform adapters. Collapsing two session_keys into one session would mean rewriting that machinery for a benefit this issue doesn't need. Instead: at `session:start` (hook point already exists per `gateway/hooks.py` docstring — `platform, user_id, chat_id, thread_id, session_id` are in the hook context), look up other open/recent session_keys sharing the same `user_id` email, and inject a short summary of their resolved facts into the new session's context. Additive, not a session-model rewrite. Reuses the same sqlite-table-in-`plugin-data/` pattern as `kanban_origin.db` (`erpnext-hermes/plugins/qkeee-erp/qkeee_erp/core/kanban_origin.py`) for whatever small index this needs (e.g. email → list of open session_keys) — stdlib only, same convention.

3. **Same-task heuristic: don't build one — build a Request Registry instead.** Free-text "is this the same task" classification is open-ended and the failure mode (wrongly injecting unrelated-task context) is worse than the failure mode it fixes (missing context). Revised per 2026-11-20 discussion with nikhil.sharma@qkeee.ai:

   Every distinct request gets a durable row — not just an ERPNext record, since read-only asks (MIS report, GST report for a month) never produce one:

   - `request_id`: short `uuid4`, assigned once (not a content hash — nothing needs the ID to be derivable, and a hash design adds complexity for no benefit here).
   - `topic`: human-readable label, agent-generated from the first message (e.g. "GST report — Oct 2026"). Not a classification score; just a label.
   - `sender_email`: the identity key from (1).
   - `anchor`: ERPNext doctype+name when one exists; null for read-only asks.
   - `linked_session_keys`: list — one Request can span an email thread and a chat thread over time.
   - `agent_status`: `open` / `in_progress` / `done` — the agent's own belief, self-marked when it sends what it judges a final reply. Mutable.
   - `requester_confirmed`: `confirmed` / `disputed` / `null` (no response) — set only when the Requester answers the agent's own confirm-check. Kept separate from `agent_status` so neither field silently overwrites the other — the gap between them (agent says done, Requester hasn't confirmed) is itself a useful signal.
   - `created_at` / `closed_at`: timestamps.

   Store: `plugin-data/qkeee-erp/request_registry.db`, sqlite, same pattern as `kanban_origin.db` (`erpnext-hermes/plugins/qkeee-erp/qkeee_erp/core/kanban_origin.py`) — stdlib only.

   Ambiguity handling: when the agent can't tell if a new message continues an open Request or starts a new one, it says the topic+id out loud and asks ("Continuing REQ-7f3a, GST report Oct? or a new ask?") instead of guessing. The agent may also reference topic+id unprompted, as a running confirmation that it has the right thread (per nikhil.sharma@qkeee.ai's ask), not only when ambiguous.

   This gives issue 03 a hard key (identity email + request_id) to build its requester/participant model on, not a fuzzy score, and gives the weekly tasks-received-vs-completed report its data (agent_status + requester_confirmed + timestamps) as a free side effect of the schema, not a separate build.

**Scope decision (2026-11-20):** issue 02 builds the Request Registry schema and the cross-channel linking/confirm behavior only. The weekly report itself (query + output format over this schema) is a separate, later issue — filed as issue 11 (`11-weekly-request-report.md`), blocked by this one.

Net: no new identity infra beyond a small cross-session lookup keyed on existing `user_id` email values, plus the Request Registry above; the design risk the original open questions implied (new person/mapping table, LCM session-model rewrite, NLP task-matching) isn't needed for the confirmed-in-use channels.

**Decision needed from you:** sign off on (1)-(3) above, or redirect, before code starts.

**Implemented 2026-11-20 (erpnext-hermes develop, 5f7be1f):**

- `plugins/qkeee-erp/qkeee_erp/core/request_registry.py` (new): the sqlite
  Request Registry (`request` + `request_session` tables), same pattern as
  `kanban_origin.db`. `request_id` is a full `uuid4` (not truncated —
  caught and fixed in code review, see below).
- `plugins/qkeee-erp/qkeee_erp/core/client.py`: the encapsulation boundary
  over it — `request_open/lookup/link/status()` — the same layer every
  other core/ store is reached through. Lowercases `sender_email`
  throughout (decision 1). A new `RequestOwnershipError(GateRefusal)`
  refuses a `link`/`status` call whose `request_id` belongs to a
  different Requester. `sqlite3.Error` (lock/corruption) is translated to
  `ConnectorError` instead of crashing.
- `plugins/qkeee-erp/erp_tools.py`: new 6th tool `erp_request`
  (`open`/`lookup`/`link`/`status`), calling only the `client.py` facade
  above — never `request_registry` directly.
- `plugins/qkeee-erp/skills/usage/SKILL.md` §9: when/how the agent should
  call it (open on a new ask, lookup before assuming a continuation,
  always ask the Requester rather than auto-matching, status only after
  the Requester actually answers a confirm-check).
- Tests: `tests/test_request_registry.py` (new, store-level), new
  `RequestRegistryFacadeTests` in `tests/test_client.py` (facade-level,
  including the case-insensitive email matching this was built to fix),
  new `RequestToolTests` in `tests/test_plugin_erp_tools.py`
  (tool-level). 962 plugin tests pass; the one pre-existing unrelated
  failure (`test_match_the_shipped_config`, a `config.yaml` drift
  unconnected to this change) is untouched.
- `/code-review` (Standards + Spec axes) ran before commit; both axes'
  findings were fixed in the same pass except two, left as follow-ups:
  the `session:start` auto-surfacing hook (decision 2) lives in
  `hermes-agent`, not this repo — not delivered here, status unconfirmed;
  and `requester_confirmed` has no code-level proof the Requester actually
  answered, only SKILL.md wording — same class of gap as the open
  "same-task ask → confirm" question already flagged above.
- Not yet done: a live round trip against a running gateway session (no
  retest performed this session).
