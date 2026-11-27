# qkeee-erp: Profile + Plugin split

Status: ready-for-agent (design agreed 2026-10-09 after grilling; issues 01–12)
Raised: 2026-10-09, after identity issues 03/04/06 put ERP code in a gateway plugin
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (erpmate: Profile + `plugins/qkeee-erp`), `hermes-agent` (reference only)
Decisions: `docs/adr/0005-profile-and-plugin-split.md`, `docs/adr/0006-erp-access-requires-proven-isolation.md`
Terms: `CONTEXT.md` (Profile, Plugin, Isolation, Operator, Instance Notes, Deployment)

## Goal

All ERP code lives in the Plugin and runs only in the gateway. The Profile defines the agent. A Docker install, a Linux/macOS host install, an update and an existing install (dev-erp, demo) all reach the same state through one command, `hermes qkeee-erp setup`. ERPNext access exists only on a Deployment with proven Isolation.

## Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Product shape | Profile-first. The Plugin owns its own config and usage rules and reads no Profile files, so it could serve another agent later. |
| D2 | Library home | Plugin package `plugins/qkeee-erp/qkeee_erp/`. The skill keeps docs only. |
| D3 | Distribution | Same repo, bundled (`distribution_owned: plugins/qkeee-erp`). One version for Plugin and Profile. |
| D4 | Operator commands | `hermes -p <profile> qkeee-erp <command>`. ERP commands name a Requester (`--requested-by`), pass the Requester Gate, audit channel `cli`. Setup, repair and diagnostic commands need no Requester: `health` runs as the bot account, like the `erp_discover health` tool (decided 2026-10-09). |
| D5 | Install path | One idempotent `setup` in the Plugin. Docker hooks only call it; host Operators run it after `profile install` / `update`. Root-only steps stay outside. |
| D6 | Profile settings | The Plugin checks them as prerequisites and fails closed; it changes them only with `setup --apply-profile-fixes`. |
| D7 | Unmet step or prerequisite | No `erp_*` tools registered; log "run `hermes qkeee-erp setup`". The Plugin never writes config on load. |
| D8 | Isolation | Required on every Deployment, every Instance (ADR 0006). Proven by a probe; stored with a terminal-config fingerprint. |
| D9 | Isolation mechanism | Hermes `ssh` backend: sidecar container (Docker), separate local OS user (Linux/macOS host). Windows later. One ERP-enabled Profile per terminal target. |
| D10 | Plugin config | Moves from `skills.config.qkeee_erp` to `plugins.entries.qkeee-erp.settings`. The agent learns the active Instance from `erp_discover health`. |
| D11 | Tool usage rules | Plugin: tool descriptions + load-on-demand skill `qkeee-erp:usage`. Profile skill: ERP domain know-how. |
| D12 | Instance Notes | Stay in `skills/qkeee-erp-learned/`; owned by the Deployment; updates must not touch them. |
| D13 | Rollback | No down-steps. Restore the volume backup or the `config.yaml.bak-*` that `setup` writes. |

## Facts this design rests on

- `profile update` replaces each `distribution_owned` path with `rmtree` + `copytree` and keeps `config.yaml` unless `--force-config` (`hermes-agent/hermes_cli/profile_distribution.py`). erpmate owns all of `skills/`, so every update deletes Instance Notes today (issue 01).
- A distribution manifest has no plugin-dependency field and no install/update hook. A plugin has `register()`, `register_cli_command`, `register_skill` (explicit load, not listed) and `on_session_start`.
- The `local` terminal is "the user's trusted operator shell" (`tools/environments/local_env_policy.py`): it strips provider keys from the env but blocks no files.
- The ssh backend syncs skills, skill credential files and `cache/` to the terminal home and syncs changed skill files back on cleanup (`tools/environments/file_sync.py`).
- CI runs only `skills/qkeee-erp/*/scripts` tests, on push to `main` only; the default branch is `develop` (issue 02).

## Boundary

| Profile | Plugin |
|---|---|
| SOUL.md, profile.md, cron | `qkeee_erp/` library: client, operations, confirm_token, kanban_origin, memory_promote, domains/, discover, execute_write, schema_mapping, doctype_defs, item_write_helpers, init_bot |
| `config.yaml`: toolsets, terminal backend, skills settings | Own settings: mode, active Instance, Instance tags, setup state |
| `skills/qkeee-erp/*`: ERP domain know-how, no code | `erp_*` tools, identity guard, Kanban hooks, `qkeee-erp:usage` skill |
| Docker: compose, `docker/terminal/`, thin boot hooks | Operator CLI incl. `setup` |
| | `plugin-data/qkeee-erp/`: credentials, setup state, Isolation record, Kanban origin store |

Rule: if it needs the ERPNext credentials or must run in the gateway, it is Plugin. If the agent reads it to decide how to work, it is Profile. The Plugin may read Profile settings to check prerequisites; it writes them only on `--apply-profile-fixes`.

## `hermes qkeee-erp setup`

Idempotent. Each step checks whether its result is already present. A failed step stops the run; the next run continues from it.

| Step | Kind | Does |
|---|---|---|
| S1 credentials | Plugin | Move a legacy `<profile>/qkeee-erp.env` into `plugin-data/qkeee-erp/` (mode 600). |
| S2 config | Plugin | Copy `skills.config.qkeee_erp` into Plugin settings once; drop `plugins.entries.qkeee-erp.settings.scripts_dir`. |
| P1 toolsets | Prerequisite | Chat platforms (google_chat, …) have `qkeee_erp` and no `code_execution`; `known_plugin_toolsets` lists `qkeee_erp`. |
| P2 terminal | Prerequisite | `terminal.backend == ssh` with host, user and key set. |
| S3 terminal key | Plugin | Create the gateway ssh client key if `terminal.backend == ssh` and none exists; print where the public key must go. |
| I2 exclusive target | Plugin | Refuse if another Profile under the Hermes root (`<root>/plugin-data/…` and `<root>/profiles/*/plugin-data/qkeee-erp/isolation.json`) records proven Isolation for the same `ssh_user@ssh_host` (loopback names folded to `localhost`). |
| I1 Isolation probe | Plugin | Through the configured backend (probe-only ssh: no sync), try to read the credentials file and `.env` by absolute gateway path. Isolated only if every read fails; a backend error or missing output marker is "not proven". Refuse without probing when `terminal.env_passthrough` / `terminal.credential_files` would hand a real session a secret (the probe-only connection does neither). Record result + fingerprint (backend, host, user, port, key as written, env_passthrough, credential_files) + target + probed paths in `plugin-data/qkeee-erp/isolation.json`. Waits for S1 and I2. |

- Before the first config write in a run: `config.yaml.bak-<UTC timestamp>`.
- `--dry-run` prints planned changes. `setup status` lists each step and prerequisite: done / pending / failed, with the reason.
- `--apply-profile-fixes` lets P1/P2 write the Profile settings (erpmate values). Without it they only report.

Run order is the table order: S3 follows P2, so `--apply-profile-fixes` can switch to ssh and create the key in one run (decided in issue 07). I2 runs before I1, so a shared terminal target is never probed or recorded as Isolated (decided in issue 10). The live fingerprint uses Hermes' precedence: a `terminal.*` key in config.yaml wins, a `TERMINAL_*` env var fills a key the config leaves out.

**On load (`register()`):** re-check every item against the live files and config, require `setup.json` to record a complete run for this plugin version (`plugin.yaml` `version`; a Profile update that brings a new plugin version needs `setup` again), read `isolation.json` and compare the fingerprint with the live terminal config. If anything is unmet, register no `erp_*` tools and log the `setup status` summary. Hooks (identity guard, Kanban origin) always register: they protect, they do not grant access.

## Install flows

**Docker, fresh:** 016 installs the Profile → 018 runs `hermes plugins enable qkeee-erp` → 0157 publishes the sidecar key (root) → `0185` runs `setup --apply-profile-fixes`. Operator adds credentials, runs `qkeee-erp init-bot`, then `setup` again (S1 and I1 now pass).

**Docker, update / existing install (dev-erp, demo):** boot once with `HERMES_PROFILE_AUTO_UPDATE=1`; `0185` runs `setup --apply-profile-fixes`. Runbook in issue 09.

**Linux/macOS host, fresh:**
1. Root, once: create OS user `hermes-terminal` with no read access to `HERMES_HOME`; enable sshd (key auth only, no forwarding; the sidecar `sshd_config` is the template).
2. `hermes profile install <erpmate> --name <p>`; `hermes -p <p> plugins enable qkeee-erp`.
3. `hermes -p <p> qkeee-erp setup --apply-profile-fixes` (sets ssh terminal, replacing the shipped Docker sidecar values on a host; creates key; prints the public key path).
4. Root: add the public key to `hermes-terminal`'s `authorized_keys`.
5. Add credentials; `hermes -p <p> qkeee-erp setup`; `setup status` shows all done, Isolation proven.

**Host, update:** `hermes profile update <p>` then `hermes -p <p> qkeee-erp setup`. If skipped, the Plugin registers no ERP tools and says why.

## Operator CLI

| Today | Becomes |
|---|---|
| `client.py health/whoami/roles/query/get/report` | `qkeee-erp health`, `qkeee-erp whoami`; `qkeee-erp roles/query/get/report --requested-by <email>` |
| `discover.py <action>` | `qkeee-erp discover <action>` |
| `execute_write.py`, `confirm_token.py render` | `qkeee-erp execute-write`, `qkeee-erp render` |
| `init_bot.py` | `qkeee-erp init-bot` |
| (new) | `qkeee-erp setup [--dry-run] [--apply-profile-fixes]`, `qkeee-erp setup status` |

## Testing

| Layer | What | Where / when |
|---|---|---|
| Unit | library, tools, guard, hooks | `python -m pytest plugins/qkeee-erp`; CI on PRs and push to `develop` |
| Plugin contract | `register()` against a fake ctx: tools present only when setup state and Isolation are good; hooks always present; CLI registered | unit suite |
| Setup | config fixtures (fresh, dev-erp-like, half-done, comment-heavy, non-erpmate agent): each step idempotent; prerequisites report without the flag and write with it; backup once per run; failure stops the run | unit suite |
| Isolation | probe with a fake backend (read succeeds / fails), fingerprint change, shared terminal target refused | unit suite |
| Distribution | install + update a profile with hermes-agent pinned to the base-image version: Instance Notes survive, old `scripts/` gone | CI job |
| Docker smoke | build, boot, hook logs, `setup status` all done, terminal runs in the sidecar | manual on a Docker host until CI has Docker |
| Linux host | runbook on a clean VM: Isolation proven; same user ssh → refused | manual, before calling host support done |
| Live acceptance | Google Chat scenarios on DEMO_ERP (issue 09) | after each rollout |

## Phases

1. **Prerequisites:** 01 (keep Instance Notes), 02 (CI).
2. **Spike:** 03.
3. **Move:** 04 (library), 11 (Plugin config), 05 (Operator CLI), 06 (docs split).
4. **Setup and Isolation:** 07 (setup + prerequisites + hook), 10 (Isolation probe), 12 (host runbook).
5. **Rollout:** 09. **Clean up:** 08, one release later.

## Known gaps (not in scope)

- **Requester data separation:** `cache/` (uploads, large tool results) from every session reaches the terminal. Hermes-wide; own effort.
- **Windows host Isolation:** later runbook (OpenSSH Server, NTFS ACLs).

## Risks

- **Import churn:** about 13k lines with flat imports. Move with `git mv` first, rewrite imports in a separate commit, run the full suite.
- **Hermes API drift:** `register_tool`, `register_hook`, `register_cli_command`, `register_skill`, the `pre_tool_call` directive. Pinned Distribution job catches it.
- **Comment loss** when `setup` writes a user's `config.yaml`. Backup kept.
- **Instance Notes may already be lost** on dev-erp from earlier updates. Check before the next update.
- **Host dev friction:** ERP tools need the ssh user even for DEMO_ERP (ADR 0006 revisit trigger).
