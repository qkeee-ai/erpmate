# Spec-driven execution's step 4 stacks a chat approval gate in front of the job-email approval itself

Status: ready-for-agent
Blocked by: none

## What happened

28-Oct live run: the user's first prompt asked the agent to render the job plan and send the ADR 0008 approval email in the same turn. The agent instead drafted the spec, showed the digest, and stopped — per `03-spec-driven-execution.md` step 4's rule to wait for an explicit chat go-ahead before proceeding. The user had to send a second prompt ("did you trigger email for approval yet?") before the agent actually rendered and sent the job email. The job email is itself the approval-seeking artifact (ADR 0008's entire point); requiring a separate chat "yes" in front of it is a redundant gate, not an extra safety check — the Requester's eventual email reply is the real approval, same as always.

## Fix

Add an explicit carve-out to `03-spec-driven-execution.md` step 4 (`qkeee-erp-associate/references/03-spec-driven-execution.md`): when the execution path is the ADR 0008/0010 job-level email approval, rendering and sending that email **is** the step-4 approval-seeking action. Don't wait for a separate chat go-ahead before sending it — proceed straight from draft/persist to rendering and sending the job email in the same turn the user asked for it, then wait for the Requester's email reply (steps 5/6) as the actual approval.

Keep the existing step-4 gate (chat digest + wait for "yes") for every other execution path (a plain spec executed turn-by-turn in one session, a single confirmation-token write) — this carve-out is scoped to the job-email path only.

## Depends on

Nothing.
