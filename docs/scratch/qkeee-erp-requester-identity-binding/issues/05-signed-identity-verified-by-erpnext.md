# L4: signed identity assertion verified by ERPNext

Status: wontfix
Type: task
Blocked by: 04 (recommended, not strict)

## Problem

Even with issue 04, ERPNext trusts whatever `requested_by` the bot account sends. Anyone holding the bot key can attribute a call to any user.

## Change

- The gateway signs `(sender_email, session_id, expiry)` with an HMAC key. Only the gateway and ERPNext hold the key.
- The connector sends it as a request header.
- A Qkeee app endpoint in ERPNext verifies the signature and expiry, then runs the permission check (or the operation) as that user on the server, for example with `frappe.set_user`.
- Without a valid signature, ERPNext refuses any call that carries a `requested_by`.

## Notes

- The signing key must never reach the terminal or `execute_code` env. The local terminal backend shares the gateway's filesystem; see issue 06.
- Hermes core change: the signature has to be minted where the session ContextVar lives, either in the gateway or in the issue 04 plugin.

## Comments

**Decided 2026-10-08** (grill-with-docs). Not built. See `docs/adr/0004-no-signed-requester-identity.md`. After 04 and 06 the signing key would sit beside the API key in the gateway, so L4 only stops a leaked Bot Account key, which D1 already limits. It also needs a server app per Instance, rejected on 2026-09-01. Revisit if a Qkeee server app gets deployed for another reason, or an audit demands non-repudiable proof of the Requester.
