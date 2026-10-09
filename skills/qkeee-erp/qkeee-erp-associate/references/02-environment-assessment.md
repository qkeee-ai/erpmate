# Environment assessment procedure

Runs once per environment tag, on first contact with that tag (per the
activation sequence in `SKILL.md`), and again whenever the target instance
looks different from what durable memory says (a version bump, an app
that wasn't there before).

This is the *procedure* — what to check, in what order. It is not the
memory mechanism: that's `erp_discover promotion_plan` (redact +
format, then hand off to Hermes' `skill_manage` tool), writing into
`qkeee-erp-learned/<env-tag>` per step 6. Don't invent a bespoke
file-write step — use `erp_discover promotion_plan` and Hermes' native memory
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
- **Staleness mid-session.** A `erp_discover apps` version that doesn't
  match durable memory, an app installed/removed since last cataloged, or
  a `meta` call that disagrees with what's recorded — re-run the relevant
  step and update. Live metadata always wins over a prior session's
  memory.
- **Never complete.** The learned skill's `environment.md` says
  `catalog_complete: false`. The catalog was promoted with gaps still
  open, so it is missing data. Re-run the failed steps (the listed gaps
  name them) on every session until they pass, then promote again with
  no gaps. Never latch a partial catalog as if it were complete.

## Procedure

0. **Health and gaps.** `erp_discover health` confirms
   connectivity + auth, and probes, as the bot, what discovery needs
   (`capabilities`). Show each entry of `gaps[]` with the role-gap prompt
   (`00-conventions.md`), exactly as printed, before anything else. Keep
   the gaps: step 6 passes them to `erp_discover promotion_plan`. Report a later
   permission error as its own distinct failure mode, never lumped in
   with a connectivity failure. Whose permission each read needs: the
   table in `01-connectivity.md`.
1. **Installed apps + versions.** Run `erp_discover modules`, then
   `erp_discover apps` for version numbers `modules` can't derive. Both are
   Environment Metadata (agents ADR 0001): the requester's own
   permission is not checked, so an HR user can run them. They need the
   **bot** to read `Module Def` (and get_versions to be callable). If
   either fails, it is a bot gap that `health` already named: say so, do
   not guess the app list, and carry the gap into step 6. Ask the user to
   paste the Help → About dialog only if exact versions genuinely matter.
2. **Write readiness, before any create.** For every doctype a task will
   create, `erp_discover(action="preflight", doctype="<DocType>", payload={<json>})` checks
   mandatory and conditional fields, naming, an active workflow and the
   doctype's settings (`03-spec-driven-execution.md` step 2 pastes it into
   the spec). Not part of first-contact cataloging; listed here so the
   discovery calls live in one place.
3. **Which domains apply.** Cross-reference the installed-app list
   against the domain table in `SKILL.md`. A companion app (Frappe CRM,
   Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments, or
   an org-specific custom app) outside the eleven fixed domain slugs is
   fallback-investigation territory: catalog it like a custom app (see
   `non-erpnext-adapter.md`'s catalog convention). Never silently ignore
   it or invent a twelfth domain slug.
4. **Investigate an unfamiliar doctype or custom app** (what a seasoned
   ERPNext/Frappe SME actually does, in order):
   a. `erp_discover resolve "<DocType>"` — module, owning app,
      submittable/custom flags. Confirms whether it's core, a companion
      app, or genuinely custom.
   b. `erp_discover meta "<DocType>"` — live field list, mandatory flags,
      Link targets, from Frappe's merged meta (Custom Fields and Property
      Setters included — e.g. India Compliance's `gstin`/`gst_category`).
      If the output says `custom_fields_merged: false`, custom fields are
      missing from it: say so, and don't conclude a field doesn't exist.
      Never propose a shape without this.
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
6. **Record what was found.** Call
   `erp_discover(action="promotion_plan", findings={...}, summary="...")`:
   it redacts, formats, and plans the promotion of Frappe/ERPNext/app
   versions, the custom doctype catalog, and any non-ERPNext system notes
   into `qkeee-erp-learned/<env-tag>`'s references (`environment.md`,
   `doctypes-catalog.md`, `custom-apps/<slug>.md`, `non-erpnext/<slug>.md`
   — see `00-conventions.md`'s naming table), plus a one-line breadcrumb
   in `<profile>/memories/MEMORY.md` naming the environment tag and
   pointing at the full skill. The tool cannot issue the
   `skill_manage`/`memory` calls for you (they belong to your own turn's
   write-approval context); issue the calls it returns yourself, in
   order, and stop at the first failure. Pass every gap still open from
   step 0 as `findings.gaps`: the plan then writes `catalog_complete:
   false` and lists them, so the next session knows to re-assess.

## Out of scope

Not a general-purpose Frappe-app auditor. Doesn't reverse-engineer
business logic behind a custom app's doctypes beyond what live metadata
and the user's own explanation cover. If a specific investigated
capability becomes trusted and repeatedly used, that's a signal it
deserves a proper domain reference of its own (a twelfth domain slug,
deliberately added — see `00-conventions.md`), not a reason to grow this
procedure's scope indefinitely.
