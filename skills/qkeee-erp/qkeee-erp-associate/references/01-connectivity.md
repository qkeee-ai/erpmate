# Connectivity: REST/Frappe, env resolution, erp_discover

The mechanics every domain shares — auth, the environment/tag model, env
var resolution, and metadata discovery. Domain judgment (what counts as a
valid GST return, whether an offer letter needs a second approval) does
NOT belong here — that lives in each `references/domains/<slug>.md` file.
If `qkeee-erp-associate` ever needs to target a different ERP backend,
this file and `qkeee_erp.core.client` are what change; the domain files
and `00-conventions.md` don't (they're written to be ERP-agnostic in
substance, ERPNext specifics called out as pointers). Worked tool
calls — the copy-paste call shapes, not the mechanics behind them —
live in `tool-cookbook.md`, latched only once a call is actually about to
run.

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** in the ERPNext UI, or by an Operator in the bot-user
provisioning flow (see
`references/domains/system-admin.md`). **Must be a dedicated bot/
integration user, never a human's personal login** — see
`00-conventions.md`'s GRC baseline for why.

## Environment / tag model

Config is tagged, not a fixed dev/test/qa/prod enum. At install time the
frontmatter declares exactly one literal tag, `DEFAULT`
(`QKEEE_ERP_DEFAULT_BASE_URL`/`_API_KEY`/`_API_SECRET`). A user who wants a
different first tag name, or a second/third environment later, sets that
tag's three vars themselves at runtime — this skill walks them through
naming and var-setting, it doesn't declare the vars for them:

| Variable | Purpose |
| --- | --- |
| `QKEEE_ERP_<TAG>_BASE_URL` | e.g. `https://org.erpnext.com` |
| `QKEEE_ERP_<TAG>_API_KEY` | API key for that site/user |
| `QKEEE_ERP_<TAG>_API_SECRET` | API secret for that site/user |
| `QKEEE_ERP_<TAG>_ALLOW_INSECURE` | OPTIONAL. Set `1` to allow a non-`https://` base URL (local/dev only — `get_env_config()` refuses plaintext by default since credentials go in the clear otherwise) |

There is no `_DEBUG` var — read audit logging is unconditional (every read
logs to `Qkeee Bot Audit Log`, no per-tag opt-in), so there is no debug flag
to resolve. See `00-conventions.md`'s GRC baseline.

There is also no `_REQUESTED_BY` or `_ENV_CLASS` var — see below.

`<TAG>` is uppercased/sanitized from whatever the user names it (`qa`,
`client-a-prod`, etc). Adding a second/third environment is a runtime
action for the Operator: they name a new tag, append its three vars to
`qkeee-erp.env` (below), and, if wanted, switch the plugin's active
Instance. The active Instance and the write mode are two separate plugin
settings (`plugins.entries.qkeee-erp.settings`, Operator-only) —
switching Instance never also changes write access. You learn both from
`erp_discover(action="health")`; pass `tag` only when the user names
another Instance.

**Requester identity: one rule, every tag, no config.** `requested_by` has
exactly one source on every tag, PROD or not: the live inbound channel
identity of whoever sent the message this call is answering — the Google
Chat/Teams/Slack sender's own work email, the email channel's From address,
whatever the platform actually hands over for "who sent this." Resolve it
fresh on every single call (never cache or reuse a value from an earlier
call in the same conversation), confirm it as a real ERPNext `User` (already
enforced in code — `_validate_prod_requester()` in `qkeee_erp.core.client` refuses
a call outright without a validated requester, on every tag). The
`qkeee_erp` gateway tools take the requester only from the gateway: the
session's sender email (Google Chat, Email), or in a Kanban worker the
sender recorded for the task's origin (`core/kanban_origin.py`).
`erp_discover(action="whoami")` shows it as `resolved_sender_email`. No
tool takes a requester argument; with no sender the tools refuse, and a
Kanban worker blocks the task as `needs_input`. An Operator running
`hermes qkeee-erp …` on the gateway host passes `--requested-by`; inside a
session, the gate refuses any requester other than the session's sender. The connector refuses to run inside `execute_code`
(no session identity there). See `SKILL.md` step 3 for the per-turn
procedure. Never invent, guess, or fall back to any standing default to get
past this — there is no default to fall back to.

## Credentials — `qkeee-erp.env`, gateway-side

The ERPNext credentials live in one file,
`$HERMES_HOME/plugin-data/qkeee-erp/qkeee-erp.env` (mode 600), read only
by the `qkeee-erp` plugin in the gateway process. One file holds every
tag's vars (`QKEEE_ERP_<TAG>_BASE_URL` / `_API_KEY` / `_API_SECRET`); a
new Instance is a new trio, never a second file. No credential is passed
to a terminal or `execute_code` sandbox, and the gateway guard blocks any
command that reaches for the file. The Operator maintains it; the
template is `qkeee-erp-associate.env.example`.

**Ask the user to check a value in `qkeee-erp.env` themselves,
out-of-band, whenever it needs confirming.** This file is deliberately
**not** the profile's main `.env` — keeps credentials physically separate
from any LLM-provider secret that might live there. **Never read its
contents into your own context to "confirm" it, and never compose a
command that embeds a raw secret value** — that's exactly what reading
it into context to work around a missing-var error looks like.

## Discovering a doctype's live shape — `erp_discover`

Resolve a doctype's field/shape against the live instance first
(Non-negotiable 4 in `00-conventions.md`) — never propose it from general
ERPNext knowledge alone:

- `erp_discover meta "<DocType>"` — full live field list from Frappe's
  merged meta (`frappe.desk.form.load.getdoctype`: the DocType plus its
  Custom Fields and Property Setters). Per field: mandatory flag, Link
  target, and the conditional keys (`mandatory_depends_on`, `depends_on`,
  `read_only_depends_on`, `fetch_from`, `permlevel`, `length`,
  `non_negative`). Per doctype: `autoname`, `naming_rule`, `title_field`,
  `naming_series_options`, `conditional_mandatory[]`, `active_workflows`
  (`null` = unknown). If getdoctype fails it falls back to the bare
  `DocType` record and says `custom_fields_merged: false`: custom fields
  are then missing, so never conclude a field doesn't exist.
  Authoritative over any doc or memory of a prior instance.
- `erp_discover resolve "<DocType>"` — module + owning app +
  submittable/custom flags. Run before assuming any doctype is uncovered
  custom territory.
- `erp_discover(action="preflight", doctype="<DocType>", payload={<json>})` — the
  write-readiness gate, once per doctype before any create: meta source,
  mandatory fields against the payload, conditional mandatory fields to
  confirm, naming (incl. a settings rule — Employee: HR Settings
  `emp_created_by`), active workflow, owning and custom apps. `ready:
  false` lists `blockers`; permission problems come back as `gaps[]` in
  the `health` shape. `03-spec-driven-execution.md` step 2 pastes it into
  the spec.
- `erp_discover modules` — installed-app inventory via a `Module Def` list
  read. `erp_discover apps` mirrors the Help → About dialog and includes
  version numbers `modules` can't derive (the `get_versions` RPC). Either
  can fail on the **bot's** permissions — `health` names that gap — but
  never on the requester's. Ask the user to paste Help → About only if
  exact versions matter and both fail. See `02-environment-assessment.md`
  for the full cataloging procedure this feeds.

### Whose permission each discovery read needs

The one table; other files link here. The gate checks the **requester**;
every HTTP call is sent as the **bot**, so the bot needs the read too.
`DocType`, `Module Def` and the `get_versions` RPC are Environment
Metadata (agents ADR 0001, a closed list): the requester's own permission
is never checked for them, but the requester must still be given and
bound to the session.

| Capability | Used by | Requester needs | Bot needs (role) | Effect if missing |
| --- | --- | --- | --- | --- |
| Merged meta (getdoctype) | `meta`, `resolve`, `preflight`, schema mapping | nothing (ADR 0001) | call getdoctype — any logged-in user on stock Frappe | falls back to bare DocType; `custom_fields_merged: false`; preflight not ready |
| Bare DocType record | `meta` fallback only | nothing (ADR 0001) | read `DocType` — System Manager only on stock, so a correct bot 403s | no meta at all; ask the user to paste fields from Customize Form |
| Module Def list | `modules`, `resolve` (app), `preflight` (custom apps) | nothing (ADR 0001) | read `Module Def` (`Qkeee Bot`, granted by `hermes qkeee-erp init-bot`) | no app list; catalog partial; `health` gap `module_def_read` |
| Installed apps (get_versions) | `apps` | nothing (ADR 0001) | call get_versions — any logged-in user | no versions; use `modules` |
| Workflow list | `health` probe | — | read `Workflow` (`Qkeee Bot`, `hermes qkeee-erp init-bot`) | `health` gap `workflow_read`; preflight reads active workflows from merged meta instead |
| HR Settings | `preflight Employee` | read `HR Settings` (HR Manager on stock) | read `HR Settings` (a DocPerm on `Qkeee Bot`) | preflight not ready: cannot tell whether `employee_number` is mandatory |
| Any business read/write | domain work | the doctype permission (gate) | the same, as DocPerms on `Qkeee Bot` (ADR 0003) | refused by the gate, or 403 from ERPNext |

A missing grant is reported with the role-gap prompt
(`00-conventions.md`), never in the agent's own words, and never fixed by
the agent itself.

## Query cost: list endpoint vs. single-resource GET

`core.client.query_resource()` (the list endpoint, with `filters`/`fields`)
silently drops Table-type (child-table) fields even when named in
`fields` — a structural Frappe behavior, not a bug to work around.
`core.client.get_resource()` (single-resource GET) ignores `fields`
entirely and always returns the full doc, including child tables — the only
path that returns them, needed whenever a review step checks child-table
Link validity (line items, roles, permission rows). Use `query_resource()`
with explicit `fields` whenever child-table data isn't needed — routinely
20-25x cheaper (a few hundred bytes vs. several thousand). `get_resource()`
noise-strips audit/system metadata and presentation-only HTML fields by
default (`strip_noise=True`) — never strips Link fields or child tables.

Always check `has_more` on a `query_resource()` response before treating a
result set as complete — a truncated pull is the easiest way to produce a
report or review that looks right but doesn't reconcile.

## Built-in reports vs. hand-aggregated queries

Prefer `core.client.run_query_report()` (wraps
`frappe.desk.query_report.run`) over hand-aggregating raw rows whenever a
built-in ERPNext report already covers the need — it already implements
dimension filters, Finance Book gates, and currency conversion correctly; a
hand-rolled aggregation risks silently missing one of those. `filters` is a
plain dict of report-specific values; field names vary per report — confirm
the exact filter keys a given report expects by opening it in the ERPNext UI
once, since this generic endpoint doesn't self-document per-report filter
schemas.

## Working scratch (rare)

Any file this skill's tooling needs to write that isn't the final
deliverable — a staged artifact genuinely too large to hold in the
conversation — goes under `<profile>/workspace/qkeee-erp/<env-tag>/`,
plain file I/O, never `/tmp`. **Not `terminal.cwd`:** the local CLI
backend (this skill's primary usage path) deliberately ignores that
config key and always uses the launch directory — only gateway- and
cron-driven sessions bridge it into a fixed path. `<profile>/workspace/`
is the one directory that's stable regardless of which backend or
invocation mode is running, provisioned at profile creation alongside
`memories/`, `skills/`, etc. —
see `00-conventions.md`'s naming table. Most tasks need none of this:
Hermes' own session transcript already retains the working conversation, so
reach for scratch only when something is genuinely too bulky to keep in
context. Clean scratch files up once the task no longer needs them —
disposable, never assumed present next session.

**Task specs are the deliberate exception.** They target the working
directory directly instead of `<profile>/workspace/` — the whole point is
for the user to see the file sitting alongside their own project, not
tucked inside the Hermes profile. "Ignores the `terminal.cwd` config key"
above doesn't mean "writes nowhere near the working directory" — the local
CLI backend still writes relative to the real launch directory it started
from, and a spec uses exactly that. See
`references/03-spec-driven-execution.md`.

## Worked calls

The copy-paste tool calls (read-only shapes, the write flow, worked
write examples) are in `tool-cookbook.md`, not restated here. Latch that
file once a call is actually about to run.
