# L3: ERPNext calls as native plugin tools; credentials out of terminal reach

Status: implemented (erpnext-hermes `feat/discovery-write-readiness` ab5ef65, 2026-10-08); dev-erp retest pending
Type: task
Blocked by: none

## Problem

The ERPNext API key is in `$HERMES_HOME/qkeee-erp.env`, which the terminal can read. Any check in `client.py` runs in the agent's own sandbox, so a deliberate bypass (`curl` with the bot key) skips all of them.

## Change

- Register ERPNext reads and writes as Hermes plugin tools (`PluginContext.register_tool`, see `hermes-agent/plugins/google_meet/__init__.py`). They run in the gateway process.
- The tools take the requester from the session ContextVar (`gateway/session_context.py`). There is no `requested_by` parameter.
- The tools reuse `core/client.py` and `core/operations.py` as a library.
- Move the API key and secret out of `qkeee-erp.env` into the plugin's own scoped secret, which is not passed through to the terminal or `execute_code`.
- The terminal scripts stay for CLI and cron use, with credentials available only there.

## Decisions needed

- Tool surface: one generic tool (query/get/report/execute_write op) or one tool per operation?
- How CLI and cron get credentials once the file is not readable from gateway sessions.

## Related

Pair with the narrow-role bot account (`qkeee-erp-write-path-hardening/issues/05-decide-rbac-strategy-and-admin-credential.md`), so a leaked key cannot do everything.

## Comments

**Decided 2026-10-08** (grill-with-docs).

- **Tool surface: four tools, one per operation kind**: `erp_query`, `erp_get`, `erp_report`, `erp_execute_write`. The business op name is an argument to `erp_execute_write`, validated against the write-path 07 registry. A platform toolset can then drop writes, and hooks can target writes by name.
- **Packaging:** one `qkeee-erp` plugin in `erpnext-hermes/plugins/`, shared with issue 09 (one origin resolver module). Enabled in cont-init like `018-hermes-lcm`.
- **Credentials:** ERP keys live only in the gateway (under `/opt/data`). The agent reaches ERPNext only through these tools, never through terminal scripts; `SKILL.md` drops that path. With issue 06 (`docs/adr/0002-terminal-on-ssh-sidecar.md`) the terminal cannot read them.
- **CLI:** manual runs by a trusted operator inside the gateway container (`docker compose exec hermes ...`), passing `--requested-by`.
- **Cron:** `cron/jobs.json` is empty. A cron turn has no Sender, so writes are refused. Decide when the first job exists.

**Implemented 2026-10-08** (ab5ef65). Deviation approved by the user: a fifth tool `erp_discover` (health, whoami, roles, apps, modules, meta, resolve, preflight). `erp_execute_write` has phases `list_ops`, `render`, `execute` (+ `batch`). Mode is read from `skills.config.qkeee_erp.mode`, never a tool argument. Credentials moved to `<HERMES_HOME>/plugin-data/qkeee-erp/qkeee-erp.env` by cont-init `018-qkeee-erp-plugin`. The terminal can still read that file until issue 06 lands.

Manual steps on dev-erp: boot once with `HERMES_PROFILE_AUTO_UPDATE=1` (pulls `plugins/qkeee-erp`). `config.yaml` is preserved on update, so apply the `platform_toolsets` / `known_plugin_toolsets` changes by hand (or `--force-config`).
