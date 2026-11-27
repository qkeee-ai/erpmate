# Task-spec write_file path convention doesn't account for HERMES_WRITE_SAFE_ROOT

Status: needs-triage (found live, 2026-10-24 sales dataset test; pre-existing host quirk, unrelated to ADR 0008)

## What happened

Same live test: the agent's spec write to
`/opt/cwd/qkeee-erp-specs/demo-sales-dataset-20261024.md` was refused —
"outside `HERMES_WRITE_SAFE_ROOT` (`/opt/data`)". `00-conventions.md`'s
Task spec naming row says to write relative to `terminal.cwd`
(`/opt/cwd` on this host) with no mention of this `write_file`
restriction, so the agent tried the documented path and it failed.

This isn't new: an earlier session on the same host already worked out
the fix and saved it to its own **personal** Hermes memory — "the
Hermes write_file/read_file tools are confined to
`HERMES_WRITE_SAFE_ROOT=/opt/data`; `/opt/cwd` is refused by those tools
but IS writable from the terminal, so write under `/opt/data` and copy
with the shell." That memory is per-profile, has a hard character
budget (seen at 88% full earlier), and isn't guaranteed to survive or be
consulted every session — relying on it is why this keeps resurfacing
instead of being fixed once.

## Proposed fix

Fold the known workaround into `00-conventions.md`'s Task spec row
itself (and `references/03-spec-driven-execution.md` wherever it
describes writing the spec file), not just the agent's own memory:
write the spec under the profile's write-safe root first
(`$HERMES_WRITE_SAFE_ROOT`, `/opt/data` by convention on a sidecar
deployment) and, when `terminal.cwd` differs from the safe root, copy it
to the documented `./qkeee-erp-specs/...` location via the terminal
afterward — same two-step the earlier session already improvised, just
written down once instead of rediscovered per host.

## Depends on

Nothing. Independent of the ADR 0008 work; just surfaced by the same
test run.
