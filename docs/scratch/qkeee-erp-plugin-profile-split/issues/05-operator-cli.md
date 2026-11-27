# Operator CLI: hermes qkeee-erp <command>

Status: done
Type: task
Blocked by: 03, 04, 11

## Change

- `plugins/qkeee-erp/cli.py`: `register_cli_command("qkeee-erp", …)` with one subcommand per entry point from issue 03's inventory (spec table "Operator CLI"), same arguments.
- Every operator command that reads or writes ERPNext data on a Requester's behalf requires `--requested-by <email>`: a CLI run has no gateway Sender. Audit rows record the CLI channel. `health` (runs as the bot account) needs none.
- Credentials from `plugin-data/qkeee-erp/qkeee-erp.env`, the same path the tools use.

## Tests

- Argument parsing per subcommand.
- Each subcommand calls the same library function as the matching `erp_*` tool (no second code path).
- A subcommand without `--requested-by` is refused.

## Comments

2026-10-09 (erpnext-hermes `53597c4`, `75f9a83`):
- `plugins/qkeee-erp/cli.py`, registered in `register()` before the library loads: `health`, `whoami`, `list-envs`, `list-ops`, `query`, `get`, `report`, `roles`, `discover`, `render`, `execute-write`, `init-bot`. Exit codes 0/1/2/3/4. Verified under the real Hermes loader (`hermes qkeee-erp --help`, `list-ops`, refusal exit 3).
- One code path: each ERP command runs the matching `ErpTools` handler (requester + audit injected: `--requested-by`, channel `cli`, OS user in channel_metadata). `init-bot` calls `init_bot` directly (no tool).
- Library `_cli()`/`main()` entry points removed; their tests moved to `test_plugin_cli.py` / `test_write_failures.py`.
- **Deviations from "same arguments":** no `--mode` (plugin setting), no `--session-id/--domain-code/--channel/--channel-metadata` (fixed: generated session, `cli`), no legacy execute_write shorthand (`--domain/--doctype/--action/--payload/--kyc…`): use `--op` + `--args`. `discover` audits with domain code `qkeee-erp-associate` (as the tool does).
- **Deviation from spec table:** `health` needs no `--requested-by` (runs as the bot, like the `erp_discover health` tool; a diagnostic, D4 "repair commands need no Requester"). Accepted by the user 2026-10-09; spec D4 and the Operator CLI table updated.
