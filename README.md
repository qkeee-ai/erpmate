# erpnext-hermes Agent Profile

Git-tracked source of truth for a Hermes Agent profile specialized as an ERPNext functional consultant and operations agent — running the single `qkeee-erp-associate` skill across HR/Payroll, Accounts, Inventory, Procurement, Sales, Fixed Assets, System Admin, MIS reporting, Manufacturing, and Doc Extraction.

**Live Hermes profile:** `$HERMES_HOME/profiles/<profile-name>` — `~/.hermes/profiles/dev-erpnext` for a host CLI install, `/opt/data/profiles/dev-erp` inside the Docker image. The profile name is whatever `--name` (CLI) or `HERMES_PROFILE_NAME` (Docker) says; with neither it is derived from the source repo name.

Edit the portable files **here**, not in the live profile directory. On a host install Hermes reads them through symlinks back into this repo; under Docker they are copied in by `hermes profile install` at first boot, so a change here needs a `hermes profile update` or `HERMES_PROFILE_AUTO_UPDATE=1`.

Two deployment paths, both covered below: [Hermes CLI](#hermes-cli-profile-from-this-repo) for a host install, [Docker](#deployment-docker) for the containerised agent (which also layers in the jev model router and the LCM context manager).

## What this profile is

An ERPNext specialist that acts like a functional consultant, not a click-executor — it explains the "why" behind a process, detects per-instance customizations before assuming stock ERPNext behavior, and treats every submittable document as requiring human review before it goes live. Full scope boundaries (owns / should-not-own) are defined in [`profile.md`](./profile.md); identity and voice are defined in [`SOUL.md`](./SOUL.md).

## Directory layout

### Tracked and symlinked (edit these, in this repo)

| File / dir | Purpose |
|---|---|
| `distribution.yaml` | Profile manifest / distribution metadata — `distribution_owned` lists exactly what `hermes profile update` is allowed to overwrite: `SOUL.md`, `skills/` (the whole tree, not just `skills/qkeee-erp/`), `cron/jobs.json`, `config.yaml`, `mcp.json` |
| `SOUL.md` | Agent identity, voice, personality — loaded into system prompt slot #1 |
| `config.yaml` | Model, provider, toolsets, `skills.external_dirs`, `skills.write_approval`, plus the cost-control block: `auxiliary.*` (every background task pinned to a `:free` model), `compression.threshold`, trimmed `platform_toolsets`, and `context.engine: lcm` written in by the Docker boot hooks |
| `mcp.json` | MCP server connections (currently no servers configured; ERPNext access goes through `qkeee-erp-associate`'s REST connector scripts) |
| `skills/qkeee-erp/` | The `qkeee-erp-associate` skill — mounts read-only into the live profile via `skills.external_dirs`, edited here only |
| `cron/` | Scheduled jobs (e.g. recurring MIS reports); currently empty |
| `profile.md` | Purpose / Owns / Should-Not-Own / safety policy / operating protocol for this agent — user-owned, not replaced on `profile update` |

**Skill** (`skills/qkeee-erp/qkeee-erp-associate/`) — one skill, thin `SKILL.md` router, domain procedures loaded on demand:

| Path | Role |
|---|---|
| `scripts/core/client.py` | Shared connector — auth, discovery, RBAC pre-check, write-allowlist gate, PII redaction, audit logging |
| `scripts/domains/*.py` | Per-domain functions + `ALLOWED_WRITE_DOCTYPES`: `hr_payroll`, `accounts`, `mis` (no write path), `sales`, `procurement`, `inventory`, `fixed_assets`, `system_admin` |
| `references/domains/*.md` | Per-domain procedure, one per module above, plus `manufacturing.md` and `doc-extraction.md` |
| `references/00-conventions.md` | Naming rules, GRC baseline, scope guardrail — single copy, referenced by every domain file |
| `scripts/init_bot.py` | Admin-invoked, one-time: provisions the `Qkeee Bot` Role + `Qkeee Bot Audit Log` doctype |

Domain modules import the shared core directly (same-skill imports) — there is only one connector implementation.

### Runtime-only (never commit)

`.env`, `auth.json`, `memories/`, `sessions/`, `state.db*`, `logs/`, `workspace/`, `plans/`, `home/`, `local/`, `*_cache/`, `tmp/` — these belong to the live profile instance and can carry secrets or grow to tens of GB. See [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) for what a profile directory holds and why.

### ERPNext credentials: `qkeee-erp.env`, not `.env`

ERPNext instance credentials (`QKEEE_ERP_*`) live in their own file at `$HERMES_HOME/qkeee-erp.env`, deliberately **outside** the profile's main `.env`. `scripts/core/client.py` reads this file directly, bypassing Hermes' sandbox env-stripping (`execute_code`/`terminal` sandboxes strip env vars by default; only statically-declared `required_environment_variables` for the DEFAULT tag survive) and the `env_passthrough` allowlist. This also keeps ERPNext secrets physically separate from any LLM-provider key in the main `.env`.

- Copy `skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example` to `$HERMES_HOME/qkeee-erp.env` and fill in real values out-of-band — never by having the agent read/cat this file or echo the values back.
- One file holds every environment **tag** (`qkeee_erp.active_env`): `QKEEE_ERP_<TAG>_BASE_URL` / `_API_KEY` / `_API_SECRET` (required), plus optional `_ALLOW_INSECURE`, `_ENV_CLASS` per tag. There is deliberately **no** `_REQUESTED_BY` var — `client.py` ignores it if set (see `test_client.py`'s `test_stray_requested_by_env_var_is_ignored`); `requested_by` is resolved fresh on every call from the live inbound channel identity and passed explicitly via `--requested-by` / `requested_by=`, never from config. Add a new ERPNext instance by appending another tag's trio, never by creating a second file.
- See `qkeee-erp-associate/references/01-connectivity.md`'s "Env resolution" section for the full rationale.
- Read audit logging is unconditional on every `query_resource()`/`get_resource()`/`run_query_report()` call — there is no debug flag to gate it.

### Audit-trail doctypes

`scripts/init_bot.py` provisions 1 `Qkeee Bot *` doctype directly in ERPNext (no custom app) to give every bot action a compliance-grade trail.

| Doctype | Created | Purpose |
|---|---|---|
| `Qkeee Bot Audit Log` | **Always**, every read and write | One row per ERPNext record read/written by the bot — action, reference doc, before/after payload, field diff, `domain_code` (which `references/domains/*.md` procedure made the call), `user_approved` (Approved/Not Confirmed/Not Required — detection, not a write gate), submittable/locked once resolved |

## Example prompts / tasks this profile handles

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

## Hermes CLI: profile from this repo

This repo *is* a Hermes profile distribution (`distribution.yaml`). Standard flow:

**Install fresh** (clones this repo, validates the manifest, copies distribution-owned files, checks required env vars):
```bash
hermes profile install github.com/qkeee-ai/erpmate --alias --name dev-erpnext
```

**Pull latest after a commit lands here** (fetches from the recorded source; preserves your local `config.yaml` edits unless overridden):
```bash
hermes profile update dev-erpnext
hermes profile update dev-erpnext --force-config   # reset config.yaml to distro defaults
```

**Inspect / list:**
```bash
hermes profile info dev-erpnext   # manifest: version, author, required env vars
hermes profile list               # all local profiles, distribution source column
```

`hermes profile update` only ever touches what `distribution.yaml`'s `distribution_owned` lists — currently `SOUL.md`, `skills/`, `cron/jobs.json`, `config.yaml`, `mcp.json`. Runtime state (`.env`, `qkeee-erp.env`, `memories/`, `sessions/`, etc.) is never touched by install/update.

Other profile commands (not specific to this repo, general Hermes usage): `hermes profile create`, `hermes profile show`, `hermes profile rename`, `hermes profile delete`, `hermes profile use <name>` (set default), `hermes profile export` / `hermes profile import` (tar.gz, for one-off sharing without git).

## Deployment: Docker

[`Dockerfile`](./Dockerfile) + [`docker-compose.yaml`](./docker-compose.yaml) build on `nousresearch/hermes-agent:latest` and add three things the base image does not ship: this profile, the **jev** model router, and the **LCM** context manager. Everything is driven from `.env`:

```bash
cp env.example .env     # then edit — see the annotated blocks in that file
docker compose up -d --build
docker logs -f <container>
```

The container name comes from `HERMES_CONTAINER_NAME`, else `HERMES_PROFILE_NAME`, else the compose project name (this folder).

### Storage: two bind mounts, both from `.env`

| Variable | Mounts at | Holds |
|---|---|---|
| `HERMES_DATA_DIR` | `/opt/data` (`$HERMES_HOME`) | Profiles, sessions, skills, `config.yaml`, `.env`, `qkeee-erp.env`, logs, `state.db`. Delete to reset an agent. |
| `HERMES_CWD_DIR` | `$HERMES_CWD` (default `/opt/cwd`) | The agent's working directory — where the terminal tool starts and generated files land. Also exported as `TERMINAL_CWD`. |

Point these at per-profile host paths to run several agents from one compose file:

```
HERMES_DATA_DIR=/work/storage/hermes/agent-profiles/dev-erp/hermes-home
HERMES_CWD_DIR=/work/storage/hermes/agent-profiles/dev-erp/cwd
```

Set `HERMES_UID`/`HERMES_GID` to the host owner of those directories. A mismatch leaves the cwd mount unwritable, and the agent then silently relocates to the nearest usable ancestor rather than failing — `0155-cwd-setup` warns when it detects this.

`terminal.cwd` is deliberately absent from `config.yaml` (a machine-local absolute path has no business in a distribution-owned file), so `TERMINAL_CWD` is the only thing setting the working directory.

### Boot hooks

s6 runs `/etc/cont-init.d/*` in lexicographic order. Base-image hooks are unmarked; this repo adds the rest:

| Hook | Does |
|---|---|
| `01-hermes-setup` | *(base)* volume chown, config seed, bundled-skill sync into the **default** profile |
| `0155-cwd-setup` | Creates and chowns the cwd mount so `TERMINAL_CWD` is actually enterable |
| `016-profile-install` | Installs this distribution on first boot; records its skill inventory to `.distribution-skills` |
| `017-jev-skills` | Runs the jev installer — CLI, `hermes-jev` + `hermes-handoff` plugins, per-profile shims |
| `018-hermes-lcm` | Links and enables the `hermes-lcm` plugin, sets `context.engine: lcm` |
| `019-jev-init` | Seeds `<profile>/jev/routing.json`, runs `jev doctor`, writes the routing mode |
| `0195-gateway-state` | First boot only: marks this profile `desired_state=running` and `default` stopped |
| `0196-dashboard-auth` | Resolves dashboard auth; mirrors creds into the default home; stops a crash-loop |
| `0197-skills-lockdown` | Restricts the profile to the skills it ships |
| `02-reconcile-profiles` | *(base)* creates the s6 gateway slots from each profile's `desired_state` |

All are idempotent and non-fatal — a failure logs a warning and boot continues.

The compose `command` is `["sleep", "infinity"]`, **not** `gateway run`. In an s6 container `hermes gateway run` does not run a foreground gateway: it redirects to `gateway start` on the `gateway-default` slot and then sleeps anyway, which starts the *default* profile's gateway and fights `0195-gateway-state`. The gateway that should run is started by `02-reconcile-profiles`; the CMD only keeps `/init` alive.

### Model routing: jev

[`hermes-jev-skills`](https://github.com/kerpopule/hermes-jev-skills) is baked in at `/opt/jev-skills` (pin with `--build-arg JEV_REF=<sha>`). jev is a decision model: it classifies each turn's difficulty and specialty, then picks the worker model from pools in `<profile>/jev/routing.json`.

**Two different keys, easy to conflate.** jev itself (the classifier) needs its own provider key; the models it routes *to* use your normal provider keys. TypeSafe is the default and wins whenever several keys exist, so a bare `OPENROUTER_API_KEY` in the environment is deliberately ignored. For an OpenRouter-only install:

```bash
docker compose exec hermes /opt/data/bin/jev setup-key --provider openrouter
# then in .env:
JEV_PROVIDER=openrouter
```

Verify with `jev doctor` — `key.provider` should name your provider, and `models list` should be non-empty. Note that `docker exec` inherits `HERMES_HOME=/opt/data`, i.e. the **default** profile, so scope it explicitly:

```bash
docker exec -e HERMES_HOME=/opt/data/profiles/dev-erp <container> /opt/data/bin/jev doctor
```

Pools ship in [`docker/defaults/jev/routing.json`](./docker/defaults/jev/routing.json), seeded on first boot and never overwritten afterwards. They are validated against the live catalog: price monotonic per specialty (simple ≤ medium ≤ hard), no "dead" pool whose lead matches its tier's `general` lead, and every member of a vision pool actually vision-capable. `jev doctor` re-checks all of this and reports `price_order` plus `dead_specialty_cells` — **re-run it after any pool edit**, because a stale pool inverts silently. The shipped default once led the `hard` tier with an `anthropic:` prefix absent from the OpenRouter catalog (those models are `openrouter:anthropic/claude-*`), so every hard pool fell through to a $0.11 model and routing *down* to medium cost 25× more than hard.

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

LCM supersedes manual context tuning for long-running sessions — but `compression.threshold: 0.30` in `config.yaml` still matters as the backstop, and it only works if the auxiliary lane is alive (see **Cost controls** below).

### Skill lockdown

A new profile would otherwise inherit the base image's entire bundled skill library (~47 skills) on top of the ~25 this distribution ships, all competing for the model's attention on every skill-selection pass. `0197-skills-lockdown` drives both levers Hermes exposes, because neither suffices alone:

- the `.no-bundled-skills` marker stops *future* seeding but deletes nothing;
- `skills.disabled` hides a skill but is a **denylist** — there is no allowlist key — so it must be recomputed from what is actually installed.

It runs every boot, which is what makes it survive `HERMES_PROFILE_AUTO_UPDATE=1` rewriting `config.yaml`. Skills that `017`/`018` link in from outside the profile stay enabled automatically.

Pruning needs `016`'s `.distribution-skills` record: without it a bundled skill seeded into the tree is indistinguishable from one this distribution ships, and the bundled manifest baselines any distribution skill byte-identical to its bundled namesake — so the hook denylists but refuses to delete.

| Variable | Effect |
|---|---|
| `HERMES_SKILLS_LOCKDOWN` | `0` keeps the full builtin library. |
| `HERMES_SKILLS_LOCKDOWN_PROFILES` | Space-separated profiles (`default` = `/opt/data`). Empty = the installed profile. |
| `HERMES_SKILLS_LOCKDOWN_PRUNE` | `0` disables on disk without deleting (reversible via `hermes skills opt-in --sync`). |
| `HERMES_SKILLS_KEEP` | Extra skill names to keep enabled, space- or comma-separated. |

Check the result with `docker exec <container> <profile-alias> skills list`.

### Dashboard

The dashboard binds `0.0.0.0` inside the container, and a non-loopback bind **always** requires a registered auth provider — without one the service exits, s6 restarts it, and the same refusal repeats forever. Set a username and either a password or a hash:

```
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=<a strong password>
```

Precompute the hash instead to keep plaintext out of `.env`:

```bash
docker compose exec hermes /opt/hermes/.venv/bin/python -c \
  "from plugins.dashboard_auth.basic import hash_password; print(hash_password('your-password'))"
```

The env vars are the reliable surface: they outrank `config.yaml` and are read whichever profile is active. `config.yaml` is **not** equivalent — the dashboard service runs against `/opt/data` (the default profile), so credentials in this profile's `config.yaml` are invisible to it. `0196-dashboard-auth` mirrors them across for you and persists a signing secret so logins survive a dashboard restart. When the gate would engage with no provider, it disables the dashboard for that boot and prints the fix once instead of crash-looping (`HERMES_DASHBOARD_AUTOGUARD=0` to opt out). The gateway is unaffected either way.

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

### Private source, auto-update, promotion

| Variable | Effect |
|---|---|
| `HERMES_PROFILE_SOURCE` | Git URL to install from. Empty **string** skips the install entirely. |
| `HERMES_PROFILE_LOCAL_PATH` | In-container path of a mounted clone (must hold `distribution.yaml` at its root); takes precedence over the URL. |
| `HERMES_PROFILE_GIT_TOKEN` / `_TOKEN_FILE` | HTTPS auth, sent as an `Authorization` header for the source's host only — never written to disk or into the URL. |
| `HERMES_PROFILE_SSH_KEY_FILE` | For `git@host:org/repo` sources. |
| `HERMES_PROFILE_AUTO_UPDATE` | `1` runs `hermes profile update` every boot. User data is preserved; distribution-owned files are overwritten. |
| `GATEWAY_PROMOTE_ENABLE` / `_PROFILE` | First boot only: which profile `02-reconcile-profiles` starts. `0` leaves `default` running. |

### Cost controls

Worth knowing before tuning, because two of these failed silently in earlier builds:

- **`auxiliary.*`** — every background task (compression, curator, `background_review`, classifiers, the aux vision lane) is pinned to a `:free` OpenRouter model, with `auxiliary.free_only: true` kept as the guard. That guard is load-bearing in an unobvious way: each block defaults to inheriting the *main* model, so `free_only` rejects the whole lane rather than just a fallback. Leaving a block unpinned means it fails loudly in the log instead of quietly billing main-model rates.
- **`compression.threshold: 0.30`** — lowered from 0.50 so compaction fires before a turn's tool history balloons. Dead config unless the auxiliary lane above actually resolves.
- **`platform_toolsets`** — trimmed from the stock set; every enabled toolset's schema is sent on every API call. `browser`/`computer_use`/`image_gen`/`tts` dropped, `vision` kept for doc extraction.
- **`_config_version`** must track the base image's schema default. When it lags, `migrate_config()` rewrites `config.yaml` through a PyYAML dump and strips every comment from it — and since the file is distribution-owned, that recurs after each update. Re-verify each migration step's precondition before bumping.

## Safety & governance

- **Skill write approval:** `skills.write_approval: true` in `config.yaml` stages every agent-initiated skill write (create/edit/patch/delete) under `~/.hermes/pending/skills/` for approve/deny review via `/skills pending`, `/skills diff`, `/skills approve`, `/skills reject` — nothing lands unreviewed. See [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security).
- **Skill source separation:** `qkeee-erp-associate` (this repo, shipped/pinned) mounts via `skills.external_dirs` (read-only) and should be marked externally-owned so Hermes' autonomous background-review pass can't silently patch its audit/RBAC/GRC logic. The satellite `qkeee-erp-learned/<env-tag>` skills it writes via `skill_manage` (per-instance environment notes — versions, custom doctypes, non-ERPNext API notes) live in the profile's normal local/learned skill space and stay open to that same background review, since letting the agent refine its own instance notes is the point. See `qkeee-erp-associate/references/00-conventions.md` and [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).
- **Save-draft-then-review-then-submit, always:** every docstatus-bearing document requires a review-before-submit step with explicit human confirmation, defined in `profile.md` — and code-enforced for submit/cancel via a fresh confirmation token, not prompt discipline alone (see `references/00-conventions.md`'s Non-negotiable 5).
- **No auth fallbacks:** token auth (`QKEEE_ERP_*` env vars) only — no session-cookie/password workarounds that drop audit attribution.
- **RBAC pre-check + read audit logging, every tag:** `scripts/core/client.py`'s requester-permission check and audit logging both run unconditionally on every environment and every read/write — no PROD-only or debug-only carve-out.
- **Audit log & tracing:** every ERPNext access goes through `scripts/core/client.py`, which stamps audit-log entries with the acting bot's session id, `requested_by` (resolved fresh per call, never from a `_REQUESTED_BY` env var — see above), and the calling domain — no audit-log row is written without them.

## Open items

There are many openitems, lacunas to be worked upon, below is just a short list from top of our mind -
- **`requested_by` identity:** resolved per-call from `--requested-by`/`requested_by=` only (no config/env-var default, by design — see `01-connectivity.md`); remaining gap is verifying the *caller* actually resolved it from a live inbound channel identity rather than passing a stale or invented value.
- **ERPNext/Frappe MCP tooling:** pending a comprehensive MCP adapter for Frappe/ERPNext — REST connector (`scripts/core/client.py`) is the interim approach.
- **Other ERPs:** extend beyond ERPNext with connector/client handlers for other popular ERPs.
- **Efficiency transparency:** task-level efficiency and token-consumption scoring/visibility. Partly addressed by the `auxiliary.*` pinning and `compression.threshold` above, but there is still no per-task cost attribution.
- **Dynamic LLM selection:** delivered under Docker by jev (see [Model routing](#model-routing-jev)) — per-turn tier and specialty classification instead of one fixed `model.default`. Remaining gaps: it is Docker-only (a host CLI install still runs a single model), `escalation` is off so a misjudged "simple" turn cannot climb, and the pools need re-validating against the catalog whenever models are added or retired.
- **Test coverage:** comprehensive testing across skills and connector.
- **Skill/prompt tightening:** make skill instructions and prompts more crisp and robust.

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
