# Kanban board: a triage-stage card has no agent-side exit

Status: ready-for-human (upstream board-capability ask; skill-level avoidance is issue 16/ADR 0010 point 5, separate)
Blocked by: none. Blocks nothing else directly, but ADR 0010's "never create into triage" (point 5) only avoids the cause — it does not fix a card that lands in triage some other way (manual board action, a future decomposer path).

## What happened

Live test, 29-Oct sales-demo run (`erpnext-hermes/logs/sales-demo-29-Oct/prompt.log` ~line 214). A kanban card that had escalated to `triage` stage (after repeated blocks on the same cause) could not be moved by anything the agent could call: `kanban_unblock`, `kanban_block`, and `kanban_complete` were all tried and refused, with the board reporting "a triage card has no agent-side exit." The card showed `promoted: 0` — it was never claimed by any worker, and nothing in the agent's own tool set could promote, split, or close it.

This is a capability gap, not a decision the agent got wrong: there is no `kanban_*` tool call documented anywhere in this skill tree that moves a card out of `triage` once it's there.

## Impact

Any card that reaches `triage` — by manual action, a future decomposer path, or (before ADR 0010 point 5 ships) the old block-escalation behavior — is permanently stuck from the agent's side. Combined with ADR 0008/0010's job-execution path, a card stuck here means the job itself is stuck with no way to recover except a human directly operating the board.

## Proposed fix

Two tracks, same shape as issue 01:

1. **Immediate (skill-level avoidance).** ADR 0010 point 5: never create a job-execution card into `triage`; create directly into `todo`/`ready`. This reduces how often a card reaches the dead end, but does not fix the dead end itself.
2. **Upstream (hermes-agent).** File against hermes-agent: a real agent-side way to promote, split, or close a `triage`-stage card — at minimum, a `kanban_promote`/`kanban_specify` call that works on a card in `triage`, not only on cards already past it. Until this ships, a card that reaches `triage` for any reason needs a human on the actual dashboard to move it.

## Depends on

Nothing. Independent finding; track in parallel with everything else in this file set.
