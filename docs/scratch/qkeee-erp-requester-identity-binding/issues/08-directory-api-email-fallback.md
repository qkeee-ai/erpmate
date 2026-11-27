# Fallback: resolve users/{id} to email via the Admin SDK Directory API

Status: needs-info (only needed if Google Chat ever delivers an event without `sender.email`)
Type: task
Blocked by: none
Repo: `hermes-agent`, `plugins/platforms/google_chat/adapter.py`

## Context

The 2026-10-07 dev-erp test showed the email arrives. The Chat API docs guarantee `User.email` only for user-auth API calls, not for app-auth events, so this may change. Today a missing email makes the gate refuse (issue 01), which is safe but blocks the user.

## Change

In `_build_message_event()`, when `sender.email` is empty and `sender.name` is `users/<digits>`:

- call Admin SDK `users.get(userKey=<digits>)` and use `primaryEmail`
- auth: the bot service account with a read-only Users admin role (or domain-wide delegation), scope `admin.directory.user.readonly`
- run it off the event loop (`asyncio.to_thread`) and cache id → email with a TTL
- on failure, leave the email empty (fail closed)

The Chat `users/{id}` equals the Directory user id and the People API person id (Chat API `User.name` reference).

## Trigger to pick this up

A Google Chat session where `client.py whoami` shows `HERMES_SESSION_USER_ID=users/...`, or the gate logs `email_platform_without_session_sender`.
