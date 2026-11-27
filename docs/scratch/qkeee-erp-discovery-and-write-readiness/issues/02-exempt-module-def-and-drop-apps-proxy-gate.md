# Exempt Module Def reads, drop the `apps` proxy gate

Status: ready-for-human (code done: 46e16f3; live DEMO_ERP check open)
Type: task
Blocked by: none (01 resolved: option A, ADR 0001)

## Problem

- `list_modules()` (`discover.py:127`) runs `query_resource(tag, "Module Def", ...)`. The requester gate refuses it for a requester without Module Def read.
- `list_installed_apps()` (`discover.py:97`) calls `_validate_prod_requester(tag, requested_by, "Module Def", "read")` as a proxy. The RPC `frappe.utils.change_log.get_versions` itself is whitelisted for any logged-in user.
- `resolve_doctype()` (`discover.py:229`) reads one Module Def to map module → app. It reports `app_lookup_error` for the same reason.

## Change

1. `client.py`: add `"Module Def"` to `PROD_GATE_EXEMPT_DOCTYPES`. Keep it out of `WRITE_GATE_EXEMPT_DOCTYPES`. Update the set's comment: it is the closed Environment Metadata list from `docs/adr/0001-environment-metadata-exempt-from-requester-gate.md` (`DocType`, `Module Def`, `get_versions`). Adding to it needs a new ADR.
2. `discover.py list_installed_apps()`: remove the proxy `_validate_prod_requester()` call. Keep `requested_by` mandatory and keep `_log_read()`.
3. Keep the `requested_by` presence check and the session binding for these calls. Only the per-doctype permission check is skipped.
4. Update the module docstring (`discover.py:26-32`).

## Acceptance

- Tests (red first): a requester with no Module Def permission gets a result from `modules`, `apps` and `resolve`. Each call writes one audit row attributed to that requester.
- A call with no `requested_by` is still refused.
- A Module Def write is still gated.
- Live: on DEMO_ERP, `discover.py modules` and `apps` succeed for the HR requester.

## Comments

**Implemented 2026-10-08** (/implement verification).

Code done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`), commit 46e16f3. Unit acceptance covered in `test_discover.py` (modules/apps/resolve with no Module Def permission, one audit row each; missing requester refused) and `core/test_client.py` (Module Def write still gated). Suite: 410 passed.

Open: live check. On DEMO_ERP, run `discover.py modules` and `apps` as the HR requester.
