# ERP documentation lookup

Where to find authoritative docs for whatever Frappe/ERPNext package or
app a target environment runs — used when live metadata (`discover.py`,
`01-connectivity.md`) tells you *what* a field/doctype is but not *why*,
or a functional area is unfamiliar. Live metadata always wins over
documentation on a shape question (Non-negotiable 4, `00-conventions.md`);
docs are for behavior/workflow context metadata can't give.

## Step 1 — identify what's installed

Already part of `02-environment-assessment.md` step 2 — don't re-run it,
reuse the result:

- `discover.py modules` — installed-app inventory (always works).
- `discover.py apps` — same, plus version numbers, opportunistic (a
  whitelisted RPC blocked on at least one real instance — fall back to
  `modules` silently).
- If both are unavailable and an exact version matters, ask the user to
  paste ERPNext's own Help → About dialog.

Record the result (package/app name + version) in
`qkeee-erp-learned/<env-tag>/references/environment.md` via
`memory_promote.py` — that step already promotes this, so a doc lookup
should never need to rediscover it mid-session.

## Step 2 — map app to doc source

Frappe-ecosystem docs live at predictable per-app subpaths under
`docs.frappe.io` — convention, not guaranteed for every app. Confirm the
page exists before citing it.

| Installed app | Docs |
| --- | --- |
| `frappe` (framework) | `https://docs.frappe.io/framework/` |
| `erpnext` | `https://docs.frappe.io/erpnext/` |
| `hrms` (Frappe HR) | `https://docs.frappe.io/hr/` |
| `crm` (Frappe CRM) | `https://docs.frappe.io/crm/` |
| `helpdesk` | `https://docs.frappe.io/helpdesk/` |
| `lms` | `https://docs.frappe.io/lms/` |
| `insights` | `https://docs.frappe.io/insights/` |
| `wiki` | `https://docs.frappe.io/wiki/` |
| `drive` | `https://docs.frappe.io/drive/` |
| `gameplan` | `https://docs.frappe.io/gameplan/` |
| `builder` | `https://docs.frappe.io/builder/` |
| `payments` | check `https://docs.frappe.io/payments/` first, fall back to the app's GitHub README (`frappe/payments`) — thinner doc coverage |

For an app not in this table (a companion app not yet common enough to
list, or a docs subpath that 404s): fetch its GitHub README instead —
most live under the `frappe` GitHub org (`github.com/frappe/<app-slug>`),
same as `02-environment-assessment.md` step 4c. Note which source you
actually used, in the spec or response.

**Version-specific behavior.** `docs.frappe.io` tracks current/latest by
default. Where the cataloged version is materially older (a
major-version gap), say so plainly rather than presenting current docs as
authoritative for an old instance — cross-check against live metadata
for anything version-sensitive (a field docs describe but `discover.py
meta` doesn't show, or vice versa).

## Step 3 — unfamiliar functional area: search, don't guess

For a domain question docs don't answer directly (an edge-case workflow,
"how do other orgs typically handle X in ERPNext"), search
`discuss.frappe.io` rather than answering from general LLM knowledge or
guessing at ERPNext's intended behavior. Use whatever web-search/fetch
tool this harness exposes (Non-negotiable 8, `00-conventions.md` —
prefer a harness-native tool over improvising one). Treat a forum answer
as community input, not authoritative the way official docs or live
metadata are — say so when citing one, and prefer an answered/accepted
thread or one from Frappe staff when results disagree.

## Out of scope

Not a general web-research capability. Scope stays: identify the
installed package/version, find its official docs, fall back to a
targeted forum search only when docs genuinely don't cover the question.
A doc/forum finding never overrides live metadata or an explicit user
statement (Non-negotiable 4) — it fills in *why*, never *what's actually
there*.
