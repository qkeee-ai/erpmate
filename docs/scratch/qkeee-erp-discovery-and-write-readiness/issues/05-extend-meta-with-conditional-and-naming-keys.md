# Extend `discover.py meta` with conditional and naming keys

Status: ready-for-human (code done: f13c47c; live DEMO_ERP check open)
Type: task
Blocked by: none
Blocks: 06

## Problem

`_META_FIELD_KEYS` (`discover.py:70`) keeps `reqd` only. A field made mandatory by `mandatory_depends_on`, or by a Property Setter on a conditional, is invisible. The create then fails with `MandatoryError`. The doc-level naming rule is not returned either.

## Change

1. Add to `_META_FIELD_KEYS`: `mandatory_depends_on`, `depends_on`, `read_only_depends_on`, `fetch_from`, `permlevel`, `length`, `non_negative`.
2. Add doc-level keys to the result: `autoname`, `naming_rule`, `title_field`, and the `naming_series` field's `options` when present.
3. Add `conditional_mandatory[]`: every field with a non-empty `mandatory_depends_on`, with its expression.
4. Keep `custom_fields_merged` and `custom_fields_error` as they are.

## Acceptance

- Test: a fixture meta with one `mandatory_depends_on` field appears in `conditional_mandatory[]`.
- Test: `autoname` and `naming_series` options appear in the result.
- Live: `discover.py meta Employee` on DEMO_ERP shows the naming series options and any conditional mandatory field.

## Comments

**Implemented 2026-10-08** (/implement verification).

Code done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`), commit f13c47c. `_META_FIELD_KEYS` is extended. The result carries `autoname`, `naming_rule`, `title_field`, `naming_series_options` and `conditional_mandatory[]`. Fixture tests are in `test_discover.py`.

Open: live check. Run `discover.py meta Employee` on DEMO_ERP.
