# Environment assessment procedure

Runs once per environment tag, on first contact with that tag (per the
activation sequence in `SKILL.md`), and again whenever the target instance
looks different from what durable memory says (a version bump, an app
that wasn't there before).

This is the *procedure* — what to check, in what order. It is not the
memory mechanism: that's `scripts/core/memory_promote.py` (redact +
format, then hand off to Hermes' `skill_manage` tool), writing into
`qkeee-erp-learned/<env-tag>` per step 6. Don't invent a bespoke
file-write step — use `memory_promote.py` and Hermes' native memory
tools.

## When this runs

- **First contact with a tag.** Resolve the env tag, run `health`, then
  check whether a `qkeee-erp-learned/<env-tag>` skill already exists
  (Hermes' skill discovery surfaces it). If it exists, latch it like any
  other reference and skip to intent classification — don't re-run this
  procedure against an already-cataloged environment unless something
  looks stale.
- **No `qkeee-erp-learned/<env-tag>` skill found.** Run the full
  procedure below before anything substantive, then promote the findings
  (step 6).
- **Staleness mid-session.** A `discover.py apps` version that doesn't
  match durable memory, an app installed/removed since last cataloged, or
  a `meta` call that disagrees with what's recorded — re-run the relevant
  step and update. Live metadata always wins over a prior session's
  memory.

## Procedure

1. **Health check.** `core/client.py --tag <tag> health` confirms
   connectivity + auth, not query/write-time permission. Report a later
   permission error as its own distinct failure mode, never lumped in
   with a connectivity failure.
2. **Installed apps + versions.** Run `discover.py modules` first (plain
   REST read, always works). Run `discover.py apps` as a bonus for
   version numbers `modules` can't derive — treat it as opportunistic
   (`01-connectivity.md` notes it's a confirmed-blocked RPC method on at
   least one real instance). If `apps` fails, fall back to `modules`
   silently; ask the user to paste the Help → About dialog only if exact
   versions genuinely matter.
3. **Which domains apply.** Cross-reference the installed-app list
   against the domain table in `SKILL.md`. A companion app (Frappe CRM,
   Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments, or
   an org-specific custom app) outside the eleven fixed domain slugs is
   fallback-investigation territory: catalog it like a custom app (see
   `non-erpnext-adapter.md`'s catalog convention). Never silently ignore
   it or invent a twelfth domain slug.
4. **Investigate an unfamiliar doctype or custom app** (what a seasoned
   ERPNext/Frappe SME actually does, in order):
   a. `discover.py resolve "<DocType>"` — module, owning app,
      submittable/custom flags. Confirms whether it's core, a companion
      app, or genuinely custom.
   b. `discover.py meta "<DocType>"` — live field list, mandatory flags,
      Link targets. Never propose a shape without this.
   c. For a companion Frappe-ecosystem app: fetch its GitHub README/docs
      (most live under the `frappe` GitHub org) for what it's for, its key
      doctypes, and typical workflows — cross-check against the live
      metadata from step (b); note any discrepancy rather than silently
      preferring one source.
   d. For a genuinely org-specific custom app with no public repo: build
      the understanding from live metadata + whatever the user explains,
      and say so explicitly rather than inventing an upstream source.
   e. **Reproduce red before diagnosing an unexpected result.** A **red**
      result — an error, a 403, a blocked call — needs a concrete repro
      that actually fails on the exact thing that's wrong before it gets
      explained, not a plausible-sounding guess about why it might be
      expected. Read the rest of the same tool output first: a WARN or
      status field printed alongside the error outranks any prior
      assumption about what should be true (a `privileged: true` WARN one
      line above a 403 means the 403 isn't a low-privilege story, however
      intuitive that story feels) — never call a failure "expected"
      without having read everything the call itself already said.
5. **Cross-check the requester's identity.** Per the activation
   sequence's step 2 (`SKILL.md`) — runs every session, not just first
   contact. First contact is also where "is this bot account a dedicated
   service identity, not a personal login" gets its first check
   (`00-conventions.md`'s GRC baseline).
6. **Record what was found.** Run `memory_promote.py` (or call its
   `build_promotion_plan()` directly): redact, format, and promote
   Frappe/ERPNext/app versions, the custom doctype catalog, and any
   non-ERPNext system notes into `qkeee-erp-learned/<env-tag>`'s
   references (`environment.md`, `doctypes-catalog.md`,
   `custom-apps/<slug>.md`, `non-erpnext/<slug>.md` — see
   `00-conventions.md`'s naming table), plus a one-line breadcrumb in
   `<profile>/memories/MEMORY.md` naming the environment tag and pointing
   at the full skill. `memory_promote.py` can't issue the
   `skill_manage`/`memory` tool calls itself (it runs as a subprocess
   script — see its own docstring); issue the calls it returns yourself,
   in order, and stop at the first failure.

## Out of scope

Not a general-purpose Frappe-app auditor. Doesn't reverse-engineer
business logic behind a custom app's doctypes beyond what live metadata
and the user's own explanation cover. If a specific investigated
capability becomes trusted and repeatedly used, that's a signal it
deserves a proper domain reference of its own (a twelfth domain slug,
deliberately added — see `00-conventions.md`), not a reason to grow this
procedure's scope indefinitely.
