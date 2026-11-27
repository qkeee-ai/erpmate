# Structured create-failure errors and batch stop rule

Status: resolved (code done: dff4aaa + review fix 4b0cb3c, in feat/discovery-write-readiness)
Type: task
Blocked by: none

## Problem

Preflight (issue 06) cannot see everything: server scripts and custom-app `validate` hooks run only on save. When a create fails, the agent gets raw Frappe error text. It then guesses a fix, or retries.

A batch has no stop rule. The 2026-10-07 spec created two Employees. If the second had failed, nothing defined what to report or whether to continue.

## Change

1. `operations.py`: on a failed create or update, parse Frappe's `exc_type` and message into:
   `{error_class, missing_fields[], invalid_links[{field, value}], message}`
   for `MandatoryError`, `LinkValidationError`, `ValidationError`, `DuplicateEntryError`, `UniqueValidationError`.
2. `execute_write.py`: print that object as JSON on failure.
3. `03-spec-driven-execution.md` step 6: on a structured failure, stop. Show the missing or invalid fields, ask the user, update the spec, re-confirm. Never fill a value the user did not give.
4. Batch rule: stop at the first failure. Report created / failed / not attempted as a table.

## Acceptance

- Tests: one fixture per error class maps to the object above.
- Test: a batch of three where the second fails creates one record and reports one failed and one not attempted.

## Comments

**Implemented 2026-10-08** (/implement verification).

Done in erpnext-hermes branch `feat/discovery-write-readiness` (not yet merged to `develop`). `operations.py` parses the 5 error classes into `{error_class, missing_fields, invalid_links, message}`. `execute_write.py` prints `write_failure` JSON, and `--batch` stops at the first failure with succeeded/failed/not_attempted. Review fix 4b0cb3c makes approval apply per step only (a batch-level `--user-approved` is refused). Tests: `test_write_failures.py`. The step 6 stop rule is in `03-spec-driven-execution.md`.
