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
| Domain script module | `qkeee_erp/domains/<domain_slug>.py` | `domains/fixed_assets.py` |
| Write operation | `<domain>.<name>` in `qkeee_erp.core.operations`'s registry | `fixed_assets.scrap`, `sales.generic` |
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

**Term rule — "draft", every domain.** "Draft" means docstatus 0 on a
**submittable** doctype only (Sales Order, Journal Entry, Leave
Application, ...): staged, reversible, not yet on record. A
non-submittable doctype (Employee, Customer, Supplier, Item, Asset
Category, Warehouse, ...) has no draft: docstatus 0 is its final state,
and a save creates a live record. Say "saved" or "created" and say it is
live. For something proposed but not yet written, say "proposed" or
"the payload". `erp_discover meta`'s `issubmittable` tells you which.
Calling two live Employees "drafts" (DEMO_ERP, 2026-10-07) made the user
read them as reversible staging.

Code-side slugs use underscores where doctype/reference-file slugs use
hyphens (`hr-payroll.md` <-> `domains/hr_payroll.py`, `fixed-assets.md` <->
`domains/fixed_assets.py`, `system-admin.md` <-> `domains/system_admin.py`)
— Python module names can't contain hyphens. This is the one place the two
naming styles diverge, deliberately; not an inconsistency to fix.

## Non-negotiables (code-enforced, not just prompt discipline)

These hold across every domain. Writes are enforced by the one write
pipeline in `qkeee_erp.core.operations` (every write is a named
operation; `erp_execute_write list_ops` lists them), reads by
`qkeee_erp.core.client` (both in the `qkeee-erp` plugin). The calling
rules are in `qkeee-erp:usage`; `references/tool-cookbook.md` has the
write flow.

1. **Never issue a write while the write mode (`erp_discover health` → `mode`) is `read-only`.**
   The pipeline checks `mode` before every write and raises
   `ReadOnlyModeError` otherwise.
2. **Never issue a read or write without a resolved requester identity.**
   Every read/write authenticates as one shared ERPNext bot/service
   account — without a `requested_by`, ERPNext's own audit trail would show
   only the bot, never who actually asked. There is no env-var or config
   default for `requested_by`: it is resolved fresh, on every call, from
   the live inbound channel identity (the chat/email sender's own work
   email) and passed explicitly. The pipeline raises
   `MissingRequesterError`; every read/write path raises
   `UnvalidatedProdRequesterError` via `_validate_prod_requester()`
   (universal, not PROD-only) if it's missing or doesn't resolve to a real,
   permitted ERPNext `User`. No doctype is exempt for a write — User,
   Role and DocType writes need a requester holding that permission too.
   Environment Metadata reads (`DocType`, `Module Def`, the
   `get_versions` RPC — a closed list, agents ADR 0001) skip only the
   requester's permission check; the requester must still be given and
   bound to the session.
3. **Never write outside the active domain's `ALLOWED_WRITE_DOCTYPES`.**
   Every `qkeee_erp/domains/<slug>.py` module declares this tuple and
   registers it via `core.client.register_domain_allowlist()`. The
   `<slug>.generic` operation raises `DoctypeNotAllowedError` for any
   doctype outside it, or for an unregistered/unknown domain name — a
   typo'd domain fails closed. A generic operation also refuses any
   (doctype, action) a gated operation owns (e.g. User create is only
   `system_admin.create_user`), and `unscoped.generic` refuses every
   domain-owned doctype and every privilege/code/credential doctype.
   `domains/mis.py` registers an empty tuple, so MIS can never write (see
   `domains/mis.md`).
4. **Never propose a field, doctype, or workflow step that isn't confirmed
   against this instance's live metadata (`erp_discover`) or an explicit
   statement from the user.** Public docs describe the general shape of
   ERPNext; they don't confirm what a specific org's instance has
   customized, added, or removed. An honest "I don't see that field on this
   DocType" beats a guessed field name that happens to resolve.
   Code-enforced for every `create`/`update`, not left to `erp_discover`
   being called by hand: every generic create/update runs its payload
   through `schema_mapping.map_payload_for_write()` (identically at
   render and execute, so the confirmation token covers the mapped
   payload), which fetches
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
   it doesn't. `submit`/`cancel`/`delete` on every domain need a fresh
   confirmation: render it (`erp_execute_write render`), show the user
   the rendered request and its confirmation code, and execute with their
   own reply. The render binds the record's `modified`; if the record
   changed since, the submit is refused. Fixed-assets and system-admin
   writes are separate named operations that always need the
   confirmation (see those domains' docs).
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
8. **Reach ERPNext only through the `erp_*` tools.** Never through
   another HTTP-capable tool, the terminal or `execute_code`: only the
   tools carry the requester gate, the audit row and the credentials.

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
  messages (`UnvalidatedProdRequesterError`, `qkeee_erp.core.client`) say so
  explicitly. `resource_exists(tag, "User", requested_by)`/
  `check_user_permission()` validate against whatever identity is passed
  in; they cannot detect a plausible-looking but fabricated one — which
  is exactly why the identity must come from the channel field, never
  from anyone's say-so mid-conversation. Two code-enforced backstops
  (2026-10-07, after the agent passed its own bot account on dev-erp and
  the gate validated the bot against itself): `resolve_requested_by()`
  binds the requester to the gateway's `HERMES_SESSION_USER_ID` when it is
  an email (fills it in when none is given, refuses a mismatch), and `_validate_prod_requester()` refuses `requested_by` equal
  to the connector's own bot account. The gate also applies the session
  binding itself, so a direct `requested_by=` library call gets the same
  check. Since 2026-10-08 (issue 04) the agent calls ERPNext only through the
  `qkeee_erp` gateway tools, which have no requester argument at all;
  `erp_discover(action="whoami")` prints what the session holds (in a
  Kanban worker, the task's recorded origin). `User`/`Role` reads are no longer
  gate-exempt for business reads — only the gate's own `internal=True`
  plumbing skips the gate for them.
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
  verified confirmation token no longer rescues either case, since those
  attest a write's *shape* was reviewed ahead of time, never that
  `requested_by` specifically can do it. Trade-off, stated plainly: this
  makes System-Manager-level DocType read a hard requirement for ANY
  write once precheck is unreliable — availability loss, in exchange for
  never proceeding without positive, locally-confirmed evidence. A
  corroborating signal, not a reimplementation of Frappe's permission
  engine — User Permissions and `if_owner` scoping aren't visible to it;
  see `_requester_has_role_permission()`'s own docstring in
  `qkeee_erp.core.client`.
- **Read audit logging, always on.** Every access gets an audit row in
  `Qkeee Bot Audit Log`, reads included, unconditionally — no debug flag
  gates this in `qkeee_erp.core.client`. The read-path exemption
  (`_LOG_READ_RECURSION_EXEMPT_DOCTYPES`) is narrow and purpose-keyed, not
  doctype-keyed: only a doctype/name check this connector runs on its own
  behalf (`resource_exists()`, `_fetch_doctype_role_permissions()`,
  `_bot_identity()`) skips logging, via an explicit `internal=True` —
  never a business-intent read, even of `User`/`Role`/`DocType` — contrast
  the *write* path's `AUDIT_EXEMPT_DOCTYPES`, which does exempt those
  doctypes wholesale for `hermes qkeee-erp init-bot`'s own bootstrap reasons; read and
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
  write operation requires a resolved
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
  `AUDIT_EXEMPT_DOCTYPES` in `qkeee_erp.core.client` prevents the logger from
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
  context the whole time. `erp_execute_write` (worked examples in
  `tool-cookbook.md`) is the fix: it's the one write entry point, it
  takes the session and channel from the gateway itself, and it refuses a
  write that needs confirmation without `latest_prompt`. Pass
  `prompt_summary` and `latest_prompt` on every write.
- **`session_id` specifically — regenerate per platform session, never
  carry forward indefinitely.** A conversation resumed across a long gap
  (a day, a context-compaction event) can hand the connector a `session_id`
  that's drifted stale or malformed — unlike `requested_by`/
  `reference_doctype`, this value is never validated anywhere upstream of
  the raw Audit Log insert, so a bad one silently drops the Audit Log row
  (real write unaffected) with nothing but a log warning to show for it.
  `qkeee_erp.core.client` clamps/sanitizes `session`/`domain_code`/`channel`
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
  ever answers for the calling session. `qkeee_erp.core.client` enforces the
  consequence in code: a live probe (`verify_rbac_precheck_reliable()`)
  runs per tag and, when the bot identity is privileged or the probe shows
  the check doesn't discriminate, `_requester_has_role_permission()`
  answers the permission question a different way instead — locally, from
  `requested_by`'s own live role list and the doctype's own live DocPerm
  rows, never through the broken RPC. Only a **positively-confirmed grant**
  (`True`) lets the write through on a warning. Neither a `domain=`
  allowlist nor a verified confirmation token rescues
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
  hasn't run on this target) — say so and suggest that an Operator runs
  `hermes qkeee-erp init-bot` (see `references/domains/system-admin.md`).
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
  the call is byte-for-byte identical to what `erp_execute_write render` last returned
  and that it happened within the token's freshness window (15 minutes,
  `DEFAULT_TOKEN_TTL_SECONDS`) — it does **not** prove a human read and
  approved it. Never render a confirmation and consume its token in the
  same turn; `confirmation_token`/`issued_at` are only used after the
  user's own reply affirmatively confirms that specific rendered draft.
- **Every confirmed write also requires `user_confirmation_text` — the
  literal text of the user's own reply.** A matching `confirmation_token`
  alone is computable and verifiable by the same process in the same
  turn, proving only that the request wasn't altered since render — same
  limit as the paragraph above. The render step must show the user the
  printed `confirmation_code` (not just say "confirmed?") and the execute
  step must pass their actual reply text — never a string this skill's own
  process constructs itself, which would defeat the point (see
  `confirmation_code()`'s own docstring for the honest limits of what this
  does and doesn't prove).
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

## Demo and test data

Synthetic records (a demo, a test, a training walkthrough) follow one
convention, so a later session can find and remove them:

1. **Never on a tag marked PROD.** Refuse, and say why.
2. **Mark every record two ways.** A name prefix `Demo ` on the record's
   title field (Employee `first_name`, Customer `customer_name`, ...), and
   the tag `qkeee-demo`. This skill has no tag write yet: the spec lists
   "add tag qkeee-demo" as a follow-up for the user to do in the ERPNext UI
   (sidebar → Tags) until one exists. The prefix alone is enough for the
   teardown query.
3. **Pick the least-live status the doctype allows** — Employee
   `Inactive`, Customer/Supplier `disabled: 1`, Item `disabled: 1` —
   unless the task needs the record live. When it does, say why in the
   spec, and list the side effects (Side effects section).
4. **No real personal data.** Synthetic values only (names, dates, emails
   on `example.com`), flagged as synthetic in the spec.
5. **The spec has a Teardown section:** the query that finds the records
   (by prefix, and by tag once set) and the action that removes them
   (delete while nothing links to them, else disable or set Inactive),
   with the exact calls.
6. **Check for existing demo records first** with the same query. Reuse
   or report them; never create a second set silently (Idempotency).

`references/examples/spec-demo-employees.md` follows this convention.

## Role-gap prompt

A missing permission is reported with one fixed prompt, never in the
agent's own words. "The requester has no read permission on Module Def"
(DEMO_ERP, 2026-10-07) told the user nothing about what to grant, to whom,
or where.

`erp_discover health` and `erp_discover preflight` return `gaps[]`. Each gap
has `capability`, `who` (`bot` or `requester`), `user`, `role`,
`doctype`, `perm`, `effect`, `grant_steps`, and `prompt`: the prompt
below, already filled in. Show `prompt` exactly as printed:

```
⚠ Environment catalog incomplete on <TAG> (<base URL>).
Missing: <perm> on "<doctype>" for <who> <user> (role "<role>").
Effect: <effect>.
Fix (System Manager): Role Permission Manager → Document Type "<doctype>"
→ Add rule → Role "<role>", Level 0 → tick <perm> → Save.
Then reply RECHECK ENV.
```

Rules:

1. Show each gap once per session, before any write. Activation step 1
   (`SKILL.md`) runs `health` first for this reason.
2. On "RECHECK ENV", run `health` again and show only the gaps left.
3. Never grant the role yourself, even when asked and even with the
   admin credential. A write to a Service Account's user, to a role it
   holds, or to a permission row on that role is self-escalation, and the
   pipeline refuses it (agents ADR 0003). The fix is a human admin in the
   ERPNext UI.
4. A `bot` gap is fixed on the `Qkeee Bot` role, never by giving the bot a
   stock role. `hermes qkeee-erp init-bot` grants the two reads the bot needs for
   discovery (Module Def, Workflow).

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
