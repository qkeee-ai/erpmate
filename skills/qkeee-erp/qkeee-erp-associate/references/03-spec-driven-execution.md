# Spec-driven execution

Every non-trivial task gets a written spec before any domain action
starts: clarify, draft, persist, approve, execute. This procedure sits
between the activation sequence (`SKILL.md`) and the domain reference
doing the actual work.

## Kanban instead of a spec file

A spec file suits a single-session, single-actor task. When the work
instead **crosses agent boundaries, needs to survive a restart, needs
human input mid-task, or needs to stay discoverable later** — an
onboarding checklist spanning HR + system-admin, work that genuinely has
no ERPNext writes of its own on the critical path — propose Hermes'
Kanban feature (the `kanban_*` tools: `kanban_create`, `kanban_show`,
`kanban_list`) instead of, or alongside, a flat spec file. A spec file has no board view and
nothing else can discover or pick it up later; Kanban is durable,
human-visible, and built for that shape of work. Write the spec below
when the work fits one sitting with one actor — don't reach for Kanban by
default.

**Not for ERPNext execution work itself (`00-conventions.md`).** A
multi-day payroll run with checkpoints or a bulk data migration — the
exact shape this section used to point at `kanban_create` for — is
ERPNext execution work: the board's auto-decomposer splits it into
sub-cards no worker can run (`.scratch/qkeee-erp-kanban-execution-steering/issues/01-*`),
a live finding, not a hypothetical. For that shape, stay in one spec/one
session and use the job-level email approval path instead (agents ADR
0008, `tool-cookbook.md`'s "Starting a kanban job" example): one email
covers every batch in the plan, so a multi-batch job no longer needs
either per-batch chat codes or a kanban card to stay coherent across a
restart. Kanban remains the right tool only for work that is genuinely
not an ERPNext write sequence (routing a checklist to the right humans,
tracking non-ERPNext steps).

**Cards go through the tools.** Create, read and route cards only with
the `kanban_*` tools; they run in the gateway, which records each card's
requester for its workers. A card you create lands in triage: tell the
user it waits there to be specified and split, then reaches workers.
When the `kanban_*` tools are missing from your tool list, say so (the
Operator runs `hermes qkeee-erp setup --apply-profile-fixes`) and keep
the plan in a spec file.

**The board runs the work.** Once the user picks a board for a task, the
work runs as its cards. Work a card in chat only when the user asks for
that, and record the result on the card with `kanban_comment`, so the
board stays the index.

**Two rules once a board is in play.** *Refer by name*: in anything a
human reads — narration, a status update, a hand-off — name a card by
its title, never by a bare id ("the GSTIN backfill card," not "card
#14"); a wall of ids is illegible, a name reads at a glance. *The board
is an index, not a store*: each card gists its own work and links out to
where the detail actually lives (a spec file, a domain doctype, a
comment thread) — a decision or a finding lives in exactly one place,
never restated across the card and its detail going out of sync.

**One anchor spec per objective, one per-card spec per card.** For a
multi-card objective, write one **anchor spec** at the usual path
(`./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md`): the objective, the
global Deviations log, and the planned batch/card count. Then write one
**per-card spec** per kanban card
(`./qkeee-erp-specs/<slug>-card-<NN>-<YYYYMMDD-HHMM>.md`): that card's
own scope, its actual batch boundary, and its confirmation/token state.
Each card's `kanban_comment` links to its own per-card spec path rather
than restating that detail inline — the card stays an index, per the
rule above. This is a parallel, human-readable record alongside the
doctype-backed per-step ledger (issue 16), not a replacement for it: a
human can read what a card is doing without querying ERPNext. It also
makes a divergence between the anchor's planned count and a card's
actual batch boundary a diffable fact instead of something only a human
re-reading the chat transcript would catch.

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
   `00-conventions.md`) rather than papering over the gap. **For every
   doctype the spec creates, run `erp_discover(action="preflight", doctype="<DocType>",
   payload={<the exact payload>})`** and paste its result into Schema
   evidence. Run `preflight` early, with the draft payload (a partial
   payload is fine: it lists every mandatory field with `in_payload`), and
   use `erp_discover meta` only for a field `preflight` does not cover —
   `meta` on a large doctype (Item: about 33k characters) costs context on
   every later call. `ready: false` means the create does not go ahead (this includes a
   `Bot permission` blocker: the write is sent as the bot, so a requester
   who may create is not enough): resolve
   each blocker with the user first. `custom_fields_merged: false` blocks
   until the user explicitly overrides; record the override, in their
   words, in the spec. Show any `gaps[]` with the role-gap prompt
   (`00-conventions.md`). Run the idempotency query (does a matching
   record already exist?) and record its result.
   **Cite a live call for every live fact.** Each "exists", "needs no
   code" or "is named X" line names the call that showed it (a query,
   `list_ops`, `preflight`, a render's `policy`), because a fact from
   memory or from the user's wording is a guess: an HRMS spec once said
   "creates need no token" and "Designation HR Executive exists", and both
   were wrong. **Done when:** every
   template section below is filled, or explicitly marked unconfirmed
   rather than left blank, and every create has a `ready: true` preflight
   (or a recorded override) in Schema evidence.
3. **Persist it — in the session's actual working directory.** Write the
   spec file to `./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md`, resolved
   relative to whatever directory this session is actually running in:
   - **Gateway/cron-driven session:** `01-connectivity.md` notes these
     backends bridge Hermes' `terminal.cwd` config key into a real
     path — resolve against that. In the Docker deployment that is
     `/opt/cwd`, which the gateway's `HERMES_WRITE_SAFE_ROOT` includes.
     The chat user cannot open a server path: show the spec digest in the
     chat at step 4, not only the path.
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
     and say so explicitly — the exception, not the default. Record the
     fallback under Deviations, with the denial text.

   Never put a spec under `qkeee-erp-learned/*` or `memories/MEMORY.md` —
   those are durable environment knowledge, not per-task working state; a
   spec is disposable once its task closes. **Done when:** the file
   exists at its resolved path and that path is stated to the user.
4. **Seek approval — unless running autonomously (see below).** Show
   the spec digest in the reply: objective, the values to write, side
   effects, rollback, open risks, and the spec path. Wait for an explicit
   go-ahead or edits. A "yes" to field values asked at step 1, before the
   spec existed, answers the clarify questions; it is not spec approval.
   For a single-record write with a confirmation token, one message may
   carry both approvals: the spec digest plus the rendered `request` and
   its code. The user's reply with the code then approves the spec and
   the write; write `Approval: approved <date> (with confirmation code)`
   in the header. Don't start step 5 on a spec that hasn't been approved
   or silently-generated (autonomous mode). **Done when:** the user has
   seen the digest and given an explicit go-ahead or edits, or
   (autonomous mode) the header carries the `Approval: autonomous (…)`
   mark.

   **Carve-out: the job-level email approval path is its own
   approval-seeking action.** When execution follows the ADR 0008/0010
   job-level path (`erp_execute_write` render with `batches`, then
   `op=notify.job_approval_request`), rendering and sending that email
   **is** this step's approval-seeking action — it is the artifact that
   asks the Requester to approve, same as ADR 0008 intends. Don't also
   wait for a separate chat "yes" in front of it: once the user has asked
   for the job plan to be rendered and sent, proceed straight from
   draft/persist to rendering and sending the job-approval email in the
   same turn, then wait for the Requester's email reply (steps 5/6) as
   the actual approval. This carve-out is scoped to the job-email path
   only; every other execution path (a plain spec run turn-by-turn in one
   session, a single confirmation-token write) keeps the chat-digest-then-
   wait-for-"yes" gate above.
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
   `qkeee_erp.core.client`. Run exactly the calls in Exact calls.
   - **Any change to an approved value is a deviation.** Log it under
     Deviations (what, why, who agreed) and re-confirm with the user
     before the write that uses it. There is no silent fix-up step: "the
     Link didn't resolve, so update that field" changes data outside what
     was approved.
   - **A structured failure stops the step.** When `erp_execute_write`
     prints `{"write_failure": {error_class, missing_fields,
     invalid_links, message}}`, stop. Show the user the missing or
     invalid fields, ask for the values, update the spec, re-confirm.
     Never fill a value the user did not give, and never retry blind.
   - **A batch stops at the first failure.** Run several writes as one
     `erp_execute_write batch`. On a failure, nothing after it runs.
     Report every step as a table: created, failed, not attempted.
   - **Take each created record's name from its result.** A later step
     links to that name. When steps need each other's names, end the
     batch there and render the next batch after it ran, because
     ERPNext's naming (series widths, a Job Applicant named by its email)
     differs from any prediction.
   - **Count a write as proven by execute plus read-back.** A render
     shows the request, nothing more; a submit or cancel is done when its
     result has `state_confirmed: true`.
   **Done when:** every technical step has run, or is marked
   deviated-with-reason in the spec file — never silently skipped.
7. **Close out.** Before telling the user the task is done:
   1. Reconcile Risks / open questions: close, update or carry forward
      each one. A risk that says "the user must confirm" after the user
      confirmed is stale; strike it.
   2. Append Outcome, shaped by `00-conventions.md`'s Report-back items
      1–4: warnings first; a records table (doctype, name, action,
      docstatus, and for a non-submittable doctype the word "live");
      a pointer to the Deviations section; the audit status per write
      (`_audit_log_status`, and the approval basis); open follow-ups
      (gaps, pending skills, teardown due).
   Leave the file in place — it's the audit trail for this task, not
   deleted on success. Working-scratch files are disposable across
   *sessions* (`01-connectivity.md`), not mid-task. Tell the user per
   the same Report-back contract, not a free-form summary. **Done when:**
   Risks are reconciled and the Outcome section is appended to the spec
   file, before — not after — telling the user the task is done.
8. **Summary email — after the user confirms the task is complete.**
   End the step-7 reply with one question: "Is this task complete? If
   yes, I will email you a summary." Send nothing until the user's own
   reply says yes in a later turn. A "no" or a new request means the task
   continues; ask again at its next close-out. Autonomous mode never
   sends it. Then:
   1. `erp_execute_write(phase="render", op="notify.summary_email",
      args={"title": "<what was done, e.g. Supplier, Item and Purchase
      Invoice created>", "records": [<every record in the Outcome table,
      primary record first>], "notes": [<key values>], "follow_ups":
      [<open follow-ups>]})`. The primary record is the one the task was
      for (the Purchase Invoice, not the Supplier made on the way); the
      email is logged on its timeline. Render reads every record again
      and builds the HTML from the plugin template. Pass data, never
      HTML. Do not paste the HTML into the chat.
   2. `erp_execute_write(phase="execute", op="notify.summary_email",
      args=<rendered args unchanged>, confirmation_token=...,
      issued_at=..., latest_prompt="<the user's yes>")`. The gateway sends
      the email from the agent's own mailbox to the requester only, then
      logs it as a Sent `Communication` on the primary record. Past the
      render's `expires_at`: render again first.
      - Never send it another way: no email skill, no terminal, no other
        address or Cc, even when the user asks (tell them to forward it).
      - `EmailNotConfiguredError`: the agent's mailbox is not set up.
        Tell the user that an Operator sets the `EMAIL_*` settings; stop.
      - `EmailSendError`: the send failed and nothing was logged. Report
        the message as is. Do not retry in the same turn.
      - `outcome_unknown` with "The email WAS SENT": it was delivered;
        only the timeline log failed. Say so, and never send it again.
      - A refusal that the bot cannot create `Communication`: no email
        went out. Show the gap prompt; after `RECHECK ENV`, render again.
   3. Add one line to the spec's Outcome: "Summary email sent to
      <requester> <time>, Message-ID <id>; Communication <name>", or why
      it was not sent.
   **Done when:** the email is sent and logged, or the reason it was not
   is in the spec and in the reply.

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
- Approval: <pending | approved <date> | approved <date> (with confirmation code) | autonomous (<why>)>
- Times: file-name slug in UTC; record times quoted with their zone

## Objective
One or two sentences: what this task accomplishes and why.

## Plan
Numbered, high-level. Each line maps to one or more technical steps below.

## Functional steps
What happens in ERPNext terms — doctypes touched, records read/created/
updated/submitted, reports run, any approval/workflow implication. Say
"draft" only for docstatus 0 on a submittable doctype; a non-submittable
record is "saved" and live on save (`00-conventions.md`).

## Schema evidence
Per doctype written, from `erp_discover preflight` (paste the result, or
these lines from it):
- Meta source: getdoctype | bare_doctype; custom_fields_merged: true|false
  (false: the user's override, quoted)
- Mandatory fields, each with the value set (or "filled by default/fetch")
- Conditional mandatory fields, each confirmed with the user
- Naming: autoname, series used, settings rule (e.g. HR Settings emp_created_by)
- Workflow: none active | <name> (and what the user agreed)
- ready: true (or the blockers and how each was resolved)
Field counts ("129 fields") carry no decision: leave them out.

## Side effects
What each write triggers downstream (e.g. an Active Employee enters
payroll, attendance, leave allocation, headcount reports and birthday
reminders).

## Idempotency
The query run before create to find an existing matching record, and
its result. A re-run of this spec must not create duplicates.

## Rollback
How to undo each write (delete, cancel, set a status), and who may run
it. Every path gets its full `erp_execute_write render` call, including
the fallback (for example "disable instead of delete" as a full `update`
render with its payload), not prose.

## Teardown (demo/test data only)
The query that finds the records (name prefix, `qkeee-demo` tag) and the
action that removes them — `00-conventions.md`'s Demo and test data.

## Exact calls
Per write: the full `erp_execute_write` command (or the `--batch` step)
and the payload JSON, exactly as it will run. Nothing is decided at
execution time, except a name an earlier batch created (step 6). Never
record `confirmation_token` or `issued_at`: they
are one-time values, not part of the plan. Write "render, then execute
with the rendered values and the user's reply".

## Risks / open questions
Anything ambiguous, anything requiring a double-confirm
(`00-conventions.md`'s GRC baseline), anything dependent on an
unconfirmed assumption. Reconciled at close-out: closed, updated or
carried forward.

## Deviations
"None", or each item: what, why, who re-confirmed. Two kinds:
- a change to an approved value;
- a procedure deviation: the spec path fell back, a step was skipped or
  reordered, an operation flag was used outside its documented purpose.
A confirmation reply that contains the code (e.g. the code alone) is not
a deviation.

## Outcome (filled in at close-out)
Report-back items 1–4 (`00-conventions.md`): warnings first; records
table (doctype | name | action | docstatus, "live" for non-submittable);
a pointer to the Deviations section (do not repeat it); audit status per
write (`_audit_log_status`, and the approval basis); open follow-ups.
```

A worked example, the 2026-10-07 DEMO_ERP spec rewritten to this
template: `references/examples/spec-demo-employees.md`.
