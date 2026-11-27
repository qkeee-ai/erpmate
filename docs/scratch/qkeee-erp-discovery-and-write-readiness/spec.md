# qkeee-erp discovery gaps and write readiness

Status: ready-for-human. Code for 02–13 is on erpnext-hermes `feat/discovery-write-readiness` (not merged). Open: live DEMO_ERP checks on 02–06, then merge.
Raised: 2026-10-07, from the review of the DEMO_ERP spec `demo-employees-20261007-0851.md` (copied from the dev server to `erpnext-hermes/logs/`)
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (skill `skills/qkeee-erp/qkeee-erp-associate/`)

## Goal

1. The agent can always read environment metadata (apps, modules, merged DocType meta) without the requester holding System Manager.
2. When the agent cannot read that metadata, it names the exact missing role, who must hold it, and where to grant it. It does not guess.
3. A create does not fail on a customised instance because of a field, naming rule or workflow that the agent could have found first.
4. A spec records the evidence behind every write, so a reviewer can audit it.

## Findings

- **`discover.py modules` and `apps` fail for a non-System-Manager requester.** Both gate on the requester's own read permission on `Module Def` (`discover.py:97`, `discover.py:127`). An HR user does not hold it. `DocType` is already exempt from the requester read gate (`client.py:198`), so `meta` worked. Module Def holds the same kind of non-sensitive schema data.
- **`apps` uses Module Def only as a proxy.** `frappe.utils.change_log.get_versions` is whitelisted for any logged-in user. The proxy gate is the only reason it failed.
- **Conditional mandatory fields are invisible.** `_META_FIELD_KEYS` keeps `reqd` only. Nothing reads `mandatory_depends_on`, `depends_on`, `fetch_from` or `permlevel`. The naming rule and an active Workflow are not checked.
- **The spec did not record `custom_fields_merged`.** If the merged meta had fallen back, custom mandatory fields would have been missed with no warning.
- **The spec called a non-submittable Employee a "draft".** Employee has no submit step. A save creates a live record. Both demo records are `Status: Active`, so they feed payroll, attendance, leave and birthday reminders.
- **The spec template has gaps.** No schema evidence, no duplicate check, no rollback, no exact payload, a post-approval fix-up step, stale risks at close-out, and an Outcome that does not follow the Report-back contract.
- **The first learned-skill promotion is partial.** Pending skill `e701c144` carries a catalog with no app list. Later sessions would trust it.
- **Docs are out of date.** `01-connectivity.md:131-145` still describes `/api/resource/DocType` and says `modules` "works broadly". `02-environment-assessment.md:38` says it "always works".

## Tickets

| # | Ticket | Type | Blocked by | Status |
|---|---|---|---|---|
| 01 | Decide: environment metadata reads are exempt from the requester gate | grilling | — | resolved |
| 02 | Exempt Module Def reads, drop the `apps` proxy gate | task | 01 | code done; live check open |
| 03 | `init_bot.py`: grant the bot role read on Module Def and Workflow | task | 01 | code done; live check open |
| 04 | `health` capability probe and role-gap prompt | task | 02, 03 | code done; live check open |
| 05 | Extend `discover.py meta` with conditional and naming keys | task | — | code done; live check open |
| 06 | `discover.py preflight`: write-readiness gate | task | 04, 05 | code done; live check open |
| 07 | Structured create-failure errors and batch stop rule | task | — | resolved |
| 08 | Refuse bot self-escalation through system-admin writes | grilling | — | resolved |
| 09 | Mark partial environment catalogs in `memory_promote.py` | task | 04 | resolved |
| 10 | Spec template: evidence, idempotency, rollback, deviations, close-out | task | 06 | resolved |
| 11 | hr-payroll: non-submittable ≠ draft; Employee pre-create checks | task | — | resolved |
| 12 | Convention for demo and test data | task | 11 | resolved |
| 13 | Align the discovery docs | task | 02, 04, 06 | resolved |
| 14 | DEMO_ERP: decide what to do with HR-EMP-00006/7 and skill `e701c144` | task | — | resolved |

## Recommended order

01 → 02 + 03 → 04 → 05 → 06 → 07 → 10 → 11 → 12 → 09 → 13. Do 14 now: it needs no code. Decide 08 before the next system-admin write run.

## Related

- `qkeee-erp-write-path-hardening/issues/05-decide-rbac-strategy-and-admin-credential.md`: the narrow-role bot decision. Issue 01 here must not conflict with it.
- `qkeee-erp-write-path-hardening/issues/02-gate-user-role-writes.md`: User/Role write gating. Issue 08 builds on it.
