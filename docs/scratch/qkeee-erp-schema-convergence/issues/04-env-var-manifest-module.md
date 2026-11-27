# New env_defs.py: machine-readable required/optional env var manifest

Status: done
Type: task
Blocked by: none

## Change

1. New module `plugins/qkeee-erp/qkeee_erp/env_defs.py`: pure data, no
   imports (same posture as `doctype_defs.py`) — `REQUIRED_PER_TAG`
   (BASE_URL, API_KEY, API_SECRET, ADMIN_API_KEY, ADMIN_API_SECRET),
   `OPTIONAL_PER_TAG` (ADMIN_EMAIL, OPS_CHAT_WEBHOOK, ALLOW_INSECURE,
   ENVIRONMENT, KYC_TAX_ID_FIELD), `OPTIONAL_GLOBAL`
   (AUDIT_MASK_FIELDS), each a `{suffix: one-line purpose}` dict sourced
   from `qkeee-erp-associate.env.example`'s existing documented keys.
2. `missing_required_keys(present_suffixes) -> list`: pure, sorted,
   presence-only — never sees or returns a value.
3. Deliberately no import of `core.client` (which would cycle, since
   `core.client.health_check()` imports this module — issue 05).

## Done when

- `env_defs.py` exists, is pure (no imports), and its three manifests
  match `qkeee-erp-associate.env.example`'s documented keys exactly.

## Comments

2026-11-11:
- Implemented in `plugins/qkeee-erp/qkeee_erp/env_defs.py`.
- Round-trip enforced by `tests/test_env_defs.py::
  EnvExampleRoundTripTests` (issue 07) — every manifest suffix appears
  in the `.env.example`, and every `QKEEE_ERP_DEFAULT_<SUFFIX>=` line in
  the `.env.example` is a known manifest suffix.
