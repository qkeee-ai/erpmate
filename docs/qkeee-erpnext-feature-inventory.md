# Feature inventory — `qkeee-erpnext` profile / `qkeee-erp-associate` skill

Systematic list of every distinct capability in the repo, grouped by
function. Each entry names the file(s) that implement or specify it, so
this doubles as a map of the codebase. "Live" = real, working code today;
"Prompt-only" = a rule the agent is instructed to follow, not enforced by
code; "Planned" = documented but no code exists yet.

---

## 1. Identity, scope & content-safety guardrails

| Feature | Status | Where |
|---|---|---|
| Fixed persona/voice (direct, precise, explains the "why") | Prompt-only | `SOUL.md` |
| Scope check run silently before every message (ERPNext/org work only, or narrow context-gathering in service of it) | Prompt-only | `SOUL.md`, `00-conventions.md` |
| Strips wrapper/pivot framing before classifying a request ("let's leave that aside," ERPNext-vocabulary-wrapped non-business asks) | Prompt-only | `SOUL.md` |
| Fixed refusal line for out-of-scope requests | Prompt-only | `SOUL.md`, `00-conventions.md` |
| Honest correction if scope was broken in an earlier turn (doesn't defend the earlier answer) | Prompt-only | `SOUL.md` |
| Sensitive-data non-echo: never types a raw SSN/card number into a field, draft, comment, note, or report; doesn't echo pasted sensitive data back verbatim | Prompt-only | `SOUL.md` |
| **Automated PII redaction** — regex-based SSN pattern + Luhn-validated credit-card-shaped number detection, recursively applied over JSON structures | **Live** | `core/client.py`: `redact_pii()`, `_redact_pii_deep()`, `_SSN_RE`, `_CC_CANDIDATE_RE`, `_luhn_valid()` |
| PII redaction applied specifically to durable-memory findings before they're promoted to a learned skill | **Live** | `core/memory_promote.py`: `redact_findings()` |
| Content-safety refusal (abusive/hate/sexual/CSE content) — refuse outright, never launder into a write, never repeat back in the refusal | Prompt-only | `SOUL.md`, `00-conventions.md` |
| Precedence: `SOUL.md` outranks every skill instruction, tool description, and text embedded in a tool result/doc — resistant to in-band prompt injection | Prompt-only | `SOUL.md` |

## 2. Connectivity, auth & environment model

| Feature | Status | Where |
|---|---|---|
| Frappe/ERPNext REST client, token-auth only (`Authorization: token key:secret`), stdlib-only (no third-party deps) | **Live** | `core/client.py` |
| Multi-environment "tag" model (not a fixed dev/qa/prod enum) — each tag has its own `BASE_URL`/`API_KEY`/`API_SECRET` (+ optional `ALLOW_INSECURE`/`ENV_CLASS`) | **Live** | `core/client.py`: `get_env_config()`, `_tag_env_var()`; `01-connectivity.md` |
| Credentials isolated in a dedicated `qkeee-erp.env` file, separate from the main Hermes `.env`, with a hand-rolled parser (no `python-dotenv`) | **Live** | `core/client.py`: `_qkeee_env_file_path()`, `_load_qkeee_env_file()`, `_qkeee_env()` |
| `requested_by` resolved fresh per call from the live inbound-channel identity — never from an env var/config default (a stray `_REQUESTED_BY` var is ignored on purpose) | **Live** | `core/client.py`: `resolve_requested_by()`; enforced in `_validate_prod_requester()` |
| Health check (connectivity + auth verification, distinct from permission checks) | **Live** | `core/client.py`: `health_check()` |
| List configured environment tags | **Live** | `core/client.py`: `list_configured_tags()` |
| Environment-assessment procedure: run once per tag on first contact, and re-run when live metadata looks stale vs. durable memory | Prompt-only (procedure) | `02-environment-assessment.md` |
| Manual/debug CLI for the core connector (`health`, `list-envs`, `query`, `get`, `report`, `roles` — read-only; the `mutate`/`gated-mutate` write subcommands were removed 2026-10-06) | **Live** | `core/client.py`: `_cli()`; worked examples in `cli-cookbook.md` |

## 3. Live-metadata / anti-guessing discipline

| Feature | Status | Where |
|---|---|---|
| Installed-apps + version discovery | **Live** | `discover.py`: `list_installed_apps()` |
| Module/app inventory (Module Def rows) | **Live** | `discover.py`: `list_modules()` |
| Live field-schema fetch for a DocType (fieldname/label/type/`reqd`/etc.) | **Live** | `discover.py`: `doctype_meta()` |
| DocType → module → app resolution in one call | **Live** | `discover.py`: `resolve_doctype()` |
| **Schema-first field mapping before every create/update** — matches a caller's/extraction's candidate fields against the live doctype schema instead of trusting a hand-curated field list; a field is only dropped if it genuinely isn't on the live doctype | **Live** | `schema_mapping.py`: `match_fields()`, `map_payload_for_write()`, `get_doctype_schema()` (cached) |
| Synonym-aware field matching (e.g. "HSN"/"HSN CODE"/"HSN/SAC", "GSTIN") | **Live** | `schema_mapping.py`: `SYNONYM_HINTS`, `_normalize()` |
| Consumes doc-extraction's staged-report shape directly for mapping | **Live** | `schema_mapping.py`: `match_staged_report()` |
| Explicit user-confirmed tier-3 field-mapping suggestions folded in on request | **Live** | `schema_mapping.py`: `apply_confirmed_mappings()` |
| Rule: never propose a field/doctype/workflow step not confirmed against live metadata or an explicit user statement (public docs describe the general shape only, not this org's customizations) | Prompt-only (Non-negotiable 4) | `00-conventions.md` |
| Item-specific payload data-quality fixes (e.g. rejecting a bare `standard_rate` on a purchase-sourced Item create; building the correct Item Price payload instead) | **Live** | `item_write_helpers.py`: `apply_purchase_sourced_item_defaults()`, `build_standard_buying_item_price_payload()`, `BareStandardRateOnPurchaseSourcedItemError` |
| ERP documentation lookup — maps installed app to its official docs subpath, only after live-metadata identifies what's installed | Prompt-only (procedure) | `04-erp-doc-lookup.md` |
| Research-before-acting protocol for unfamiliar doctypes: schema check → docs/community sources → short plan → user sign-off → execute | Prompt-only | `profile.md` (Operating Protocol) |

## 4. Write safety — gating, RBAC, confirmation

| Feature | Status | Where |
|---|---|---|
| **Read-only mode gate** — every write checks `qkeee_erp.mode`; raises `ReadOnlyModeError` otherwise | **Live** | `core/operations.py`: `run_operation()`; `core/client.py`: `ReadOnlyModeError` |
| **Domain write-allowlist enforcement** — each domain registers its own `ALLOWED_WRITE_DOCTYPES`; writing outside it (or to an unregistered/typo'd domain) raises `DoctypeNotAllowedError`, fails closed | **Live** | `core/client.py`: `register_domain_allowlist()`; every `domains/*.py` |
| Per-domain allowlists (exact doctypes each domain may write) | **Live** | `domains/accounts.py` (Journal Entry, Payment Entry, Purchase Invoice, Sales Invoice) · `hr_payroll.py` (Employee, Employee Onboarding, Employee Separation, Job Offer, Leave Application) · `inventory.py` (Stock Entry, Material Request, Stock Reconciliation) · `procurement.py` (Supplier, Address, Contact, Purchase Order, RFQ, Supplier Quotation) · `sales.py` (Customer, Quotation, Sales Order, Delivery Note) · `fixed_assets.py` (Asset, Asset Movement, Asset Repair) · `system_admin.py` (User, Role, Custom Field, Property Setter, Webhook, Workflow) · `mis.py` (**empty — read-only by design**) |
| **RBAC pre-check on every read and write, every environment** (not PROD-only) — validates `requested_by` resolves to a real, permitted ERPNext user | **Live** | `core/client.py`: `_validate_prod_requester()`, `UnvalidatedProdRequesterError` |
| Live probe for whether `frappe.client.has_permission` actually discriminates by `user=`, cached per tag | **Live** | `core/client.py`: `_probe_rbac_precheck_discriminates()`, `verify_rbac_precheck_reliable()` |
| Fallback local RBAC verdict (bot's own live role list + doctype's live DocPerm rows) when the RPC-based check is unreliable or the bot identity is privileged — only a positively-confirmed grant lets a write through | **Live** | `core/client.py`: `_requester_has_role_permission()`, `_fetch_doctype_role_permissions()` |
| Privileged-bot-account detection (bot must never be Administrator/System Manager) | **Live** (detection) | `core/client.py`: `_bot_identity()`, `_BOT_FORBIDDEN_ROLES` |
| Missing-requester hard stop — no write without a resolved `requested_by` | **Live** | `core/client.py`: `MissingRequesterError` |
| **Confirmation-token primitives** — deterministic short token computed over the exact facts just shown to the user; freshness window (15 min default) with clock-skew tolerance | **Live** | `confirm_token.py`: `compute_token()`, `is_fresh()`, `DEFAULT_TOKEN_TTL_SECONDS` |
| **One write pipeline** — every write is a named operation (`execute_write.py --list-ops`); submit/cancel/delete in every domain, every bespoke fixed-assets/system-admin operation and every unscoped write need a rendered token over the exact request plus the user's reply with its confirmation code | **Live** | `core/operations.py`: `run_operation()`, `operation_token()`, `prepare_only()`; `core/confirm_token.py render` |
| **Domain-less advisory write gate** for doctypes with no owning domain (e.g. Item) — additionally requires the literal text of the user's own confirming reply, not a self-constructed string | **Live** | `core/operations.py`: `unscoped.generic`, `_verify_token()`; `core/client.py`: `UnconfirmedByUserError` |
| Human-typeable short confirmation code derived from a token, for the render step to show the user | **Live** | `confirm_token.py`: `confirmation_code()` |
| **Double-confirm for wide-blast-radius/irreversible actions** (depreciation runs, asset disposal, destructive sysadmin actions, user/role/permission changes, config changes) — a second explicit confirmation after the render, never in the same turn | **Live** (token + user code) + Prompt-only (the "ask again" step) | `fixed_assets.py`: `fixed_assets.depreciation_run`/`scrap`/`restore`/`sell` · `system_admin.py`: `system_admin.create_user`/`set_user_roles`/`disable_user`/`delete`/`create_webhook`/`toggle_workflow`/`permission_*` |
| Concurrency check on every submit/cancel/delete (refused if the record changed since render) | **Live** | `core/operations.py`: `check_not_modified_since_render()` |
| Save-draft-then-review-then-submit for every docstatus-bearing document, no exceptions for urgency/batch size/confidence | Prompt-only | `SOUL.md`, `profile.md` |
| Offer Letter and Employee Onboarding: advisory-only, never auto-committed, no exceptions | Prompt-only | `profile.md`, `hr_payroll` domain docs |
| One `execute_write.py` entry point for every actual write (domain-scoped or advisory-gated) — `core/client.py` has no write subcommand (removed 2026-10-06) | **Live** | `execute_write.py` |
| Loud (non-blocking) warning if a write is about to fire missing `session_id`/`channel_metadata`/`latest_prompt` | **Live** | `execute_write.py`: `_preflight_context_check()` |
| No auth fallbacks (session-cookie/password) that would bypass token auth or drop audit attribution | Prompt-only | `SOUL.md`, `profile.md` |

## 5. Audit trail / GRC

| Feature | Status | Where |
|---|---|---|
| **Unconditional read audit logging** on every `query_resource()`/`get_resource()`/`run_query_report()` call, every environment — no debug flag to gate it | **Live** | `core/client.py`: `_log_read()` |
| **Two-phase write audit logging** — an `Attempted` row inserted before the call, flipped to `Success`/`Failure` after | **Live** | `core/client.py`: `record_audit_log_start()`, `record_audit_log_finish()` |
| Field-by-field diff computed and stored for Update actions | **Live** | `core/client.py`: `_diff_fields()` |
| RBAC gate-decision logging (a denial gets its own audit row, not just the eventual write) | **Live** | `core/client.py`: `_log_gate_decision()` |
| Audit-log doctype + role provisioning tool (`Qkeee Bot Audit Log`, `Qkeee Bot` role) — admin-invoked, one-time per environment | **Live** | `init_bot.py`, `doctype_defs.py` |
| Best-effort audit writes that never block a real user request even if bot-init hasn't run on the target yet (swallows `ConnectorError` internally) | **Live** | `core/client.py` (`_audit_insert`/`_audit_update`/`_audit_submit`) |
| `_audit_log_status` surfaced on every write result (`ok`/`exempt`/`insert_failed`/`update_failed`) so a caller can detect a best-effort failure rather than assume success | **Live** | `core/client.py`; consumption discipline in `00-conventions.md` |
| Cross-cutting "GRC/statutory-audit" conversational framing — pulls session/requester/action/before-after/diff/approval data into an audit narrative on request | Prompt-only | `references/domains/grc-audit.md` |
| Attribution comment auto-posted on records touched by higher-risk domain actions (fixed-assets whitelisted methods, system-admin destructive actions) | **Live** | `fixed_assets.py`, `system_admin.py`: `_record_attribution_comment()` |
| Best-effort Comment posting helper (generic) | **Live** | `core/client.py`: `record_comment()` |

## 6. Spec-driven execution & task tracking

| Feature | Status | Where |
|---|---|---|
| Written spec required before any non-trivial action (any write, multi-step investigation, cross-domain work, anything framed as a project) | Prompt-only (procedure) | `03-spec-driven-execution.md` |
| Spec skippable for a single read-only lookup answerable in 1-2 calls | Prompt-only | `03-spec-driven-execution.md` |
| Kanban-board hand-off for work that crosses sessions/agents or needs mid-task human input, instead of a flat spec file | Prompt-only | `03-spec-driven-execution.md` |
| Fixed file-naming convention for specs/questionnaires, resolved against the real working directory | Prompt-only | `00-conventions.md` |

## 7. Environment learning / durable memory

| Feature | Status | Where |
|---|---|---|
| Per-environment "learned" satellite skill (`qkeee-erp-learned/<env-tag>`) distinct from the shipped, externally-owned skill | **Live** (formatting/plan) | `memory_promote.py` |
| Auto-formats `SKILL.md`, `environment.md`, `doctypes-catalog.md`, `custom-apps/<slug>.md`, `non-erpnext/<slug>.md` for the learned skill | **Live** | `memory_promote.py`: `format_learned_skill_md()`, `format_environment_md()`, `format_doctypes_catalog_md()`, `format_custom_app_md()`, `format_non_erpnext_md()` |
| Builds the ordered `skill_manage`/`memory` tool-call plan for the calling agent to execute (this module never calls those Hermes tools itself) | **Live** | `memory_promote.py`: `build_promotion_plan()` |
| One-line `MEMORY.md` breadcrumb per known environment | **Live** (formatter) | `memory_promote.py`: `format_memory_breadcrumb()` |
| Skill-name sanitization to satisfy Hermes' `skill_manage` naming rules | **Live** | `memory_promote.py`: `sanitize_env_tag_for_skill_name()` |
| Check-before-create discipline: search this skill's own reference tree, then the env-specific learned skill, before ever creating a genuinely new skill | Prompt-only | `00-conventions.md` |
| Live metadata always wins over stale durable memory (re-run assessment on drift) | Prompt-only | `02-environment-assessment.md` |

## 8. Skill governance / self-protection

| Feature | Status | Where |
|---|---|---|
| Shipped skill marked externally-owned (`skills.external_dirs`) so Hermes' autonomous background-review/curator pass can't silently rewrite its audit/RBAC/redaction logic | **Live** (config) | `config.yaml`, `references/governance.md` |
| Foreground, user-directed edits to the shipped skill remain possible — only the *autonomous* pass is blocked | Prompt-only | `references/governance.md` |
| All local skill writes staged for human approval (`skills.write_approval`) rather than committed unreviewed | **Live** (config) | `config.yaml` |
| Doc-claim vs. actual-config verification discipline — don't repeat a doc's claim about config without checking `config.yaml` itself (two confirmed historical drift cases documented) | Prompt-only | `references/governance.md` |
| Fixed, closed set of domain slugs (a 12th domain requires a deliberate edit, not an ad hoc file) | Prompt-only (convention) | `00-conventions.md` |

## 9. Report-back discipline

| Feature | Status | Where |
|---|---|---|
| First line of every reply states outcome + scope, no restating the request | Prompt-only | `00-conventions.md` |
| Touched records rendered as a table (doctype/name/action/docstatus), never prose | Prompt-only | `00-conventions.md` |
| Warnings (`_audit_log_status` not ok/exempt, a waived KYC step, a `local-*` fallback session, truncated results, an unconfirmed mapping) surfaced *before* narrative detail | Prompt-only | `00-conventions.md` |
| Verified vs. assumed information never share a sentence — labeled distinctly every time | Prompt-only | `00-conventions.md` |
| Any remediation claim is either live-confirmed against schema or explicitly labeled a guess | Prompt-only | `00-conventions.md` |

## 10. Domain-specific business logic

| Domain | Write path | Notable features |
|---|---|---|
| **HR/Payroll** | Employee, Employee Onboarding, Employee Separation, Job Offer, Leave Application | Offer Letter/Onboarding advisory-only; submit/cancel token-gated |
| **Accounts** | Journal Entry, Payment Entry, Purchase Invoice, Sales Invoice | Submit/cancel token-gated |
| **Inventory** | Stock Entry, Material Request, Stock Reconciliation | Stock-reconciliation item helpers, Bin-quantity lookups (`get_stock_reconciliation_items()`, `bin_rows_to_actual_source_qty()`, `get_bin_qty()`); submit/cancel token-gated |
| **Procurement** | Supplier, Address, Contact, Purchase Order, RFQ, Supplier Quotation | Linked Supplier-KYC sub-payload creation (`_create_linked_kyc_record()`); submit/cancel token-gated |
| **Sales** | Customer, Quotation, Sales Order, Delivery Note | Submit/cancel token-gated |
| **Fixed Assets** | Asset, Asset Movement, Asset Repair | Depreciation-run and disposal double-confirm tokens; concurrency-safe mutate wrapper; whitelisted-RPC dispatch for `make_depreciation_entry`/`scrap_asset`/`restore_asset`/`make_sales_invoice` |
| **System Admin** | User, Role, Custom Field, Property Setter, Webhook, Workflow | Permission-manager calls, destructive-action gate, elevated-user creation, config-change gate, scheduler-status check, get_roles_and_doctypes/get_permissions |
| **MIS** | *(none — empty allowlist by design)* | Structurally read-only; operation `mis.generic` always raises `DoctypeNotAllowedError` |
| **Manufacturing** | *(none yet — no code)* | **Planned**: BOM/Work Order/Job Card domain, documented as an unvalidated hypothesis pending a real-instance confirmation pass |
| **Doc Extraction** | *(none — structurally no connector at all)* | Turns attached PDFs/DOCX/XLSX/images or a shared URL into a staged, human-reviewable field report for a target doctype; never writes directly, not even gated by `qkeee_erp.mode`; low-confidence/illegible fields explicitly flagged rather than guessed; URL fetches never bypass a login/paywall |
| **GRC-Audit** | *(cross-cutting, no doctypes of its own)* | Statutory-audit-trail conversational framing layered on top of another domain's work |

## 11. Non-ERPNext systems adapter

| Feature | Status | Where |
|---|---|---|
| Procedure for tasks touching a genuinely non-ERPNext system (Tally, a payment gateway dashboard, an internal tool) that still falls inside org-work scope | Prompt-only | `non-erpnext-adapter.md` |
| Never guesses a non-ERPNext system's shape — requires the system's own docs/guide/URL before acting | Prompt-only | `non-erpnext-adapter.md` |
| Same credential-handling discipline as ERPNext (never typed into chat, never written to agent memory) extended to a second system | Prompt-only | `non-erpnext-adapter.md` |
| Learnings cataloged the same way as a custom Frappe app, under `qkeee-erp-learned/<env-tag>/references/non-erpnext/<slug>.md` | **Live** (formatter exists) | `memory_promote.py`: `format_non_erpnext_md()` |

## 12. Admin/bootstrap tooling

| Feature | Status | Where |
|---|---|---|
| One-time, admin-invoked provisioning of the `Qkeee Bot` role + `Qkeee Bot Audit Log` doctype on a target instance | **Live** | `init_bot.py` |
| Dry-run mode (existence-checks only, no writes) before the real run | **Live** | `init_bot.py`: `run_dry_run()`, `compute_plan()` |
| Confirmation-token-gated real run | **Live** | `init_bot.py`: `_init_plan_token()`, `run_real()` |
| `qkeee-erp.env` skeleton file creation (header only, no secrets) | **Live** | `init_bot.py`: `ensure_qkeee_env_file_skeleton()` |
| Manual audit-log entries for the provisioning step itself | **Live** | `init_bot.py`: `log_role_provisioning()` |

---

## Coverage note

Categories 1–2 and parts of 6–9 (spec discipline, report-back format,
scope guardrail) are **prompt-only** — real, documented requirements, but
enforced by the model following instructions rather than by code raising
an exception. That's exactly the gap the behavioral eval suite
(`erpmate-behavioral-evals/`) tests. Categories 3–5, 7, and most of 10–12
are **code-enforced** and already covered by the repo's own pytest suite
(`scripts/**/test_*.py`) — this document doesn't re-litigate that; see
`README.md`'s directory-layout table for the file/test mapping.
