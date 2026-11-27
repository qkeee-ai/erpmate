# L1: bind the requester to the gateway session in client.py

Status: resolved (erpnext-hermes `develop` 89e5ec6; dev-erp retest passed 2026-10-07, all three checks)
Type: task
Blocked by: none

## Change

All in `skills/qkeee-erp/qkeee-erp-associate/scripts/core/client.py`:

- **`whoami` subcommand** (`session_identity()`): prints `HERMES_SESSION_PLATFORM`, `USER_ID`, `USER_ID_ALT`, `USER_NAME` and `resolved_sender_email`. No network call.
- **Gate session binding:** `_validate_prod_requester()` refuses a `requested_by` that differs from the session sender email (case-insensitive). Before, only the CLI wrappers (`resolve_requested_by()`) did this, so `requested_by=` library calls were not bound. Audit reason: `requester_not_session_sender`.
- **Email-platform refusal:** on `google_chat` / `email` with no sender email, the gate refuses outright instead of accepting a typed requester. Audit reason: `email_platform_without_session_sender`.
- **`execute_code` refusal:** `_request()` raises `SandboxedCallerError` (a `GateRefusal`, CLI exit 3) when `HERMES_RPC_SOCKET` is set. That sandbox strips session vars, so binding is impossible there.
- **Skill text:** `SKILL.md` step 3 is now a per-turn procedure (run `whoami`, omit `--requested-by`, refuse on email platforms with no email, never reuse an earlier turn's value). `01-connectivity.md` and `00-conventions.md` were updated to match.

## Tests

`test_client.py`: `GateSessionBindingTests`, `EmailPlatformRequiresSenderTests`, `ExecuteCodeSandboxRefusalTests`, `SessionIdentityTests`. Full skill suite: 338 passed (run from `scripts/`, where `conftest.py` lives).

## Retest on dev-erp

1. In Google Chat, ask for a read. Expect `whoami` first and no `--requested-by` in the command.
2. Ask the agent to run a read with `--requested-by someone-else@qkeee.in`. Expect exit 3, "not this session's authenticated sender".
3. Ask the agent to query ERPNext from `execute_code`. Expect `SandboxedCallerError`.
