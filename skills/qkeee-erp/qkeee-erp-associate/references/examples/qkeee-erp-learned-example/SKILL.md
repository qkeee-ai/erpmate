---
name: qkeee-erp-learned/example-env
description: "Learned notes for ERPNext environment tag 'example-env' — versions, custom doctypes, non-ERPNext systems."
---

# qkeee-erp-learned/example-env

Durable, per-environment knowledge for `qkeee-erp-associate`'s `example-env`
tag — created and updated via `skill_manage` from the plan that
`erp_discover` (action `promotion_plan`) returns. This is a satellite
skill, not a copy of the associate itself: `qkeee-erp-associate` stays
protected/externally-owned (see its own SKILL.md status note); this
skill is the deliberately-open counterpart Hermes' background-review pass
may evolve over time as more is learned about this specific environment.

## What's here

- `references/environment.md` — Frappe/ERPNext/app versions, last
  assessed via the environment-assessment procedure
  (`qkeee-erp-associate/references/02-environment-assessment.md`).
- `references/doctypes-catalog.md` — custom doctypes discovered on this
  environment, with their owning app/module where known.
- `references/custom-apps/<slug>.md` — one file per companion/custom
  Frappe app investigated on this environment.
- `references/non-erpnext/<slug>.md` — one file per non-ERPNext system
  this environment's associate sessions have been told about (see
  `qkeee-erp-associate/references/non-erpnext-adapter.md`).

## Convention

Every entry below is appended under a `## Learned <YYYY-MM-DD>` heading —
never edit or delete a prior entry, per the naming conventions in
`qkeee-erp-associate/references/00-conventions.md`. All content here has
already been through `redact_pii()`/`_redact_pii_deep()` before landing —
see the qkeee-erp plugin's `qkeee_erp.core.memory_promote` for why that pass is
load-bearing, not a courtesy.
