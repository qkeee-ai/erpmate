# Spec-driven execution

Every non-trivial task gets a written spec before any domain action
starts: clarify, draft, persist, approve, execute. This procedure sits
between the activation sequence (`SKILL.md`) and the domain reference
doing the actual work.

## Kanban instead of a spec file

A spec file suits a single-session, single-actor task. When the work
instead **crosses agent boundaries, needs to survive a restart, needs
human input mid-task, or needs to stay discoverable later** — a
multi-day payroll run with checkpoints, a bulk data migration, an
onboarding checklist spanning HR + system-admin — propose Hermes' Kanban
feature (`kanban_create`/`kanban_show`, `/kanban` CLI, dashboard) instead
of, or alongside, a flat spec file. A spec file has no board view and
nothing else can discover or pick it up later; Kanban is durable,
human-visible, and built for that shape of work. Write the spec below
when the work fits one sitting with one actor — don't reach for Kanban by
default.

**Two rules once a board is in play.** *Refer by name*: in anything a
human reads — narration, a status update, a hand-off — name a card by
its title, never by a bare id ("the GSTIN backfill card," not "card
#14"); a wall of ids is illegible, a name reads at a glance. *The board
is an index, not a store*: each card gists its own work and links out to
where the detail actually lives (a spec file, a domain doctype, a
comment thread) — a decision or a finding lives in exactly one place,
never restated across the card and its detail going out of sync.

## When a spec is required

**Required:** any write (create/update/submit/cancel/delete), any
multi-step investigation (environment assessment, cross-domain work, a
report touching more than one doctype), anything the user frames as a
project/task rather than a single question, and anything run
autonomously — autonomous mode doesn't exempt a task from this.

**Skippable:** a single read-only lookup answerable in one or two
`query`/`get`/`report` calls ("what's the leave balance for X", "pull
the last 5 POs for supplier Y"). Don't wrap a one-shot read in spec
overhead. When in doubt, write the spec — a short one costs little; an
unreviewed multi-step write costs a lot.

## Procedure

1. **Clarify.** Resolve ambiguity in the request before drafting — target
   environment tag (if not already resolved per `SKILL.md`'s activation
   sequence), which domain(s) it touches, expected scope of a write
   (how many records, which doctype), and any constraint the user implied
   but didn't state. Ask; don't guess a scope-defining detail. **More
   than one open question: ask the whole frontier in one round, not one
   at a time.** The frontier is every question whose prerequisites are
   already settled — what you can ask now without guessing at an answer
   you haven't heard yet. Number each, give your own recommended answer
   alongside it, then wait for the user's reply before drafting; don't
   drip questions across turns when they could all be asked together.
   **Done when:** target tag, domain(s), and write scope are each
   resolved from the user's actual answer, not assumed.
2. **Draft the spec.** Use the template below. Keep it crisp — this is a
   working contract, not a report. State plainly where a functional
   detail is still unconfirmed against live metadata (Non-negotiable 4,
   `00-conventions.md`) rather than papering over the gap. **Done when:**
   every template section below is filled, or explicitly marked
   unconfirmed rather than left blank.
3. **Persist it — in the session's actual working directory.** Write the
   spec file to `./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md`, resolved
   relative to whatever directory this session is actually running in:
   - **Gateway/cron-driven session:** `01-connectivity.md` notes these
     backends bridge Hermes' `terminal.cwd` config key into a real
     path — resolve against that.
   - **Local CLI session (this skill's primary usage path):**
     `01-connectivity.md`'s "Working scratch" section says this backend
     ignores the `terminal.cwd` config key but still always writes
     relative to the launch directory — the real OS working directory
     the session started from, which *is* the project/task directory the
     user is sitting in. Use it directly with plain file I/O. Don't
     route through the config key here, and don't redirect into
     `<profile>/workspace/...` — that tier is disposable scratch too
     bulky to keep in context, not a spec the user is meant to see beside
     their own files.
   - **Neither resolves to a writable path** (a sandboxed/read-only
     launch dir): fall back to
     `<profile>/workspace/qkeee-erp/<env-tag>/specs/<slug>-<YYYYMMDD-HHMM>.md`
     and say so explicitly — the exception, not the default.

   Never put a spec under `qkeee-erp-learned/*` or `memories/MEMORY.md` —
   those are durable environment knowledge, not per-task working state; a
   spec is disposable once its task closes. **Done when:** the file
   exists at its resolved path and that path is stated to the user.
4. **Seek approval — unless running autonomously (see below).** Present
   the spec's objective/plan/steps to the user, plainly, and wait for an
   explicit go-ahead or edits. Don't start step 5 on a spec that hasn't
   been approved or silently-generated (autonomous mode). **Done when:**
   the user has given an explicit go-ahead or edits, or (autonomous mode)
   the header carries the `Approval: autonomous (…)` mark.
5. **Update on feedback.** Fold every user edit into the persisted file
   itself (not just into conversation) before proceeding — the file on
   disk is the record of what was actually approved, so it must match
   what execution follows. Re-confirm after a substantive edit; a typo
   fix doesn't need a second round. **Done when:** the persisted file
   matches what was actually approved, word for word on any changed
   scope.
6. **Execute against the spec.** Follow the technical steps in order.
   Each domain's own procedure (`references/domains/<slug>.md`) and every
   non-negotiable in `00-conventions.md` still apply in full — the spec
   sequences the work, it doesn't relax save-draft-then-review-then-submit,
   the write-allowlist gate, or anything else already enforced in
   `scripts/core/client.py`. **Done when:** every technical step has run,
   or is marked deviated-with-reason in the spec file — never silently
   skipped.
7. **Close out.** Append a short "Outcome" section to the same spec file
   (what actually happened, any deviation from plan and why) before
   telling the user the task is done. Leave the file in place — it's the
   audit trail for this task, not deleted on success. Working-scratch
   files are disposable across *sessions* (`01-connectivity.md`), not
   mid-task. Tell the user per `00-conventions.md`'s
   `## Report-back` contract, not a free-form summary. **Done when:** the
   Outcome section is appended to the spec file, before — not after —
   telling the user the task is done.

## Autonomous mode

A user asking this associate to "just do it," run unattended, or skip
check-ins does not skip spec creation — it only skips step 4's
interactive approval gate. Still draft the spec (steps 1-3), persist it
to the same path, mark it `Approval: autonomous (user opted out of
interactive review — <short quote or paraphrase of their instruction>)`
in the header, then proceed straight to execution. This keeps every
autonomous run auditable against a written plan exactly like an
interactively-approved one — the only difference is who signs off and
when.

A write's own non-negotiables (mode check, requester resolution,
allowlist, double-confirm for wide-blast-radius actions) are unaffected
by autonomous mode. A double-confirm requirement still requires an
actual second confirmation — in autonomous mode that means the user's
original instruction must have explicitly covered that specific action,
not a blanket "go ahead."

## Spec template

```markdown
# Spec: <short task name>

- Env tag: <tag>            Mode: <read-only | read-write>
- Domain(s): <slug, slug>
- Requested by: <resolved ERPNext user id/email>
- Approval: <pending | approved <date> | autonomous (<why>)>

## Objective
One or two sentences: what this task accomplishes and why.

## Plan
Numbered, high-level. Each line maps to one or more technical steps below.

## Functional steps
What happens in ERPNext terms — doctypes touched, records read/created/
updated/submitted, reports run, any approval/workflow implication.

## Technical steps
The actual calls: which `core/client.py` or `domains/<slug>.py`
functions, in order, with the doctype/filters/payload shape. Note where a
field/doctype shape still needs live confirmation via `discover.py`
(`01-connectivity.md`) before the call can be finalized.

## Risks / open questions
Anything ambiguous, anything requiring a double-confirm
(`00-conventions.md`'s GRC baseline), anything dependent on an
unconfirmed assumption.

## Outcome (filled in at close-out)
What happened, any deviation from plan and why, links/names of records
touched.
```
