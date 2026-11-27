# Plugin owns its config: move out of skills.config.qkeee_erp

Status: done
Type: task
Blocked by: 04

## Problem

Mode, active Instance and Instance tags live in `skills.config.qkeee_erp`, which is Profile config, and the skill docs tell the agent to read `qkeee_erp.active_env`. A Plugin that could serve any agent must read only its own section, and the agent should learn the active Instance from a tool, not from config.

## Change

- The Plugin reads `plugins.entries.qkeee-erp.settings` only (mode, active Instance, Instance tags).
- Setup step S2 (issue 07) copies the old `skills.config.qkeee_erp` values once and drops `scripts_dir`.
- `erp_discover health` returns the active Instance tag and base URL (it may already; check).
- Remove `skills.config.qkeee_erp` from the shipped `config.yaml`.

## Tests

Settings read from the Plugin section only; S2 copies once and is idempotent; a config with only the old section works after S2 and is refused before it (fail closed, "run setup").

## Comments

2026-10-09 (erpnext-hermes `53597c4`, review fix `75f9a83`):
- `plugins/qkeee-erp/settings.py`: reads `plugins.entries.qkeee-erp.settings` only (`active_env`, `mode`). Instance tags stay in the credentials file.
- S2 = `settings.migrate_legacy_config(cfg)`: copies legacy values the plugin section lacks, removes `skills.config.qkeee_erp`, drops `scripts_dir`; idempotent; null YAML sections handled. **Not wired to any command yet**: issue 07 calls it (with the backup).
- Legacy section present → `register()` adds no `erp_*` tools, logs "run `hermes qkeee-erp setup`"; ERP CLI commands refuse (exit 3). Hooks and CLI always register.
- `erp_discover health` already returned tag + base_url; now also `mode` (the agent can no longer read it from config).
- Shipped `config.yaml` never held the section: the seed was SKILL.md `metadata.hermes.config`, now removed. Nothing seeds plugin settings: a fresh install runs `default` / `read-only` (old defaults). Issue 07 may write them.
- **Rollout blocker:** an existing install (dev-erp, demo) with the legacy section has no `erp_*` tools and no `setup` command until 07 lands.
