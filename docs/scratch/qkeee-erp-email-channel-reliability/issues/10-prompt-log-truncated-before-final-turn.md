# Prompt-chain log cuts off before the final reply

Status: needs-triage
Type: task
Blocked by: none
Findings: F7

## Problem

`prompts.log` ends right after the last tool result (memory-update call, L1212) — the actual email sent back to the requester (with attachment caveat, RBAC caveat, submit confirmation) is not captured in the file at all. This means any audit/review of what the user actually received has to come from the mailbox, not the log — which is exactly the gap that made it impossible to confirm, from this file alone, whether the final email followed the To/Cc/threading rules in issue 01.

Exact cause not yet identified — needs tracing where/how `prompts.log` is written (buffered write that doesn't flush before process exit? log rotation mid-write? a final-turn write that's conditioned on something that didn't fire?) in `erpnext-hermes`.

## Change

Ensure the complete final turn (including the model's final reply content, not just the last tool call/result) is captured in the prompt-chain log before the process considers the task done.

## Acceptance

- A fresh run's log file includes the literal final reply text sent to the user, confirmed by diffing against the actual sent email/chat message.

## Comments
