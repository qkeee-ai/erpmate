---
name: qkeee-erp-handoff
description: "Emit a fixed ERPNext continuity block before a hand-off or context compaction — env tag, requester rule, session_id reset, active spec path."
metadata:
  hermes:
    tags: [ERPNext, Handoff, Continuity, Session]
    related_skills: [qkeee-erp-associate]
---

# qkeee-erp-handoff

A context hand-off or compaction is a **new logical session** for this
connector's purposes (`00-conventions.md`'s GRC baseline on
`session_id`) — but nothing carries the environment tag, the
requester-identity rule, or the active spec's path across that boundary
on its own. A generic hand-off document has no way to know this
ERP-specific state exists; this skill emits exactly that, and nothing
more.

**Not a general session summary.** Deliberately narrow and fixed-shape —
the facts the activation sequence (`SKILL.md`) needs to not re-derive,
or worse, wrongly reuse stale.

## Procedure

1. **Fill the block below from what this session actually knows right
   now.** Never guess a field it hasn't resolved — mark it "not yet
   resolved" instead. **Done when:** every field is either filled from
   confirmed session state or explicitly marked unresolved.
2. **Emit it as plain text** — the closing content of this turn, or the
   full reply if invoked as a deliberate hand-off. No file write needed;
   the channel itself is durable. Only write to a file if the user asks
   for one, and then follow `03-spec-driven-execution.md`'s spec-file
   placement rule, not an invented path.
3. **The next session must re-derive `requested_by` and `session_id`
   fresh, never carry this block's values forward as a shortcut** — the
   block exists to inform the next session's own activation sequence,
   not to let it skip steps 1 and 3 of `SKILL.md`'s activation sequence.
   **Done when:** the block is emitted and nothing downstream treats its
   `requested_by`/`session_id` values as already-resolved state.

## Continuity block

```markdown
## qkeee-erp continuity
- Environment tag: <tag> (base URL: <url, or "not yet resolved">)
- Mode: <read-only | read-write>
- Requester: <resolved ERPNext user id/email, or "not yet resolved"> —
  resolved fresh from the channel field this session; the next session
  re-resolves it fresh too, never reuses this value
- session_id: <this session's id> — this hand-off is a new logical
  session boundary; the next turn derives its OWN session_id
  (00-conventions.md GRC baseline), never carries this one forward
- Active domain(s): <slug, slug — or "none latched">
- Active spec: <path, or "none">
- Open questions / pending confirmation: <anything mid-flight, or "none">
```

## Relationships

Complements `03-spec-driven-execution.md` (the active spec's path is one
of the block's fields) and `00-conventions.md`'s GRC baseline (the
`session_id`-regeneration rule this block exists to enforce across a
hand-off, not just within one session).
