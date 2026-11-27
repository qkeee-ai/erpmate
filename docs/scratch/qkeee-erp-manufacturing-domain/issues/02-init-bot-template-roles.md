# Add manufacturing to doctype_defs.py's DOMAIN_TEMPLATE_ROLES (init-bot provisioning)

Status: partially done (erpnext-hermes@eb99887) — code shipped, live confirmation of the role names still open

## Partial progress, 2026-11-05

`DOMAIN_TEMPLATE_ROLES["manufacturing"] = ("Manufacturing Manager",
"Manufacturing User")` added, code-reviewed, 889 tests pass. Steps 1
and 4 of the proposed fix (live-confirm the real role names; live-run
`init-bot --domains manufacturing` against demo_erp) were NOT done this
session: a real write attempt against demo_erp was correctly blocked
by the auto-mode classifier ("Modify Shared Resources") as an
autonomous action needing the user's explicit go-ahead. Step 3
(DOMAIN_MASTER_DOCTYPES check) also not done — not needed, all 7
manufacturing doctypes are covered directly by
`manufacturing.ALLOWED_WRITE_DOCTYPES`, no unowned master doctype
surfaced. Remaining: live-confirm role names, run init-bot, re-test the
BOM/Work Order preflight gap end to end.

## Problem

The 4-Nov demo run's BOM/Work Order 403 (bot role `Qkeee Bot` had no
create/read) was fixed manually via the Role Permission Manager UI —
not through any code path. Every other domain's bot permissions are
provisioned through `doctype_defs.py`'s `DOMAIN_TEMPLATE_ROLES`:
`init-bot --domains <domain>` reads a named stock role's live
permlevel-0 rows and copies the same ticks (cut to `PROVISION_PTYPES`:
read/create/write/submit/cancel, never a privileged doctype, never an
`if_owner` row) onto `Qkeee Bot`. Manufacturing has no entry, so
`init-bot --domains manufacturing` grants nothing today.

## Proposed fix

1. Live-confirm the real ERPNext stock role names for the Manufacturing
   module on demo_erp (`erp_discover resolve`/a `Role` query) — don't
   assume "Manufacturing Manager"/"Manufacturing User" from general
   knowledge without checking this instance's actual role list, same
   discipline every other `DOMAIN_TEMPLATE_ROLES` entry was held to.
2. Add the confirmed role(s) to `DOMAIN_TEMPLATE_ROLES["manufacturing"]`.
3. Check whether any manufacturing doctype needs a `DOMAIN_MASTER_DOCTYPES`
   entry too (a master the domain's work creates on the way that no
   domain owns, written through `unscoped.generic` — e.g. a BOM's
   Workstation/Operation master records, if those aren't fully covered
   by being in `ALLOWED_WRITE_DOCTYPES` directly per issue 01).
4. Live-test: run `init-bot --domains manufacturing` against demo_erp,
   confirm the bot role picks up exactly the expected ticks, then
   re-run the BOM/Work Order preflight check that failed in the 4-Nov
   log and confirm it's now clean without any manual UI step.

This directly replaces the manual fix from the 4-Nov run with the
audited, repeatable path every other domain already has.
