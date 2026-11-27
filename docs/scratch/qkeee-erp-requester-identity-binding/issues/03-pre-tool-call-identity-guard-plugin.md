# L2: gateway pre_tool_call guard against identity override

Status: implemented (erpnext-hermes `feat/discovery-write-readiness` 6902f4e + review fix a0e8261, 2026-10-08); dev-erp retest pending
Type: task
Blocked by: none

## Problem

The agent writes the terminal command, so `HERMES_SESSION_USER_ID=x python client.py …` overrides the gateway's value. The agent can also `cat` or source `qkeee-erp.env` and call ERPNext directly.

## Change

Add a Hermes plugin to `erpnext-hermes` that registers a `pre_tool_call` hook. The hook runs in the gateway process, can block a call, and fails closed on timeout (`hermes-agent/hermes_cli/plugins_dispatch.py`).

Block when:

- a `terminal` command sets, exports, unsets or `env`-overrides any `HERMES_SESSION_*` name
- a `terminal` command reads `qkeee-erp.env` (`cat`, `source`, `.`, `grep`, `python -c open(...)`, and similar)
- `execute_code` source references `qkeee-erp.env`, `core.client`, or `/api/resource`

Return a block message that names the rule.

## Limits

Pattern matching. `eval`, base64 or a script written to disk and then run can get past it. Defence in depth until issue 04 lands; not a boundary.

## Tests

Table-driven hook tests: allowed commands (`python …/client.py query …`) and blocked ones (each pattern above).

## Comments

**Implemented 2026-10-08.** `plugins/qkeee-erp/identity_guard.py`, registered as a second `pre_tool_call` hook. Rules: `session_override`, `env_file`, `core_client`, `erpnext_api`. Beyond the ticket: `/api/method`, `env -i`, file tools' path arguments, session rules on execute_code too. Review fix a0e8261 added `printf -v`, `read`, `+=`, `setdefault`/`__setitem__`, env-file globs, any `plugin-data` path; file-tool content no longer counts. Still pattern matching only.
