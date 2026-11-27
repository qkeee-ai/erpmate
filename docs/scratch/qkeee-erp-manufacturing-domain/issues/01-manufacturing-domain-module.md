# Build domains/manufacturing.py: ALLOWED_WRITE_DOCTYPES + generic op

Status: done (erpnext-hermes@eb99887, branch feature/manufacturing-domain)

## Problem

No `qkeee_erp/domains/manufacturing.py` exists. BOM/Work Order/Job
Card/Production Plan/Routing/Workstation/Operation are writable only
through `unscoped.generic` since `erpnext-hermes@78bfd89` removed them
from `UNSCOPED_DENY` — correct as an interim floor, but these doctypes
should be domain-owned like every other business doctype, not
perpetually relying on the unscoped fallback's absence of a deny entry.

## Proposed fix

Mirror `domains/sales.py`/`domains/procurement.py`'s existing shape
exactly:
- `DOMAIN_NAME = "manufacturing"`, `ALLOWED_WRITE_DOCTYPES = ("BOM",
  "Work Order", "Job Card", "Production Plan", "Routing", "Workstation",
  "Operation")`.
- `core_client.register_domain_allowlist(DOMAIN_NAME,
  ALLOWED_WRITE_DOCTYPES)` at import time, same as every other domain
  module.
- A `manufacturing.generic` op registered the same way other domains'
  generic ops are (standard `prepare`/`token_policy` shape — no
  manufacturing-specific `prepare` logic needed here; issues 03/04
  carry the two actual side-effect checks as `enrich`/`preconditions`
  hooks layered on top, not baked into this floor).
- Wire the import into `execute_write.py`'s domain-registration list
  (currently skips manufacturing with the comment "manufacturing/
  doc_extraction have no write path" — update that comment once this
  lands).
- Update `domains/__init__.py`'s docstring (currently says
  "`manufacturing.py` is intentionally absent").
- Once this lands, `UNSCOPED_DENY`'s absence of these 7 doctypes is
  correct for the right reason (domain-owned, `_check_ownership()`
  never reaches the `op.domain is None` branch for them) rather than
  just "nobody re-added the deny."

Add a test class mirroring `test_allowlist_gates.py`'s pattern for
every other domain (confirms the allowlist registers, confirms
`unscoped.generic` now redirects to `manufacturing.generic` instead of
either silently allowing or hard-denying).
