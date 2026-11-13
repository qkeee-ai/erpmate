# erpnext-hermes Agent Profile

Git-tracked source of truth for a Hermes Agent profile specialized as an ERPNext functional consultant and operations agent — running the single `qkeee-erp-associate` skill across HR/Payroll, Accounts, Inventory, Procurement, Sales, Fixed Assets, System Admin, MIS reporting, Manufacturing, and Doc Extraction.

**Live Hermes profile:** `$HERMES_HOME/profiles/<profile-name>` — `~/.hermes/profiles/dev-erpnext` for a host CLI install, `/opt/data/profiles/dev-erp` inside the Docker image. The profile name is whatever `--name` (CLI) or `HERMES_PROFILE_NAME` (Docker) says; with neither it is derived from the source repo name.

Edit the portable files **here**, not in the live profile directory. On a host install Hermes reads them through symlinks back into this repo; under Docker they are copied in by `hermes profile install` at first boot, so a change here needs a `hermes profile update` or `HERMES_PROFILE_AUTO_UPDATE=1`.

Two deployment paths: [Hermes CLI](#hermes-cli-host) for a host install, [Docker](#docker) for the containerised agent (which also layers in the jev model router and the LCM context manager). Both are covered under [Getting started](#getting-started).

## Overview

### What this profile is

An ERPNext specialist that acts like a functional consultant, not a click-executor: it explains the "why" behind a process, detects per-instance customizations before assuming stock ERPNext behavior, and treats every submittable document as requiring human review before it goes live. Full scope boundaries (owns / should-not-own) are defined in [`profile.md`](./profile.md); identity and voice are defined in [`SOUL.md`](./SOUL.md).

The agent reaches ERPNext only through the `qkeee-erp` plugin's `erp_*` tools, which run in the gateway and take the requester identity from the gateway session — never from config. Calling rules ship with the plugin as the `qkeee-erp:usage` skill; the Operator CLI (`hermes -p <profile> qkeee-erp …`) runs the same handlers a chat turn does, so every code path is audited the same way regardless of who or what calls it.

### Audit logging and governance

Every ERPNext access — read or write, chat or CLI, any environment — goes through one connector (`plugins/qkeee-erp/qkeee_erp/core/client.py`) that unconditionally runs a requester-permission (RBAC) pre-check and writes an audit-log row. There is no debug flag or PROD-only carve-out that skips either check.

Core guarantees, enforced in code rather than by prompt discipline:

- **Token auth only.** `QKEEE_ERP_*` API key/secret pairs — no session-cookie or password fallback that would drop audit attribution.
- **Requester identity is never configured.** It is the gateway session's sender for the agent, or `--requested-by` on an Operator CLI run; there is deliberately no `_REQUESTED_BY` env var, and the connector ignores one if set.
- **Draft, review, submit — always.** Every docstatus-bearing document gets a review-before-submit step with explicit human confirmation (`profile.md`'s Safety Policy), enforced for submit/cancel by a fresh confirmation token, not by prompt discipline (`references/00-conventions.md`'s Non-negotiable 5).
- **Skill write approval.** `skills.write_approval: true` stages every agent-initiated skill write under `~/.hermes/pending/skills/` for `/skills approve`/`/skills reject` review — nothing lands unreviewed.
- **Skill source separation.** `qkeee-erp-associate` (this repo) mounts read-only via `skills.external_dirs` and is marked externally-owned so Hermes' autonomous background-review pass cannot silently patch its audit/RBAC/GRC logic. The satellite `qkeee-erp-learned/<env-tag>` skills the agent writes via `skill_manage` (per-instance notes: versions, custom doctypes, non-ERPNext API quirks) stay open to that same background review by design.

Every bot action lands in ERPNext itself, via doctypes `hermes qkeee-erp init-bot` provisions directly (no custom app):

| Doctype | Written | Holds |
|---|---|---|
| `Qkeee Bot Audit Log` | Every read and write, unconditionally | Action, reference doc, before/after payload, field diff, `domain_code` (which domain procedure made the call), `user_approved` (Approved/Not Confirmed/Not Required — detection, not a write gate), submittable/locked once resolved |
| `Qkeee Bot Job Approval` | First `batches=` write of a kanban-dispatched multi-step job | The `batches` plan and `job_token` it was computed over; an append-only `execution_log` (per-step `{batch_index, step_index, name, modified, outcome}`, succeeded and failed steps alike — survives a worker respawn); `monitor_platform`/`monitor_channel_id`, captured at render time, so a status update always addresses the session that actually dispatched the job |

See [Repository layout](#repository-layout) for where the code behind each of these lives, and [Safety & governance](#safety--governance) for the full policy list.

## Getting started

### Hermes CLI (host)

This repo *is* a Hermes profile distribution (`distribution.yaml`).

```bash
hermes profile install github.com/qkeee-ai/erpmate --alias --name dev-erpnext
hermes profile info dev-erpnext   # manifest: version, author, required env vars
hermes profile list               # all local profiles, distribution source column
```

A host (non-Docker) install gets ERPNext access only with proven Isolation (agents ADR 0006): the agent's terminal must run as a separate OS user that cannot read the gateway's files. Docker gets this for free from its terminal sidecar; on a host, root sets it up once. **Without these steps a host install has no ERPNext access**, not even to DEMO_ERP — the plugin registers no `erp_*` tools. The profile itself (chat, skills, cron) works either way. Windows hosts are not supported.

Names below: the gateway runs as your own user (`$USER`). `HERMES_HOME` is `~/.hermes`. The profile is `<p>`, so the profile home is `~/.hermes/profiles/<p>`. The terminal user is `hermes-terminal`; `setup` writes this name into `config.yaml`. The terminal user runs commands with the host's own tools (`bash`, `python3`), not the gateway's Hermes venv.

**0. Your user — get this repo** (steps 1 and 5 use files from it):

```sh
git clone https://github.com/qkeee-ai/erpmate.git && cd erpmate
```

**1. Root, once per machine — the terminal user and sshd.**

Linux:
```sh
sudo useradd --create-home --shell /bin/bash hermes-terminal
sudo usermod -p '*' hermes-terminal   # no password, not locked: key login works with UsePAM no too
sudo install -m 644 host/hermes-terminal.sshd.conf /etc/ssh/sshd_config.d/10-hermes-terminal.conf
sudo sshd -t && sudo systemctl reload-or-restart ssh   # the unit is `sshd` on RHEL/Fedora/Arch
```

macOS:
```sh
sudo sysadminctl -addUser hermes-terminal -shell /bin/bash -password "$(openssl rand -base64 24)"
sudo createhomedir -c -u hermes-terminal
sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist   # Remote Login on; or System Settings > General > Sharing
sudo dseditgroup -o edit -a hermes-terminal -t user com.apple.access_ssh   # only if Remote Login is limited to some users
sudo install -m 644 host/hermes-terminal.sshd.conf /etc/ssh/sshd_config.d/10-hermes-terminal.conf
sudo sshd -t   # launchd starts sshd per connection: no reload needed
```

The drop-in ([host/hermes-terminal.sshd.conf](host/hermes-terminal.sshd.conf)) applies the sidecar's login and forwarding rules to `hermes-terminal` (and `hermes-terminal-*`) only: key login, no forwarding. It needs `Include /etc/ssh/sshd_config.d/*` in `/etc/ssh/sshd_config` (default on Debian/Ubuntu, Fedora and macOS 13+); without that line, append the file's content to the end of `sshd_config`. If `sshd_config` has an `AllowUsers` line, add `hermes-terminal` to it. Confirm the drop-in applies to `hermes-terminal` and leaves your own user alone:

```sh
sudo sshd -T -C user=hermes-terminal,host=localhost,addr=127.0.0.1 | grep -Ei '^(authenticationmethods|allowtcpforwarding|x11forwarding|permittty)'
sudo sshd -T -C user=$USER,host=localhost,addr=127.0.0.1 | grep -Ei '^(authenticationmethods|permittty)'
```

**2. Your user — install the profile and lock down `HERMES_HOME`.** Other users must not be able to read anything under `~/.hermes`; the Isolation probe checks exactly this.

```sh
hermes profile install github.com/qkeee-ai/erpmate --name <p>
chmod 700 ~/.hermes
```

**3. Your user — enable the plugin and run setup.**

```sh
hermes -p <p> plugins enable qkeee-erp
hermes -p <p> qkeee-erp setup --apply-profile-fixes
```

`--apply-profile-fixes` sets the chat toolsets and points the terminal at the host user (`terminal.backend: ssh`, `ssh_host: localhost`, `ssh_user: hermes-terminal`, `cwd: ~`), replacing the shipped Docker-sidecar values while keeping any value you set by hand. Before it changes `config.yaml` it writes `config.yaml.bak-<UTC>`, and it creates the gateway's ssh key at `~/.hermes/profiles/<p>/.terminal-ssh/id_ed25519`. Credentials and Isolation stay pending until steps 4 and 5.

**4. Root — authorize the gateway key** for `hermes-terminal`, from this machine only (`restrict` also turns off forwarding and the pty):

```sh
PUB=~/.hermes/profiles/<p>/.terminal-ssh/id_ed25519.pub
TH=~hermes-terminal
LINE="restrict,from=\"127.0.0.1,::1\" $(cat "$PUB")"
sudo install -d -m 700 -o hermes-terminal "$TH/.ssh"
sudo touch "$TH/.ssh/authorized_keys"
sudo grep -qxF "$LINE" "$TH/.ssh/authorized_keys" || echo "$LINE" | sudo tee -a "$TH/.ssh/authorized_keys" >/dev/null
sudo chown hermes-terminal "$TH/.ssh/authorized_keys" && sudo chmod 600 "$TH/.ssh/authorized_keys"
```

**5. Your user — credentials, setup, check.**

```sh
mkdir -p -m 700 ~/.hermes/profiles/<p>/plugin-data/qkeee-erp
install -m 600 skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example ~/.hermes/profiles/<p>/plugin-data/qkeee-erp/qkeee-erp.env
hermes -p <p> qkeee-erp setup
hermes -p <p> qkeee-erp setup status
```

Fill in `qkeee-erp.env` before running `setup` (see [ERPNext credentials](#erpnext-credentials)). `setup status` exits 0 and shows every item `done`; read any non-`done` item against [Common issues](#common-issues). For a new ERPNext Instance, provision the bot account first: `hermes -p <p> qkeee-erp init-bot --dry-run --requested-by <email>`, then run it again with the printed confirm token (see [Operator CLI reference](#operator-cli-reference)). Restart the gateway — the plugin registers the `erp_*` tools when it loads.

**Self-check (proves the probe, not just the config):** set `terminal.ssh_user` to your own user and authorize the key for it; `hermes -p <p> qkeee-erp setup` then reports Isolation as failed and the gateway registers no `erp_*` tools. Set it back to `hermes-terminal` and run `setup` again.

Isolation covers the gateway's secrets only. Uploaded documents and large tool results from every session still reach the terminal (`cache/`); separating one Requester's data from another's is an open problem (see [Open items](#open-items)).

### Docker

[`Dockerfile`](./Dockerfile) + [`docker-compose.yaml`](./docker-compose.yaml) build on `nousresearch/hermes-agent:latest` and add this profile plus the **jev** model router and the **LCM** context manager. Everything is driven from `.env`:

```bash
cp env.example .env     # then edit — see the annotated blocks in that file
docker compose up -d --build
docker logs -f <container>
```

The container name comes from `HERMES_CONTAINER_NAME`, else `HERMES_PROFILE_NAME`, else the compose project name (this folder). Boot hooks install the profile, enable the plugin, run `qkeee-erp setup --apply-profile-fixes`, wire up jev/LCM and lock down skills automatically — see [Boot hooks](#boot-hooks) for the full sequence if you need to debug one.

**Storage — two bind mounts, both from `.env`:**

| Variable | Mounts at | Holds |
|---|---|---|
| `HERMES_DATA_DIR` | `/opt/data` (`$HERMES_HOME`) | Profiles, sessions, skills, `config.yaml`, `.env`, `qkeee-erp.env`, logs, `state.db`. Delete to reset an agent. |
| `HERMES_CWD_DIR` | `$HERMES_CWD` (default `/opt/cwd`) | The agent's working directory — where the terminal tool starts and generated files land. Also exported as `TERMINAL_CWD`. |

Point these at per-profile host paths to run several agents from one compose file:

```
HERMES_DATA_DIR=/work/storage/hermes/agent-profiles/dev-erp/hermes-home
HERMES_CWD_DIR=/work/storage/hermes/agent-profiles/dev-erp/cwd
```

Set `HERMES_UID`/`HERMES_GID` to the host owner of those directories — see [Common issues](#common-issues) for the symptom when they don't match.

Once the container is up: `docker exec <container> hermes -p <profile> qkeee-erp setup status` should show every item `done`; provision the bot account for a new ERPNext Instance the same way as the CLI path (`init-bot --dry-run --requested-by <email>`, then confirm). For a new ERPNext instance, provision the bot account first: Operator ERP commands always run in the gateway container, never the terminal sidecar: `docker compose exec -u hermes hermes hermes -p <profile> qkeee-erp <command>`.

## Updating

**Hermes CLI:**

```bash
hermes profile update dev-erpnext
hermes profile update dev-erpnext --force-config   # reset config.yaml to distro defaults
```

`hermes profile update` only ever touches what `distribution.yaml`'s `distribution_owned` lists — currently `SOUL.md`, `skills/`, `cron/jobs.json`, `config.yaml`, `mcp.json`. It fetches from the profile's recorded source and preserves your local `config.yaml` edits unless `--force-config` is given. Runtime state (`.env`, `qkeee-erp.env`, `memories/`, `sessions/`, etc.) is never touched by install or update.

**Run `hermes -p <p> qkeee-erp setup` again after every `profile update`.** Until it runs, the plugin registers no `erp_*` tools and logs why. Run `init-bot` again after any app upgrade or any `doctype_defs.py` field addition — re-provisioning converges every field against each doctype's own definition, so it is always safe to re-run.

**Docker auto-update:**

| Variable | Effect |
|---|---|
| `HERMES_PROFILE_SOURCE` | Git URL to install from. Empty **string** skips the install entirely. |
| `HERMES_PROFILE_LOCAL_PATH` | In-container path of a mounted clone (must hold `distribution.yaml` at its root); takes precedence over the URL. |
| `HERMES_PROFILE_BRANCH` | Branch of the git source; empty = default branch; ignored with `HERMES_PROFILE_LOCAL_PATH`. Hermes cannot clone a branch directly, so the install hook shallow-clones it into `$HERMES_HOME/.profile-src/<name>` and installs from there. If the branch cannot be fetched, nothing is installed or updated. |
| `HERMES_PROFILE_GIT_TOKEN` / `_TOKEN_FILE` | HTTPS auth, sent as an `Authorization` header for the source's host only — never written to disk or into the URL. |
| `HERMES_PROFILE_SSH_KEY_FILE` | For `git@host:org/repo` sources. |
| `HERMES_PROFILE_AUTO_UPDATE` | `1` runs `hermes profile update` every boot. User data is preserved; distribution-owned files are overwritten. Before the update, the install hook sets the profile's recorded source to the configured one (`_SOURCE`, `_LOCAL_PATH` or `_BRANCH`), so a changed source takes effect. |
| `GATEWAY_PROMOTE_ENABLE` / `_PROFILE` | First boot only: which profile gets promoted to running. `0` leaves `default` running. |

With `HERMES_PROFILE_AUTO_UPDATE=1`, `qkeee-erp setup --apply-profile-fixes` and skill lockdown both re-run every boot, so a config/skill drift introduced by the update is corrected automatically; an existing install's hand-edited `terminal:` block is not touched by a plain update, only by `--force-config` — copy that block across by hand if you use it.

## Common issues

| Symptom | Fix |
|---|---|
| `setup status` I1 `not Isolated: the terminal … can read …` | `chmod 700 ~/.hermes` (host install step 2); confirm `hermes-terminal` is not your own user and has no sudo |
| `setup status` I1 `probe could not run … Permission denied (publickey)` | Host install step 4 not done, the `from=` address doesn't match, an `AllowUsers` line leaves out `hermes-terminal`, or the drop-in isn't loaded (`sshd -T`, step 1) |
| `setup status` I1 `probe could not run … Connection refused` | sshd not running (Linux: `systemctl status ssh`; macOS: Remote Login off) |
| `setup status` I1 `not Isolated: terminal.env_passthrough forwards QKEEE_ERP_…` | Remove that name from `terminal.env_passthrough` |
| `setup status` I2 `terminal target hermes-terminal@localhost is already Isolated for …` | One ERP-enabled profile per terminal user: create `hermes-terminal-<p>` (host install steps 1 and 4), set `terminal.ssh_user`, run `setup` again |
| `setup status` P2 `Docker sidecar values in terminal.…` | Run `setup --apply-profile-fixes`, or set `terminal.*` by hand |
| A dispatched kanban worker (or `chat -q`) shows zero `erp_*` tools | `setup status` P1 must cover `platform_toolsets.cli`, not just `known_plugin_toolsets.cli` — run `qkeee-erp setup --apply-profile-fixes`; `setup status` names the exact gap if still unmet |
| Chat and kanban workers get no kanban tools at all | `setup status` P3: `kanban.orchestrator_profile`/`kanban.default_assignee` must name this profile, and the top-level `toolsets` list must include `kanban` |
| Agent's working directory silently relocates to a different path | `HERMES_UID`/`HERMES_GID` don't match the host owner of `HERMES_CWD_DIR`, leaving the mount unwritable; `0155-cwd-setup` logs a warning when it detects this — fix the UID/GID and recreate the container |
| Dashboard refuses to start / restarts in a loop | A non-loopback bind (`0.0.0.0`, the default) always requires `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` + a password or hash; set one, or bind `HERMES_DASHBOARD_HOST=127.0.0.1` and tunnel in. `HERMES_DASHBOARD_AUTOGUARD=0` disables the autoguard that prevents the loop |
| Gateway refuses the terminal sidecar's host key | After deleting the `terminal-hostkeys` volume the sidecar gets a new key; clear the pin: `docker compose exec -u hermes hermes ssh-keygen -R terminal -f /opt/data/.ssh/known_hosts` |
| Every terminal call returns `degraded` | No terminal sidecar in this deployment — set `terminal.backend: local` in `config.yaml` (note: `local` gives the agent read access to everything in `/opt/data`) |
| `/lcm doctor repair apply` can't fix `lcm.db` (`database disk image is malformed` on every compaction) | Follow [adminops/lcm-db-recover.md](adminops/lcm-db-recover.md); the automated `lcm-corruption-check` cron/boot job already diagnoses and emails/chats `QKEEE_ERP_<TAG>_ADMIN_EMAIL`/`_OPS_CHAT_WEBHOOK` when it finds damage |
| `config.yaml` loses all its comments after an update | `_config_version` lagging the base image's schema default triggers `migrate_config()`, which rewrites the file through a PyYAML dump and strips every comment; since the file is distribution-owned this recurs on every update once it happens — keep `_config_version` current |
| `jev doctor` reports `price_order`/`dead_specialty_cells` | A routing pool inverts silently on a catalog change — re-run `jev doctor` after any pool edit or model retirement, and fix the flagged tier in `<profile>/jev/routing.json` |

## Common usage

### Example prompts

**HR & Payroll**
- "Check leave balance for HR-EMP-00014 and apply 3 days casual leave starting next Monday."
- "Run attendance regularization for the engineering team for the days the biometric device was down last week."
- "Draft salary slips for all Support-structure employees for July 2026 — don't submit, I'll review first."
- "Walk me through the exit checklist for HR-EMP-00027, separation date end of month."

**Recruitment**
- "Open a Job Opening for a Senior Frappe Developer, and draft an Offer Letter for the candidate we discussed — advisory draft only."

**Accounts & MIS**
- "Pull this month's AR aging report, grouped by customer."
- "Draft a Journal Entry to correct the misclassified expense from PR 4021 — show me before submitting."

**Inventory / Procurement / Sales**
- "What's current stock for item X across all warehouses?"
- "Draft a Purchase Order for the reorder list you flagged last week — I'll confirm before submit."

**Process / customization research**
- "We use a custom doctype for asset warranty tracking — check the live schema and tell me what's mandatory before I create a new record."
- "This ERPNext instance behaves differently from stock on Sales Invoice — is that a known customization or a bug? Check discuss.frappe.io if the docs don't explain it."

**System admin**
- "List all users with System Manager role and flag anyone who hasn't logged in in 90 days."

Every example ending in submission (payslips, journal entries, purchase orders, etc.) stops for review and explicit confirmation before the write — no exceptions. See the **Safety Policy** section of [`profile.md`](./profile.md).

### Kanban-dispatched ERPNext work

A single-turn read gets a direct reply: no plan, no kanban card. Anything else — any write, any multi-step request, anything spanning more than one turn — gets a full plan up front, decomposed into atomic, independently-dispatchable tasks, each its own `kanban_create` card (agents ADR 0011). A single ERPNext write is one atomic card like any other; the `qkeee-erp` plugin's Kanban hooks record each dispatched task's requester origin (`plugins/qkeee-erp/qkeee_erp/core/kanban_origin.py`) and carry it through to the write pipeline, same as a live chat session.

The board's own auto-decomposer is never used to split a `kanban_create`d card, for any domain. The planner decomposes up front; handing the board's own decomposer a multi-step card to split instead produces "decomposition roots" that re-wake every time a child completes and refuse `kanban_complete` as terminal — a board-architecture issue, open upstream (`agents` repo `.scratch/qkeee-erp-kanban-execution-steering/issues/01-*`). A user-dispatched, non-board-decomposed kanban task assigned to this profile is the normal case.

A kanban worker is headless, so a multi-step ERPNext write it carries out goes through one job-level email/chat approval instead of per-batch codes (agents ADR 0008/0010): `erp_execute_write phase=render batches=[...]` computes one `job_token` over the whole plan; the Requester approves once; every batch in the plan executes under that approval (`tool-cookbook.md`'s "Starting a kanban job" example has the actual calls). Current behavior:

- A batch step that can't get its own confirmation **parks** instead of failing the whole job; job status reports surface what's parked.
- A later batch resolves an earlier batch's result (`<FROM_BATCH_N_STEP_M>`) from the job's own durable record (`Qkeee Bot Job Approval`), including across a worker respawn.
- Every step that completes, succeeded or failed, gets an append-only execution-ledger entry; the platform/channel that first dispatched the job is captured at render time, so a status update always addresses the right thread.
- A scheduled poller (`cron/qkeee-erp-job-status-poller`, every 5 minutes) reports open parks/blocks once `QKEEE_ERP_<TAG>_ADMIN_EMAIL` is set and an Operator resumes it.

### Operator CLI reference

`hermes -p <profile> qkeee-erp <command>` — for the Operator, never the agent (the agent uses the `erp_*` tools). Every command that reaches ERPNext takes `--tag` (default: the active `plugins.entries.qkeee-erp.settings.active_env`) and `--requested-by <email>` (required — the Requester Gate checks it exactly as it checks a chat sender), plus optional `--prompt-summary`/`--latest-prompt` for the audit row. Full list, always current: `hermes -p <profile> qkeee-erp --help`.

**`setup [status] [--dry-run] [--apply-profile-fixes]`** — idempotent; run after every `profile install`/`profile update` and after any manual terminal/toolset edit:

| Form | Effect |
|---|---|
| `setup` (no flag) | Applies whatever prerequisites are currently unmet: moves a legacy `qkeee-erp.env`/`skills.config.qkeee_erp` block (S1/S2), checks the terminal key (S3), refuses a terminal target another profile already uses (I2), probes Isolation through the sidecar (I1) |
| `setup status` | Reports every item's state (done / pending / what's missing) and changes nothing — run this first when ERP tools don't show up |
| `--dry-run` | Prints the planned changes, writes nothing |
| `--apply-profile-fixes` | Also writes the `config.yaml` prerequisites `setup` can't infer on its own: `platform_toolsets.cli` / `known_plugin_toolsets.cli` (P1), the ssh terminal block — `terminal.backend`/`ssh_host`/`ssh_user`/`cwd` (P2). Snapshots `config.yaml.bak-<UTC>` before touching the file |

**`init-bot [--dry-run] [--confirm-token <t> --issued-at <ts>] [--domains <csv>] [--strip-stock-roles] --requested-by <email>`** — elevated credentials, Operator-only, provisions the `Qkeee Bot` role and the audit doctypes:

| Flag | Effect |
|---|---|
| `--dry-run` | Reports the plan (role grants, doctypes to create/converge) and prints a confirm token — no write |
| `--confirm-token` / `--issued-at` | The token/timestamp pair from a prior `--dry-run`; required to actually run it |
| `--domains <csv>` | Comma-separated domains to grant the bot role's permissions for (e.g. `procurement,inventory`); omit for every domain; `none` provisions only the role + audit doctypes, no domain permissions |
| `--strip-stock-roles` | Also removes ERPNext's stock roles from the bot user (shown in the plan first, before you confirm) |

**Every other command** — `health` (connectivity/auth/role gaps as the bot account, no Requester needed), `whoami`, `list-envs`, `list-ops` (no network), `query`, `get`, `report`, `roles`, `discover`, `render` and `execute-write` (the only write path; takes `--op`/`--args` or `--batch` plus the confirmation flags `--confirmation-token`, `--issued-at`, `--user-confirmation-text`, `--user-approved`, `--approval-note` — same shape the agent's own `erp_*` tools use) — is listed with its own flags by `--help`. Exit codes: `0` ok, `1` error, `2` usage/invalid arguments (nothing sent), `3` refused by a gate (nothing sent), `4` outcome unknown or partial.

## Repository layout

### Tracked and symlinked (edit these, in this repo)

| File / dir | Purpose |
|---|---|
| `distribution.yaml` | Profile manifest / distribution metadata — `distribution_owned` lists exactly what `hermes profile update` is allowed to overwrite: `SOUL.md`, `skills/` (the whole tree, not just `skills/qkeee-erp/`), `cron/jobs.json`, `config.yaml`, `mcp.json` |
| `SOUL.md` | Agent identity, voice, personality — loaded into system prompt slot #1 |
| `config.yaml` | Model, provider, toolsets, `skills.external_dirs`, `skills.write_approval`, plus the cost-control block: `auxiliary.*` (every background task pinned to a `:free` model), `compression.threshold`, trimmed `platform_toolsets`, and `context.engine: lcm` written in by the Docker boot hooks |
| `mcp.json` | MCP server connections (currently no servers configured; ERPNext access goes through the `qkeee-erp` plugin's `erp_*` tools) |
| `skills/qkeee-erp/` | The `qkeee-erp-associate` skill: ERP domain know-how only, no code — mounts read-only into the live profile via `skills.external_dirs`, edited here only |
| `plugins/qkeee-erp/` | The `qkeee-erp` gateway plugin: all ERP code, the `erp_*` tools, the identity guard, Kanban hooks, the `qkeee-erp:usage` skill and the Operator CLI (`hermes -p <profile> qkeee-erp …`). Its settings: `plugins.entries.qkeee-erp.settings` (`active_env`, `mode`) |
| `cron/` | Scheduled jobs. Ships `qkeee-erp-job-status-poller` (agents ADR 0010, issue 23), a `no_agent` script every 5 minutes reporting open kanban-job parks/blocks — paused on install until `QKEEE_ERP_<TAG>_ADMIN_EMAIL` is set and an Operator resumes it. Also ships `lcm-corruption-check`, a `no_agent` script (`scripts/lcm_corruption_check.py`) every 12 hours — plus once at container boot via `docker/cont-init.d/0186-lcm-corruption-check` — diagnosing (and, on damage, rebuilding a candidate for) this profile's `lcm.db`, emailing/Google-Chatting `QKEEE_ERP_<TAG>_ADMIN_EMAIL`/`_OPS_CHAT_WEBHOOK` on a finding. See `skills/adminops/lcm-self-heal/` for the reactive, chat-triggered counterpart |
| `adminops/` | Operator maintenance tools, baked into the image at `/opt/adminops/` (not run at boot). See [adminops/README.md](adminops/README.md) |
| `profile.md` | Purpose / Owns / Should-Not-Own / safety policy / operating protocol for this agent — user-owned, not replaced on `profile update` |

**Plugin** (`plugins/qkeee-erp/`) — runs only in the gateway process:

| Path | Role |
|---|---|
| `qkeee_erp/core/client.py` | Shared connector — auth, discovery, RBAC pre-check, write-allowlist gate, PII redaction, audit logging |
| `qkeee_erp/core/operations.py` | The one write pipeline and the registry of named write operations |
| `qkeee_erp/domains/*.py` | Per-domain allowlists + operations: `hr_payroll`, `accounts`, `mis` (no write path), `sales`, `procurement`, `inventory`, `fixed_assets`, `system_admin`, `manufacturing` (BOM default-ambiguity + Work Order stock-availability checks), `notify` (task-summary email to the requester; no ERPNext write of its own) |
| `qkeee_erp/init_bot.py` | Operator-only (`hermes qkeee-erp init-bot`): provisions the `Qkeee Bot` Role + the audit doctypes, assigns the role to the bot user, and grants its business rights — each domain's code doctypes plus its template roles' rows (`doctype_defs.DOMAIN_TEMPLATE_ROLES`, e.g. HR Manager); never delete, never a privileged doctype (agents ADR 0003). Re-provisioning converges every field against each doctype's own definition (`doctype_defs.managed_keys()`), not just type changes, so it is safe to re-run after any app upgrade or any `doctype_defs.py` field addition; each run logs one audit-log row via `log_migration_summary()` instead of a state file |
| `qkeee_erp/env_defs.py` | Required/optional `qkeee-erp.env` key manifest — presence-only, report-only; `setup`/`init-bot` use it to name what's missing instead of failing silently |
| `erp_tools.py`, `cli.py`, `settings.py` | The `erp_*` tools, the Operator CLI (same code path as the tools), the plugin settings |
| `skills/usage/SKILL.md` | `qkeee-erp:usage`: the rules for calling the `erp_*` tools, loaded on demand |

**Skill** (`skills/qkeee-erp/qkeee-erp-associate/`) — thin `SKILL.md` router, domain procedures loaded on demand, no code:

| Path | Role |
|---|---|
| `references/domains/*.md` | Per-domain procedure: one per domain module above (`manufacturing.md` included), plus `doc-extraction.md` (vision-only, no code module) and `grc-audit.md` (cross-cutting statutory-audit framing, no doctype allowlist of its own — latches alongside a functional domain, not instead of it) |
| `references/00-conventions.md` | Naming rules, GRC baseline, scope guardrail — single copy, referenced by every domain file |
| `references/tool-cookbook.md` | Worked `erp_*` calls |

### Runtime-only (never commit)

`.env`, `auth.json`, `memories/`, `sessions/`, `state.db*`, `logs/`, `workspace/`, `plans/`, `home/`, `local/`, `*_cache/`, `tmp/` — these belong to the live profile instance and can carry secrets or grow to tens of GB. See [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) for what a profile directory holds and why.

### ERPNext credentials

ERPNext instance credentials (`QKEEE_ERP_*`) belong to the `qkeee-erp` plugin, not the profile: `$HERMES_HOME/plugin-data/qkeee-erp/qkeee-erp.env` (mode 600), read only in the gateway by the plugin's connector. They never reach the agent's terminal or `execute_code`, and the plugin's identity guard blocks commands that reach for the file. This keeps ERPNext secrets physically separate from any LLM-provider key in the main `.env`.

- Copy `skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example` to `$HERMES_HOME/plugin-data/qkeee-erp/qkeee-erp.env` and fill in real values out-of-band — never by having the agent read/cat this file or echo the values back. A legacy `$HERMES_HOME/qkeee-erp.env` is read until it is moved.
- One file holds every Instance **tag**: `QKEEE_ERP_<TAG>_BASE_URL` / `_API_KEY` / `_API_SECRET` (required), plus optional per-tag extras — `env_defs.py` names the full set. The active tag and the write mode are plugin settings (`plugins.entries.qkeee-erp.settings.active_env` / `.mode`). Add a new ERPNext instance by appending another tag's trio, never by creating a second file.
- Read audit logging is unconditional on every `query_resource()`/`get_resource()`/`run_query_report()` call — there is no debug flag to gate it.

## Design notes

### Boot hooks

s6 runs `/etc/cont-init.d/*` in lexicographic order. Base-image hooks are unmarked; this repo adds the rest. All are idempotent and non-fatal — a failure logs a warning and boot continues.

| Hook | Does |
|---|---|
| `01-hermes-setup` | *(base)* volume chown, config seed, bundled-skill sync into the **default** profile |
| `0155-cwd-setup` | Creates and chowns the cwd mount so `TERMINAL_CWD` is actually enterable |
| `0157-terminal-ssh` | Creates the gateway's ssh key for the terminal sidecar and publishes its public key to the `terminal-auth` volume |
| `0158-erp-skills-unlock` | Gives `skills/qkeee-erp` back to hermes so the profile install/update can rewrite it |
| `016-profile-install` | Installs this distribution on first boot; records its skill inventory to `.distribution-skills` |
| `017-jev-skills` | Runs the jev installer — CLI, `hermes-jev` + `hermes-handoff` plugins, per-profile shims |
| `018-hermes-lcm` | Links and enables the `hermes-lcm` plugin, sets `context.engine: lcm` |
| `018-qkeee-erp-plugin` | Enables the profile's `qkeee-erp` plugin |
| `0185-qkeee-erp-setup` | Runs `hermes qkeee-erp setup --apply-profile-fixes` (credentials move, plugin settings, toolsets, ssh terminal, exclusive terminal target, Isolation probe through the sidecar); retries while incomplete (`QKEEE_ERP_SETUP_ATTEMPTS`, default 3, for a sidecar still starting), then warns. `QKEEE_ERP_SETUP=0` skips |
| `019-jev-init` | Seeds `<profile>/jev/routing.json`, runs `jev doctor`, writes the routing mode |
| `0195-gateway-state` | First boot only: marks this profile `desired_state=running` and `default` stopped |
| `0196-dashboard-auth` | Resolves dashboard auth; mirrors creds into the default home; stops a crash-loop |
| `0197-skills-lockdown` | Restricts the profile to the skills it ships |
| `0198-erp-skills-readonly` | Makes `skills/qkeee-erp` root-owned, so the terminal sidecar's file sync-back cannot change the ERP code the gateway plugin imports |
| `02-reconcile-profiles` | *(base)* creates the s6 gateway slots from each profile's `desired_state` |

The compose `command` is `["sleep", "infinity"]`, **not** `gateway run`. In an s6 container `hermes gateway run` does not run a foreground gateway: it redirects to `gateway start` on the `gateway-default` slot and then sleeps anyway, which would start the *default* profile's gateway and fight `0195-gateway-state`. The gateway that should run is started by `02-reconcile-profiles`; the CMD only keeps `/init` alive.

`terminal.cwd` is deliberately absent from `config.yaml` (a machine-local absolute path has no business in a distribution-owned file), so `TERMINAL_CWD` is the only thing setting the working directory.

### Terminal sidecar

The agent's terminal, file tools (`read_file`, `write_file`, `patch`, `search_files`) and `execute_code` do **not** run in the gateway container. `config.yaml` sets `terminal.backend: ssh`, and the gateway logs in over ssh to the compose `terminal` service ([`docker/terminal/`](./docker/terminal/)). That service mounts the cwd (`$HERMES_CWD`) and nothing from `/opt/data`, so no command the agent writes can read the ERPNext credentials, the LLM keys or the gateway's own ssh key. Agents ADR 0002 records why this is ssh and not the Hermes `docker` backend (that one needs the docker socket in the gateway: root on the host).

| Piece | Where |
|---|---|
| Client key (gateway only) | `/opt/data/.terminal-ssh/id_ed25519`, made by `0157-terminal-ssh`; `terminal.ssh_key` points here |
| Public key | `terminal-auth` volume: read-write in the gateway, read-only in the sidecar, read by its sshd at every login |
| Host key | `terminal-hostkeys` volume, made on the sidecar's first start; the gateway pins it in `/opt/data/.ssh/known_hosts` |
| Sidecar home | `terminal-home` volume at `/opt/data` *inside the sidecar*: Hermes syncs the skills tree (and any skill-declared credential file, e.g. Google Workspace tokens) into `~/.hermes` there |

When a session's terminal closes, Hermes copies changed skill files from the sidecar back into the gateway's `skills/`. `0198-erp-skills-readonly` makes `skills/qkeee-erp` root-owned so that copy fails for the ERP code the gateway plugin imports; that failure stops the sync-back pass with a warning, so other changed skill files in the same pass may not come back either. Still synced to the sidecar: skill-declared credential files (e.g. Google Workspace tokens) and the `cache/` tree (uploaded documents and large tool results, from every session). The sidecar's sshd accepts key auth only, allows no forwarding, and has no `AcceptEnv`, so skill env passthrough does not reach it. Each terminal call pays a small ssh round trip over a ControlMaster connection.

### Model routing: jev

[`hermes-jev-skills`](https://github.com/kerpopule/hermes-jev-skills) is baked in at `/opt/jev-skills` (pin with `--build-arg JEV_REF=<sha>`). jev is a decision model: it classifies each turn's difficulty and specialty, then picks the worker model from pools in `<profile>/jev/routing.json`.

jev itself (the classifier) needs its own provider key; the models it routes *to* use your normal provider keys — these are two different keys. TypeSafe is the default and wins whenever several keys exist, so a bare `OPENROUTER_API_KEY` in the environment is deliberately ignored. For an OpenRouter-only install:

```bash
docker compose exec hermes /opt/data/bin/jev setup-key --provider openrouter
# then in .env:
JEV_PROVIDER=openrouter
```

Verify with `jev doctor` — `key.provider` should name your provider, and `models list` should be non-empty. `docker exec` inherits `HERMES_HOME=/opt/data`, i.e. the **default** profile, so scope it explicitly:

```bash
docker exec -e HERMES_HOME=/opt/data/profiles/dev-erp <container> /opt/data/bin/jev doctor
```

Pools ship in [`docker/defaults/jev/routing.json`](./docker/defaults/jev/routing.json), seeded on first boot and never overwritten afterwards. They are validated against the live catalog: price monotonic per specialty (simple ≤ medium ≤ hard), no "dead" pool whose lead matches its tier's `general` lead, and every member of a vision pool actually vision-capable. `jev doctor` re-checks all of this and reports `price_order` plus `dead_specialty_cells` — re-run it after any pool edit, since a stale pool inverts silently.

| Variable | Effect |
|---|---|
| `JEV_PROVIDER` | Provider for jev's own decision calls. Empty = TypeSafe. |
| `TYPESAFE_API_KEY` | TypeSafe key, read before the OS secret store. |
| `JEV_ROUTING_MODE` | `on` \| `shadow` \| `off`. **Start in `shadow`** — it logs what jev would do without acting. Only written when `state.json` has no routing value, so a later `/jev routing` choice is never overridden. |
| `JEV_ROUTING_FILE` | Use a mounted `routing.json` instead of the baked default. |
| `JEV_ROUTING_PREFER_SUGGEST` | `1` = let `jev models suggest --write` pick pools from the catalog. Cheaper on paper, but blind to the workload. |
| `JEV_INIT_PROFILES` / `JEV_INIT_ENABLE` | Target profiles; `0` skips jev init entirely. |

### Context management: LCM

[`hermes-lcm`](https://github.com/stephenschoettler/hermes-lcm) is baked in at `/opt/hermes-lcm` (pin with `--build-arg LCM_REF=<sha>`) and enabled per profile by `018-hermes-lcm`, which links it into `<profile>/plugins/` and sets `context.engine: lcm`. It is enabled with `--no-allow-tool-override`, so it cannot replace built-in tools. `HERMES_LCM_ENABLE=0` skips it.

LCM supersedes manual context tuning for long-running sessions — but `compression.threshold: 0.30` in `config.yaml` still matters as the backstop, and it only works if the auxiliary lane is alive (see [Cost controls](#cost-controls)).

`LCM_ENABLE_SLASH_COMMAND=1` (compose default) registers `/lcm` (`/lcm doctor`, `/lcm doctor repair apply`, `/lcm backup`); the plugin hides it otherwise.

### Skill lockdown

A new profile would otherwise inherit the base image's entire bundled skill library on top of the one this distribution ships, all competing for the model's attention on every skill-selection pass. `0197-skills-lockdown` drives both levers Hermes exposes, because neither suffices alone: the `.no-bundled-skills` marker stops *future* seeding but deletes nothing, and `skills.disabled` hides a skill but is a **denylist** — there is no allowlist key — so it must be recomputed from what is actually installed.

It runs every boot, which is what makes it survive `HERMES_PROFILE_AUTO_UPDATE=1` rewriting `config.yaml`. Skills that `017`/`018` link in from outside the profile stay enabled automatically. Pruning needs `016`'s `.distribution-skills` record: without it a bundled skill seeded into the tree is indistinguishable from one this distribution ships, and the bundled manifest baselines any distribution skill byte-identical to its bundled namesake — so the hook denylists but refuses to delete.

| Variable | Effect |
|---|---|
| `HERMES_SKILLS_LOCKDOWN` | `0` keeps the full builtin library. |
| `HERMES_SKILLS_LOCKDOWN_PROFILES` | Space-separated profiles (`default` = `/opt/data`). Empty = the installed profile. |
| `HERMES_SKILLS_LOCKDOWN_PRUNE` | `0` disables on disk without deleting (reversible via `hermes skills opt-in --sync`). |
| `HERMES_SKILLS_KEEP` | Extra skill names to keep enabled, space- or comma-separated. |

Check the result with `docker exec <container> <profile-alias> skills list`.

### Dashboard

The dashboard binds `0.0.0.0` inside the container, and a non-loopback bind **always** requires a registered auth provider — without one the service exits, s6 restarts it, and the same refusal repeats forever (see [Common issues](#common-issues)). Set a username and either a password or a hash:

```
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=<a strong password>
```

Precompute the hash instead to keep plaintext out of `.env`:

```bash
docker compose exec hermes /opt/hermes/.venv/bin/python -c \
  "from plugins.dashboard_auth.basic import hash_password; print(hash_password('your-password'))"
```

The env vars are the reliable surface: they outrank `config.yaml` and are read whichever profile is active. `config.yaml` is **not** equivalent — the dashboard service runs against `/opt/data` (the default profile), so credentials in this profile's `config.yaml` are invisible to it. `0196-dashboard-auth` mirrors them across for you and persists a signing secret so logins survive a dashboard restart.

There is no unauthenticated public-dashboard option. For local-only use set `HERMES_DASHBOARD_HOST=127.0.0.1` and reach it over an SSH tunnel; a non-loopback `HERMES_DASHBOARD_PUBLIC_URL` engages the gate even on a loopback bind.

| Variable | Effect |
|---|---|
| `HERMES_DASHBOARD` | `0` disables the dashboard service entirely. |
| `HERMES_DASHBOARD_HOST` / `_PORT` | Bind address and port (default `0.0.0.0:9119`, host port ephemeral). |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` | Precomputed scrypt hash; preferred over `_PASSWORD` so no plaintext sits at rest. |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | Session-signing key. Empty = `0196` generates one and persists it at `$HERMES_HOME/.dashboard-auth-secret`; without it the provider mints a random per-process key and every restart invalidates every session. |
| `HERMES_DASHBOARD_OAUTH_CLIENT_ID` | OAuth instead of a password (`hermes dashboard register`). |
| `HERMES_DASHBOARD_PUBLIC_URL` | External URL behind a reverse proxy. |
| `HERMES_DASHBOARD_MIRROR` | `0` stops `0196` copying `dashboard.basic_auth` from this profile into `/opt/data/config.yaml`. |
| `HERMES_DASHBOARD_AUTOGUARD` | `0` opts out of the autoguard that disables the dashboard for a boot (and prints the fix once) instead of crash-looping when the gate would engage with no provider. The gateway is unaffected either way. |

### Cost controls

- **`auxiliary.*`** — every background task (compression, curator, `background_review`, classifiers, the aux vision lane) is pinned to a `:free` OpenRouter model, with `auxiliary.free_only: true` kept as the guard. That guard is load-bearing in an unobvious way: each block defaults to inheriting the *main* model, so `free_only` rejects the whole lane rather than just a fallback. Leaving a block unpinned means it fails loudly in the log instead of quietly billing main-model rates.
- **`compression.threshold: 0.30`** — fires compaction well before a turn's tool history balloons. Dead config unless the auxiliary lane above actually resolves.
- **`platform_toolsets`** — trimmed from the stock set; every enabled toolset's schema is sent on every API call. `browser`/`computer_use`/`image_gen`/`tts` dropped, `vision` kept for doc extraction.
- **`_config_version`** must track the base image's schema default — see [Common issues](#common-issues) for what happens when it lags.

## Safety & governance

- **Skill write approval:** `skills.write_approval: true` in `config.yaml` stages every agent-initiated skill write (create/edit/patch/delete) under `~/.hermes/pending/skills/` for approve/deny review via `/skills pending`, `/skills diff`, `/skills approve`, `/skills reject` — nothing lands unreviewed. See [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security).
- **Skill source separation:** `qkeee-erp-associate` (this repo, shipped/pinned) mounts via `skills.external_dirs` (read-only) and is marked externally-owned so Hermes' autonomous background-review pass can't silently patch its audit/RBAC/GRC logic. The satellite `qkeee-erp-learned/<env-tag>` skills it writes via `skill_manage` (per-instance environment notes — versions, custom doctypes, non-ERPNext API notes) live in the profile's normal local/learned skill space and stay open to that same background review, since letting the agent refine its own instance notes is the point. See `qkeee-erp-associate/references/00-conventions.md` and [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).
- **Save-draft-then-review-then-submit, always:** every docstatus-bearing document requires a review-before-submit step with explicit human confirmation, defined in `profile.md` — and code-enforced for submit/cancel via a fresh confirmation token, not prompt discipline alone (see `references/00-conventions.md`'s Non-negotiable 5).
- **No auth fallbacks:** token auth (`QKEEE_ERP_*` env vars) only — no session-cookie/password workarounds that drop audit attribution.
- **ERPNext access is the plugin's:** the agent reaches ERPNext only through the `qkeee-erp` plugin's `erp_*` tools, which run in the gateway and take the requester from the gateway session. Calling rules ship with the plugin as `qkeee-erp:usage`; the Operator CLI runs the same handlers. See `plugins/qkeee-erp/__init__.py`.
- **RBAC pre-check + read audit logging, every tag:** the plugin connector's (`qkeee_erp/core/client.py`) requester-permission check and audit logging both run unconditionally on every environment and every read/write — no PROD-only or debug-only carve-out.
- **Audit log & tracing:** every ERPNext access goes through the plugin connector, which stamps audit-log entries with the acting bot's session id, `requested_by` (resolved fresh per call, never from a `_REQUESTED_BY` env var — see above), and the calling domain — no audit-log row is written without them.

## Open items

- **`requested_by` identity:** bound to the gateway session's sender for the agent (no config/env-var default, by design — see `01-connectivity.md`); an Operator CLI run names it with `--requested-by`, which the Requester Gate checks but cannot prove is the person at the keyboard.
- **ERPNext/Frappe MCP tooling:** pending a comprehensive MCP adapter for Frappe/ERPNext — REST connector (the `qkeee-erp` plugin) is the interim approach.
- **Other ERPs:** extend beyond ERPNext with connector/client handlers for other popular ERPs.
- **Efficiency transparency:** task-level efficiency and token-consumption scoring/visibility. Partly addressed by the `auxiliary.*` pinning and `compression.threshold` above, but there is still no per-task cost attribution.
- **Dynamic LLM selection:** delivered under Docker by jev (see [Model routing](#model-routing-jev)) — per-turn tier and specialty classification instead of one fixed `model.default`. Remaining gaps: it is Docker-only (a host CLI install still runs a single model), `escalation` is off so a misjudged "simple" turn cannot climb, and the pools need re-validating against the catalog whenever models are added or retired.
- **Test coverage:** comprehensive testing across skills and connector.
- **Skill/prompt tightening:** make skill instructions and prompts more crisp and robust.
- **`UNSCOPED_DENY` scope:** manufacturing doctypes are currently excluded from `operations.py`'s unscoped-write denylist for dev testing — revisit/restore before pointing this profile at anything but a dev/demo instance.
- **Kanban board auto-decomposer:** still splits a multi-step card on its own rather than honoring the planner's atomic decomposition (`agents` repo `.scratch/qkeee-erp-kanban-execution-steering/issues/01-*`) — see [Kanban-dispatched ERPNext work](#kanban-dispatched-erpnext-work).

## Reference

- [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Profile Distributions](https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions)
- [Configuration | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
- [Personality & SOUL.md](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)
- [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security)
- [hermes-jev-skills](https://github.com/kerpopule/hermes-jev-skills) — the jev decision model and model-routing pools
- [hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) — lossless context manager (`context.engine: lcm`)
- [`env.example`](./env.example) — every Docker variable with its rationale inline; the authoritative list
