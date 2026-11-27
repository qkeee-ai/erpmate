# Upstream again: `tool_call` wrapper; Jev skill suggestions off for this profile

Status: ready-for-human
Type: task
Blocked by: none
Findings: F4, F18
See also: `../../qkeee-erp-item-run-review/issues/07-upstream-tool-call-and-skill-router.md` (still open)

## Problem

- The `tool_call` wrapper rejected `name` inside `arguments`, and a stringified `calls`, 25 times in this run, with five loop warnings in a row. Model: `deepseek/deepseek-v4.1-flash`.
- Jev added a skill suggestion to most user turns. It suggested `qkeee-erp-associate` while that skill was loaded, and `jev-browser-use` for API work.

## Change

- hermes-agent (upstream), either:
  - accept `arguments.name` when the call-level `name` is missing, and accept a JSON-string `calls`; or
  - register an always-used plugin toolset (`qkeee_erp` on google_chat) directly instead of behind `tool_call`.

  Attach the counts from this run to the upstream issue.
- erpnext-hermes `config.yaml`: turn Jev skill suggestions off for this profile, if the plugin has such a setting; otherwise raise its threshold. Record the setting here.

## Acceptance

- The upstream issue is filed and linked here.
- One chat turn on dev-erp shows no Jev skill suggestion.

## Progress

- 2026-10-22: Jev skill suggestions are not a `config.yaml` setting. They are the `skills` toggle in `<profile>/jev/state.json`, seeded by `docker/cont-init.d/019-jev-init` from `JEV_SKILLS_MODE`. Default set to `off` in `docker-compose.yaml` and `env.example` (erpnext-hermes c1ffa4b, pushed). An existing profile keeps its value: on dev-erp run `/jev skills off` once (see issue 16).
- Open: the upstream `tool_call` issue (GitHub connector failed auth this session).
