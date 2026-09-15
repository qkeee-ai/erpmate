# Connectivity: REST/Frappe, env resolution, discover.py

The mechanics every domain shares — auth, the environment/tag model, env
var resolution, and metadata discovery. Domain judgment (what counts as a
valid GST return, whether an offer letter needs a second approval) does
NOT belong here — that lives in each `references/domains/<slug>.md` file.
If `qkeee-erp-associate` ever needs to target a different ERP backend,
this file and `scripts/core/client.py` are what change; the domain files
and `00-conventions.md` don't (they're written to be ERP-agnostic in
substance, ERPNext specifics called out as pointers). Worked CLI
invocations — the copy-paste call shapes, not the mechanics behind them —
live in `cli-cookbook.md`, latched only once a call is actually about to
run.

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** in the ERPNext UI, or via `scripts/init_bot.py`/
`ensure_bot_user.py`'s provisioning flow (see
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
action: walk the user through naming a new tag and appending its three vars
to `qkeee-erp.env` (below), then offer to switch `qkeee_erp.active_env`.
`qkeee_erp.active_env` and `qkeee_erp.mode` stay global
`metadata.hermes.config` values — switching environments should never
silently also change write access, so `mode` requires its own explicit
confirmation independent of which tag is active.

**Requester identity: one rule, every tag, no config.** `requested_by` has
exactly one source on every tag, PROD or not: the live inbound channel
identity of whoever sent the message this call is answering — the Google
Chat/Teams/Slack sender's own work email, the email channel's From address,
whatever the platform actually hands over for "who sent this." Resolve it
fresh on every single call (never cache or reuse a value from an earlier
call in the same conversation), confirm it as a real ERPNext `User` (already
enforced in code — `_validate_prod_requester()` in `core/client.py` refuses
a call outright without a validated requester, on every tag), and pass it
explicitly via `--requested-by` / `requested_by=`. Never invent, guess, or
fall back to any standing default to get past this — there is no default to
fall back to.

## Env resolution — why `qkeee-erp.env`, not native frontmatter passthrough

**A deliberate exception, documented here so it doesn't read as an
oversight on review.** Hermes' own `required_environment_variables`/
`required_credential_files` frontmatter auto-registers passthrough into
`execute_code`/`terminal` sandboxes — but those sandboxes strip *all* env
vars by default, and only names a skill *statically* declares in
frontmatter survive the strip. That mechanism only covers the single
`DEFAULT`-tag case: a user-chosen tag name (`qa`, `client-a-prod`, a
second/third environment added at runtime) can never be declared ahead of
time in static frontmatter, so `QKEEE_ERP_<TAG>_*` for any tag other than
the one named at install time would silently never reach the sandbox — even
when correctly set in the profile's real `.env`.

`qkeee-erp.env` fixes this structurally: `core/client.py`'s
`_qkeee_env_file_path()` resolves `$HERMES_HOME/qkeee-erp.env` and reads it
**directly**, bypassing the sandbox's env stripping and Hermes'
`env_passthrough` allowlist entirely — the script never needs the values to
have survived sandbox stripping in the first place, because it isn't
reading `os.environ` for them (it only falls back to `os.environ` if the
file is absent, for back-compat with a manually-exported shell). One file
holds every tag's vars at once, and `HERMES_HOME` itself is unconditionally
forwarded into every sandbox child regardless of skill declarations, so
it's a reliable anchor even when the tag-specific vars themselves aren't.

**Where a script only ever needs the `DEFAULT` tag** (e.g. `init_bot.py`'s
elevated key, which is admin-invoked and one-time, not a per-conversation
tag switch), prefer the native frontmatter declaration instead of this
custom `.env` read — the exception above exists specifically for the
multi-tag case, not as a blanket preference over the native mechanism. Use
the `${HERMES_SKILL_DIR}` template token for any path a script needs to
itself, rather than a hardcoded relative path.

**Ask the user to check a value in `qkeee-erp.env` themselves,
out-of-band, whenever it needs confirming.** This file is deliberately
**not** the profile's main `.env` — keeps credentials physically separate
from any LLM-provider secret that might live there. **Never read its
contents into your own context to "confirm" it, and never compose a
command that embeds a raw secret value** — that's exactly what reading
it into context to work around a missing-var error looks like.

## Discovering a doctype's live shape — `discover.py`

Resolve a doctype's field/shape against the live instance first
(Non-negotiable 4 in `00-conventions.md`) — never propose it from general
ERPNext knowledge alone:

- `discover.py resolve "<DocType>"` — module + owning app +
  submittable/custom flags. Run before assuming any doctype is uncovered
  custom territory.
- `discover.py meta "<DocType>"` — full live field list, mandatory flags,
  Link targets. Authoritative over any doc or memory of a prior instance.
  **Known prerequisite:** both calls hit `GET /api/resource/DocType/<name>`,
  which requires System Manager–level read access — a correctly
  least-privileged steady-state bot account can 403 here. Don't present
  that as a connectivity bug; tell the user their bot account needs read
  access to `DocType`, or ask them to paste the relevant field list from
  the Customize Form screen instead.
- `discover.py modules` — installed-app inventory via a plain `Module Def`
  list read (works broadly). `discover.py apps` mirrors the Help → About
  dialog and includes version numbers `modules` can't derive, but its
  whitelisted RPC method can be blocked (`PermissionError: not whitelisted`)
  on some instances — treat it as opportunistic, fall back to `modules`
  silently, and only ask the user to paste Help → About if exact versions
  matter and both fail. See `02-environment-assessment.md` for the full
  cataloging procedure this feeds.

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

## Harness capability discovery

If the host harness exposes a way to enumerate installed tools/skills and
one already does authenticated HTTP well, prefer it over shelling out to
`core/client.py`. `core.client.discover_harness_http_tool()` is a stub that
reports "nothing pre-discovered" — this skill's own bundled connector is
always the fallback, never assume discovery itself is supported; degrade
gracefully if it isn't.

## CLI usage

`core/client.py` and each `domains/<slug>.py` module are runnable
directly for manual/ad hoc use — the actual invocations (subcommand
list, exact path, read-only copy-paste blocks, write examples) are in
`cli-cookbook.md`, not restated here. Latch that file once a call is
actually about to run.
