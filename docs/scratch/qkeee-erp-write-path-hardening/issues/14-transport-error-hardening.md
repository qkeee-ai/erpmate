# Harden _request and audit finish against non-ConnectorError failures

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P0
Blocked by: none
Closes: W14

## Problem

`core/client.py _request()` (`:993`) only wraps `HTTPError` and `URLError`. These failures escape as raw exceptions:

- A read timeout during `resp.read()` raises `TimeoutError` / `socket.timeout`, not `URLError`.
- A 200 response with a non-JSON body, such as a WAF/Cloudflare HTML page, raises `json.JSONDecodeError`.
- A connection reset during read raises `ConnectionResetError` / `http.client.RemoteDisconnected`.

Every caller does `except ConnectorError: record_audit_log_finish(... "Failure")`, so these leave the audit row stuck at `Attempted`. `execute_write.py` then prints a traceback instead of `ERROR:`.

A timeout on a non-idempotent POST is ambiguous: the write may have landed.

## Change

1. In `_request`, also catch `TimeoutError`, `socket.timeout`, `ConnectionError`, `http.client.HTTPException` and `json.JSONDecodeError`. Re-raise each as a `ConnectorError` subclass:
   - `TransportTimeoutError` for timeouts, with a message saying the outcome is unknown for a write and the record must be re-read before any retry
   - `MalformedResponseError` for a non-JSON body, quoting the first 200 characters
2. In every write wrapper (`mutate_resource` and the bespoke ones until 07 replaces them), change the failure handler to `except Exception as e:` → finish with status `Failure`, adding `outcome_unknown=True` to `error_detail` for a timeout → re-raise. A non-`ConnectorError` is wrapped first.
3. Coerce `issued_at` safely in one helper, `_coerce_issued_at()`, used by every token check. This overlaps 04, so whichever lands second reuses the other's helper.

## Acceptance

- [x] Tests patch `urlopen` to raise `TimeoutError`, to return an HTML body with status 200, and to raise `RemoteDisconnected`. Each surfaces as a `ConnectorError` subclass.
- [x] In each case `record_audit_log_finish` is called with `status="Failure"`.
- [x] `execute_write.py` prints `ERROR:` and exits 1, with no traceback.

## Comments

**Implemented 2026-10-06.**

- New exceptions: `TransportTimeoutError`, `MalformedResponseError` and `InvalidIssuedAtError`.
- `_request` now also handles:
  - `URLError` wrapping a timeout
  - a bare `TimeoutError`
  - `ConnectionError` and `http.client.HTTPException`
  - a non-JSON or non-UTF-8 body
  For writes, the message says the outcome is unknown.
- `finish_audit_failure_and_reraise()` is used by `mutate_resource`, `call_whitelisted_method` and `call_permission_manager`. It closes the audit row as Failure for any exception, adds `outcome_unknown=True` for timeouts, and wraps a non-`ConnectorError`.
- `coerce_issued_at()` is shared with 04.
- Not done: distinct CLI exit codes, which are part of 11.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests; note: transport failures now exit 4 (outcome unknown), by ticket 11's exit-code design; still `ERROR:` and no traceback. Suite: 305 passed, 861 subtests.
