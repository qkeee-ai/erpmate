---
name: lcm-self-heal
description: "Diagnose and prepare a repaired lcm.db when hermes-lcm reports page-level corruption (\"database disk image is malformed\", FTS integrity failures, or /lcm doctor repair apply failing). Runs diagnose + rebuild from adminops/lcm-db-recover.py automatically; stops short of swap/gateway restart and hands those off to a human operator."
metadata:
  hermes:
    tags: [lcm, sqlite, corruption, recovery, adminops, diagnostics]
    category: adminops
    related_skills: []
---

**Unattended counterpart:** `scripts/lcm_corruption_check.py` (the
`lcm-corruption-check` cron job, every 12h, plus once at every container
boot) runs this same diagnose+rebuild pair and emails/Google-Chats the
finding when nobody's in a live chat session to trigger this skill.
Same scope boundary, same reasoning — see that script's own docstring.

# lcm-self-heal

Prepares a verified, repaired copy of a corrupted `lcm.db` using
`/opt/adminops/lcm-db-recover.py` (runbook: `adminops/lcm-db-recover.md`).

**Scope boundary — read this first.** This skill only ever runs
`diagnose` and `rebuild`. Both are read-only against the live database;
`rebuild` writes nothing but a new file under `/tmp`. It **never** runs
`gateway stop`, `swap`, or `gateway start`.

Why: the final repair step needs the gateway stopped, but the agent
running this skill is itself being served *by* that gateway. There is no
safe way for this session to stop its own serving process and then keep
running later steps — the session would die mid-task with no way to
confirm the stop happened, let alone continue to `swap`. Diagnose and
rebuild are genuinely safe to automate because the README's own rule
holds for them ("read-only by default... a command that writes to live
data has a dry-run mode"); stop/swap/start is not, so it stays a human
action.

## When this skill activates

Any of:
- A gateway log line matching `sqlite3.DatabaseError: database disk
  image is malformed` out of `hermes-lcm`'s `store.py`
  `_append_protected_batch` — i.e. compaction/append is failing.
- A startup or background log line: `Background FTS integrity-check
  found corruption in 'messages_fts'`.
- `/lcm doctor repair apply` itself returns `error: FTS repair failed:
  database disk image is malformed` (plugin-level repair can't fix
  B-tree damage).
- The user directly asks to diagnose or fix `lcm.db` corruption.

If only the FTS background-check log line appears and `/lcm doctor
repair apply` has **not** been tried yet, try that first — it's the
plugin's own cheap fix for index drift alone. Only fall through to this
skill once that fails, or once the compaction error or the malformed-FTS
failure confirms base-data damage.

## Procedure

1. **Confirm the trigger.** Quote the actual error/log line that
   triggered this, not a guess. If nothing matches the list above, stop
   and ask — don't run recovery speculatively against a healthy DB.

2. **Resolve the profile and the tool path**, via `execute_code`:
   - Profile name: `$HERMES_PROFILE_NAME` if set, else `default`
     (`default` maps to `/opt/data/lcm.db` directly, not a
     `profiles/default/` subpath). If genuinely ambiguous (more than one
     profile under `/opt/data/profiles/`), ask which one before
     proceeding.
   - Tool path: `/opt/adminops/lcm-db-recover.py`. If it's missing, stop
     and tell the user — it needs `docker cp adminops/lcm-db-recover.py
     <container>:/opt/adminops/` from the host (per
     `adminops/README.md`). Do not reconstruct or improvise the script
     from memory; only ever run the vetted copy on disk.

3. **Run diagnose** (safe with the gateway up):
   ```bash
   python3 /opt/adminops/lcm-db-recover.py diagnose --profile "$P" --scan-all
   ```
   Read the verdict using the table in `adminops/lcm-db-recover.md`
   ("Read the output like this"). Two outcomes end the skill here:
   - `verdict: healthy` — report that and stop, nothing to do.
   - Damaged trees are **all** `derived FTS index (rebuildable)` and
     every table scan is `OK` — report that `/lcm doctor repair apply`
     is the right next step (again, if not already tried) and stop. A
     rebuild is overkill for FTS-only drift.

   Otherwise (`BASE DATA` damaged, or a `PROBLEM <table>` scan line),
   continue.

4. **Run rebuild** to a fresh, timestamped output file — never reuse a
   path from a previous attempt, and never pass `--force` over a file
   you didn't just create this run:
   ```bash
   python3 /opt/adminops/lcm-db-recover.py rebuild --profile "$P" \
     --out "/tmp/lcm-clean-$(date +%Y%m%d_%H%M%S).db"
   ```
   This also runs `verify` at the end automatically.

5. **Check the rebuild against the runbook's checklist** (section "3.
   Rebuild" in `lcm-db-recover.md`):
   - `RESULT: PASS`
   - `messages: kept / read` — equal, or lower only by the `NOT NULL`/
     garbage-row count `diagnose` already reported
   - `messages_fts: N indexed / N content rows` — same `N` both sides
   - `info triggers` lists `msg_fts_insert`, `msg_fts_delete`,
     `msg_fts_update`, and everything else the source schema had
   - `messages store_id gaps` — note the count; a new cluster of gaps
     right around the damaged pages is real loss, isolated old gaps are
     expected deletes

   If any of these fail, or `rebuild` exits non-zero, **do not retry
   blindly**. Report the exact output. If it looks like genuine data
   loss beyond the expected garbage rows, point at the runbook's "Root
   cause" checklist (storage type, unclean shutdowns, concurrent
   writers, OOM kills) as what a human should check next — don't
   investigate host-level storage or `dmesg` yourself unless asked.

6. **Report and hand off.** Give the user/operator:
   - The full `diagnose` and `rebuild` output (or a faithful summary if
     long).
   - The candidate file path from step 4.
   - The exact remaining commands, verbatim from
     `adminops/lcm-db-recover.md` steps 2, 4 and 5 — stop the gateway,
     dry-run `swap`, `swap --yes`, start the gateway, confirm via `/lcm
     doctor repair`, then clean up `/tmp/lcm-clean-*` and
     `/tmp/lcm-recover-*` (but keep `backups/lcm/pre-swap-*` until
     they're sure nothing's missing).
   - A reminder that these steps stop the gateway and must be run by a
     human from outside this chat session — not by asking this agent to
     "continue."

## Hard rules

- **Never run `gateway stop`, `gateway start`, or the `swap`
  subcommand**, under any phrasing of the request, from inside this
  skill. If the user asks you to "just finish it" / "go ahead and
  swap it in," explain the self-stop problem above and give them the
  commands to run themselves instead.
- **Never delete** the rebuild candidate or the `/tmp/lcm-recover-*`
  snapshot directories yourself — the runbook leaves that to the human,
  after `swap` and a confirmed `/lcm doctor repair`, and `rebuild`'s
  snapshot is the only copy of the pre-repair state if something looks
  wrong later.
- **Never edit `lcm-db-recover.py`** mid-incident to work around an
  unexpected error. Report the traceback verbatim and stop; a bug in
  the tool is a separate, deliberate fix, not something to patch live
  during recovery.
- **Stick to the profile that reported the symptom.** Don't diagnose or
  rebuild another profile's `lcm.db` because `--scan-all` happened to
  flag it — report that finding (per the runbook, it means suspect the
  storage, not that profile's data specifically) and let a human decide
  what to do about it.
- **`--scan-all` flags a *different* `.db` as `DAMAGED`:** that's a
  storage-level signal (see "Root cause" in the runbook), not something
  this skill fixes. Say so; don't run `rebuild` against it.
