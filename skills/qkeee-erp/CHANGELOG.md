# qkeee-erp skills — changelog

Repo-side record only. Never loaded into agent context — nothing in
`qkeee-erp-associate/references/` should point here or at a finding
number; this file is where that provenance moved to (2026-09-13 writing
pass, batch 1 / C1). Findings F1–F13 were logged in a companion repo's
local issue tracker (`.scratch/hermes-erp-bot-reliability/spec.md` and
its `issues/01-schema-first-attribute-mapping.md`), not part of this
repo — read there for the full incident writeup, reproduction, and code
diff discussion behind each entry below. This file maps each finding to
the rule it produced and where that rule now lives in the shipped skill.

| Finding | What happened | Rule it produced | Lives in |
| --- | --- | --- | --- |
| F1 | Hand-written per-write scripts left `session_id` blank, never built `channel_metadata`, never passed `latest_prompt` — despite the real values being in context | `execute_write.py` is the one write entry point; WARNs on stderr before firing if session/channel/prompt context is missing | `00-conventions.md` GRC baseline; `01-connectivity.md` "Worked calls"; worked examples in `tool-cookbook.md` |
| F2 | Supplier KYC-completeness rule was treated as optional scope; GSTIN retry targeted the wrong doctype (Supplier instead of Address) | `procurement.py` refuses Supplier `create` without `kyc={...}` or an explicit waiver; tax ID documented as an Address field, never Supplier | `domains/procurement.md` non-negotiables |
| F3 | `Item` has no owning domain and no documented India-compliance field list; HSN code was embedded in free text instead of attempted as a field | Generalized into issue 01 (schema-first attribute mapping) rather than a narrow Item patch | `domains/procurement.md` (cross-reference); issue 01 in the companion repo |
| F4 | doc-extraction's mandatory staged report (confidence ratings + `reconciliation_check`) never ran — went straight from OCR to free text | Confidence/value-key refusal is code-enforced one step downstream (`schema_mapping.match_staged_report()`); a dedicated `render_*.py` staged-report script is still owed | `domains/doc-extraction.md` step 6 "Known gap" |
| F5 | The Item write's confirmation token was self-computed and self-verified in the same turn/process | `gated_mutate_resource()` requires `user_confirmation_text` — the literal text of the user's own reply — not just a matching token | `00-conventions.md` GRC baseline; worked example in `tool-cookbook.md` |
| F6 | Purchase cost auto-saved as this org's selling price (`standard_rate` bare on Item create defaults to Standard Selling) | `item_write_helpers.py` refuses a bare `standard_rate` on a purchase-sourced item create; points at the Standard Buying alternative | worked example comment in `tool-cookbook.md` |
| F7 | Every write proceeded on a WARN, not a real per-requester RBAC check, under a privileged bot identity where `has_permission` doesn't discriminate | `_requester_has_role_permission()` — a local, RPC-free role/DocPerm check — replaces the allowlist/token fallback when the RPC is known unreliable; `False`/`None` both refuse | `00-conventions.md` GRC baseline (RBAC pre-check) |
| F8 | Schema discovery (`discover.py`) didn't exist in the shipped skill; a 403 querying `DocField` directly was misdiagnosed as expected low-privilege access | `discover.py` resurrected and ported to the current connector API; `meta`/`resolve` go through `get_resource()`, never `query DocField` | `01-connectivity.md` "Discovering a doctype's live shape" |
| F9 | `is_sales_item` set to 1 with nothing in a purchase invoice supporting it | Same fix as F6 — `item_write_helpers.py` defaults `is_purchase_item=1, is_sales_item=0` for a purchase-sourced item unless told otherwise | worked example comment in `tool-cookbook.md` |
| F10 | Agent offered to re-attribute a permission-denied read to a different `requested_by` | Refusal messages and the GRC baseline explicitly forbid rerouting to a substitute requester; only "report the gap" or "decline" | `00-conventions.md` GRC baseline (requester identity) |
| F11 | A denied requester-permission check left zero trace in the audit log — the gate raised before the guarded read/write ever ran, and the gate itself never logged | `_log_gate_decision()` writes one row per `_validate_prod_requester()` call, denial or allow | `00-conventions.md` GRC baseline; `domains/grc-audit.md` |
| F12 | Business-intent reads of `User`/`Role` were silently dropped by a doctype-keyed audit exemption built for a different purpose (stopping internal plumbing calls from logging themselves) | Read-path exemption made purpose-keyed (`internal=True`), not doctype-keyed; a business-intent `User`/`Role` read is now a real logged row | `00-conventions.md` GRC baseline; `domains/grc-audit.md` |
| F13 | Update's `field_diff` had been silently dead since it was built — the pre-image fetch never passed `requested_by`, so it was refused every time and the refusal was swallowed | Pre-image `get_resource()` call now carries the write's own `requested_by` and full session/channel/prompt context | `plugins/qkeee-erp/qkeee_erp/core/client.py` `mutate_resource()` (code fix; no doc rule needed) |
| W31 | Live smoke run (2026-10-06): no audited record could be deleted — the audit log's `reference_name` was a Dynamic Link, so Frappe raised `LinkExistsError`; the same link silently dropped audit rows naming a non-existent record | `reference_name` is a Data field; `init_bot.py` re-runs migrate provisioned instances | `plugins/qkeee-erp/qkeee_erp/doctype_defs.py`, `plugins/qkeee-erp/qkeee_erp/init_bot.py`, `tool-cookbook.md` |
| W32 | Live smoke run: a finalizing action on a record that no longer exists exited 1 ("ERPNext rejected it") although nothing was sent | A 404 in the unchanged-since-render read is a gate refusal (exit 3) | `plugins/qkeee-erp/qkeee_erp/core/operations.py` |
| RIB 02/04/09 | Requester identity binding (2026-10-08, agents `.scratch/qkeee-erp-requester-identity-binding`): the agent wrote the shell command, so it could override the session sender or call ERPNext with the bot key; `execute_code` could read the key; Kanban workers had no requester and chat-made tasks skipped triage | ERPNext only through the `qkeee-erp` gateway plugin tools (no requester or mode argument); credentials moved to `plugin-data/qkeee-erp/`; `code_execution` off on google_chat; chat `kanban_create` forced into triage; each task's origin recorded from the session context and resolved via `from_decompose_of` | `plugins/qkeee-erp/`; `plugins/qkeee-erp/qkeee_erp/core/kanban_origin.py`; `SKILL.md` "Calling ERPNext" + step 3 |
| RIB 03 | Requester identity binding L2 (2026-10-08): after 04 the agent could still prefix `HERMES_SESSION_*` overrides on a terminal command or read the credentials file | A `pre_tool_call` guard blocks terminal/`execute_code` session overrides, terminal/`execute_code` source or a non-`erp_*` tool's path argument naming `qkeee-erp.env` (or a glob for it) or `plugin-data`, and `execute_code` using `core.client` or `/api/resource|method`. Pattern match only, not a boundary | `plugins/qkeee-erp/identity_guard.py`; `SKILL.md` "Calling ERPNext" |
| W01–W29 | Write-path hardening review (2026-10-06): the generic write path could bypass the bespoke gated functions; several tokens weren't bound to what was sent; User/Role writes skipped the requester check and the audit log; RPC audit rows used invalid Select values; the unscoped path could write privilege/code/credential doctypes and bypass the Supplier KYC gate | One registry of named write operations and one pipeline (mode → requester → allowlist → ownership → preconditions → token over the exact request + user code → RBAC → audit → send); `execute_write.py --op` is the only write CLI, `confirm_token.py render` its render step; separate admin credential for system-admin writes | `plugins/qkeee-erp/qkeee_erp/core/operations.py`; `tool-cookbook.md` write flow; `00-conventions.md`; domain docs for fixed-assets, system-admin, procurement |
| PPS 04/05/06/11 | Profile + Plugin split (2026-10-09, agents `.scratch/qkeee-erp-plugin-profile-split`): the ERP library lived in the skill's `scripts/` although only the gateway plugin may run it; the plugin read Profile config (`skills.config.qkeee_erp`); the agent's docs told it to run scripts | Library moved into the plugin package; plugin settings in `plugins.entries.qkeee-erp.settings` (legacy section = setup pending, no `erp_*` tools); Operator CLI `hermes qkeee-erp …` runs the tool handlers; calling rules ship as plugin skill `qkeee-erp:usage`; the skill keeps domain know-how only; Instance Notes promotion via `erp_discover` `promotion_plan` | `plugins/qkeee-erp/` (`settings.py`, `cli.py`, `erp_tools.py`, `skills/usage/SKILL.md`); `SKILL.md` "Calling ERPNext"; `tool-cookbook.md` |
| BR 2026-10-13 | DEMO_ERP bot-role session: with stock roles removed, Frappe hid the bot's own `roles` table, so the stock-role check passed a bot holding System Manager; every business read was refused (the bot could not read the requester's User); `health` showed no gap for it; preflight said ready; RECHECK ENV used identities cached before the admin's fix; the agent reported "the bot holds no roles" from an empty read | The Admin Account reads the bot's roles, the requester's roles and DocPerm rows (ADR 0003 amendment); unread bot roles or a bot without `Qkeee Bot` refuse with a gap; `health` clears identity caches, returns `bot_roles` and an `rbac_identity` gap; refusals carry `gaps[]`; preflight rule 7 checks the requester; `erp_discover bot_permissions`; the check counts Frappe's implicit All/Guest/Desk User roles and denies a disabled requester | `plugins/qkeee-erp/qkeee_erp/core/client.py`, `discover.py`, `erp_tools.py`; `00-conventions.md` role-gap prompt rules 4–5, Report-back rules 6–7 |
| ADR 0003 open item, 2026-10-13 | The self-escalation rule counted every role the Admin Account held, so with an all-roles admin no permission edit through the agent was possible; preflight could not show a bot read gap on a mandatory Link target; the permission check denied Administrator | Admin roles protected by reach (`operations.admin_key_doctypes()`), `Qkeee Bot` protected everywhere and never given to a user; preflight rule 8 probes bot read per mandatory Link target (gap, not blocker); Administrator holds every permission | `core/operations.py` `_self_escalation_target`; `discover.py` preflight; `domains/system-admin.md`; agents ADR 0003 |
| Bot provisioning, 2026-10-13 | Admins added the bot's permissions one by one in the UI, guided by the agent; a live reply leaked formatter placeholders ("GC6"); and since the bot-identity gate change, init-bot could not run on a bot without `Qkeee Bot` | `init-bot` assigns `Qkeee Bot` (optional `--strip-stock-roles`) and with `--domains` grants the business permission rows from code (never delete, never privileged); bot-identity gate checks only bot-key calls; Google Chat formatter restores nested placeholders (hermes-agent) | `plugins/qkeee-erp/qkeee_erp/init_bot.py`, `doctype_defs.py`, `cli.py`, `core/client.py`; agents ADR 0003 |

## 2026-10-06 — write-path hardening (P0–P3, plus the P4 docs and tests)

Findings register and tickets: the companion repo's
`.scratch/qkeee-erp-write-path-hardening/`. Every write now runs through
`core/operations.py`. `mutate_resource()`/`gated_mutate_resource()` and
each domain's `mutate()` wrapper were removed too (later the same day):
`core/client.py` is the lower layer and never imports `core/operations.py`,
so the dependency runs one way, domains -> operations -> client. The
old per-domain token constructors, `call_whitelisted_method()`,
`destructive_mutate()`, `create_user()`, `gated_config_mutate()`,
`call_permission_manager()`, `mutate_resource_with_concurrency()` and
`register_domain_token_gate()` are removed. `core/client.py` has no write
subcommand. New env vars: `QKEEE_ERP_<TAG>_ADMIN_API_KEY/_SECRET`,
`QKEEE_ERP_<TAG>_KYC_TAX_ID_FIELD`, `QKEEE_ERP_AUDIT_MASK_FIELDS`.

## 2026-09-13 — writing pass, batch 1

Stripped all `F<n>` / `.scratch/hermes-erp-bot-reliability` references from
`qkeee-erp-associate/references/` (11 refs across `00-conventions.md`,
`01-connectivity.md`, `domains/doc-extraction.md`, `domains/grc-audit.md`,
`domains/procurement.md`) — see this file's table above for where each
finding's rule now lives. No rule's enforceable meaning changed; only the
case-number/path provenance moved here. Also added a **Done when:**
completion criterion to every numbered step across `SKILL.md`,
`03-spec-driven-execution.md`, and every `domains/*.md` procedure, and a
new `## Report-back` contract in `00-conventions.md`. See
`agents/.scratch/mattpocock-skills-adoption/plan.md` (companion repo) for
the full analysis this pass executes against.

## 2026-09-13 — writing pass, batch 2

Moved `SKILL.md`'s Governance and Status-note sections (operator/
maintainer material) to new `qkeee-erp-associate/references/governance.md`
— router 248 → 200 lines. Split `01-connectivity.md`'s worked CLI/
`execute_write.py` invocations into new `references/cli-cookbook.md`,
latched only once a call is about to run; `01-connectivity.md` keeps the
always-needed mechanics. Normalized domain-file section templates:
`mis.md`'s stray sixth heading folded into its Non-negotiables section,
`manufacturing.md`'s per-heading status parentheticals moved into each
section's one-line caveat and its missing Relationships section added.
10 of 11 domain files now share an identical five-heading skeleton
(`grc-audit.md` deliberately kept its own second heading — its content
isn't a behavioral-rule list, so the standard name would misdescribe
it). No rule's enforceable meaning changed.

## 2026-09-13 — writing pass, batch 3

- **Dedup (C8):** `00-conventions.md`'s GRC baseline had drifted into
  restating `governance.md`'s own two sections. Replaced both restated
  bullets with one-line pointers; moved the "doc claims vs. actual
  config" content (not previously in `governance.md`) there as its own
  section. `00-conventions.md` GRC baseline: ~237 → ~205 lines.
- **Positive-target rewrite (C4):** rewrote the two most negation-led GRC
  bullets — requester-identity resolution (F10) and the RBAC pre-check
  — to lead with the positive target, retaining exactly one prohibition
  each for the hard security guardrail underneath (never substitute a
  requester; never trust `has_permission` once known unreliable without
  the local fallback). Two similarly negation-led lines in
  `01-connectivity.md` (the `qkeee-erp.env` read-into-context ban, the
  `discover.py` intro) rewritten the same way. Every other GRC-baseline
  bullet was already positive-led with a necessary retained ban — left
  as-is rather than force a rewrite that wouldn't change meaning.
- **Folds:** `grilling`'s frontier/numbered-rounds clarify method into
  `03-spec-driven-execution.md` step 1 (C10). `diagnosing-bugs`' "red"
  signal + "a WARN in the same output outranks your prior" into
  `02-environment-assessment.md` step 4 — targets F8's exact
  misdiagnosis (C11). `wayfinder`'s "refer by name" + "the board is an
  index, not a store" into the Kanban section (C17).
- **Two new shipped skills (C13/C14):** `qkeee-erp-questionnaire` (turn
  a data gap only someone else can fill — supplier KYC, an HR field, a
  doc-extraction low-confidence field — into a questionnaire document to
  hand them; fills the gap F2 hit) and `qkeee-erp-handoff` (emit a fixed
  ERP continuity block — env tag, requester-identity rule, `session_id`
  reset, active spec path — before a hand-off or context compaction;
  F1's "compaction is a new logical session" rule had nothing carrying
  state across that boundary). Both cross-referenced from
  `qkeee-erp-associate/SKILL.md`'s intro and, for the questionnaire's
  output path, `00-conventions.md`'s naming table.
- **Category router (C15):** new `skills/qkeee-erp/DESCRIPTION.md`.
- **C18 (requires_tools gating) — substituted, not implemented as
  specified.** Hermes' conditional-activation fields
  (`requires_tools`/`requires_toolsets`/`session_platforms`) gate on
  tool/toolset/platform presence; neither new skill has a real
  dependency of that kind to gate on; a "child of qkeee-erp-associate"
  gate doesn't exist as a mechanism. Substituted narrowly-scoped,
  ERPNext-specific descriptions plus `related_skills` metadata as the
  practical equivalent — controls accidental firing the way the plan
  intended, without declaring a tool dependency that isn't real.

No rule's enforceable meaning changed anywhere in this pass. 216 tests,
28 subtests still pass throughout (docs/new-skills only — `scripts/`
untouched). Closes the mattpocock-skills-adoption plan's in-scope items
(C1–C11, C13–C15, C17, C18); C12 and C16 remain deliberately deferred
until F1–F13 is reviewed and committed.
| IRR 01 | Item run review (2026-10-14, agents `.scratch/qkeee-erp-item-run-review`): a purchase service Item was created with no expense account, SAC or tax template, and with `purchase_sourced_item` set for a typed request | Item master clarify checklist (accounts, SAC, tax, flags, code convention, near-duplicate search, instance defaults); `purchase_sourced_item` only for purchase-document evidence | `domains/inventory.md` "Item master"; `tool-cookbook.md` |
| IRR 02 | `user_approved` is agent-set, and the Audit Log showed "Approved" alike for a verified token and an agent assertion | `approval_note` starts with `[approval: token+code \| token \| agent-asserted \| none]` | `plugins/qkeee-erp/qkeee_erp/core/operations.py`; `00-conventions.md`; `domains/grc-audit.md`; `qkeee-erp:usage` |
| IRR 03 | Base image `HERMES_WRITE_SAFE_ROOT=/opt/data` denied every spec write to the cwd; specs fell back into the sidecar's hidden volume | Gateway safe root includes `HERMES_CWD`; chat sessions show the spec digest, not just a path | `docker-compose.yaml`; `03-spec-driven-execution.md` step 3 |
| IRR 04 | The user approved field values before the spec existed; the spec kept a confirmation token, prose rollback, and "Deviations: none" over three deviations | Approval shows the spec digest (one combined message allowed for a single token-gated write); no token in specs; exact rollback calls; procedure deviations listed; a code-only reply is valid | `03-spec-driven-execution.md`; `tool-cookbook.md`; `qkeee-erp:usage` |
| IRR 05/06 | About 73% of the run's context was references, `meta Item` and the full `list_ops` | `preflight` before `meta`; load references when their step starts; `list_ops` takes `op` / `domain` | `SKILL.md`; `03-spec-driven-execution.md`; `plugins/qkeee-erp/qkeee_erp/execute_write.py`, `erp_tools.py` |
| IRR 08 | User request (2026-10-14): email the requester a formatted summary when a task is done | `notify.summary_email`: render re-reads the records and builds escaped HTML for the requester only; the agent sends it with the email skill after the user confirms completion; execute logs it as a Sent Communication on the primary record | `plugins/qkeee-erp/qkeee_erp/domains/notify.py`; `03-spec-driven-execution.md` step 8; `tool-cookbook.md`; `qkeee-erp:usage` §7a |
| dev-erp 2026-10-15 | 403 "Insufficient Permission for Supplier Group" after procurement was provisioned: init-bot and preflight rule 8 covered only mandatory Link targets, and ERPNext v15 `Supplier.supplier_group` is optional (the test fixture wrongly marked it mandatory) | init-bot grants read on every Link target a user can set (not hidden, read-only or fetched) plus mandatory ones; preflight rule 8 also probes the optional Links the payload sets | `plugins/qkeee-erp/qkeee_erp/doctype_defs.py` `is_bot_read_link()`; `init_bot.py`; `discover.py` |
| dev-erp 2026-10-15 (2) | Supplier Group create refused with 403 although preflight said ready: init-bot gave masters read only, and preflight never checked the bot's own create | `DOMAIN_MASTER_DOCTYPES` (procurement: Supplier Group, Payment Terms Template; sales: Customer Group, Territory; inventory: Item Group, Brand, UOM, Warehouse) get read/create/write with their domain; preflight rule 9 reads the bot role's create row and blocks when it is missing | `plugins/qkeee-erp/qkeee_erp/doctype_defs.py`, `init_bot.py`, `core/client.py` `bot_role_grants()`, `discover.py`; `domains/procurement.md`; `03-spec-driven-execution.md` |
| dev-erp 2026-10-15 (3) | Supplier run: the agent set `default_currency = INR` and `gst_category = Overseas` for a US supplier without asking, and called an OIDAR GSTIN (`99…OS…`) invalid | Supplier checklist (currency, GST category, tax ID type) asked before approval; derived values shown as proposals; tax ID types incl. OIDAR | `domains/procurement.md` "Supplier checklist" |
| dev-erp 2026-10-15 (4) | Summary email not sent: the terminal sidecar has no email tooling, and giving it a mail credential would let the agent mail anyone | The gateway sends: `mailer.py` (multipart HTML + text over SMTP from the agent's `EMAIL_*` mailbox, one recipient only); `notify.summary_email` sends in a new `Operation.pre_send` step, after every gate and before the Audit Log row, then logs the Communication with its Message-ID | `plugins/qkeee-erp/qkeee_erp/mailer.py`, `domains/notify.py`, `core/operations.py`; `03-spec-driven-execution.md` step 8; `qkeee-erp:usage` §7a |
| dev-erp 2026-10-15 (5) | "Email the Item list to Nikhil and Sanjay": no write path existed, so the agent searched himalaya / Gmail skills, which would have sent ERP data with no check | `notify.share_records`: the plugin runs the query as the requester, emails one message per recipient in `QKEEE_ERP_EMAIL_DOMAINS` (else the requester's domain), confirmation code for anyone beyond the requester, logs one Communication; SKILL.md: ERP data leaves by email only through `notify.*` | `plugins/qkeee-erp/qkeee_erp/domains/notify.py`; `SKILL.md` "Calling ERPNext"; `qkeee-erp:usage` §7a; `tool-cookbook.md`; `env.example` |
| dev-erp 2026-10-15 (6) | `erp_discover promotion_plan` crashed (`'str' object has no attribute 'get'`) when `doctypes` was a list of names; the agent then improvised a `skill_manage` call with no action | Bare DocType names accepted in the catalog; a malformed `findings` shape is an `invalid_arguments` refusal that shows the expected shape; the tool schema documents it | `plugins/qkeee-erp/erp_tools.py` `_promotion_findings()`; `qkeee_erp/core/memory_promote.py` |
| dev-erp 2026-10-15 (7) | `notify.share_records`: the email went out, the Communication log then failed with 403 (bot had no create), and the agent was told "ERPNext refused the write", so it reported "not sent" and offered to send again | Render and pre-send refuse before any email while the bot cannot create Communication (gap names `init-bot --domains notify`); a log failure after a send is `outcome_unknown` saying "The email WAS SENT … Do not send it again"; preflight rule 9 names the init-bot domain for any doctype | `plugins/qkeee-erp/qkeee_erp/domains/notify.py`; `discover.py` `_granting_domains()`; `qkeee-erp:usage`; `03-spec-driven-execution.md` step 8 |
| dev-erp 2026-10-16 | A Purchase Invoice create failed with a bot gap because init-bot had only been run for procurement and inventory; the user had to know which domain owns which doctype | `init-bot` without `--domains` provisions every bot domain (`--domains` narrows, `none` = role + audit log only); the dry-run checks that `--requested-by` may grant (create on Custom DocPerm) and prints no token when not; gap prompts say "run init-bot" instead of naming a domain | `plugins/qkeee-erp/qkeee_erp/init_bot.py` `resolve_domains()`, `requester_can_grant()`; `cli.py`; `discover.py` |
| dev-erp 2026-10-21 | HR demo setup: the bot got 403 on Salary Structure, Salary Component, Leave Allocation, Leave Policy Assignment, Shift Assignment, Attendance and Job Opening after a full `init-bot` run, because no code list named them; the agent then asked an admin for ~40 hand-made Role Permission Manager rows | `init-bot` also copies each domain's template roles' permlevel-0 rows onto `Qkeee Bot` (`DOMAIN_TEMPLATE_ROLES`, e.g. hr_payroll → HR Manager, HR User): read/create/write/submit/cancel only, never a privileged doctype, if_owner row or child table, read only on a Single. Template-only doctypes are written through `unscoped.generic` (token-gated). Preflight's bot-create gap names the covering domain and says "re-run init-bot" | `plugins/qkeee-erp/qkeee_erp/doctype_defs.py` `DOMAIN_TEMPLATE_ROLES`; `init_bot.py` `template_rights()`, `bot_permission_matrix()`; `discover.py` `_granting_domains()`; agents ADR 0003 amendment 2026-10-21 |
