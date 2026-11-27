# Minor: party_type role gap, tool_call batching bug, confirmation code lost to compaction

Status: wontfix (no doc/code gap found; (2) is upstream, (3) folded into ADR 0008)

## Problem (three small, unrelated items)

1. **`party_type` listing blocked.** Qkeee Bot had no DocType-read permission to list `party_type` values — same class of role gap as the existing Role Permission Manager checklist items, just a new doctype. Add it to that checklist.
2. **`tool_call` batching harness bug.** A batch failed twice with "requires a 'name'" — mechanical harness validation bug in hermes-agent's tool_call batching, not specific to ERPNext. Worth a bug report upstream, not a skill fix.
3. **Confirmation code E59802 lost to context compaction**, forcing a re-render. This is a *different* loss mode than the already-known ~15-minute expiry (agent memory: "Confirmation codes expire ~15 min") — here the code was lost because compaction dropped it from context before the 15 minutes was up, not because the window elapsed. Worth tracking as a second, distinct cause of lost confirmation codes alongside the expiry one, since the fix (if any) would be different — e.g. keeping a pending confirmation code pinned/exempt from compaction.

## Proposed fix

- (1) Add `party_type` to the Role Permission Manager checklist entry already tracked for Qkeee Bot role gaps.
- (2) File upstream against hermes-agent's tool_call batch validator.
- (3) Note in the confirmation-code handling docs (and consider for ADR 0008's job-level token design) that a pending code must survive compaction, not just stay within its time window.

## Comments

2026-10-23: (1) checked `00-conventions.md`'s "Role-gap prompt" section — it's a generic, live-computed mechanism (`health`/`preflight` → `gaps[]` → fixed prompt), not a static per-doctype checklist, so `party_type` needs no new doc entry; the mechanism already covers any doctype, including this one. (2) is a hermes-agent harness bug, not ours to fix in this repo. (3) is already carried as an open question in ADR 0008 ("a pending code must survive compaction") — not duplicating it here. Closing `wontfix`: nothing left that's actionable in this repo.
