# Spec: approve the spec, not bare values; spec hygiene; confirmation reply format

Status: resolved
Type: task
Blocked by: none
Findings: F5, F6, F7

## Problem

1. Order. The user replied "yes" to a list of field values. The spec was written after that, in the same turn as render, and its header says "Approval: approved". The user never saw the spec's side effects, rollback or risks before execute.
2. Hygiene in the written spec:
   - `confirmation_token` and `issued_at` persisted in "Exact calls".
   - Rollback "disable" path is prose, not an exact call.
   - Deviations says "None", but: the spec path fell back; the reply was the code only; `purchase_sourced_item` was used off-label.
   - Slug in UTC (`0619`), ERPNext record times in the Instance's zone (`11:57`), neither labelled.
   - "Deviations: none" repeated in Outcome; no Audit Log row name.
3. Reply format. `usage` and `tool-cookbook.md` say "reply `yes 3F0A9C`". `_verify_token` accepts any reply that contains the code. The agent demanded "exactly `yes 65AE45`" and then logged a "deviation" when the user sent the code alone.

## Change

`references/03-spec-driven-execution.md`:

- Step 4: the approval request shows the spec digest (objective, values, side effects, rollback, open risks) and the path. A "yes" to field values given before the spec existed is a clarify answer, not spec approval. For a single-record master-data create, one combined message (spec digest + rendered request + code) may serve as both approvals; say so in the header.
- Template:
  - Exact calls: never record `confirmation_token` or `issued_at`; record the render call and "code shown to user".
  - Rollback: an exact call for every path, including "disable".
  - Deviations: list procedure deviations too (spec path fallback, off-label flags), not only changed values.
  - Header: "Times: UTC" for the slug; the Outcome quotes record times with their zone.
  - Outcome: the Audit Log status and row name; no second "Deviations" line.

`plugins/qkeee-erp/skills/usage/SKILL.md` §7 and `tool-cookbook.md`: "the reply must contain the code, e.g. `yes 3F0A9C` or `3F0A9C`". A code-only reply is not a deviation.

## Acceptance

- 03 and the template carry the rules above.
- usage and cookbook say the reply must contain the code.

## Comments

- Implemented 2026-10-14: `03` step 4 (digest, combined approval for a single token-gated write), template (Times line, Rollback exact calls, no token in Exact calls, procedure deviations, Outcome points to Deviations + approval basis), step 7 aligned; `spec-demo-employees.md` Outcome aligned; usage §7 and cookbook: any reply containing the code confirms.
