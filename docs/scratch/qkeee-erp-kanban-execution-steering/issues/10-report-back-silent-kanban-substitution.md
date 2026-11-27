# Live test: agent silently substituted the job-level path for "kanban", and the write_file bug recurred

Status: done (doc fixes shipped; write_file recurrence likely a stale-session read, not a doc gap — flag to retest fresh)

## What happened (2026-10-24, live sales-dataset job-approval test)

The actual ADR 0008 chain worked: `JOB-CB87362C25` stored, 34 steps
across 5 batches, approval email genuinely sent (Message-ID returned),
confirmation code generated. Issues 03-09 are confirmed live, not just
unit-tested. Two report-quality/doc problems surfaced on top of that:

1. **No explanation of the kanban substitution.** The original ask in
   this whole thread was "a kanban based demo data setup." The agent
   built it as one job-level email approval instead (correct, per the
   `00-conventions.md` rule from issue 01) but never said so — the reply
   read as if kanban had simply been forgotten, not deliberately
   replaced. Same class of problem as
   `qkeee-erp-demo-procurement-run-review/issues/01` (silently
   substituting unlinked documents for a named PO-linkage scenario) —
   a deliberate substitution needs to be stated, not left for the user
   to notice and ask about.
2. **The write_file-outside-safe-root bug recurred** (`/opt/cwd/...`
   refused, same as issue 08) even though issue 08's fix had already
   landed in `00-conventions.md` days earlier. Likely cause: that
   test session had already loaded `00-conventions.md`'s content earlier
   in its own conversation, before the fix was committed, and never
   reloaded it — a stale-context problem, not a doc gap. The fix text
   itself was also buried inside a dense table cell, which wouldn't have
   helped even on a fresh load.

## Fix

Both promoted into their own standalone, hard-to-miss paragraphs in
`00-conventions.md` (not table-cell asides): (1) an explicit instruction
that naming "kanban" in the request and substituting the job-level email
path requires saying so in the reply, with the reason; (2) the
write_file fallback as its own flagged paragraph right after the naming
table instead of a parenthetical inside one cell.

## Open

The write_file recurrence specifically should be retested in a **fresh**
session (not a continuation of the long-running test conversation) to
confirm it was staleness, not a doc gap that survived the first fix.
