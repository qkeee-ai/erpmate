# Verify the Requester Gate and audit log can't be bypassed from inside the sandbox

Status: needs-info
Type: research
Blocked by: 01, 02

## Problem

ADR 0013's whole premise is that moving ERPNext calls into generated code doesn't weaken enforcement: every wrapper call still passes the Requester Gate and writes the audit log. This needs an adversarial check, not just a design claim — the issue-02 hole existed exactly because nobody had checked whether `execute_code` could reach ERPNext a different way.

## Change

- Once issues 01/02 exist, attempt to call ERPNext from inside the sandbox without going through a wrapper (raw `fetch`, reading the credentials file, importing an unvetted library that shells out) and confirm each attempt fails.
- Attempt to call a wrapper with a Requester the current session shouldn't be allowed to act as, and confirm the Gate still rejects it from inside generated code the same way it would a direct tool call.
- Treat this as a security-review pass before the sandbox ships on any Deployment with live ERPNext write access.

## Done when

Each bypass attempt above is tried and fails, documented here with what was tried.

## Comments

Blocked until issues 01 and 02 produce something to test against; marked needs-info rather than needs-triage since the open question (can it be bypassed) has a concrete test, not a design decision.
