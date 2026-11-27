# PII tokenization at the wrapper boundary

Status: needs-triage
Type: research
Blocked by: 02

## Problem

Fields like employee salary or personal contact details currently reach model context in full when read through a direct tool call. The code-execution pattern lets the wrapper boundary substitute a token (`[EMAIL_1]`, `[SALARY_1]`) for the model, while the real value flows between wrapper calls without the model ever seeing it.

Naively, detokenization is "a lookup" — but a lookup the sandbox script controls would let generated code fetch any tokenized value it wants, regardless of whether the current Requester may see it. That silently defeats the Requester Gate for exactly the data it's meant to protect.

## Change

- Design the token↔value lookup so detokenization happens only inside a wrapper call that re-checks the Requester Gate for that field/record, not as a bare table the sandbox script can query directly.
- Decide which ERPNext fields are tokenized by default (start from fields already called out in the Requester identity binding and audit log work) versus opt-in per wrapper.

## Done when

A design note exists covering: where the token table lives, what re-checks the Gate on detokenization, and which fields tokenize by default. No code yet — this blocks on issue 02 existing to test against.

## Comments
