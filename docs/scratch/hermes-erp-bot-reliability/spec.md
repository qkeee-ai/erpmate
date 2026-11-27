# Hermes ERP bot reliability — findings & improvement plan

Status: resolved (issues 01–03 in erpnext-hermes `develop`: 7597704, 9ec68e9; verified 2026-10-08)
Source: `erpnext-hermes/logs/dev-hermes-11-Sep.log` (dev-hermes, Google Chat, 2026-09-11)
Task reviewed: read invoice PDF → create Supplier + Item on `DEMO_ERP` (demo.qkeee.in) via `qkeee-erp-associate` skill
Repo: `erpnext-hermes` (skill at `skills/qkeee-erp/qkeee-erp-associate/`)

No action taken yet — this is the findings record. Action/implementation decisions come later, per finding, via this repo's normal issue-tracker flow (`docs/agents/issue-tracker.md`).

## Original trigger (user-reported)

a. Bot Audit Log entries don't consistently capture channel id / user prompt.
b. Supplier created from PDF was missing address and tax ID (GSTIN).
c. Item created was missing HSN code, despite it being present on the source PDF.

## How to read this doc

Each finding: **Severity**, **Root cause** (code/doc reference, not speculation), **Evidence** (log line / file:line), **Proposed fix** (not yet actioned). Severity is about blast radius on future runs, not how bad this one run turned out.

**Status line note (2026-09-13):** every finding below carries `Status: ready-for-human`. Per this repo's `triage-labels.md`, that label canonically means "requires human implementation" — but every finding's own Update paragraph shows the code/docs/tests were already written, tested, and (as of `e838ed8`) committed and pushed to `origin/develop`. In this doc `ready-for-human` is being used narrower than its canonical meaning: **implementation is done; what's outstanding is human review/approval**, not human implementation work. There's no cleaner slot for that state in the current 5-label vocabulary — flagging the usage explicitly here rather than silently minting a sixth label. Don't read a `ready-for-human` line below as "nobody has started this."

---

## F1 — Critical — Audit rows lose channel/session/prompt context by construction

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): reusable write CLI (`execute_write.py`, takes payload/context as flags instead of a freshly hand-written script per call) **+** an explicit instruction added to `00-conventions.md`/domain docs mandating session_id/channel_metadata/latest_prompt be resolved and passed on every call. Both, not either/or.

**Update 2026-09-11 (implemented):** `skills/qkeee-erp/qkeee-erp-associate/scripts/execute_write.py` + `test_execute_write.py` added. Imports every `domains/*.py` module up front (fixes a second, previously-undocumented bug found while building this: `core/client.py`'s own bare `mutate --domain <slug>` CLI 404s standalone in a fresh process, since no domain module gets imported to run `register_domain_allowlist()` — `--domain procurement` errors "no registered ALLOWED_WRITE_DOCTYPES" even though `procurement.py` genuinely declares one). Routes to `mutate_resource(domain=...)` when `--domain` given, `gated_mutate_resource(...)` otherwise. WARNs loudly on stderr (not silent, not blocking — matches the chosen course, not the code-enforce option) when `--session-id`/`--channel-metadata`/`--latest-prompt` are omitted, and surfaces a second warning whenever `_audit_log_status` comes back anything other than `"ok"`/`"exempt"`. Docs updated: `01-connectivity.md`'s CLI-usage section now points every write at `execute_write.py` (with worked examples for both the domain-scoped and domain-less shapes) instead of describing `mutate`/`gated-mutate` as directly usable; `00-conventions.md`'s GRC baseline session_id bullet broadened to cover `channel_metadata`/`latest_prompt` and references `execute_write.py`. `python -m pytest scripts` — 116 passed, 28 subtests, no regressions. CLI sanity-checked: missing-context warnings fire correctly, missing-token and bad-`--domain` both refuse cleanly (argparse error, no traceback). Landed in commit `cc72045`.

**Maps to trigger (a).**

**Root cause:** `core/client.py`'s `mutate_resource()` accepts `session_id`, `channel_metadata`, `latest_prompt` specifically to carry this into `Qkeee Bot Audit Log`. Nothing forces a caller to populate them. The agent hand-writes a fresh Python script per write (via `write_file` + `terminal`) instead of calling one shared entry point, and both write scripts this session hardcode `SID = ""`, never build `channel_metadata`, and never pass `latest_prompt` (only a paraphrased `prompt_summary`) — despite the real Google Chat thread id (`spaces/AAQAx8K3UZ8/threads/HzGEqyIl3Mk`) being present in the session's own system context throughout. An empty `session_id` silently falls back to a synthetic `local-<timestamp>` via `_session_or_fallback()` (`core/client.py`) — the audit row still inserts and reports `"ok"`.

**Evidence:** `workspace/qkeee-erp/DEMO_ERP/update_supplier.py` and `create_item.py` (both written mid-session, since deleted) — `SID = ""`, no `channel_metadata` key, no `latest_prompt` kwarg, in both.

**Proposed fix (undecided):** Resolve session_id + channel_metadata + verbatim latest_prompt once per task, thread through one reusable write entry point instead of a freshly generated script per write. Treat a `local-*` fallback session as its own surfaced warning, same tier as `_audit_log_status != "ok"`.

---

## F2 — Critical — Supplier KYC-completeness rule was overridden mid-task; GSTIN retried against the wrong doctype

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): code-enforce (procurement.py gate: refuse Supplier create/update unless a linked Address with `gstin`/`tax_id` exists, or an explicit `kyc_waiver_confirmed=True` is passed) **+** fix procurement.md's field-mapping error and document the Supplier → Address → Contact write order.

**Update 2026-09-11 (implemented):** `procurement.py`: added `Address`/`Contact` to `ALLOWED_WRITE_DOCTYPES`; `mutate()` now raises `IncompleteSupplierKYCError` on a Supplier `create` carrying neither `kyc={"address": {...}}` nor `kyc_waiver_confirmed=True` — refuses before any write fires. When `kyc` is given, the Supplier create is followed by a linked Address (and Contact, if given) create in the same call, Dynamic Link (`links`) wired automatically. Gate scoped to `create` only, not `update` (KYC completeness is an onboarding-time bar, not a block on later patches — Address is independently allowlisted for backfilling).

Also fixed while implementing: `execute_write.py` (F1) originally called `core_client.mutate_resource(domain=...)` directly, which would have **bypassed this new gate entirely** — refactored to dispatch through each domain module's own `mutate()` instead (`_DOMAIN_MODULES[args.domain].mutate(...)`), so a domain-specific rule like this one can't be routed around regardless of caller. Added `--kyc`/`--kyc-waiver-confirmed` flags to `execute_write.py`, validated as procurement/Supplier/create-only (clean argparse error otherwise).

Docs: `procurement.md` — corrected the GSTIN-belongs-on-Supplier error, documented the Supplier → Address (→ Contact) write order and the `kyc`/`kyc_waiver_confirmed` call shape, updated `ALLOWED_WRITE_DOCTYPES` listing and the Quick Reference row. `01-connectivity.md`'s F1 worked example updated to include `--kyc` (it would otherwise now hit this gate).

Tests: `domains/test_procurement.py` added (9 tests — refusal without kyc/waiver, waiver path, kyc→linked-Address(+Contact) creation, existing `links` on a kyc payload preserved not overwritten, update not gated, Address/Contact writable standalone). `domains/test_allowlist_gates.py`'s shared cross-domain test updated to probe procurement via "Purchase Order" instead of `ALLOWED_WRITE_DOCTYPES[0]` ("Supplier"), since that test is deliberately scoped to the allowlist gate in isolation, not this domain-specific one. `test_execute_write.py` updated for the domain-module-dispatch refactor + new kyc-flag tests. `python -m pytest scripts` — 127 passed, 28 subtests, no regressions. Landed in the same `cc72045` commit as F1.

**Maps to trigger (b).**

**Root cause:** `references/domains/procurement.md`'s own non-negotiable: *"Never create a live Supplier record with incomplete mandatory KYC/bank fields... tax ID, bank/payable details... must be enforced before a draft is marked 'ready.' Incomplete extractions must be flagged, never silently filled with a placeholder."* The agent's own task spec quotes this domain, then two paragraphs later marks Address/Bank Account/Contact "out of scope" and proceeds without asking the user to confirm the narrower scope. Separately, the GSTIN retry (`update_supplier.py`) targeted a `gstin` field on **Supplier** — ERPNext's India-Compliance GSTIN field lives on **Address** (linked via Dynamic Link), not Supplier, so the retry could never have worked regardless of the instance's customization. The bot's closing message ("it'd need a custom field on Supplier") is therefore inaccurate.

**Evidence:** `references/domains/procurement.md` non-negotiables section; task spec `qkeee-erp-specs/supplier-item-from-invoice-20260911-1300.md`, "Outcome" section: *"No Address/Contact/Bank Account doctypes created (separate doctypes, out of requested scope)."*

**Proposed fix (undecided):** Make the KYC-completeness rule code-enforced in `procurement.py`/`mutate_resource()` (like the 8 non-negotiables already are in `00-conventions.md`), not prompt-only. Fix the field-mapping error in `procurement.md`: document the write order Supplier → Address (with `gstin`/`tax_id`, Dynamic Link) → Contact.

---

## F3 — Critical — Item's HSN/tax fields have no documented home

Status: ready-for-human (superseded — see below; issue 01 implemented, committed 7597704 on develop, pushed to origin — not yet reviewed)
Decided course (2026-09-11): not fixed by a narrower Item-only patch. Generalized into a cross-cutting design task — see `.scratch/hermes-erp-bot-reliability/issues/01-schema-first-attribute-mapping.md`: fetch live doctype schema (via F8's `discover.py meta`) and map extracted/provided fields against it for every doctype, on every write, rather than maintaining per-doctype curated field lists in domain docs.

**Update 2026-09-11 (implemented):** issue 01 landed — `schema_mapping.py` (new), wired into `execute_write.py` for every create/update. See issue 01's own Comments for the full design/implementation writeup. F3 closed by this rather than a narrower Item/HSN patch, as decided.

**Maps to trigger (c).**

**Root cause:** `Item` is not in any domain's `ALLOWED_WRITE_DOCTYPES` (checked `procurement.py` and `inventory.py`) — it's created via `gated_mutate_resource()`, the domain-less advisory-token path for doctypes with no design-time capability review. No `references/domains/*.md` file enumerates Item's India-compliance fields the way `procurement.md` enumerates Supplier's KYC bar. The agent had already confirmed, one write earlier, that an unrecognized field is silently dropped rather than rejected (`gstin` on Supplier) — same would apply to `gst_hsn_code` on Item — but never attempted it. HSN 84713090 was instead embedded in the free-text `description` field, unfilterable/unreportable.

**Evidence:** `create_item.py` payload — `description` contains `"HSN/SAC 84713090"`, no `gst_hsn_code` key attempted. `procurement.py`/`inventory.py` `ALLOWED_WRITE_DOCTYPES` — Item absent from both.

**Proposed fix (undecided):** Give Item a real domain owner (procurement or inventory) with a documented India-compliance field list (`gst_hsn_code`, `is_nil_exempt`, `is_non_gst`, `taxes` child table). Until then, doc-extraction's procedure should say: for any doctype not yet owned by a domain, attempt every extracted field as its own key rather than folding it into free text — a dropped unknown key costs nothing, a field left in text costs the record's searchability.

---

## F4 — High — doc-extraction's mandatory staged report (confidence ratings + reconciliation_check) never ran

Status: ready-for-human (folded — see below; issue 01 implemented, committed 7597704 on develop, pushed to origin — not yet reviewed)
Decided course (2026-09-11): not a separate fix. Folded into `.scratch/hermes-erp-bot-reliability/issues/01-schema-first-attribute-mapping.md` — the design item now owns sequencing the staged-report/confidence step (extraction side) together with schema-mapping (write side) as one pipeline, rather than treating them as independent gates.

**Update 2026-09-11 (implemented):** `schema_mapping.match_staged_report()` code-enforces doc-extraction.md step 6's confidence/value-key requirement for the first time (refuses malformed entries) and flags a low-confidence+schema-unmatched field as `high_risk`, hard-refused by `execute_write.py` before any write. Known remaining gap, documented not fixed: no `render_*.py` staged-report renderer exists anywhere in the tree yet — see issue 01's Comments.

**Root cause:** `references/domains/doc-extraction.md` step 6: *"Render the staged report through a script... Refuse to render if any field is missing a confidence rating or a value key... record [the reconciliation self-check] as its own field (`reconciliation_check`)."* This run went straight from OCR to a free-text task spec — no per-field confidence tags on any of the ~15 extracted fields, no rendered report, no `reconciliation_check` field. The tax-total reconciliation only appears informally in the closing chat message.

**Evidence:** No `render_*.py` staged-report call anywhere in the session between the OCR read and the task-spec write; doc-extraction.md step 6 vs. actual sequence.

**Proposed fix (undecided):** Treat the staged-report render as a hard prerequisite before procurement/inventory consumes extraction output, not an easy-to-skip step when the OCR "looks clean."

---

## F5 — High — the Item write's confirmation token is self-computed and self-verified in the same turn

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): require a short code derived from the advisory token to appear in the user's own confirmation reply before `gated_mutate_resource()` proceeds — ties execution to an actual human having seen the rendered draft, instead of proving only payload-integrity.

**Update 2026-09-11 (implemented):** `confirm_token.py` — added `confirmation_code(token)` (6 uppercase hex chars derived from the token); its CLI now prints it alongside `confirmation_token`/`issued_at`. Docstring is deliberately honest about the limit: the code itself is not cryptographically secret (derivable from a token the agent already holds) — what it adds is a concrete "must round-trip through an actual reply from the user" discipline point, the same trust model this skill already leans on for `requested_by` (channel-sourced, never reconstructed), not a defense against a fully adversarial agent.

`core/client.py` — new `UnconfirmedByUserError`; `gated_mutate_resource()` takes a new required `user_confirmation_text` param, checked (case-insensitively) for the token's `confirmation_code` after the existing token/freshness/payload checks, before any write fires. `gated-mutate` CLI subcommand gained `--user-confirmation-text` (required).

`execute_write.py` — `--user-confirmation-text` added, required (clean argparse error, not a traceback) whenever `--domain` is omitted; forwarded to `gated_mutate_resource()`.

Scoped to `gated_mutate_resource()` only (the domain-less path, Item today), not `register_domain_token_gate()`'s submit/cancel/delete gate on named domains — matches the decided course's wording and F5's own finding (which was specifically about Item's self-computed-and-self-verified token).

Docs: `00-conventions.md`'s "Double confirm" GRC bullet gained a paragraph on this; `01-connectivity.md`'s F3/issue-01 worked example updated to show `--user-confirmation-text`.

Tests: `core/test_confirm_token.py` added (3 tests). `core/test_client.py` — 2 pre-existing `gated_mutate_resource()` success-path tests updated to supply `user_confirmation_text` (would otherwise now fail on the new gate), 4 new tests added (missing text refused, wrong/foreign code refused, case-insensitive match accepted, mode gate still applies alongside the new one). `test_execute_write.py` updated similarly. `python -m pytest scripts` — 135 passed, 28 subtests, no regressions. CLI sanity-checked: `confirm_token.py` prints the code, `execute_write.py` refuses cleanly without `--user-confirmation-text`. Landed in the same `cc72045` commit as F1/F2.

**Root cause:** `advisory_write_token()` (`core/confirm_token.py`) is a plain SHA-256 over the payload — no shared secret, nothing that requires a genuine render-shown-to-human step to produce. This session, the same agent process called `confirm_token.py` to compute the token, then immediately called `gated_mutate_resource()` with it. `00-conventions.md` already documents the limit ("does **not** prove a human read and approved it") — F2/F3 show what that gap costs in practice, and it's the *only* reviewed control ahead of Item (ownerless doctype, F3) on a bot already flagged RBAC-unreliable (F7).

**Evidence:** `core/confirm_token.py` `advisory_write_token()` implementation; `create_item.py` computes and consumes the token within the same script sequence.

**Proposed fix (undecided):** For domain-less writes, require the token (or a short derived code) to appear in the user's own reply rather than being computed and consumed by the same process. Cheaper interim: bring Item under a real domain (F3) so it no longer depends on this weaker gate.

---

## F6 — High — purchase cost auto-saved as this org's selling price

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): fix in code — for a purchase-document-sourced item, don't pass `standard_rate` bare on create; set the rate explicitly against Standard Buying (or a Supplier Item / buying-side Item Price) instead.

**Update 2026-09-11 (implemented):** `item_write_helpers.py` (new) — `apply_purchase_sourced_item_defaults()` refuses a bare `standard_rate` key outright (`BareStandardRateOnPurchaseSourcedItemError`), pointing the caller at `build_standard_buying_item_price_payload()` (also new) for the buying-side alternative as a separate, explicitly confirmed write — deliberately not auto-chained onto the Item create (matches this skill's save-draft-then-review-then-submit spirit; an auto-chained second write without its own confirmation would repeat a version of the original bug). Wired into `execute_write.py` via `--purchase-sourced-item` (Item/create-only, clean argparse error otherwise). Tests: `test_item_write_helpers.py` new (9 tests), `test_execute_write.py` extended (4 tests). `python -m pytest scripts` — 148 passed, 28 subtests, no regressions. CLI sanity-checked. Landed in the same `cc72045` commit as F1/F2/F5. Item still has no owning domain (F3/issue 01 unchanged) — this is a standalone helper, cross-referenced from issue 01 for whenever that design lands.

**Root cause:** Setting `standard_rate` on Item create auto-creates an Item Price against **Standard Selling** by default in ERPNext. Verified re-fetch: `price_list: "Standard Selling", selling: 1, buying: 0`. ₹40,677.98 is what the org paid a supplier, not a selling price — nothing in the source invoice supports a selling price. No Standard Buying-side Item Price or Supplier Item link was created.

**Evidence:** `create_item.py` payload includes bare `standard_rate`; verified Item Price query result in log — `price_list_rate: 40677.98, selling: 1, buying: 0`.

**Proposed fix (undecided):** For a purchase-sourced item, don't pass `standard_rate` bare — create without it and add the rate explicitly against Standard Buying, or follow up with a Supplier Item / buying-side Item Price entry.

---

## F7 — Medium — every write this session proceeded on a WARN, not a real per-requester RBAC check

Status: ready-for-human (committed 2c62e1e on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): deferred, then articulated same day. User's alternate approach, in place of `init_bot.py` narrow-role provisioning: **a)** bot resolves `requested_by` from the channel; **b)** confirms that user exists in the ERP instance; **c)** fetches `requested_by`'s own role/permission info; **d)** logs the audit entry naming `requested_by`; **e)** the bot writes using its OWN login, gated on whether `requested_by`'s role/permission actually supports the doctype/action; **f)** a Comment on the record names `requested_by`. (a)/(b)/(d)/(f) already existed — the gap was (c)/(e): the only way this codebase had to check "does `requested_by` actually hold this permission" was `frappe.client.has_permission?user=requested_by`, and F7's own root cause is that this RPC doesn't discriminate under a privileged bot identity.

**Impersonation researched and set aside first:** before landing on the above, considered having the bot impersonate `requested_by` for the actual mutation (Frappe's real `frappe.core.doctype.user.user.impersonate` RPC, added in ERPNext v15 core). Validated live against Frappe's own source (`frappe/auth.py`'s `LoginManager.impersonate()`): it requires an existing persisted, cookie-based login session (`frappe.local.session_obj`) to swap — a token-authenticated request (`Authorization: token key:secret`, this connector's entire auth model) never creates that session object, so it cannot be called meaningfully under the current architecture. Using it for real would require provisioning the bot with an actual usable password (tension with an API-key-only bot with no login password), adding cookie-jar session handling, and accepting that every impersonation writes a Notification Log entry and emails the impersonated user ("X just impersonated as you...") — likely too noisy at per-write frequency. Set aside as a real architecture change, not a small patch; may revisit later.

**Update 2026-09-11 (implemented):** `core/client.py` — new `_requester_has_role_permission()` (+ `_fetch_doctype_role_permissions()`, in-process cached per (tag, doctype)): computes, purely from two plain doc reads — `get_user_roles(tag, requested_by)` for the requester's own live role list, and a direct `get_resource(tag, "DocType", doctype)` read for that doctype's own live DocPerm rows (permlevel 0 only) — whether any role the requester holds actually grants the needed `perm_type`, entirely independent of the broken `has_permission` RPC. Wired into `_validate_prod_requester()`'s existing "RBAC pre-check unreliable" branch only (a reliable `has_permission` is still the authoritative, fully-Frappe-native answer — this doesn't replace it, it backstops the case where it's already known broken):

- `False` (positive evidence of no permission) refuses the call outright — overrides even a domain allowlist or a verified advisory-draft token, since those exist to cover "can't verify," not "verified, and it's a no." Applies to reads too, not just writes.
- `True` lets an otherwise-unscoped write through on its own — the strongest evidence available while the RPC can't be trusted.
- `None` (either read failed — e.g. this bot also lacks System-Manager-level DocType read, F8's own acknowledged gap on a correctly least-privileged bot; or `get_user_roles()`'s own "empty roles is ambiguous" case) falls back to the pre-existing domain-allowlist/advisory-token-or-refuse behavior, completely unchanged from before this fix.

Deliberately NOT a reimplementation of Frappe's permission engine — disclosed limitations in the function's own docstring: User Permissions (per-record Link-based restriction) are invisible to it, and `if_owner`-scoped DocPerm rows are never counted toward a `True` verdict (ownership can't be cheaply verified pre-fetch, and is meaningless for `create`).

Docs: `00-conventions.md`'s RBAC pre-check GRC bullet now describes this second, independent check and its fallback semantics.

Tests: `core/test_client.py` — `RequesterRoleFallbackTests` (9 new: permlevel-0 filtering, cached fetch failure, matching/non-matching role, wrong-perm-type, if_owner-only never counts as True, requester-role-fetch failure and empty-roles both inconclusive, doctype-permission-fetch failure inconclusive) and `RequesterRoleFallbackWiringTests` (7 new: `False` overrides domain-scoped/advisory-token-verified/read cases, `True` lets an unscoped write through, `None` falls back to prior behavior for both the refuse and the domain-scoped-proceeds cases). 3 pre-existing tests updated to mock the new function (avoids an unmocked `get_user_roles()` call hitting real network against the fake `https://example.com` two of them configure). `python -m pytest scripts` — 191 passed, 28 subtests, no regressions.

Committed `2c62e1e` on `develop`, pushed to `origin` — pending human review.

**Live-validated 2026-09-11 against demo.qkeee.in** (real bot creds, real requester `nikhil.sharma@qkeee.in`, a genuine narrow-role System User): `_requester_has_role_permission()` verdicts matched real role assignments exactly across every doctype tried (Purchase Order/Employee/Sales Order/Item/Supplier/Company), and the negative-evidence override was confirmed to fire even through the previously-unconditional advisory-token-verified fallback (`Employee`/`write` correctly refused with the new message despite `advisory_token_verified=True`). Full write round-trips (create/update/delete-refused-cleanup-via-disable) exercised end-to-end via `execute_write.py`, including `item_write_helpers`'s F6/F9 flag and `procurement.py`'s KYC-waiver gate. See `qkeee-erp-demo-instance` memory for the full live-testing writeup.

**Follow-up tightening decision (2026-09-11, post-live-validation):** an inconclusive local role verdict (`None` — the local check itself couldn't complete, e.g. the bot also can't read DocType metadata) now refuses outright too, same as a confirmed `False` — it no longer falls back to letting a domain-allowlisted or advisory-token-verified write through on a warning. Explicit trade-off: this makes System-Manager-level DocType read a hard requirement for ANY write once `has_permission` is unreliable, including a domain-scoped one that used to proceed on the allowlist alone — availability loss, in exchange for never proceeding without positive, locally-confirmed evidence. `PrivilegedBotAccountError` (previously raised in the "neither domain nor token" case) is no longer raised anywhere — both the `False` and `None` cases now raise `UnvalidatedProdRequesterError` uniformly, applying equally to reads and writes. `core/client.py`'s module docstring, `_validate_prod_requester()`'s own docstring, and `00-conventions.md`'s RBAC bullet all updated accordingly. Tests: 10 existing tests updated to match the new outcome (several renamed to reflect what they now demonstrate), 2 new (the True-verdict success companion for `gated_mutate_resource()`, the True-verdict success companion at the `mutate_resource()` level). `python -m pytest scripts` — 195 passed, 28 subtests, no regressions.

**Real bug found + fixed during this live pass (unrelated to F7 itself):** `record_comment()` was silently failing on every write on this instance — Frappe 16's `frappe.desk.form.utils.add_comment` now requires `comment_email`/`comment_by` args this connector never sent (worked fine on the Frappe 15 instance this was built against). Fixed in `core/client.py`, confirmed live via a real Comment round-trip, regression tests added (`RecordCommentEmailAndByFieldsTests`, 2 new). `python -m pytest scripts` — 193 passed, 28 subtests, no regressions. Landed in the same `2c62e1e` commit as the rest of F7.

---

## F8 — Medium — schema discovery is broken, and the 403 was misdiagnosed (see chat discussion above) — RESURRECTED, needs review

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)

**Root cause, two distinct bugs, neither an RBAC/permission gap on the bot's real access:**

1. **`discover.py` referenced in `01-connectivity.md`/`04-erp-doc-lookup.md` does not exist in the shipped skill.** Confirmed live in this session: `find /home/nikhildx -name "discover.py"` returns nothing. Confirmed by repo listing: `scripts/` has no `discover.py`.
2. **The improvised fallback queried the wrong REST resource.** Agent ran `client.py query DocField --filters '[["parent","=","Supplier"]]'` — treating `DocField` (a child-table / `istable=1` doctype) as a standalone list resource. Frappe does not grant standalone List permission on table-only doctypes to any role, System Manager included — they're readable only embedded inside their parent document. That's what returned 403, not a role gap. The doc-correct call, per `01-connectivity.md` itself (`discover.py meta` = `GET /api/resource/DocType/<name>`) and per `get_resource()`'s own docstring ("the only way to get child-table rows... single-resource GET... always returns the full doc"), is `client.py get DocType Supplier` — never attempted this session.
3. **Agent misdiagnosed its own result.** It called the 403 "expected on a least-privileged bot" one line below a WARN in the same tool output stating the bot **is privileged**. Never retried with the correct endpoint; moved on to master-list queries (Supplier Group, Item Group, UOM, Company) instead.

**Evidence:** log — `find ... discover.py` (empty result); `ERROR: ERPNext API error (403) on GET /api/resource/DocField`; WARN immediately above it stating `privileged (True)`; assistant text: *"DocField metadata read is permission-blocked (403) — expected on a least-privileged bot."*

**Proposed fix (undecided):** Ship `discover.py` (or remove the dangling references to it and document `get DocType <name>` directly as the schema-check call). Never point schema discovery at `query DocField`. Add a check: don't let a 403 be diagnosed as "expected, low-privilege" without first checking the RBAC WARN already printed in the same call.

**Update 2026-09-11:** Found and ported the original `discover.py` from `origin/multi-persona-v1.0` (identical across all ten old per-persona `scripts/` copies, differing only in a `--persona-code` default string — confirmed via diff). Resurrected at `skills/qkeee-erp/qkeee-erp-associate/scripts/discover.py`, adapted to the current `core.client` API:

- `meta`/`resolve` now go through `get_resource(tag, "DocType", name, ...)` — single-resource GET, never `query DocField` — closing the exact gap this finding describes.
- Every read routes through `get_resource()`/`query_resource()` (or, for the one RPC-only call, a manual `_validate_prod_requester()` + `_log_read()` pair mirroring `run_query_report()`'s pattern) instead of the old script's bare `_request()` calls, which had bypassed the requester-validation gate entirely — a second bug the old script carried that's fixed by the port, not just preserved.
- `--debug`/`--persona-code` retired in favor of `core/client.py`'s own `session_id`/`domain_code`/`channel`/`channel_metadata`/`prompt_summary`/`latest_prompt` flags, so a discovery read no longer sits outside F1's fix either.
- `test_discover.py` added alongside it (repo convention: one test file per script). `python -m pytest scripts` — 107 passed, 20 subtests passed, no regressions. CLI sanity-checked against a bad tag: clean `ConnectorError`, no traceback.
- Committed `cc72045` on `develop`, pushed to `origin`. `references/01-connectivity.md` / `02-environment-assessment.md` / `04-erp-doc-lookup.md` / `domains/manufacturing.md` already describe the shipped subcommands (`modules`/`apps`/`resolve`/`meta`) accurately — no doc changes made or needed.

---

## F9 — Low — `is_sales_item` set to 1 with nothing in a purchase invoice supporting it

Status: ready-for-human (committed cc72045 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): fix in code — default `is_purchase_item=1, is_sales_item=0` for a purchase-document-sourced item unless the user says otherwise.

**Update 2026-09-11 (implemented):** Same change as F6 — `item_write_helpers.apply_purchase_sourced_item_defaults()` fills `is_stock_item=1, is_purchase_item=1, is_sales_item=0` only where the caller's payload doesn't already set them explicitly (an explicit value always wins). Wired into `execute_write.py` via the same `--purchase-sourced-item` flag as F6 — the two share one implementation since both are corrections to the same Item-create payload. See F6's update note for tests/status.

**Root cause:** `create_item.py` payload sets `is_stock_item`, `is_sales_item`, `is_purchase_item` all to 1 without asking. Source document is a purchase invoice; nothing establishes the org resells this item. Same unstated-assumption pattern as F6.

**Evidence:** `create_item.py` payload.

**Proposed fix (undecided):** Default items sourced from a purchase document to `is_purchase_item=1, is_sales_item=0` unless told otherwise.

---

## F10 — High — agent offered to re-attribute a permission-denied read to a different requested_by

Status: ready-for-human (committed 294b149 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Decided course (2026-09-11): hard-restrict this, both in documentation and in the connector's own refusal messages — never suggest, offer, or accept a substitute `requested_by` as a way around a permission denial.

**Root cause:** a Hermes session resolved a Google Chat sender to a real ERPNext user (`nikhil.sharma@qkeee.in` on DEMO_ERP), attempted an Employee read, and was correctly refused by the F7 role/DocPerm check. Its own response then offered the user two options, one of which was: *"Tell me a different legitimate requester you want the read attributed to on this instance... I'll run it as that — but only with your explicit direction."* That's a violation of `00-conventions.md`'s existing GRC baseline ("never accept a `requested_by` that didn't originate from that channel-provided field") one conversational turn removed — a user typing a different email into chat is not the same as that email being the channel's own authenticated sender field, and "the user explicitly asked for it" doesn't make it so.

**Discrepancy worth noting:** the same transcript claimed `nikhil.sharma@qkeee.in` has an empty role list (`roles: []`) as the reason for the denial. Live-reconfirmed same session: this account actually holds 7 real roles (Accounts Manager/User, Purchase Manager/User, Item Manager, Stock Manager/User) — none HR-related, so the Employee-read refusal itself was still correct, just for a narrower reason ("no HR-covering role," not "no roles at all"). Flagging since the stated root cause didn't match live data when re-checked — don't trust a permission-denial diagnosis without reconfirming the actual role list live, same discipline this whole postmortem already applies everywhere else.

**Decided fix (implemented):** `core/client.py` — new `_NEVER_SUBSTITUTE_REQUESTER` guardrail string, appended to all three requester-permission-denial messages `_validate_prod_requester()` can raise (the reliable-RPC-says-no path, F7's role-verdict-False path, F7's role-verdict-None/inconclusive path) — baked into the exception text itself so the instruction lands at the exact moment an agent decides what to do next, not only in a reference doc it may not re-read mid-incident. `00-conventions.md`'s GRC baseline bullet on channel-sourced identity extended with an explicit statement of this failure mode and the only two correct responses (report the gap, or decline — never reroute).

**Not code-enforceable beyond this:** `resolve_requested_by()` is already a thin passthrough with no config fallback (by design, F1) — the connector has no way to distinguish a genuinely channel-authenticated identity from one a user merely typed into chat and the agent passed through. The actual enforcement is process/prompt discipline at the calling-agent layer; the code-level change here is a maximally-visible reminder at the point of refusal, not a technical barrier.

Tests: `core/test_client.py` — `NeverSubstituteRequesterGuardrailTests` (3 new — the guardrail text appears in all three denial paths). `python -m pytest scripts` — 202 passed, 28 subtests, no regressions.

Committed `294b149` on `develop`, pushed to `origin` — pending human review.

---

## F11 — High — a denied requester-permission check leaves zero trace in Qkeee Bot Audit Log

Status: ready-for-human (committed e838ed8 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Source: `erpnext-hermes/logs/logs-sep12-11-12.log` (dev-erp, Google Chat, 2026-09-12) — Employee-list task that exercised User lookups, role checks, and DocPerm reads with no corresponding Audit Log rows.

**Root cause:** `_validate_prod_requester()` (`core/client.py`) raises `UnvalidatedProdRequesterError` at every denial branch (unknown user, `has_permission`-says-no, local role/DocPerm-says-no, local check inconclusive) BEFORE any read/write it's guarding ever runs — and it never calls `_log_read()`/`record_audit_log_start()` itself. A refused call is invisible in the one place a GRC review would look for it. This is the inverse of what an audit trail is for: the row that matters most (someone was refused) was the one guaranteed never to exist.

**Evidence:** `logs-sep12-11-12.log` messages 23/25/31 — a `roles`/`_fetch_doctype_role_permissions` sequence checking whether `nikhil.sharma@qkeee.in` can read `Employee` produced no Audit Log row regardless of outcome, by construction, not by a channel/session gap (contrast F1).

**Proposed fix (agreed course, 2026-09-12):** New `_log_gate_decision()` next to `_log_read()` — one best-effort Audit Log row per `_validate_prod_requester()` call, covering both the denial branches and the final allow, so a decision (not the raw internal HTTP calls that produced it — `resource_exists`, `has_permission`, the RBAC-reliability probe, the local role/DocPerm fallback) is what lands in the trail. Reuses the existing `action` (Read/Create/Update/Submit/Cancel/Delete, mapped from `perm_type`) and `status` (Success/Failure) vocabulary already in use elsewhere in this doctype rather than inventing new Select option strings that would require a live schema change on every provisioned instance — the specific verdict/path/trust detail goes in the existing free-form `response_payload` JSON field instead. Best-effort throughout: a tag whose environment can't even be resolved (the same failure the gate itself may be reporting) skips logging silently rather than raising, same discipline as every other audit call in this module.

## F12 — High — business-intent reads of User/Role are silently dropped by a doctype-based audit exemption built for a different purpose

Status: ready-for-human (committed e838ed8 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Source: same log as F11.

**Root cause:** `_log_read()` skips logging for any doctype in `AUDIT_EXEMPT_DOCTYPES`, which includes `User`/`DocType`/`Role` — added purely to stop `_validate_prod_requester()`'s own internal `resource_exists(tag, "User", requested_by)` plumbing call from recursively audit-logging itself (see the constant's own comment). That's the right fix for the plumbing call, but the exemption is keyed on **doctype**, not **purpose** — it silently swallows a genuine business-intent read of the same doctype too. A second, narrower gap: `get_user_roles()` never routed through `_log_read()` at all (it hand-rolls its own `_request()` calls), so even a `roles <user>` CLI invocation — clearly business intent, not plumbing — was never logged, doctype exemption or not.

**Evidence:** `logs-sep12-11-12.log` messages 12/19 (`query User --filters [["name","=","nikhil.sharma@qkeee.in"]]`) and 23/25 (`roles nikhil.sharma@qkeee.in` / `roles`) — all four are the user asking "who is this / what can they do," not internal bookkeeping, and none produced an Audit Log row.

**Proposed fix (agreed course, 2026-09-12):** Replace the doctype-keyed exemption with a purpose-keyed one, scoped to the read path only (`record_audit_log_start()`/`record_audit_log_finish()`'s own `AUDIT_EXEMPT_DOCTYPES` use on the write path is untouched — it's load-bearing for `init_bot.py`'s Role/DocType bootstrap sequence and F10's manual audit-bypass logging, see that file's `log_role_provisioning()` docstring; touching it risks double-logging Role/DocType creation during bot provisioning for no benefit here). Concretely: `get_resource()` and `get_user_roles()` both gain an `internal: bool = False` parameter that skips `_log_read()` when true; the only callers that ever set it are the true plumbing call sites — `resource_exists()`, `_fetch_doctype_role_permissions()`, `_bot_identity()`, and `_requester_has_role_permission()`'s own `get_user_roles()` call. Every doctype-agnostic caller (the CLI, a domain script) defaults to `internal=False` and gets a real row. `get_user_roles()` additionally gains the same session/channel/prompt-context kwargs every other read function already has, and logs via `_log_read()` when not internal. `resource_exists()`'s own docstring ("Never logged, never gated") becomes actually true regardless of doctype, rather than true only by coincidence of which doctypes happened to be in the blacklist.

**Explicitly out of scope, flagged not fixed:** `PROD_GATE_EXEMPT_DOCTYPES` (a separate constant, same rationale) still exempts `User`/`DocType`/`Role` from `_validate_prod_requester()`'s permission gate entirely — a `query User` read still runs with no RBAC check at all, only F12's audit-visibility fix applies to it. Narrowing the gate exemption itself is a distinct, riskier change (breaks the intentional chicken-and-egg-safe path that lets `get_user_roles()`/`resource_exists()` work before a requester's own permissions can be evaluated) and wasn't asked for here.

## Comments (F11/F12)

**Not adopted: "log every Frappe API call as its own Audit Log row."** Rejected as the general design — a single validated business read already fans out to ~5 internal HTTP calls (`resource_exists` → `_bot_identity` → RBAC-reliability probe → `has_permission` **or** `get_user_roles` + DocType DocPerm fetch), each logged as insert+submit (2 more HTTP each); blanket logging multiplies read volume roughly 5x for zero added meaning, since those calls together answer one question, not five. F11/F12 instead log at the two units that actually matter: the read/write itself (already covered by F1's mechanism) and the one gate decision that authorized or refused it.

**Implementation (2026-09-12):** Both landed together in `core/client.py` (same file, same review pass — F12's `internal=` plumbing is what F11's gate-decision logging calls into for `resource_exists()` during its own "is this requester a real User" check, so splitting the patches would have meant temporarily reintroducing the bug one of them fixes).

- **F11:** new `_PTYPE_TO_ACTION` map + `_log_gate_decision()`, called from every branch of `_validate_prod_requester()` (all four raise sites, the local-fallback allow, and the final RPC-confirmed allow) via a small `_log(allowed, detail)` closure capturing the call's own context. Reuses the existing action/status vocabulary rather than adding a new Select option value (would need a live schema change on every provisioned instance); the specific verdict lands in `response_payload` tagged `gate_check: true`. `_validate_prod_requester()` gained `session_id`/`domain_code`/`channel`/`channel_metadata`/`prompt_summary`/`latest_prompt` kwargs, threaded from all four callers (`query_resource`/`get_resource`/`run_query_report`/`mutate_resource`) so gate-decision rows carry the same context every other audit row does.
- **F12:** `AUDIT_EXEMPT_DOCTYPES` (write path) is untouched, exactly as scoped above. New, narrower `_LOG_READ_RECURSION_EXEMPT_DOCTYPES = {AUDIT_LOG_DOCTYPE, "Comment"}` replaces it as `_log_read()`'s doctype check. `get_resource()`/`get_user_roles()` both gained `internal: bool = False`; `resource_exists()`, `_fetch_doctype_role_permissions()`, `_bot_identity()`, and `_requester_has_role_permission()`'s own role lookup all now pass `internal=True` explicitly. `get_user_roles()` gained the standard session/channel/prompt-context kwargs plus a distinct `requested_by` (who's asking, separate from `user`, whose roles are being looked up) and now logs via `_log_read()` when not internal. CLI `roles` command threads the top-level `--requested-by`/`--session-id`/etc through (not added to the mandatory-`--requested-by` list — deliberately unchanged, this command is the RBAC-computing primitive itself and can't depend on having already passed the gate it feeds).

**A denial is now visible where it wasn't before, and it's visible at the layer that matters.** A refused `Employee` read for `nikhil.sharma@qkeee.in` (the exact scenario in `logs-sep12-11-12.log`) now writes one `status: "Failure"`, `response_payload.gate_check: true` row — not the ~5 raw internal calls that produced the verdict, and not silently nothing.

Docs: `00-conventions.md`'s "Read audit logging, always on" bullet split in two — the read-path exemption is now described as purpose-keyed, not doctype-keyed, and a new bullet documents gate-decision logging. `references/domains/grc-audit.md`'s "What this trail proves" section and quick-reference table both gained a permission-denial row.

Tests: `core/test_client.py` — `GateDecisionLoggingTests` (5 new: missing-requester denial logged, RPC-permission denial logged with the right action mapping, an allowed call also logged, the write-path `AUDIT_EXEMPT_DOCTYPES` doctypes still skip cleanly, an unresolvable tag's own logging failure never surfaces over the original error), `GetResourceInternalFlagTests` (4 new: a business read of `User` gets logged, `resource_exists()` marks its own read internal, `_log_read()` skips on `internal=True` regardless of doctype, the two true-recursion doctypes still skip when not internal), `GetUserRolesAuditLoggingTests` (4 new: a business-intent lookup is logged with the right requester-vs-subject attribution, an internal lookup isn't, `_bot_identity()` and `_requester_has_role_permission()` both pass `internal=True`). `ProdGateWiringTests`' four existing call-signature assertions updated to include the new context kwargs (behavior change, not a false-positive fix). Two `RbacPrecheckReliabilityTests` mutate-resource tests that configure a real-looking `https://example.com` base_url without mocking `_request`/`_audit_insert` now mock `_audit_insert` explicitly — F11's gate-decision row on their denial path would otherwise have made a live network call in a networked test environment (this sandbox has no DNS, so it failed fast and silently either way; a machine with real internet access would not have). `python -m pytest scripts` — 215 passed, 28 subtests, no regressions (up from 202 before this change). CLI sanity-checked: `roles` against an unconfigured tag still fails cleanly, no traceback.

Committed `e838ed8` on `develop`, pushed to `origin` — pending human review, per this repo's existing review convention for F1–F13's own not-yet-reviewed changesets.

## F13 — Critical — Update's `field_diff` has been silently dead since it was built; F11's own new logging is what exposed it

Status: ready-for-human (committed e838ed8 on develop, erpnext-hermes; pushed to origin — not yet reviewed)
Source: live extensive testing against `demo.qkeee.in` (2026-09-13), validating F11/F12 end-to-end.

**Root cause:** `mutate_resource()`'s pre-image fetch for Update's `field_diff` — `get_resource(tag, doctype, name, strip_noise=False)` — never passed `requested_by`. `get_resource()`'s own `_validate_prod_requester("read")` gate refuses ANY call with no requester, unconditionally, no exceptions (this is by design, correctly, everywhere else) — so this specific internal call has been refused on every single invocation, forever. The refusal (`UnvalidatedProdRequesterError`, a `ConnectorError` subclass) was already caught by the surrounding `except ConnectorError: payload_before = None` and discarded — so the write itself always proceeded fine, just with `payload_before` (and therefore `field_diff`) permanently `null`. This predates F11/F12 entirely; F11's new gate-decision logging is what made the always-happening refusal visible for the first time, as a live WARN during this session's testing, rather than a fix introducing a new problem.

**Evidence:** live round-trip against `demo.qkeee.in`, `Note` doctype, before the fix — `field_diff: null`, `payload_before: null` on a real Update whose content field demonstrably changed. After the fix (below), the same round-trip produced `field_diff: [{"fieldname": "content", "old": "...", "new": "..."}]` — first time this feature has ever actually worked, confirmed live.

**Fix (implemented same session):** pass the write's own `requested_by` (plus `session_id`/`domain_code`/`channel`/`channel_metadata`/`prompt_summary`/`latest_prompt`) into that `get_resource()` call. Deliberately NOT `internal=True` — this pre-image read is a legitimate access made on the requester's behalf, not connector plumbing, so it should be gated and logged like any other read, attributed to the same requester as the write it supports (confirmed live: produces its own gate-decision row + Read row, both correctly attributed, ahead of the write's own Attempted/Success rows).

**Blast radius:** every domain's `mutate()` `update` call, on every tag, for this feature's entire life — any past claim that "Qkeee Bot Audit Log's `field_diff` shows exactly what changed" on an Update row was never true in practice; `payload_after` alone (always populated) was the only real signal.

Tests: `core/test_client.py` — `UpdatePreImageAttributionTests` (1 new: asserts the pre-image `get_resource()` call inside `mutate_resource()`'s update path carries `requested_by` plus the full session/channel/prompt context, and explicitly does NOT pass `internal=True`). `python -m pytest scripts` — 216 passed, 28 subtests, no regressions (up from 215).

Committed `e838ed8` on `develop`, pushed to `origin`, same as F11/F12 above.

## Comments (F13)

**Also confirmed working correctly during this same live pass (not separate findings, just verification notes):** the recursion exemption (`_LOG_READ_RECURSION_EXEMPT_DOCTYPES`) held with zero leaks — querying `Qkeee Bot Audit Log` itself, or triggering internal `resource_exists`/`_fetch_doctype_role_permissions` calls during every gate check in this session, produced no self-referential or stray internal rows anywhere in the ~18 rows this session generated. A denial produces exactly one row (gate-decision only); an allow produces exactly two (gate-decision + the real read/write) for a plain read or create — **correction, 2026-09-15 (issue 03, `.scratch/hermes-erp-bot-reliability/issues/03-update-audit-row-volume.md`): an allowed Update produces FOUR, not two** — the write's own gate-decision + Attempted row, plus the pre-image fetch's own gate-decision + real Read row (the pre-image read carries the write's own `requested_by` and is gated/logged like any other real read, not `internal=True` — see F13 above). Deliberately accepted as-is, not folded down: the two gate-decisions answer genuinely different permission questions (read vs. write), and correctness of the trail wins over row-count minimization here, consistent with this connector's posture everywhere else. Locked in by `core/test_client.py`'s `UpdateAuditRowVolumeTests`. A failed real write after an allowed gate still correctly shows two-phase `Attempted`→`Failure` (confirmed via a delete blocked by ERPNext's own `LinkExistsError` after the gate had already allowed it). `discover.py meta` (a `DocType` schema read) and a direct `get User <name>` both now produce real Audit Log rows post-F12, where they were silently dropped before. A `roles` call with no `--requested-by` at all correctly produces zero rows (best-effort `MandatoryError` swallow on the audit doctype's own mandatory field) rather than crashing or logging garbage — expected, not a bug, and consistent with every other attribution-less call in this connector.

**Incident during this same session, corrected:** an attempt to restore the bot account's `Qkeee Bot` role (lost again on the live instance, same regression as the 2026-09-11 note in this file) via a plain `mutate_resource(..., "User", "update", payload={"roles": [{"role": "Qkeee Bot"}]}, ...)` replaced the ENTIRE `roles` child table instead of appending to it — Frappe's REST update replaces child tables wholesale, it doesn't merge. This wiped `dev-erp-hermes@qkeee.in` down to zero roles on the live demo instance. The harness's own auto-mode classifier then correctly blocked a follow-up attempt to push the full restored role list back programmatically; the user applied the fix manually instead. **Lesson for any future skill code or agent workflow that mutates a User's `roles` child table via this connector: always fetch the full current roles list first and include it in the update payload — a plain "add this one role" payload silently discards every other role the user held.** Worth a documented warning in `00-conventions.md` or a small `add_role()`/`remove_role()` helper in `core/client.py` that does the fetch-merge-write itself, rather than leaving every caller to rediscover this the same way; not implemented this session (out of scope of F11/F12/F13), flagged for whoever picks this up next.

---

## What held up (keep as-is)

- OCR fallback (pymupdf text → page-render → multimodal read) worked cleanly on a scanned PDF.
- Draft-then-confirm sequencing followed correctly; create/update/submit stayed separate calls.
- Both writes were re-fetched from the live API and checked field-by-field before being reported done (the GSTIN-non-persistence catch is real, even though the remediation advice attached to it was wrong — F2).
- Requester attribution (`nikhil.sharma@qkeee.in`) correct on every call, resolved from the channel sender.
- `_audit_log_status` was checked and surfaced on every write rather than assumed.

## Triage outcome (2026-09-11) — all 9 findings decided

**Historical snapshot, not current status.** This table records the *initial* triage decision at 2026-09-11, before same-day implementation — hence F1/F2/F5/F6/F9 show `ready-for-agent` here ("fully specified, hand to an AFK agent") while their own `Status:` lines above now read `ready-for-human` (implemented, committed, pushed — awaiting review; see the status-line note under "How to read this doc"). Left as-is rather than rewritten, since it's a legitimate record of what was decided that day — read the per-finding `Status:` line above for the current state, not this table.

| Finding | Status (2026-09-11, at triage time) | Course |
| --- | --- | --- |
| F8 | ready-for-human | Resurrected (`discover.py` + tests), code written, committed `cc72045`, pushed to origin — see F8 entry |
| F1 | ready-for-agent | Reusable write CLI (`execute_write.py`) + explicit doc instruction |
| F2 | ready-for-agent | Code-enforced KYC gate in procurement.py + fix GSTIN→Address doc mapping |
| F5 | ready-for-agent | Token must be echoed back in the user's own confirmation reply |
| F6 | ready-for-agent | Purchase-sourced item: no bare `standard_rate`, set Standard Buying explicitly |
| F9 | ready-for-agent | Purchase-sourced item defaults to `is_purchase_item=1, is_sales_item=0` |
| F3 | needs-triage → superseded | Generalized into `issues/01-schema-first-attribute-mapping.md` (separate design task) |
| F4 | needs-triage → folded | Folded into `issues/01-schema-first-attribute-mapping.md` |
| F7 | needs-info | Deferred — user has an alternative to standard `init_bot.py` provisioning to articulate later |

## Implementation order (once actioned)

1. F1 — zero schema/data risk, every future write inherits it, unblocks trustworthy audit rows for everything after it
2. F2 — directly reopens trigger (b)
3. F5 — closes the weak-token gap Item (and anything else domain-less) currently relies on
4. F6 / F9 — contained, low-risk Item-payload fixes
5. Issue 01 (schema-first mapping, absorbing F3+F4) — larger design pass, do after the above land so it isn't competing with in-flight code changes
6. F7 — on hold pending your alternative approach

## Comments

**2026-09-13 — verification review of F1-F13 + issue 01.** Four parallel passes checked every finding's implementation claim against actual code (not just this doc's prose), plus doc-sync and process/taxonomy conformance against this workspace's `docs/agents/issue-tracker.md`/`triage-labels.md`. Result: all 13 findings' code/test claims CONFIRMED against `erpnext-hermes` `develop` (216/216 tests pass, fully pushed to `origin/develop`). Two real follow-up gaps surfaced that this doc's own writeups didn't fully capture — filed as their own tickets rather than folded in here, per this repo's one-file-per-ticket convention: `issues/02-kyc-subpayload-schema-mapping-gap.md` (issue 01's schema-mapping never actually covers the Supplier-KYC Address/Contact sub-payload, deeper than issue 01's own "wrong entry point" framing) and `issues/03-update-audit-row-volume.md` (F13's fix makes every Update log 4 audit rows, not the 2 this doc's "Comments (F13)" section claims). One stale doc bullet was also found and fixed directly: `references/00-conventions.md`'s "Bot account" section still described pre-F7-tightening behavior and cited the now-dead `PrivilegedBotAccountError` — corrected in place. Also fixed in this pass: every "not committed"/"not pushed" status annotation throughout this file was stale (everything is in fact committed and pushed) — corrected to cite the actual landing commit; see the status-line note under "How to read this doc" for the `ready-for-human` usage clarification.

