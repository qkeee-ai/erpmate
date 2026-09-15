# Non-ERPNext systems adapter procedure

Not a connector — `scripts/core/client.py` is Frappe-REST-specific by
construction, and nothing in this skill auto-discovers or drives an
arbitrary external API. This is the procedure for when a user's request
is about a system that genuinely isn't ERPNext (a third-party accounting
tool, a bank's own portal, an internal tool with its own API, Tally, a
payment gateway dashboard) but still falls inside this skill's
ERPNext/organizational-work scope guardrail (`00-conventions.md`) — e.g.
"reconcile this against our Tally export" or "check the payment status
in our gateway's dashboard."

## Procedure

1. **Never guess at a non-ERPNext system's shape.** Unlike ERPNext,
   there's no `discover.py meta` equivalent, no live-metadata fallback,
   and no prior domain-knowledge file to ground a guess in. Explicitly
   request one of: the system's API documentation, a user guide, or a
   URL to its docs — before attempting any action against it. If none is
   available, say so plainly and ask the user to describe the specific
   fields/endpoints/screens involved rather than proceeding on
   assumption.
2. **Treat whatever the user provides as the only ground truth for this
   system** — the same way `02-environment-assessment.md` handles a
   genuinely org-specific custom ERPNext app with no public repo: build
   understanding from what's actually provided plus what the user
   explains, and say so explicitly rather than inventing a source.
3. **No credential handling beyond what's already documented for
   ERPNext.** If the non-ERPNext system needs its own API key/token,
   follow the same discipline as `qkeee-erp.env` (`01-connectivity.md`)
   in spirit — never typed into chat and echoed back, never written into
   agent-curated memory, never composed into a command that embeds the
   raw value. If the harness has no equivalent isolated-credential
   mechanism for a second system, say so and ask the user how they'd
   like to handle it, rather than improvising a new storage location.
4. **Catalog what's learned the same way as a custom Frappe app.** A
   non-ERPNext system's notes land under
   `qkeee-erp-learned/<env-tag>/references/non-erpnext/<system-slug>.md`
   — same tier, same promotion path (`memory_promote.py`'s redact +
   format, then `skill_manage`), same one-line `MEMORY.md` breadcrumb
   convention as a custom app gets under `custom-apps/<app-slug>.md`. See
   `00-conventions.md`'s naming table.
5. **Every other non-negotiable in `00-conventions.md` still applies.**
   PII redaction, the scope guardrail, requester-attribution discipline
   where the action has a real-world approval analog, save-then-review
   before anything resembling a "write" against the external system (to
   whatever extent its own API supports staging) — none of it is
   ERPNext-specific, and none of it is waived because the target system
   is.

## Out of scope

Not a general integration-building capability. If a non-ERPNext system
becomes a repeated, trusted need, that's a signal it deserves a proper
adapter (or a dedicated domain) built deliberately — with its own
reviewed capability table, the way the eleven ERPNext domain slugs
were — not a reason to grow this ad hoc procedure's scope indefinitely.
