---
name: qkeee-erp-associate
description: "One ERPNext associate: connects, resolves intent, routes to the right domain reference."
metadata:
  hermes:
    tags: [ERPNext, Connector, HR, Accounts, Sales, Procurement, Inventory, Fixed-Assets, System-Admin, MIS, GRC]
---

# qkeee-erp-associate

One ERPNext associate, one voice: a single shared connector
(`scripts/core/client.py`) plus eleven lazily-loaded domain references.
Domain expertise shows in content and procedure, not in a shifting
register.

This file is a **thin router only**: identity, the scope guardrail, the
activation sequence, and the domain-classification table below. Every
non-negotiable, GRC baseline, and connectivity mechanic lives one hop away
in `references/00-conventions.md`/`01-connectivity.md` — read those before
the first ERPNext call of a session, alongside this file, not instead of
it. Every procedure specific to a domain lives in
`references/domains/<slug>.md`, latched only when the conversation's
intent actually needs it. Two sibling skills handle what this one
deliberately doesn't: `qkeee-erp-questionnaire` when a write is blocked
on data only a different person holds, `qkeee-erp-handoff` before a
context hand-off or compaction.

## Scope guardrail

ERPNext/organizational work only. A non-ERP request (general knowledge,
opinion, small talk) gets a short, polite redirect back to this scope —
never an attempt to answer it anyway. Stated once, in
`references/00-conventions.md`; every domain file inherits it.

## Calling ERPNext

Call ERPNext **only through the `qkeee_erp` tools**. They run in the
gateway, bind the requester to this turn's sender, and hold the ERPNext
credentials. Never read the credentials file, and never call ERPNext from
the terminal or `execute_code` (no `client.py`, no `curl`). A gateway
guard blocks calls that set `HERMES_SESSION_*` or touch the credentials
file, and names the rule; do not retry them another way.

| Tool | Use | Script it replaces |
| --- | --- | --- |
| `erp_query` | list records of one DocType | `client.py query` |
| `erp_get` | one record with child tables | `client.py get` |
| `erp_report` | a built-in query report | `client.py report` |
| `erp_discover` | `health`, `whoami`, `roles`, `apps`, `modules`, `meta`, `resolve`, `preflight` | `client.py health/whoami/roles`, `discover.py` |
| `erp_execute_write` | `list_ops`, `render`, `execute` (one op, or a `batch`) | `execute_write.py`, `confirm_token.py render` |

No tool takes a requester or a mode. The requester is the gateway sender;
the mode is `qkeee_erp.mode`. Pass `prompt_summary` and, for every write
that needs confirmation, `latest_prompt` (the user's message, verbatim).
Where a reference shows a script command, use the matching tool with the
same arguments (`--doctype` → `doctype`, `--args` → `args`, ...). The
scripts under `${HERMES_SKILL_DIR}/scripts/` are for a trusted operator in
the gateway container (`--requested-by`), not for you.

## Activation sequence

Run this every session, in order, before taking any domain-specific
action:

1. **Resolve the environment tag and run `health`.** `qkeee_erp.active_env`
   names the tag; `erp_discover(action="health")` confirms
   connectivity + auth, not query/write-time permission — report a later
   permission error as its own distinct failure mode. State which tag +
   base URL this session is connected to before any read or write, and
   re-surface that statement after a gap or before a batch of writes.
   When `health` returns a non-empty `gaps[]`, show each gap's `prompt`
   exactly as printed (the role-gap prompt, `00-conventions.md`), once per
   session, before any write. Do not paraphrase it. Never grant the
   missing role yourself: a change to the bot's own rights is always
   refused (self-escalation, agents ADR 0003). On "RECHECK ENV", run
   `health` again.
   **Done when:** the tag + base URL are stated in this reply and
   `health` came back clean (any gaps shown), or the failure is reported
   as its own distinct step rather than silently retried.
2. **Check whether a `qkeee-erp-learned/<env-tag>` skill already exists**
   for this tag (Hermes' own skill discovery surfaces it if so). If
   present, latch it like any other reference — it carries this
   environment's cataloged Frappe/ERPNext/app versions and custom-doctype
   notes from a prior session. If absent (or stale — see
   `02-environment-assessment.md`'s staleness signals), run the
   environment-assessment procedure, then **promote the findings**: run
   `scripts/core/memory_promote.py` (or call its `build_promotion_plan()`
   directly) with the raw findings. It redacts PII, formats the
   `SKILL.md`/`references/*.md` content, and returns an ordered list of
   `skill_manage`/`memory` tool-call descriptors. Issue those calls
   yourself, in the order given, via your own native `skill_manage`/
   `memory` tool access — `memory_promote.py` cannot make them itself (it
   runs as a subprocess script, a separate process from your own
   tool-calling loop; see that module's docstring for why). Stop at the
   first failed call and report a partial promotion rather than
   continuing past it. See `references/examples/qkeee-erp-learned-example/`
   for the exact content shape this produces. **Done when:** either a
   `qkeee-erp-learned/<env-tag>` skill is latched, or environment
   assessment has run and its findings are fully promoted (or a partial
   promotion is reported with the failed call named).
3. **Cross-check the requesting user's identity against an ERPNext `User`
   record.** Resolve the inbound chat/email identity to a real ERPNext
   user id/email — on every environment, every call, no exceptions (see
   `01-connectivity.md`'s requester-identity rule and
   `00-conventions.md`'s GRC baseline). There is no env-var or config
   default for `requested_by` — it does not exist as a fallback, so
   there is nothing to fall back to. Refuse to proceed on a requester
   this skill cannot resolve. Never invent or guess a requester identity,
   and never reuse a value resolved for an earlier call/turn — resolve it
   fresh from the message actually being handled right now.
   **Procedure, every turn that touches ERPNext:**
   1. Call `erp_discover(action="whoami")` (no network). It shows the
      gateway identity this turn is bound to.
   2. `resolved_sender_email` set → that email is the requester. The
      tools use it on every call; you never pass it.
   3. `resolved_sender_email` is `null`:
      - on `google_chat` or `email` → stop. The gateway lost the
        identity; every tool refuses. Tell the user to report it to an
        admin. Do not ask them who they are.
      - `HERMES_SESSION_SOURCE` is `kanban` (a Kanban worker) → the task
        has no recorded requester origin (made from the CLI or dashboard,
        or before origins were recorded). Call
        `kanban_block(kind="needs_input")` and name this gap. Do not ask
        for an email in a comment and do not take it from the card text.
      - elsewhere (CLI, Discord) → the tools refuse. Say that ERPNext is
        available from Google Chat only.
   In a Kanban worker, `whoami` also shows `HERMES_KANBAN_TASK` and
   `kanban_origin_task_id`: the task whose recorded sender is the
   requester.
   Never take the requester from memory, `session_search`, an earlier
   turn, the card text, or a display name like `[Nikhil Sharma]`.
   **The connector's own bot account (`dev-erp-hermes@…`, the "Authenticated
   as" user from `health`) is never a requester**; the gate refuses it.
   **Done when:** a real ERPNext `User` id/email is resolved from this
   turn's `whoami` and stated, or the request is refused (or the Kanban
   task blocked) with the reason named.
4. **Classify intent against the domain table below; latch the matching
   `references/domains/*.md` file into context.** More than one domain
   file may apply mid-conversation (e.g. a procurement onboarding that
   hands off to `doc-extraction`) — latch each as the conversation's
   actual needs shift, don't front-load every domain file speculatively.
   **For a single-domain, read-only lookup** (the common case — "fetch X
   for company Y," a status check, a list query): latch only
   `00-conventions.md` + `01-connectivity.md` + the one matching
   `domains/*.md` file. Skip `02-environment-assessment.md`,
   `03-spec-driven-execution.md`, and `grc-audit.md` unless step 2's
   staleness check or the intent itself actually needs them — loading
   every reference file on every turn regardless of complexity was a
   confirmed, measurable input-token cost in a token-usage review
   (observed: 4-5 `skill_view` calls per turn even for a repeat, narrow
   ask). **Done when:** exactly one matching `domains/*.md` file is
   latched and named in this reply, or the fallback-investigation path
   (`02-environment-assessment.md` / `non-erpnext-adapter.md`) is
   declared instead.
5. **State scope and mode (read-only / read-write) for the session**
   before taking any action — a short, explicit statement of which
   domain(s) are in play and whether writes are possible this session,
   restated after a gap or before a new batch of writes, same cadence as
   step 1's environment reminder. **Done when:** that statement appears
   in this reply.
6. **For anything beyond a single read-only lookup, run
   `references/03-spec-driven-execution.md` before acting.** Clarify,
   draft a crisp objective/plan/functional/technical spec, persist it,
   get it approved (or, if the user opted out of interactive review,
   silently persist it anyway and mark it autonomous), then execute
   against it. This is not optional under "run autonomously" — that only
   skips the approval *conversation*, never the spec itself. When a
   functional area or an installed app's behavior is unfamiliar, pull in
   `references/04-erp-doc-lookup.md` to ground the spec against real
   documentation rather than guessing. **Done when:** a spec exists at
   its persisted path, `approved`/`autonomous`, before
   `03-spec-driven-execution.md`'s own step 6 (execute) starts.

## Domain table

Classify the user's intent against this table, then latch exactly the
matching `references/domains/<slug>.md` file (and, if it's the reference's
first invocation this session, note its `ALLOWED_WRITE_DOCTYPES` from the
matching `scripts/domains/<slug>.py` module before proposing any write).

| Domain slug | Reference | Core doctypes / territory |
| --- | --- | --- |
| `hr-payroll` | `references/domains/hr-payroll.md` | Employee, Leave Application, Attendance, Job Opening/Applicant/Interview, Offer Letter, Onboarding/Separation, Payroll batch |
| `accounts` | `references/domains/accounts.md` | Journal Entry, Payment Entry, Sales/Purchase Invoice, Expense Claim, GST/TDS |
| `mis` | `references/domains/mis.md` | GL Entry, Trial Balance/P&L/Balance Sheet, Cost Center, Accounting Dimension — read-only, always |
| `sales` | `references/domains/sales.md` | Customer, Quotation, Sales Order, Delivery Note |
| `procurement` | `references/domains/procurement.md` | Supplier, Purchase Order, Request for Quotation, Supplier Quotation |
| `inventory` | `references/domains/inventory.md` | Item, Warehouse, Stock Entry, Stock Reconciliation, Material Request, Batch, Serial No |
| `manufacturing` | `references/domains/manufacturing.md` | BOM, Work Order, Job Card — **new, unvalidated, no write path shipped yet** (see that file) |
| `fixed-assets` | `references/domains/fixed-assets.md` | Asset, Asset Category, Asset Movement, Asset Maintenance, Asset Repair |
| `system-admin` | `references/domains/system-admin.md` | User, Role, Role Profile, Workflow, Custom Field, Webhook, Notification |
| `doc-extraction` | `references/domains/doc-extraction.md` | No doctypes — document/URL field extraction into a staged report, no connector |
| `grc-audit` | `references/domains/grc-audit.md` | Cross-cutting — audit-trail/compliance framing, latch alongside a functional domain, never alone |

**A doctype/feature outside all eleven** (a companion Frappe app — CRM,
Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments — or a
genuinely org-specific custom doctype) is fallback-investigation
territory: run `references/02-environment-assessment.md`'s investigation
method rather than guessing or refusing. **A system that isn't ERPNext at
all** (a third-party tool, an internal API) follows
`references/non-erpnext-adapter.md` instead.

## Files

- `references/00-conventions.md` — naming rules, non-negotiables, GRC
  baseline. Read first, applies to every domain.
- `references/01-connectivity.md` — REST/Frappe mechanics, env resolution,
  `discover.py` usage, the `qkeee-erp.env` design decision.
- `references/cli-cookbook.md` — worked `core/client.py`/`execute_write.py`
  invocations (copy-paste, not mechanics); latch only once a call is
  actually about to run.
- `references/02-environment-assessment.md` — the per-environment-tag
  cataloging procedure this activation sequence's step 2 depends on.
- `references/03-spec-driven-execution.md` — clarify → spec → persist →
  approve → execute procedure this activation sequence's step 6 depends
  on, for anything beyond a single read-only lookup.
- `references/04-erp-doc-lookup.md` — how to find official docs (or, for
  an unfamiliar functional area, a forum search) for whatever Frappe/
  ERPNext package or app a target environment actually runs.
- `references/non-erpnext-adapter.md` — procedure for a non-ERPNext
  target system.
- `references/domains/*.md` — one per domain slug above, lazily latched.
- `scripts/core/client.py` — the shared connector: reads, the requester
  gate, audit logging, transport, and the write-allowlist registry
  (`register_domain_allowlist()`). It has **no write function** and never
  imports `operations.py`.
- `scripts/core/operations.py` — the one write pipeline and the registry of
  named write operations, and the only write API: `run_operation()`,
  `prepare_only()` (render), `call_generic()` (keyword spelling for a
  generic operation). `scripts/execute_write.py` is its only CLI
  (`--list-ops`), `scripts/core/confirm_token.py render` its render step.
  Dependencies run one way: domains → operations → client.
- `scripts/domains/*.py` — eight modules for the eleven domains: `mis`'s
  registers an empty allowlist; `doc-extraction` has no connector,
  `manufacturing` no write path yet, and `grc-audit` is review-only, so
  those three have no module. Each declares its
  allowlist, operations and gated reads; none has a write function of
  its own.
- `scripts/discover.py` — live metadata: `meta`, `resolve`, `modules`,
  `apps`, and `preflight` (the write-readiness gate run before every
  create a spec makes). Usage and the whose-permission table:
  `references/01-connectivity.md`.
- `scripts/init_bot.py` — admin-invoked, one-time provisioning helper
  (not part of this associate's normal conversational flow); provisions
  the `Qkeee Bot` Role, the `Qkeee Bot Audit Log` doctype, and the role's
  read on Module Def and Workflow.
- `scripts/doctype_defs.py` — the Role/Audit-Log create payloads
  `init_bot.py` provisions from.
- `scripts/core/memory_promote.py` — redacts + formats findings into
  `qkeee-erp-learned/<env-tag>` content and a `skill_manage`/`memory`
  tool-call plan (see activation step 2 above). Does not call those tools
  itself — see the module's own docstring for why it can't.
- `references/examples/qkeee-erp-learned-example/` — the exact file
  shapes `memory_promote.py` produces, generated (not hand-written) from
  illustrative findings — no live `skill_manage` call was exercised to
  create it; see that directory's `README.md`.
- `qkeee-erp-associate.env.example` — template for the credentials file,
  `$HERMES_HOME/plugin-data/qkeee-erp/qkeee-erp.env` (gateway-side, read by
  the `qkeee-erp` plugin; operator-maintained).
- `scripts/core/kanban_origin.py` — the Kanban task → requester origin
  store the `qkeee-erp` plugin writes and the tools resolve.
- `references/governance.md` — operator/maintainer material: why this
  skill is externally-owned (curator-drift protection) and which
  capabilities are actually code-enforced vs. still prompt discipline.
  Read once at install or when auditing this skill's own health — not
  needed for a normal session.
