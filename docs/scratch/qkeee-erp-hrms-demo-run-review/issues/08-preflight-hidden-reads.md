# Preflight: reads ERPNext does inside its own checks

Status: done 2026-10-22 (erpnext-hermes 73e3606, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F10

## Problem

Company create returned 403 on `GST Settings`, because India Compliance reads it while it validates the Company. `preflight` said `bot_ready: true`: it checks only the target doctype and its Link fields.

## Change

- `doctype_defs.py`: `HIDDEN_READS = {("india_compliance", "Company"): ("GST Settings",)}`. The key is (installed app, target doctype). The value lists the doctypes the server reads with the user's rights. Adding one is a code change that cites the run that found it.
- `discover.preflight`, new rule 8b: for each installed app (`installed_apps`) and the target, probe the bot's read on each listed doctype (`probe_bot_link_reads`). A missing read is a **blocker**, with a gap that tells the Operator to re-run `init-bot`.
- `init_bot.bot_permission_matrix`: grant `read` on the hidden reads of every doctype the bot may create, when the app is installed.

## Acceptance

- Tests:
  - with `india_compliance` installed and no read, preflight on Company is not ready and names GST Settings;
  - without the app, there is no probe;
  - `init-bot` plans `GST Settings: add`.
