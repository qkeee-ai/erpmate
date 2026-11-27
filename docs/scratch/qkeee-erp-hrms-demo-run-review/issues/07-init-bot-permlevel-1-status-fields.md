# `init-bot`: permlevel-1 rows for status fields that gate submit

Status: done 2026-10-22 (erpnext-hermes b765e71, review fixes e3807f6; pushed 2026-10-22)
Type: task
Blocked by: none
Findings: F11
Decision: D3 (spec.md)

## Problem

Leave Application submit needs `status = Approved` ("Only Leave Applications with status 'Approved' and 'Rejected' can be submitted"). `status` is a permlevel-1 field. `init-bot` copies only the template roles' permlevel-0 rows (ADR 0003, 2026-10-21), so an admin added the row by hand.

## Change

- `doctype_defs.py`: `SUBMIT_GATE_FIELDS = {"Leave Application": ("status",)}`. Comment: a field whose value must be set before ERPNext allows submit. Adding one is a code change.
- `init_bot.template_rights`: for a doctype in `SUBMIT_GATE_FIELDS`, also read the template roles' rows at the field's permlevel (taken from meta). Grant `read` and `write` at that permlevel, only when a template role holds them there.
- `provisioning.grant_bot_permission`: accept a `permlevel` argument. Allow it only for a (doctype, permlevel) pair that `SUBMIT_GATE_FIELDS` and the meta yield; refuse every other permlevel.
- The plan, `extra_rights` and the plan token cover rows above permlevel 0. Key them as `<doctype>@<level>`.
- ADR 0003 amendment (2026-10-22): permlevel-1 rows only for listed submit-gate fields.

## Acceptance

- Tests:
  - a template row at permlevel 1 on Leave Application is planned as `Leave Application@1: add` and `write`;
  - permlevel 1 on any other doctype is refused by `grant_bot_permission`;
  - the dry-run token changes when the level-1 plan changes.
- demo_erp: after `init-bot`, the hand-added row shows as planned, not as `extra_rights`.
