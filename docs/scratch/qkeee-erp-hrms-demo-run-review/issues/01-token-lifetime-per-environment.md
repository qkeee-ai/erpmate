# Confirmation lifetime per environment, and `expires_at` in every render

Status: done 2026-10-22 (erpnext-hermes a86b8ab, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F1
Decision: D1 (spec.md)

## Problem

`DEFAULT_TOKEN_TTL_SECONDS = 900` (`core/confirm_token.py`) applies to every tag. On demo_erp the user replied about 21 minutes after the codes were shown, so whole batches expired four times. To check the clock the agent ran `date` in the terminal. That raised Tirith approval prompts and once blocked the turn.

## Change

- New per-tag setting `QKEEE_ERP_<TAG>_ENVIRONMENT` in `qkeee-erp.env`: `production` or `non-production`. Read it in `core/client.py` next to the other per-tag settings.
- **An unset or unknown value means production.** Never infer it from the tag name.
- Lifetime: production 900 s; non-production 3600 s. No other value. `operations._verify_token` (its `is_fresh` call) uses the tag's lifetime; `confirm_token.is_fresh` takes `max_age_seconds` from the caller.
- `operations.prepare_only` adds `expires_at` (ISO 8601 UTC) and `ttl_minutes` to its result. The `erp_execute_write` render `_note` tells the agent to show "expires at HH:MM UTC" from `expires_at`.
- An expired-token refusal names `expires_at` and the lifetime.
- `health` returns `environment` and `token_ttl_minutes`.
- `init_bot.py` keeps 900 s for its own plan token (Operator flow, unchanged).
- Skill text (`plugins/qkeee-erp/skills/usage/SKILL.md`, tool cookbook): take the expiry from `expires_at`; never run a clock command in the terminal. Add the setting to `qkeee-erp-associate.env.example`.

## Acceptance

- Tests:
  - unset environment → 900 s;
  - `non-production` → 3600 s;
  - any other value → 900 s;
  - a 30-minute-old token is accepted on non-production and refused on production;
  - `expires_at` = `issued_at` + lifetime;
  - `health` shows both fields.
- On demo_erp, after setting `QKEEE_ERP_DEMO_ERP_ENVIRONMENT=non-production`, a render shows an `expires_at` 60 minutes ahead.
