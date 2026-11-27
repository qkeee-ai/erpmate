# Email reply must preserve To/Cc and thread in-trail

Status: ready-for-human — implemented, tested, committed in hermes-agent (d7c25e0efd); live check open
Type: task
Blocked by: none
Findings: U1, U2
Decision: D1 (spec.md)

## Problem

Observed live on prod-erp, 18 Nov: when the agent replies to a user over an email chain, it reverts to a sender-only reply instead of keeping the original To/Cc list, and sends a fresh email instead of a threaded reply (quoted trail, `In-Reply-To`/`References` headers pointing at the original message).

Root cause not yet located precisely — needs a read of the send-side reply path in `erpnext-hermes/plugins/platforms/email/adapter.py` (the only email-adapter code audited so far is the session-keying logic at L809-810, which is adjacent but not confirmed to be the same code path as reply construction).

## Change

- Locate the reply-send function in `adapter.py`.
- Preserve original To + Cc (minus the bot's own address) on reply, not just the From: sender.
- Set `In-Reply-To` and append to `References` using the original message's `Message-ID`.
- Quote the original message body below the new reply (standard trail format), rather than sending a standalone body.

## Acceptance

- Live test: send an email to the bot with a second human Cc'd, reply from the bot, confirm the Cc survives in the sent message's headers and the client shows it threaded under the original.

## Comments

2026-11-19: Implemented in `hermes-agent` `plugins/platforms/email/adapter.py` (not erpnext-hermes
— the ticket's file location was wrong; confirmed by direct read, this is where the session-keying
logic at the cited lines actually lives). In-Reply-To/References threading was already correct
pre-diff; only Cc-preservation and body-quoting were missing.

- `_message_metadata` now parses To/Cc via a new `_extract_email_addresses` helper
  (`email.utils.getaddresses`); `_dispatch_message` carries them plus sender_name/date/body into
  `_thread_context`.
- `_new_reply` sets Cc from original To+Cc minus the bot and minus the primary To (new
  `_reply_cc_list`), and appends a quoted `>`-prefixed trail of the original body (new
  `_quote_trail`).
- 6 new tests in `tests/gateway/test_email.py::TestThreadContext`. Full `test_email.py` passes
  except one pre-existing, unrelated failure in `_extract_email_address` parsing (confirmed
  identical on the pre-change base). Also ran every other test file importing this adapter
  (`test_email_robustness.py`, `test_adapter_connect_classification.py`, etc.) — all failures there
  are pre-existing too (missing `pytest-asyncio` in this dev env, confirmed on base).

Committed `d7c25e0efd`.

**`/code-review` (Standards+Spec), 2026-11-19:**
- Standards: no hard violations. Judgement-call smells only: the ordered-dedupe idiom is
  duplicated between `_extract_email_addresses`/`_reply_cc_list` (could extract a
  `_dedupe_ordered` helper); `_thread_context` is growing into a 7-key dict bag (a small dataclass
  would read cleaner); `_parse_fetched_headers`'s header-only preflight dict still lacks `to`/`cc`,
  diverging from the main parse dict's shape (harmless today — nothing reads it off that path —
  but worth remembering if a header-preflight caller ever needs them). Not fixing any of these now;
  noted for a future pass if this file gets touched again.
- Spec: one real finding, judged and accepted rather than fixed: other original **To**-recipients
  (not the sender, not the bot) land in **Cc** on the reply, not To — `_new_reply` has always had a
  single-address `to_addr` contract (the chat_id/session key throughout this adapter), and widening
  it is a bigger change than D1's "smallest fix that closes U1/U2" scope. This satisfies the
  acceptance test (a single Cc'd human survives) and the actually-observed bug (sender-only reply),
  but is not literal "preserve original To" for a multi-To thread. Left as a documented, deliberate
  limitation (see `_reply_cc_list`'s docstring) rather than re-scoped — flagging here in case a
  future multi-recipient-thread incident makes it worth revisiting.

Live check (acceptance: send an email with a second human Cc'd, reply from the bot, confirm Cc
survives and the client shows it threaded) still open.
