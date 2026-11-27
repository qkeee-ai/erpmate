# Spike: Plugin CLI, package imports and terminal probe under the real Hermes loader

Status: resolved
Type: research
Blocked by: none

## Questions

1. Is a CLI command registered by a **profile** plugin (`<profile>/plugins/qkeee-erp`) available as `hermes -p <profile> qkeee-erp …`? Is the plugin loaded for every CLI run, or only for `gateway` / `chat`?
2. Does the loader (`hermes-agent/hermes_cli/plugins_loader.py`) make `from .qkeee_erp.core import client` work inside the plugin, also after a plugin reload?
3. How do tests import the plugin as a package without Hermes (a conftest that registers it under a fixed module name)?
4. Can a CLI command create the configured terminal environment (`tools/terminal_tool_backends._create_environment`) outside a gateway session and run one command through it, for the Isolation probe (issue 10)? Does that trigger the skills sync to the remote?
5. Can `register()` read the live terminal config (backend, host, user, port) for the fingerprint check without side effects?
6. Full inventory of script entry points and their arguments (`client.py`, `discover.py`, `execute_write.py`, `confirm_token.py`, `init_bot.py`, `memory_promote.py`), for issue 05.

## Method

Read `hermes_cli/plugins.py` (`register_cli_command`, where `_cli_commands` is consumed), the loader and the terminal backend factory. Prove 1, 2 and 4 with a throwaway plugin in a local hermes-agent checkout. No erpmate changes.

## Answer

Resolved 2026-10-09. Proved with a throwaway plugin (`spike-erp`, package `spike_lib/core/client.py` with a relative import) under hermes-agent installed in a scratch venv (`uv pip install -e .`), Windows, local checkout.

1. **CLI from a profile plugin: yes.** `hermes -p spk spike-erp --x 2` ran the handler with `HERMES_HOME=<root>/profiles/spk`. `_apply_profile_override` (`hermes_cli/main.py`) sets `HERMES_HOME` before imports, and the user plugin dir is `get_hermes_home()/plugins` (`plugins_discovery.collect_directory_manifests`). **When it loads:** `_register_plugin_cli_commands` runs `discover_plugins()` (every enabled plugin's full `register()`) whenever the first positional token is not a built-in (`_plugin_cli_discovery_needed`). Built-ins (`chat`, `gateway`, `profile`, …) skip that step at argparse time and load plugins on their own path.
   Consequences:
   - The plugin must be in `plugins.enabled`, or `hermes qkeee-erp` fails with `invalid choice`.
   - **If `register()` raises, its CLI command is rolled back too** (proved: `register_cli_command(...)` then `raise` → `invalid choice`). Discovery errors are logged at debug only. So `register()` must never raise, or `hermes qkeee-erp setup` becomes unreachable exactly when it is needed. Today's `register()` re-raises when the library is missing; issue 04 must stop that, issue 07 must keep it so.
   - `register()` runs on every plugin CLI invocation, so it must stay cheap and side-effect free (fits D7).
2. **Package imports: yes, also after reload.** The loader imports a directory plugin as `hermes_plugins.<slug>` (`qkeee-erp` → `hermes_plugins.qkeee_erp`) with `__path__ = [plugin dir]` (`plugins_loader._load_directory_module`). `from .spike_lib.core import client` resolved to `hermes_plugins.spike_erp.spike_lib.core.client`, and a relative `from .. import marker` inside it worked. `discover_and_load(force=True)` evicts `hermes_plugins.<slug>` and every `hermes_plugins.<slug>.*` before re-import: a changed `marker.py` was picked up on the second load. Module globals (e.g. `client.set_session_env_reader`) reset on reload, so `register()` must set them every time (it does). A second Hermes home in the same process gets `hermes_plugins.<slug>__home_<digest>`, so nothing may hard-code the module name.
3. **Tests without Hermes:** the conftest does what the loader does: `importlib.util.spec_from_file_location("qkeee_erp_plugin", <plugin>/__init__.py, submodule_search_locations=[<plugin>])`, put the module in `sys.modules`, exec it. Tests import `qkeee_erp_plugin.erp_tools`, `qkeee_erp_plugin.qkeee_erp.core.client`, … Do **not** also put the plugin dir on `sys.path` or alias `qkeee_erp` as a top-level name: a submodule imported under a second name loads twice (two registries, patches miss). `__init__.py` may import Hermes modules only inside `register()`.
4. **Terminal probe from a CLI command: yes.** `tools.terminal_tool._get_env_config()` + `tools.terminal_tool_backends._create_environment(env_type, image="", cwd=…, timeout=…, ssh_config={"host","user","port","key"}, task_id=…, probe_only=True)` works outside a gateway session (proved with `local`: `execute("echo probe-ran")` → rc 0). For `ssh`, `probe_only=True` opens a throwaway ControlMaster on its own socket and **skips** remote-home detection, dir creation and `FileSyncManager`: no skills sync up, and `cleanup()` does no `sync_back` (`tools/environments/ssh.py` `__init__`/`cleanup`). An unreachable target raises `EnvironmentConnectionError` from the constructor (proved: port 2 → "Connection refused"). Issue 10 must treat that as "not proven", never as "read failed = isolated". `execute()` on a probe-only ssh env was not run against a real sshd here; confirm in issue 10 against the sidecar.
5. **Fingerprint in `register()`: yes, with one caveat.** `_get_env_config()` returns `env_type`, `ssh_host`, `ssh_user`, `ssh_port`, `ssh_key`. It reads `TERMINAL_*` env vars, after a one-time `_ensure_terminal_env_bridged()` that back-fills them from `config.yaml` `terminal.*` into `os.environ` (process-global, idempotent, skipped under a per-turn terminal scope). Launchers already do the same bridge, so it is benign, but it is a write. Side-effect-free alternative: read `load_config_readonly()["terminal"]` and let set `TERMINAL_*` env vars win, which is the same precedence the bridge applies. Use the env view, because that is what the terminal tool will actually use.
6. **Script entry points (for issue 05):**

| Script | Global args | Subcommands / args |
|---|---|---|
| `core/client.py` | `--tag`, `--requested-by`, `--session-id`, `--domain-code`, `--channel`, `--channel-metadata`, `--prompt-summary`, `--latest-prompt` | `health`; `list-envs`; `whoami`; `query <doctype> [--filters JSON] [--fields JSON] [--limit 20]`; `get <doctype> <name> [--no-strip]`; `report <report_name> [--filters JSON]`; `roles [--user]` |
| `discover.py` | `--tag` (req), `--requested-by` (req), `--session-id`, `--domain-code` (default `qkeee-erp-associate/discover`), `--channel`, `--channel-metadata`, `--prompt-summary`, `--latest-prompt` | `apps`; `modules`; `meta <doctype>`; `resolve <doctype>`; `preflight <doctype> [--payload JSON]` |
| `execute_write.py` (`main(argv)`) | `--list-ops`; `--op`, `--args JSON`, `--batch JSON`; `--tag`, `--mode {read-only,read-write}`, `--requested-by`; legacy `--domain`, `--doctype`, `--action`, `--payload`, `--name`, `--expected-modified`; `--kyc`, `--kyc-waiver-confirmed`, `--purchase-sourced-item`, `--staged-fields`, `--confirmed-mappings`; `--confirmation-token`, `--issued-at`, `--user-confirmation-text`, `--user-approved`, `--approval-note`; audit: `--session-id`, `--domain-code`, `--channel`, `--channel-metadata`, `--prompt-summary`, `--latest-prompt` | — |
| `core/confirm_token.py` | — | `render --op --args JSON --tag --requested-by [--session-id] [--domain-code] [--channel] [--channel-metadata] [--prompt-summary] [--latest-prompt]` |
| `init_bot.py` | `--tag` (req), `--requested-by` (req), `--dry-run`, `--confirm-token`, `--issued-at INT` | — |
| `core/memory_promote.py` | `--env-tag` (req), `--findings-file`, `--summary`, `--skill-exists` | — |

   Note: SKILL.md tells the agent to run `memory_promote.py` in its terminal. After issue 04 the library is gateway-only, so issue 05/06 must give that step a new home (Operator CLI or an `erp_*` action).
