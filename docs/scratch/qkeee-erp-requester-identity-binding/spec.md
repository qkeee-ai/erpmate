# qkeee-erp requester identity binding (Google Chat)

Status: ready-for-human. Issue 01 is resolved (dev-erp retest passed 2026-10-07); issues 02–08 are deferred; 09–10 (kanban) raised 2026-10-07.
Raised: 2026-10-07, from the Google Chat identity research on the dev-erp Hermes profile
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (skill `skills/qkeee-erp/qkeee-erp-associate/`), `hermes-agent` (gateway, Google Chat adapter)

## Goal

Every ERPNext call made for a Google Chat message is attributed to the real sender's email, taken from the gateway. The agent cannot choose, guess, reuse or substitute the requester.

## Findings

- **Google Chat delivers the sender email.** The dev-erp `client.py whoami` run on 2026-10-07 showed `HERMES_SESSION_USER_ID=<sender email>` and `HERMES_SESSION_USER_ID_ALT=users/<numeric id>`. No config change was needed.
- **The gap was agent behaviour.** The system prompt shows only the display name. The skill told the agent to "resolve" the requester itself, so it used the display name, an earlier turn, or the bot's own account.
- **`execute_code` strips every `HERMES_SESSION_*` var** (`hermes-agent/tools/code_execution_env.py`, `_HERMES_CHILD_ALLOWED`). Session binding never applied there.
- **The terminal gets session vars fresh per command** from ContextVars (`hermes-agent/tools/environments/local.py`, `_inject_session_context_env`). An inline `HERMES_SESSION_USER_ID=… python …` prefix still overrides them.
- **The ERPNext API key is readable from the terminal** (`$HERMES_HOME/qkeee-erp.env`). Every check in `client.py` runs inside the agent's own sandbox. It stops mistakes, not a deliberate bypass (`curl` with the bot key).
- **Fallback if the email is ever missing:** the Chat `users/{id}` equals the Admin SDK Directory user id and the People API person id. See issue 08.

## Layers

| Layer | What | Issue | State |
|---|---|---|---|
| L1 | Client-side binding: `whoami`, gate session binding, `execute_code` refusal, email-platform refusal | 01 | done |
| L1+ | Drop `code_execution` from the `google_chat` toolset | 02 | implemented, retest pending |
| L2 | Gateway `pre_tool_call` guard plugin | 03 | deferred |
| L3 | Native plugin tools; credentials out of terminal reach | 04 | implemented, retest pending (terminal reach needs 06) |
| L4 | Signed identity assertion verified by ERPNext | 05 | deferred |
| — | Terminal backend isolated from gateway | 06 | deferred |
| K | Kanban: force triage + carry requester to workers (plugin) | 09 | implemented, retest pending |
| K+ | Upstream origin columns + forced-triage config | 10 | needs-triage |

Related, tracked elsewhere: narrow-role bot account — `qkeee-erp-write-path-hardening/issues/05-decide-rbac-strategy-and-admin-credential.md`.

## Residual risk after L1

- Inline env override in a terminal command.
- Direct HTTP with the bot API key from the terminal.
- The bot account is privileged, so a leaked key can do anything.

Recommended order: 02 → 04 (+ narrow-role bot) before PROD → 05 if audit needs proof of identity → 03 as defence in depth until 04 lands.
