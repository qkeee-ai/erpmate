# Google Chat adapter: /setup-files gets users/{id} instead of the sender email

Status: implemented (hermes-agent branch `fix/google-chat-setup-files-sender-email` 433865ea07, 2026-10-08, local only); upstream PR decision pending
Type: task
Blocked by: none
Repo: `hermes-agent`, `plugins/platforms/google_chat/adapter.py`

## Problem

`_dispatch_message()` passes `sender_email=event.source.user_id_alt` to `/setup-files`, and the comment says `user_id_alt` is the sender email. But `_build_message_event()` sets `user_id_alt=sender_name`, which is `users/{id}`. The email is in `user_id`.

Effect: per-user OAuth tokens from `/setup-files` are saved under `users_<id>.json`. `_send_file` looks them up by email (`_last_sender_by_chat`), so it never finds them and falls back to the legacy token or a text notice.

## Change

Pass `event.source.user_id` when it contains `@`, else `None`. Fix the comment. Add a test that `/setup-files` receives the email.

## Note

Upstream adapter code. Check whether the fix should go upstream rather than into a local patch.

## Comments

**Implemented 2026-10-08.** `_dispatch_message` passes `source.user_id` when it holds `@`, else `None`. Two tests in `tests/gateway/test_google_chat.py`. Branch is cut from upstream `main` and not pushed: decide whether to open a PR on NousResearch/hermes-agent.
