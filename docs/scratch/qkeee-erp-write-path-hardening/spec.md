# qkeee-erp write-path hardening — fix & improvement plan

Status: resolved. P0–P4 merged into erpnext-hermes `develop` (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c; verified 2026-10-08). The full DEMO_ERP live validation ran on 2026-10-06 (see "Live validation" below). It found six more defects (W33–W38); all are fixed, unit-tested and retested live. The W31 migration has run on DEMO_ERP. HR Leave operations can't be tested there because HRMS isn't installed.
Raised: 2026-10-06, from a code review of `erpnext-hermes/skills/qkeee-erp/qkeee-erp-associate/scripts/` plus manual review findings
Raised by: nikhil.sharma@qkeee.ai
Repo: `erpnext-hermes`. Paths below are relative to `skills/qkeee-erp/qkeee-erp-associate/scripts/` unless stated.

## Goal

Every write the skill can make goes through one pipeline that applies the same checks in the same order. No gated operation can be reached by a cheaper, ungated route. Every confirmation token is computed over the exact request sent to ERPNext.

Today the skill has two kinds of write path:

- the generic `mutate_resource()` path, reached through `execute_write.py`
- five bespoke gated functions in `domains/system_admin.py` and `domains/fixed_assets.py`, which no CLI reaches and which the generic path can bypass

## Findings register

Severity reflects the blast radius on a live ERPNext tenant.

| ID | Sev | Finding | Where | Fixed by |
|---|---|---|---|---|
| W01 | Critical | `system_admin.mutate()` and `fixed_assets.mutate()` are plain writes with no token gate. `execute_write.py --domain system_admin` can create a System Manager user, change a user's roles, delete a Webhook, Workflow or Custom Field, or create a Webhook, all with no token. Asset submit and cancel are ungated. | `domains/system_admin.py:65`, `domains/fixed_assets.py:58` | 01, then 08, 09 |
| W02 | Critical | User and Role are in `PROD_GATE_EXEMPT_DOCTYPES`. Writes to them skip the permission check and the "requester is a real User" check. Any non-empty `--requested-by` passes. | `core/client.py:208`, `:755` | 02 |
| W03 | Critical | Tokens are not bound to the executed request. In `destructive_mutate`, the payload is outside the token. In `gated_config_mutate`, the payload and name are outside it, and kind is not tied to doctype. In `call_whitelisted_method`, `token_facts` is separate from `body`. | `domains/system_admin.py:197`, `:437`; `domains/fixed_assets.py:211` | 04, then 07 |
| W04 | High | `core/client.py mutate` without `--domain` writes any doctype with any action. It has no allowlist and no token. | `core/client.py:2249` | 03 |
| W05 | High | `execute_write.py` rewrites the payload before the domain-less token check runs. Both schema mapping and the purchase-sourced defaults do this. A token over the confirmed payload never matches. The tests mock `gated_mutate_resource`, so this is never exercised. | `execute_write.py:339`, `:351`, `:393` | 10 |
| W06 | High | `restore_asset` has no token and no permission check. It cancels the scrap Journal Entry, which changes the General Ledger. | `domains/fixed_assets.py:150` | 04, then 09 |
| W07 | High | The bespoke RPC wrappers never call `_validate_prod_requester()`. This covers both `call_whitelisted_method` and `call_permission_manager`. | `domains/fixed_assets.py:183`, `domains/system_admin.py:286` | 07, 08, 09 |
| W08 | High | The docs say "one write entry point", but nothing on the CLI reaches the bespoke functions. The docs reference `render_*.py` scripts that don't exist. The cookbook forbids the hand-written scripts that the domain docs need. | `references/domains/*.md`, `references/cli-cookbook.md` | 11, 17 |
| W09 | Medium | No domain token-gates `delete`, although the registry docstring promises it. Cancelled invoices and Journal Entries can be deleted freely. | `domains/*.py` `register_domain_token_gate` calls | 12 |
| W10 | Medium | A submit token covers only the document name, not its content. A document edited after the user confirms still submits. The only concurrency check is in `fixed_assets` and is unreachable from the CLI. | `core/confirm_token.py:68`, `domains/fixed_assets.py:100` | 12 |
| W11 | Medium | Supplier creation with KYC is not atomic. If the Address create fails, a Supplier without KYC is left behind and a retry duplicates it. The KYC check only needs a non-empty address, not a tax ID. | `domains/procurement.py:127` | 13 |
| W12 | Medium | Audit context is dropped. `destructive_mutate`, `create_user` and `gated_config_mutate` take no session, channel or prompt arguments. No bespoke path returns `_audit_log_status`. | `domains/system_admin.py` | 07 |
| W13 | Medium | Domain read helpers call `_request` directly. They skip the permission check and the read audit log. | `domains/inventory.py:50`, `:109`; `domains/system_admin.py:233`, `:241`, `:453` | 15 |
| W14 | Medium | `_request` lets `TimeoutError` and `JSONDecodeError` escape as non-`ConnectorError`. `int(issued_at)` raises `ValueError`. In both cases the audit row stays `Attempted` and the CLI prints a traceback. | `core/client.py:993` | 14 |
| W15 | Medium | Copied check sequences have drifted in about seven places. The check order differs, exception types differ, and `_compute_token` is duplicated in two domains. | domains + `core/client.py` | 07 |
| W16 | Low | `destructive_mutate` posts a "deleted" Comment before the delete runs. A delete that fails, for example with `LinkExistsError`, leaves a false Comment. | `domains/system_admin.py:206` | 08 |
| W17 | Low | `call_permission_manager` accepts the `get_*` actions. It POSTs them and audits them as permission changes, with no token. | `domains/system_admin.py:286` | 08 |
| W18 | Low | Confirmed by 06: `make_depreciation_entry` takes `depr_schedule_name` and `make_sales_invoice` takes `asset`, so audit references and Comments were blank. The P0 change reads `asset` too; depreciation still has no asset key in its body. | `domains/fixed_assets.py` | 09 |
| W19 | Low | `payload_before` and `payload_after` are written to the audit log with no PII redaction. | `core/client.py:1610`, `:1647` | 16 |
| W20 | Low | Docs and code have drifted. `existing_roles` and `elevated_roles_acknowledged` exist only in the docs. | `references/domains/system-admin.md` | 17 |
| W21 | Low | The five bespoke gated functions have zero tests. | tests | 18 |
| W23 | High | `make_sales_invoice` is a mapper: it returns an unsaved Sales Invoice and persists nothing. The wrapper token-gates it, audits it as a successful disposal and posts an "applied" Comment. v16 also requires `sell_qty`. (Found in 06.) | `domains/fixed_assets.py` | 09 |
| W24 | High | `make_depreciation_entry` with both `sch_start_idx` and `sch_end_idx` posts every row in the slice, with no booked-or-due check, which can double-post depreciation. (Found in 06.) | `domains/fixed_assets.py` | interim guard landed in P0; 09 |
| W25 | Medium | `scrap_asset` uses `body.scrap_date`, which defaults to today. The token's `disposal_date` was never compared with it. (Found in 06.) | `domains/fixed_assets.py` | interim guard landed in P0; 09 |
| W26 | Medium | `make_depreciation_entry` keeps posting rows after one fails, then re-raises. The audit row says Failure although some JEs posted. (Found in 06.) | `domains/fixed_assets.py` | 09 |
| W27 | High | RPC wrappers wrote Audit Log `action` values outside its Select (`scrap_asset`, `Permission Add`, ...) and a synthetic `reference_name` (`role@permlevel0`) for permission changes. Frappe rejects both, and audit inserts fail silently, so those writes were probably never audited. (Found in P2.) | `doctype_defs.py:62`; old `fixed_assets.py`/`system_admin.py` | 07 (`PreparedRequest` validates `audit_action`), 08 |
| W28 | High | `gated_mutate_resource()` / unscoped could write any doctype outside the allowlists. That included Server Script, Custom DocPerm, Notification, credential doctypes and manufacturing doctypes. It could also write domain-owned doctypes, which bypassed the Supplier KYC gate. (Found in P2.) | `core/client.py` | 10 (`UNSCOPED_DENY`, domain-owned refusal) |
| W29 | Critical | `AUDIT_EXEMPT_DOCTYPES` held User/Role/DocType. User creation, role changes, disable/delete and permission changes never reached the Qkeee Bot Audit Log. (Found in P2.) | `core/client.py:164` | 07 (exemption narrowed; provisioning opts out explicitly) |
| W30 | Medium | `system_admin.generic` sent Role create/update and Custom Field/Property Setter create/update with the ADMIN credential but no confirmation — the one admin-key write path without a token. (Found in the admin-credential review, 2026-10-06.) | `domains/system_admin.py` | 08 (every action now confirmed) |
| W31 | High | The audit log's `reference_name` was a Dynamic Link. Every record the skill audited became undeletable (`LinkExistsError`), breaking every delete operation and the KYC rollback, and audit rows naming a non-existent record silently failed to insert. (Found live in the DEMO_ERP smoke run.) | `doctype_defs.py` | P4: Data field + `init_bot.py` migration (`provisioning.migrate_fields`); migration not yet run live |
| W32 | Low | A finalizing action on a record that no longer exists exited 1 instead of 3, although nothing was sent. (Found live in the smoke run.) | `core/operations.py` | P4: 404 is a `PreconditionFailedError` |
| W33 | High | The `init_bot.py` migration guard required the fields sent to equal `doctype_defs.py` exactly. The live audit doctype carries a Frappe-added `amended_from` that the definition lacks, and `migrate_fields()` sends every live row back. So the W31 migration could never run on such an instance. (Found live.) | `init_bot.py` | Guard checks the shipped fields exactly; live-only fields must be sent unchanged (live precondition). Migration ran on DEMO_ERP. |
| W34 | Medium | `system_admin.create_webhook` sent no name. Frappe v16 Webhook is prompt-named, so every create failed with "Please set the document name". (Found live.) | `domains/system_admin.py` | `name` is a required arg |
| W35 | High | KYC needs the tax ID on Address. Core ERPNext without India Compliance has no tax field on Address, so schema mapping dropped `gstin` and every KYC Supplier create was refused. Only a waiver worked. (Found live.) | `domains/procurement.py` | When the live Address has no tax field, the tax ID goes on the Supplier's core `tax_id` |
| W36 | High | `fixed_assets.sell` never set `customer` (mapper leaves it blank), so every sell failed with MandatoryError. The confirmed `sale_proceeds` was never applied (asset row rate 0). (Found live.) | `domains/fixed_assets.py` | `customer` and `sale_proceeds` required; the op sets both inside the token-bound request |
| W37 | Medium | A doctype missing on the instance (app not installed) was refused as "requester permission could not be verified … provision a bot account", which sent users chasing roles. (Found live: Leave Application, no HRMS.) | `core/client.py` | `DoctypeNotFoundError` (exit 3) names the missing doctype |
| W38 | Low | Exit codes drifted from the cookbook. A read-CLI gate refusal exited 1, and malformed op args exited 1 ("ERPNext rejected it") though nothing was sent. (Found live.) | `core/client.py`, `core/operations.py`, CLIs | Read refusals exit 3; `InvalidArgumentsError` exits 2 |
| W39 | High | Schema reads used the bare DocType record (`/api/resource/DocType/<dt>`), which has no Custom Fields or Property Setters. On an India Compliance instance, schema mapping silently dropped `gstin`/`pan`/`gst_hsn_code`, and KYC pre-validation missed the mandatory `gst_category`. The local RBAC fallback read standard DocPerm rows even where Custom DocPerm overrides them, so a revoked role could still pass. (Found live after IC + HRMS were installed.) | `discover.py`, `schema_mapping.py`, `core/client.py` | Merged meta (`frappe.desk.form.load.getdoctype`). A flagged fallback counts as "schema unavailable" (payload passes through unmapped). Permission rows come from merged meta, and a failure is inconclusive (refuse) |
| W40 | Medium | `discover.py meta` read `issubmittable`; Frappe's field is `is_submittable`, so every doctype, Purchase Order included, was reported non-submittable. (Found live.) | `discover.py` | Reads `is_submittable`; output key unchanged |
| W22 | Design | Creating a User needs System Manager on the bot. Holding System Manager makes the permission pre-check unreliable on every tag. The live behaviour of `has_permission`, which ignores `user=`, and the local DocPerm fallback conflict with the 2026-09-01 "Option D" decision. | `core/client.py:702` | 05 |

## Target design

### Operation registry

There is one module, `core/operations.py`. Every write the skill can make is declared there once:

```python
@dataclass(frozen=True)
class Operation:
    key: str                  # "system_admin.create_user", "accounts.generic"
    domain: str               # must have a registered allowlist
    transport: str            # "resource" | "rpc"
    doctype: str | None       # for allowlist + RBAC; None = taken from args (generic ops)
    ptype: str                # has_permission perm type for the RBAC gate
    rpc_path: str | None      # rpc transport only
    token_policy: str         # "none" | "token" | "token+user_code"
    prepare: Callable         # args -> PreparedRequest (exact method/path/name/body)
    preconditions: tuple      # e.g. kind<->doctype match, KYC, concurrency, elevated-role check
    post: Callable | None     # attribution comment, linked KYC records
```

### One pipeline: `run_operation(op_key, args, ctx)`

The steps always run in this order:

1. **Resolve** the operation. Unknown key: refuse.
2. **Prepare** the request. This applies schema mapping and defaults such as the purchase-sourced Item defaults. The result is the exact request that will be sent.
3. **Mode.** Refuse unless `read-write`.
4. **Requester.** It must be present.
5. **Allowlist.** The doctype must be in the domain's list.
6. **Ownership.** If a generic op targets a (doctype, action) pair that a bespoke op owns, refuse and name the bespoke op. This closes W01 structurally.
7. **Preconditions.**
8. **Token,** per policy:
   - check presence
   - coerce `issued_at` safely
   - check freshness
   - check the match against `operation_token(op.key, prepared_request, requested_by, issued_at)`
   - check the user confirmation code when the policy needs it
9. **RBAC.** Call `_validate_prod_requester(doctype, ptype, name)`. No doctype is exempt for business writes.
10. **Audit start,** with full context: session, channel, channel metadata, prompt summary, latest prompt.
11. **Send.** On any exception, finish the audit row as Failure and re-raise as a `ConnectorError`.
12. **Audit finish,** then post hooks.
13. **Return** the result, always with `_audit_log_status`.

### One token constructor

`operation_token()` hashes the op key and the full prepared request: method, path, name, body, requested_by and issued_at. The token CLI calls the same `prepare()` and the same `operation_token()`, so render and execute hash identical bytes. All per-domain `*_token()` constructors and `_compute_token` copies are deleted.

### One CLI

```text
execute_write.py --op <key>
confirm_token.py <op> ...
```

`execute_write.py --op <key> ...` is the only write CLI. `confirm_token.py <op> ...` prints the token, `issued_at`, the confirmation code and the prepared request. The prepared request is what the agent shows the user. The write subcommands in `core/client.py` are removed.

## Phases

| Phase | Issues | Intent |
|---|---|---|
| P0: stop the bleeding | 01, 02, 03, 04, 14 | Small, independent patches on the current code. These ship before the refactor. |
| P1: decisions and research | 05, 06, 13 (decision part) | Settle questions before the migration needs them. These run in parallel with P0. Decisions resolved 2026-10-06; 06 is research. |
| P2: unified pipeline | 07, 08, 09, 10, 11 | Build the registry and pipeline, then migrate every write onto it. |
| P3: correctness gaps | 12, 13, 15, 16 | Fixes that are easier once the pipeline exists. |
| P4: docs and tests | 17, 18 | Make the docs match the code. Lock it in with table-driven tests. |

```text
Dependency sketch

01 02 03 04 14      (P0, independent)
05 ──────────┐
06 ────────┐ │
07 ──┬─────┼─┼──> 08 (needs 05)
     ├─────┴──────> 09 (needs 06)
     └────────────> 10 ──> 12
08 09 10 ─────────> 11 ──> 17
07 ───────────────> 18 (grows with each migration; final sweep after 11)
```

## Decisions

All five were resolved on 2026-10-06 by nikhil.sharma@qkeee.ai, who accepted every recommended option.

| ID | Question | Decision | Ticket |
|---|---|---|---|
| D1 | How are system-admin writes credentialed, and what does RBAC mean when `has_permission` ignores `user=`? | **Option A.** A separate admin credential, `QKEEE_ERP_<TAG>_ADMIN_API_KEY/SECRET`, held by a System Manager account. Only system_admin ops can select it. The business bot stays without System Manager. The reliability probe runs per credential. Ticket 05 records the answers to its sub-questions. | 05 → built in 07 and 08 |
| D2 | When KYC partially fails, should the skill roll back the Supplier or return a partial result? | **C + A.** Pre-validate the Address and Contact against live meta before the Supplier create. On a server-side failure, delete the just-created Supplier and raise `KycLinkFailedError`. If the rollback fails, raise `KycPartialFailureError` carrying the Supplier name. A tax-ID field is required unless KYC is waived. | 13 |
| D3 | Do all token-gated ops need the user confirmation code, or only the domain-less path? | **All of them** (`token+user_code`). | 07 |
| D4 | Should the `core/client.py` write subcommands be removed, or kept behind a debug flag? | **Remove them.** `execute_write.py` is the only write CLI. | 03 |
| D5 | Should `delete` be token-gated in every domain? | **Yes.** | 12 |

## Definition of done

- Every write path is declared in the registry. A test enumerates each domain module's public callables and fails if any of them calls `_request` with POST, PUT or DELETE outside the pipeline.
- For every op, a table-driven test proves the pipeline refuses each of these cases:
  - read-only mode
  - no requester
  - a doctype outside the allowlist
  - no token, a stale token or a mismatched token
  - a body edited after the token was made
  - an RBAC denial
- The same test proves every successful result carries `_audit_log_status`.
- `execute_write.py` is tested end to end without mocking the token check.
- The docs name one entry point and give one worked example per op key.
- `python -m pytest scripts` is green.
- A smoke run against `DEMO_ERP` covers one op per domain.

## Out of scope

- Per-user ERPNext auth, and a custom server-side impersonation method. These are noted under D1 only.
- New capabilities, such as manufacturing writes.

## Comments

## Smoke run (DEMO_ERP, 2026-10-06)

Requester `nikhil.sharma@qkeee.in`. The harness supplied the user's reply
with the confirmation code, on the operator's instruction. The instance
has almost no transactional data: no assets, employees, POs or JEs before
the run. There is no admin key, and the bot is privileged, so permission
checks ran through the local role check.

| Check | Result |
|---|---|
| Draft create, procurement (Purchase Order), sales (Customer), accounts (Journal Entry) | Pass. Audit status "ok". Audit rows carry session, channel and approval state. |
| Delete without a token, each of the three | Pass. Exit 3, nothing sent. |
| Delete with a token rendered for another record | Exit 1 at first (W32). After the fix: exit 3, re-checked live. |
| Gated delete: render, code, execute | **Fail.** `LinkExistsError` from the audit log's Dynamic Link (W31). A Failure audit row was written correctly. |
| Read-only mode; unscoped User and Supplier create; Supplier without KYC; MIS write | Pass. Exit 3. |
| `system_admin.disable_user` (no admin key, requester without User write) | Pass. Fails closed with exit 3. |
| `inventory.get_bin_qty` gated read | Pass. |

**Left on DEMO_ERP, undeletable until the W31 migration runs** (all three deleted on 2026-10-06 once the migration ran, see "Live validation"):

- Purchase Order `PUR-ORD-2026-00001` (draft)
- Journal Entry `ACC-JV-2026-00001` (draft, no GL effect)
- Customer `SMOKE Hardening Customer 113809` (disabled)

**Not live-tested:**

- fixed-asset operations (no Asset fixtures on the instance)
- HR operations (no Employees)
- every system-admin write (no admin key)
- submit/cancel on any doctype

These are covered by the unit-test matrix only.

## Live validation (DEMO_ERP, 2026-10-06)

Instance: Frappe 16.31.0 / ERPNext 16.32.3, **no HRMS**, company DEMO LLP. Bot `dev-erp-hermes@qkeee.in` (privileged), so RBAC used the local role/DocPerm check throughout. Admin key: `ravi.khandelwal@qkeee.in` (System Manager). Requesters: `nikhil.sharma@qkeee.in` (now 43 roles, no System Manager), and a Sales-User-only user created by the run for RBAC denials. The harness supplied the "yes <code>" replies on the operator's instruction. Harness and raw results are in `live-smoke/`.

| Pass | Checks | Pass | Notes |
|---|---|---|---|
| 1 (discovery) | 137 | 125 | Found W33–W38, plus the documented ERPNext delete behaviour below |
| Retest of each failure after its fix | 17 | 17 | |
| 2 (full regression, fresh run id) | 137 | 128 | The 9 misses are not code faults: 3 one-time leftover deletes already done in pass 1 (correctly refused, "does not exist"); 2 expectations changed by the W36/W38 fixes and re-verified; 2 Address/User cleanup refusals explained below; 2 harness input errors |

Covered live, per domain:

- **system_admin:** every op. Wrong/missing code, replay to another record, another requester, stale and garbage `issued_at` are all refused. `set_user_roles` is refused when roles change between render and execute. SSRF guard verified. Permission add/update/remove, deletes of audited records (W31).
- **procurement:** KYC refusals, KYC create with linked Address and Contact, KYC rollback (Supplier deleted again), waiver. PO draft → submit → cancel → delete. W10 refusal on edit-after-render. RBAC denial, read-only mode, allowlist.
- **sales:** Customer (Sales-User requester), SO full lifecycle.
- **accounts / inventory:** JE and Stock Entry submit/cancel with GL verified. `get_bin_qty` and `get_stock_reconciliation_items` reads.
- **fixed_assets:** fixtures via `unscoped.generic`. Asset submit; depreciation (exactly the 4 due rows, then 0 pending, so no double-post). Wrong-asset, slice-index and date-tamper refusals. Scrap with date tamper refused; restore cancels the scrap JE. Sell creates a draft SI at the confirmed proceeds.
- **hr_payroll:** Employee create/delete. Leave Application → W37.
- **mis / unscoped:** refusals. **Reads:** `client.py` (health, query, get, 3 reports, roles), `discover.py` (apps/modules/meta/resolve), system-admin reads, read RBAC.
- **Audit trail:** 102 write rows in run 1, none left `Attempted`. Failure rows exist for every failed send. Gate-decision rows have a blank reference by design.

ERPNext behaviour now documented (not a skill defect):

- A cancelled JE or Stock Entry can't be deleted (`LinkExistsError`, GL Entry) unless `delete_linked_ledger_entries` is on.
- A User with history can't be deleted ("disable instead").
- Only System Manager may delete an Address. Supplier deletion removes its sole-linked Address itself.

**Left on DEMO_ERP** (ledger-posted or linked; all `SMOKE`-prefixed or numbered):

- Cancelled Journal Entries `ACC-JV-2026-00002`, `ACC-JV-2026-00012` and cancelled Stock Entries `MAT-STE-2026-00001`, `-00002`
- Submitted Assets `ACC-ASS-2026-00001`…`00004`, with their depreciation, scrap and restore JEs
- Items `SMOKE-FA-1417`/`-1444` (disabled), Asset Categories and Locations `SMOKE * 1417/1444`
- Users `smoke.narrow.1417@example.com`, `smoke.narrow.1444@example.com` (disabled)

**Still not live-tested:**

- HR Leave, Onboarding and Separation (no HRMS on DEMO_ERP)
- `toggle_workflow` success path (no Workflow exists)
- `permission_reset` (it would wipe every custom override on a doctype)
- The RBAC check through a reliable `has_permission` (needs a narrow-role bot account)

## Live validation, run 3 (DEMO_ERP with India Compliance 16.8.4 + HRMS 16.16.0, 2026-10-06)

Same actors as above. The new apps exposed W39 and W40; both are fixed, unit-tested (321 passed) and verified live. Example: `gstin` persisted on the KYC Address and Supplier, and `gst_hsn_code` persisted on Item.

| Pass | Unique checks | Pass |
|---|---|---|
| Triage (s10–s40 plus new HR suite s25) | — | Failures were W39/W40 or scenario data the new apps require. Fixed in the scenarios: IC KYC data, HSN on Item, `leave_approver`, Holiday List Assignment |
| Final clean pass (fresh run id) | 148 | 148 |

New live coverage:

- **India Compliance KYC:** GSTIN on Address; a bad-checksum GSTIN is rejected by IC and the Supplier is rolled back.
- **HRMS:** Leave Without Pay and allocated Casual Leave (draft → approve → submit → Attendance created → cancel); over-balance and Open-status refusals; Job Offer → Onboarding; Separation; RBAC (only System Manager deletes Onboarding/Separation); `permission_reset` (cleanup of Note).

Run-3 leftovers (ledger-posted or linked):

- Cancelled JEs, Stock Entries, Leave Applications and Leave Allocations
- Submitted Assets `ACC-ASS-2026-00005`…`00008` and Holiday List Assignments
- Employees `HR-EMP-00001`…`00005`, `SMOKE` Holiday Lists, Locations, Asset Categories, and disabled Items
- Disabled users `smoke.narrow.1514/1540/1540z@example.com`

Open questions (not changed):

- Leave Allocation and Holiday List Assignment are reachable only via `unscoped.generic`. Should hr_payroll own them?
- The bot still holds System Manager.

