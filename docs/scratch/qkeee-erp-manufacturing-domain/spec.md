# Manufacturing domain: real write path, two documented safety checks, init-bot provisioning

Status: needs-triage

## Context

BOM/Work Order/Job Card/Production Plan/Routing/Workstation/Operation
were hard-denied through `unscoped.generic`
(`qkeee_erp/core/operations.py` `UNSCOPED_DENY`) pending a dedicated
`manufacturing.py` domain module. 2026-11-05: the deny-list entries
were removed (`erpnext-hermes@78bfd89`, on `develop`) as a deliberate
interim step so the 4-Nov manufacturing demo run could test these
writes at the `unscoped.generic` floor (confirmation-gated, requester-
bound, audited — the same baseline any other non-domain doctype
already gets, no manufacturing-specific side-effect handling). That
run also hit a separate ERPNext-side RBAC gap (bot role `Qkeee Bot` had
no create/read on BOM/Work Order) which the user fixed manually via
the Role Permission Manager UI — not via any code path. This effort
replaces both: a real `manufacturing.py` module claims these doctypes
properly (so `UNSCOPED_DENY`'s absence of them becomes correct by
construction, not just permissive), and `doctype_defs.py`'s
`DOMAIN_TEMPLATE_ROLES` mechanism (the same one `init-bot --domains`
already uses for `accounts`/`hr_payroll`/`inventory`/`procurement`/
`sales`) gets a `manufacturing` entry so the next `init-bot --domains
manufacturing` grants the bot role everything it needs in one step,
no manual UI permission-granting ever again.

Scope decision (2026-11-05, user): floor + the two safety checks
`references/domains/manufacturing.md` already names as hypotheses, now
built rather than deferred — not floor-only.

## Issues

- `issues/01-manufacturing-domain-module.md` — **done** (`eb99887`)
- `issues/02-init-bot-template-roles.md` — partially done (`eb99887`): code shipped, role names/init-bot run not live-confirmed
- `issues/03-bom-default-ambiguity-check.md` — partially done (`eb99887`): code + unit tests shipped, live round trip open
- `issues/04-work-order-stock-availability-check.md` — partially done (`eb99887`): code + unit tests shipped, live round trip open
- `issues/05-manufacturing-md-live-validation.md` — partially done (`eb99887`): rewritten to code-complete-pending-live-round-trip; needs a final pass once 06 runs
- `issues/06-live-round-trip-demo-erp.md` — **ready-for-human**, blocked on an explicit go-ahead to write real records on demo_erp (a live-write attempt was correctly refused by the auto-mode classifier this session)

2026-11-05: all 6 issues' code is now shipped on `erpnext-hermes@eb99887`
(branch `feature/manufacturing-domain`, pushed). 889 tests pass
(883 + 6 new), no regressions. What's left across 02/03/04/05/06 is one
thing — an actual live round trip against demo_erp (role-name
confirmation, init-bot run, BOM/Work Order/Job Card create+submit) —
deliberately not done autonomously this session.

2026-11-06: merged into `develop` (`0594a7e`), after tagging develop's
pre-merge state as `v2.1` (`78bfd89`). `develop` is not yet deployed to
dev-erp — the live round trip above still needs an explicit go-ahead
either way.

## Decisions so far

- Floor + both documented checks now, not floor-only (2026-11-05 user
  decision) — BOM default-marking ambiguity flag and Work Order
  stock-availability pre-check (reusing `inventory.md`'s
  `get_bin_qty()` convention) ship in this effort, not as a follow-up.
- Permission grants go through `doctype_defs.py`'s
  `DOMAIN_TEMPLATE_ROLES` (init-bot), not a one-off manual Role
  Permission Manager edit and not `system_admin.permission_add`/
  `permission_update` ad hoc calls — matches how every other domain's
  bot permissions are provisioned.
- Scope is the 7 doctypes already freed from `UNSCOPED_DENY`:
  BOM, Work Order, Job Card, Production Plan, Routing, Workstation,
  Operation. Not scoping MRP/Production Plan's own sub-mechanics
  (raw-material aggregation report vs. hand-aggregation) beyond what
  `manufacturing.md`'s existing procedure step 3 already covers.

Related: [[qkeee-erp-kanban-execution-steering]] (the 4-Nov demo run
that motivated this), `docs/adr/0003-service-accounts-dedicated-roles-
no-self-escalation.md` (amendment pattern `DOMAIN_TEMPLATE_ROLES`
follows).
