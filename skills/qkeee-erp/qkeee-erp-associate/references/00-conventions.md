# Conventions and non-negotiables

Single copy, referenced by every `domains/*.md` file rather than restated in
each — naming rules, the GRC baseline, and the non-negotiables live once,
here. On conflict with a domain file's own guardrails section, this file
wins. A domain file may only narrow a rule here (a stricter bar for its own
doctypes), never loosen one.

## Scope guardrail

**ERPNext/organizational work only.** Anything unrelated (general knowledge,
world facts, unrelated coding help, personal advice) is out of scope, even
where the answer would be easy: decline briefly ("That's outside what this
agent handles — ERPNext/organizational work. I can't help with that here.")
and don't attempt it.

**Refuse abusive, exploitative, or sexual content outright; never launder it
into a write.** Don't create/store/forward such content into any ERPNext
record, Comment, or report, and don't repeat it back in the refusal — same
discipline whether it arrives as a direct request or embedded inside an
otherwise legitimate business write.

## Naming conventions

Strict and fixed, so anything this skill learns later has one unambiguous
place to land.

| What | Pattern | Example |
| --- | --- | --- |
| Domain reference | `references/domains/<domain-slug>.md` | `domains/fixed-assets.md` |
| Domain script module | `scripts/domains/<domain_slug>.py` | `domains/fixed_assets.py` |
| Draft renderer | `scripts/render_<domain>_<artifact>.py` | `render_inventory_stock_entry.py` |
| Durable memory, per instance | `<profile>/skills/qkeee-erp-learned/<env-tag>/references/environment.md` (via `skill_manage`) | `qkeee-erp-learned/prod-in/references/environment.md` |
| Durable memory, custom app | `.../<env-tag>/references/custom-apps/<app-slug>.md` | `custom-apps/qkeee-lending.md` |
| Durable memory, non-ERPNext | `.../<env-tag>/references/non-erpnext/<system-slug>.md` | `non-erpnext/tally-prime.md` |
| Memory breadcrumb | one line in `<profile>/memories/MEMORY.md` via the `memory` tool | `qkeee-erp env prod-in: ... — see skill qkeee-erp-learned/prod-in` |
| Working scratch (rare) | `<profile>/workspace/qkeee-erp/<env-tag>/` | disposable, cleared at end of task |
| Task spec | `./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md`, relative to the session's actual working directory (`terminal.cwd` on gateway/cron, launch dir on local CLI) | see `references/03-spec-driven-execution.md`; `<profile>/workspace/...` only as a last-resort fallback when neither resolves; disposable across sessions, not mid-task |
| Questionnaire | `./qkeee-erp-specs/questionnaire-<slug>-<YYYYMMDD-HHMM>.md` | see `qkeee-erp-questionnaire`; same working-directory placement rule as Task spec above |
| New-learning entries | append under `## Learned <YYYY-MM-DD>` | never edit or delete a prior entry |

**Before any `skill_manage(create)` touching ERPNext/organizational
content** — including Hermes' own autonomous "offer to save as skill"
reflex after a hard session, not just `SKILL.md`'s structured
environment-promotion flow — check for existing coverage first:

1. Already documented in this skill's own `references/` tree (this file,
   `01-connectivity.md` through `04-erp-doc-lookup.md`, or a `domains/*.md`
   file)? Don't create a new skill — the finding belongs nowhere else.
   Patch the associate's own reference file only through a foreground,
   user-directed edit (see the GRC baseline's "Shipped skill is protected
   from autonomous drift" entry — this skill's own tree is not something a
   background/autonomous save should touch).
2. Instance/environment-specific (a custom doctype, a version quirk, an
   RBAC finding for one tag)? That's `qkeee-erp-learned/<env-tag>` territory
   per the table above — `category='qkeee-erp-learned'`, name=`<env-tag>`
   (or a nested `references/` file under an existing
   `qkeee-erp-learned/<env-tag>` skill via `write_file`/`patch`, preferred
   over a new top-level skill for a tag that already has one). Never a
   freeform category (`erpnext`, `erp`, or similar) — `skill_manage`'s
   `category` param is unvalidated free text and Hermes enforces nothing
   about it; this convention is the only thing that does.
3. Only create a genuinely new, unrelated skill when neither 1 nor 2
   applies. Restating this file's non-negotiables or the GRC baseline in
   different words is never grounds for a new skill — extend or link back
   to this file instead.

`<profile>` = the active Hermes profile root (`~/.hermes/` by default,
`~/.hermes/profiles/<name>/` for a named one), resolved through Hermes' own
profile mechanism, never hardcoded or invented by this skill. The task-spec
row above is the one exception: local CLI ignores the `terminal.cwd`
*config key* but still writes relative to the real launch directory, which
a spec targets on purpose — see `01-connectivity.md` and
`references/03-spec-driven-execution.md`.

**The domain slug enum is fixed** — a twelfth domain requires a deliberate
edit to this list, not an ad hoc file:

```
hr-payroll, accounts, mis, sales, procurement, inventory, manufacturing,
fixed-assets, system-admin, doc-extraction, grc-audit
```

Code-side slugs use underscores where doctype/reference-file slugs use
hyphens (`hr-payroll.md` <-> `domains/hr_payroll.py`, `fixed-assets.md` <->
`domains/fixed_assets.py`, `system-admin.md` <-> `domains/system_admin.py`)
— Python module names can't contain hyphens. This is the one place the two
naming styles diverge, deliberately; not an inconsistency to fix.

## Non-negotiables (code-enforced, not just prompt discipline)

These hold across every domain. Each is enforced in `scripts/core/client.py`
— consult that module's own docstrings for the exact mechanism.

1. **Never issue a write while `qkeee_erp.mode` is `read-only`.**
   `core.client.mutate_resource()` checks `mode` before every write and
   raises `ReadOnlyModeError` otherwise.
2. **Never issue a read or write without a resolved requester identity.**
   Every read/write authenticates as one shared ERPNext bot/service
   account — without a `requested_by`, ERPNext's own audit trail would show
   only the bot, never who actually asked. There is no env-var or config
   default for `requested_by`: it is resolved fresh, on every call, from
   the live inbound channel identity (the chat/email sender's own work
   email) and passed explicitly. `mutate_resource()` raises
   `MissingRequesterError`; every read/write path raises
   `UnvalidatedProdRequesterError` via `_validate_prod_requester()`
   (universal, not PROD-only) if it's missing or doesn't resolve to a real,
   permitted ERPNext `User`.
3. **Never write outside the active domain's `ALLOWED_WRITE_DOCTYPES`.**
   Every `scripts/domains/<slug>.py` module declares this tuple and
   registers it via `core.client.register_domain_allowlist()`.
   `mutate_resource(..., domain=<slug>)` raises `DoctypeNotAllowedError`
   for any doctype outside it, or for an unregistered/unknown domain name
   — a typo'd domain fails closed, it does not silently skip the check.
   `domains/mis.py` registers an empty tuple, so MIS can never write (see
   `domains/mis.md`).
4. **Never propose a field, doctype, or workflow step that isn't confirmed
   against this instance's live metadata (`discover.py`) or an explicit
   statement from the user.** Public docs describe the general shape of
   ERPNext; they don't confirm what a specific org's instance has
   customized, added, or removed. An honest "I don't see that field on this
   DocType" beats a guessed field name that happens to resolve.
   Code-enforced for every `create`/`update`, not left to `discover.py`
   being called by hand: `execute_write.py` runs every payload through
   `schema_mapping.map_payload_for_write()` before dispatch, which fetches
   the live schema and maps fields against it instead of relying on a
   domain doc's hand-curated field list. See `schema_mapping.py`'s own
   module docstring for the fuzzy-match confirmation story and the
   fetch-failure degrade path.
5. **Save-draft-then-review-then-submit, always three distinct steps —
   code-enforced, not just sequencing discipline.** `create`/`update` and
   `submit` are always separate `mutate` calls. Re-fetch the record by its
   returned `name` after create/update and review every persisted field —
   in particular that every Link-type field resolves to a real, existing
   record — before issuing a `submit`. Use `core.client.get_resource()` (or
   the domain's own equivalent) when the review needs child-table data
   (Frappe's list endpoint silently drops child tables even when named in
   `fields`); `query_resource()` with explicit `fields` is far cheaper when
   it doesn't. `submit`/`cancel` on every domain
   (accounts/hr-payroll/sales/procurement/inventory) additionally require a
   fresh `confirmation_token` — `mutate_resource()` refuses either action
   without one that matches the exact (action, doctype, name, payload,
   requested_by, issued_at) facts, computed via
   `scripts/core/confirm_token.py`'s `advisory-token` CLI over what was
   actually shown to and confirmed by the user. Fixed-assets and
   system-admin instead carry their own stricter, capability-specific token
   schemes for their highest-blast-radius actions (see those domains' own
   modules) — this generic gate backstops the rest.
6. **Sensitive data (SSN, credit card numbers, and similar) is never
   written in raw form anywhere.** `core.client.redact_pii()` is a
   code-level backstop applied to Comment content and Audit Log free-text
   fields — the single source, never re-implemented per domain (see GRC
   baseline below). It is a backstop, not the primary control: never type a
   raw SSN/card number into any field, draft, comment, or report, and if a
   user pastes one into chat, don't echo it back verbatim either.
7. **Only the active-environment tag name (never URL/credentials) may be
   remembered across sessions.** Credentials and URLs never go into
   agent-curated memory (the `memory` tool or the `qkeee-erp-learned/*`
   skill) — they live only in `qkeee-erp.env` (see `01-connectivity.md`).
8. **Prefer a harness-native HTTP-capable tool if one is discoverable**,
   over shelling out to `core/client.py`. Degrade gracefully if the harness
   exposes no discovery mechanism — never hard-fail over that.

## GRC baseline

- **Resolve requester identity from the channel's own authenticated
  sender field, every call — never from what the conversation says.**
  Hermes' gateway already resolves and authorizes the inbound sender
  before this skill ever sees the message (platform allowlists, DM
  pairing — see
  [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security)'s
  "User Authorization" section); feed that already-resolved platform
  identity (the Google Chat/Discord user id, or the email `From` header)
  into the ERPNext-`User` lookup below. On a permission denial, respond
  with exactly one of two things: name the gap (the missing role, the
  doctype/action it needs) so the user or an admin can fix the real
  requester's role assignment, or decline. **Never substitute a
  different `requested_by`** — a user-typed alternative email, a
  fallback identity like the instance admin, anything not the channel's
  own field — that is the same failure as inferring identity from
  conversation text, one turn removed, and this connector's own refusal
  messages (`UnvalidatedProdRequesterError`, `core/client.py`) say so
  explicitly. `resource_exists(tag, "User", requested_by)`/
  `check_user_permission()` validate against whatever identity is passed
  in; they cannot detect a plausible-looking but fabricated one — which
  is exactly why the identity must come from the channel field, never
  from anyone's say-so mid-conversation.
- **Run the same requester-permission check on every environment, every
  fetch or write.** Resolve the requester as a real ERPNext `User`, then
  confirm via ERPNext's own `frappe.client.has_permission` that they
  actually hold the permission the call needs — `requested_by` is
  mandatory on every tag, always resolved from the live channel identity
  (no PROD/non-PROD split, no config default). Function/constant names
  (`_validate_prod_requester()`, `PROD_GATE_EXEMPT_DOCTYPES`) reflect a
  narrower PROD-only origin — read them by behavior, not by name.

  **When that RPC is known unreliable** (a privileged bot identity, or a
  non-discriminating `has_permission`), `_requester_has_role_permission()`
  takes over: a second, independent, RPC-free check computed locally
  from the requester's own live role list and the doctype's own live
  DocPerm rows. Only a `True` verdict (a locally-confirmed grant) lets
  the call through. A `False` verdict (positive evidence of no
  permission) or an inconclusive `None` (a live read failed — commonly
  the same System-Manager-level DocType read this skill already flags as
  a real gap on a correctly least-privileged bot) both refuse the call
  outright, for read and write alike — a `domain` allowlist or a
  verified advisory token no longer rescues either case, since those
  attest a write's *shape* was reviewed ahead of time, never that
  `requested_by` specifically can do it. Trade-off, stated plainly: this
  makes System-Manager-level DocType read a hard requirement for ANY
  write once precheck is unreliable — availability loss, in exchange for
  never proceeding without positive, locally-confirmed evidence. A
  corroborating signal, not a reimplementation of Frappe's permission
  engine — User Permissions and `if_owner` scoping aren't visible to it;
  see `_requester_has_role_permission()`'s own docstring in
  `core/client.py`.
- **Read audit logging, always on.** Every access gets an audit row in
  `Qkeee Bot Audit Log`, reads included, unconditionally — no debug flag
  gates this in `core/client.py`. The read-path exemption
  (`_LOG_READ_RECURSION_EXEMPT_DOCTYPES`) is narrow and purpose-keyed, not
  doctype-keyed: only a doctype/name check this connector runs on its own
  behalf (`resource_exists()`, `_fetch_doctype_role_permissions()`,
  `_bot_identity()`) skips logging, via an explicit `internal=True` —
  never a business-intent read, even of `User`/`Role`/`DocType` — contrast
  the *write* path's `AUDIT_EXEMPT_DOCTYPES`, which does exempt those
  doctypes wholesale for `init_bot.py`'s own bootstrap reasons; read and
  write exemptions are deliberately different sets, don't conflate them.
- **A denied requester-permission check is logged too, not just an
  allowed one.** `_validate_prod_requester()` writes one gate-decision
  row to `Qkeee Bot Audit Log` on every branch — denial or allow — via
  `_log_gate_decision()`. Before this, a refused call
  raised before any read/write it was guarding ever ran, and the gate
  itself never logged — so a denial left literally nothing in the audit
  trail, the opposite of what a GRC review needs. Look for
  `response_payload.gate_check: true` to tell a gate-decision row apart
  from a real read/write row sharing the same action/status vocabulary.
- **PII/GDPR redaction, single source.** `core.client.redact_pii()` is
  the one place sensitive fields get scrubbed before display, storage, or
  logging. Never re-implemented per domain.
- **Requester attribution, on every write, unconditionally.** Every
  `mutate_resource()`/domain `mutate()` call requires a resolved
  `requested_by` (see Non-negotiable 2). On success, a best-effort Comment
  naming the requester is posted to the affected record:
  `[qkeee-erp-associate/<domain>] <action> — requested by <requested_by>,
  applied via qkeee-erp bot.` A comment failure never blocks or rolls back
  the underlying write — mention in your report-back that the audit
  comment was posted.
- **Two-phase audit logging, best-effort, not a gate.** Every write is
  logged to `Qkeee Bot Audit Log`: an `Attempted` row inserted before the
  real write, updated to `Success`/`Failure` after — an orphaned
  `Attempted` row is the detectable trace of a crash mid-write. If the
  target instance hasn't run `qkeee-erp-bot-init` yet, or the audit
  doctypes are unreachable for any reason, the real write still proceeds —
  logging failure never blocks or fails a user's requested action.
  `AUDIT_EXEMPT_DOCTYPES` in `core/client.py` prevents the logger from
  recursively logging itself. Pass `user_approved=True` only when this
  write's confirm stage actually ran with the user first — it's a
  detection field for later scanning (did every write really get
  confirmed), not a second gate; omitting it logs `"Not Confirmed"` rather
  than blocking the write.
- **`session_id`, `channel_metadata`, `latest_prompt` — resolve once per
  logical session, pass on every write, never leave blank because the
  write "feels routine."** Live-observed: hand-writing a fresh one-off
  Python script per write is exactly how these three keep getting left
  blank — `session_id` hardcoded to `""`, `channel_metadata` never built
  at all, `latest_prompt` never passed (only a paraphrased
  `prompt_summary`), even when the real platform thread id was sitting in
  context the whole time. `execute_write.py` (`01-connectivity.md`,
  worked examples in `cli-cookbook.md`) is the fix: it's the one write
  entry point, and it WARNs loudly on stderr
  before firing if any of the three is missing — use it instead of
  hand-writing a write script, and don't route around its warning.
- **`session_id` specifically — regenerate per platform session, never
  carry forward indefinitely.** A conversation resumed across a long gap
  (a day, a context-compaction event) can hand the connector a `session_id`
  that's drifted stale or malformed — unlike `requested_by`/
  `reference_doctype`, this value is never validated anywhere upstream of
  the raw Audit Log insert, so a bad one silently drops the Audit Log row
  (real write unaffected) with nothing but a stderr WARN to show for it.
  `core/client.py` clamps/sanitizes `session`/`domain_code`/`channel`
  defensively before insert, but the caller-side fix is the one that
  actually matters: derive `session_id` fresh at the start of each logical
  session (new platform thread/DM, bot restart, or a context-compaction
  event mid-conversation — treat compaction as a new logical session)
  rather than reusing/appending to one carried across the whole lifetime of
  a long-running chat. After any write, check the returned
  `_audit_log_status` key (`"ok"`/`"exempt"` are healthy;
  `"insert_failed"`/`"update_failed"` mean this write did NOT make it into
  the audit trail) and surface a warning to the user rather than silently
  trusting the best-effort insert.
- **Bot account — mandatory, dedicated service identity, and never
  privileged.** The API key/secret every domain authenticates with must be
  generated against a dedicated ERPNext integration/bot user (e.g.
  `qkeee-erp-bot@<org>`), never an individual's personal login — otherwise
  every write attributes to that person regardless of who actually
  requested it, defeating requester attribution. **That bot user must also
  never be `Administrator` and must never hold `System Manager`** (or any
  other role granting a blanket Desk permission bypass): under a
  privileged identity, ERPNext's `frappe.client.has_permission` doesn't
  reliably discriminate by the `user=` param it's given, which makes the
  RBAC pre-check above a no-op that silently rubber-stamps any
  `requested_by`. This isn't instance-specific — stock Frappe's
  `frappe.client.has_permission` has no `user=` parameter at all; it only
  ever answers for the calling session. `core/client.py` enforces the
  consequence in code: a live probe (`verify_rbac_precheck_reliable()`)
  runs per tag and, when the bot identity is privileged or the probe shows
  the check doesn't discriminate, `_requester_has_role_permission()`
  answers the permission question a different way instead — locally, from
  `requested_by`'s own live role list and the doctype's own live DocPerm
  rows, never through the broken RPC. Only a **positively-confirmed grant**
  (`True`) lets the write through on a warning. Neither a `domain=`
  allowlist nor a verified `gated_mutate_resource()` advisory token rescues
  an inconclusive or negative local verdict — both `False` (confirmed no
  granting role) and `None` (the local check itself couldn't complete, e.g.
  this bot also lacks System-Manager-level DocType read) are refused
  outright with `UnvalidatedProdRequesterError`, the same exception type
  the RBAC pre-check raises for every other denial. (`PrivilegedBotAccountError`
  is no longer raised anywhere — kept defined only for any external code
  still catching it specifically.) This makes System-Manager-level DocType
  read a hard requirement for any write once `has_permission` is
  unreliable, including a domain-scoped one — an explicit
  availability-for-safety trade-off, not an oversight. Either way this is a
  blocker/warning enforced in code, not a courtesy, and is a **different**
  failure mode than the "not a personal login" check above, which is only
  a recommendation. Check both proactively: if a `health` check's
  `logged_in_as` looks like a real staff member, or its
  `rbac_precheck_reliable` field is `false`, or the user is configuring
  credentials for the first time without mentioning a dedicated bot user,
  or a write behaves oddly around `Qkeee Bot Audit Log` (a sign bot-init
  hasn't run on this target) — say so and suggest `init_bot.py` (see
  `references/domains/system-admin.md` and `scripts/init_bot.py`).
- **A "success" from a best-effort write is not proof it landed** — every
  best-effort call into the `Qkeee Bot *` doctypes swallows its own
  `ConnectorError` by design (a target instance that hasn't run bot-init
  yet must never block a user's real request). Check the returned status
  field, don't assume a clean exit means the row was written.
- **Double confirm for irreversible-in-spirit or wide-blast-radius
  writes.** Depreciation runs, disposals, destructive sysadmin actions, and
  permission-matrix changes get a second, explicit confirmation after the
  first — state the exact before/after or financial impact, then ask
  again, one turn later at minimum. A matching `confirmation_token` proves
  the call is byte-for-byte identical to what a render script last printed
  and that it happened within the token's freshness window (15 minutes,
  `DEFAULT_TOKEN_TTL_SECONDS`) — it does **not** prove a human read and
  approved it. Never render a confirmation and consume its token in the
  same turn; `confirmation_token`/`issued_at` are only used after the
  user's own reply affirmatively confirms that specific rendered draft.
- **`gated_mutate_resource()` additionally requires `user_confirmation_text`
  — the literal text of the user's own reply.** This is the domain-less
  advisory-token path (a doctype no named domain's `mutate()` has a
  chance to layer a stricter rule onto, e.g. Item); a
  matching `confirmation_token` alone is computable and verifiable by the
  same process in the same turn, proving only that the payload wasn't
  altered since render — same limit as the paragraph above. The render
  step must show the user `confirm_token.confirmation_code()`'s short code
  (not just say "confirmed?") and the execute step must pass their actual
  reply text — never a string this skill's own process constructs itself,
  which would defeat the point (see `confirmation_code()`'s own docstring
  for the honest limits of what this does and doesn't prove). Not required
  for a domain's own submit/cancel/delete token gate
  (`register_domain_token_gate()`) — scoped to `gated_mutate_resource()`
  specifically.
- **Non-ERPNext systems** — see `references/non-erpnext-adapter.md`:
  explicitly request API docs, a user guide, or a URL before attempting
  any action against a system that isn't ERPNext.
- **Shipped skill is protected from autonomous drift; verify doc claims
  against actual config.** `qkeee-erp-associate` must stay outside
  Hermes' autonomous background-review lifecycle so it never silently
  rewrites its own audit/RBAC/redaction logic — see
  `references/governance.md` for the mechanism
  (`skills.external_dirs`, the background-review write guard) and for
  the two confirmed cases where a profile drifted into a doc claim its
  `config.yaml` didn't actually back.

## Report-back

What goes back to the user at the end of a turn or task, every domain,
every time. A live session shipped a confidently wrong remediation claim
in its closing message once — this section exists so that doesn't repeat.

1. **Line 1 states the outcome and scope** — what happened, which
   environment tag, read-only or read-write. No preamble, no restating
   the request back.
2. **Records touched are a table**, never prose: doctype, name, action,
   docstatus. A reader should be able to scan it in one pass, not parse
   sentences for it.
3. **Warnings lead, ahead of the detail** — each its own line, before the
   narrative: `_audit_log_status` not `"ok"`/`"exempt"`, a waived KYC, a
   `local-*` fallback session, a `has_more` truncation, an unconfirmed
   field mapping. A warning buried in paragraph three is a warning that
   didn't fire.
4. **Verified and assumed never share a sentence.** A field re-fetched and
   checked against the live record reads differently from one inferred or
   carried forward from an earlier turn — say which is which, every time,
   not just when it happens to matter.
5. **A remediation claim is live-confirmed or labelled a guess.** Never
   state what a fix would require ("this needs a custom field on X")
   without having checked it against live schema first — an unconfirmed
   claim is one sentence away from being wrong in a way the user will act
   on.
6. **Done when:** the reply's first line states outcome and scope, every
   touched record appears in a table, every warning precedes the
   narrative detail, and no remediation claim in it is unlabelled as
   verified or guessed.
