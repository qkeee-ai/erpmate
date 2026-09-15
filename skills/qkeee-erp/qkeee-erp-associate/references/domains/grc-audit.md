# Domain: grc-audit (cross-cutting statutory-audit framing)

Not a doctype-scoped domain like the other ten — there is no
`scripts/domains/grc_audit.py` and no `ALLOWED_WRITE_DOCTYPES` of its own.
This is the cross-cutting frame that pulls together the RBAC/audit/
redaction guardrails already stated once in `00-conventions.md`'s GRC
baseline, into the shape a statutory-audit conversation actually needs:
"can you show me every write this bot made to Journal Entry in Q3, who
requested each one, and prove nothing was tampered with." Latch this file
alongside a functional domain (accounts, fixed-assets, system-admin —
wherever the audit's actual subject matter lives) rather than instead of
it; this file adds the audit-trail lens, not the domain knowledge.

## When this domain applies

A user asks for an audit trail, a compliance review, "prove who did
this and when," a GRC (governance/risk/compliance) framing on top of
otherwise-normal domain work, or a statutory auditor's request for
evidence of controls (segregation of duties, change logs, access
reviews).

## The audit trail this skill produces

Every write goes through `core.client.mutate_resource()`'s two-phase
logging into `Qkeee Bot Audit Log` (`Attempted` → `Success`/`Failure`),
carrying: `session`, `requested_by`, `action`, `reference_doctype`/
`reference_name`, `timestamp`, `status`, `payload_before`/`payload_after`
(Update only), `field_diff` (computed, Update only), `user_approved`,
`approval_note`, `prompt_summary`, and `latest_user_prompt`. Every read
(query/get/report) carries the same `prompt_summary`/`latest_user_prompt`
plus `response_payload` — the actual response body returned to the
caller, so the trail shows exactly what records a read exposed, not just
that a read happened. See `00-conventions.md`'s GRC baseline: RBAC
pre-check runs on every environment and read logging is always on,
unconditionally — neither is debug-gated or PROD-only.

**What this trail proves, and what it doesn't:**
- Proves: which record was touched, by which domain, attributed to which
  `requested_by`, when, and (for Update) exactly which fields changed.
  Best-effort — a target instance that hasn't run `qkeee-erp-bot-init`
  yet, or a doctype temporarily unreachable, means the real write still
  happens but doesn't get logged; an orphaned `Attempted` row is the
  detectable trace of a crash mid-write, not a cover-up.
  `AUDIT_EXEMPT_DOCTYPES` in `core/client.py` deliberately excludes the
  audit log itself and `Comment` from being logged, to avoid infinite
  recursion — not a gap in coverage of business writes. On the read
  path, the equivalent exemption (`_LOG_READ_RECURSION_EXEMPT_DOCTYPES`)
  is narrower still and purpose-keyed rather than doctype-keyed — a
  business-intent read of `User`/`Role`/`DocType` (e.g. "who is this
  person, what can they do") is a real logged row, not silently dropped
  the way it used to be.
- Also proves: that a requester was REFUSED, and why. Every
  `_validate_prod_requester()` call — read or write — writes one
  gate-decision row (`response_payload.gate_check: true`), on the denial
  branches as well as the allow. Before this fix, a
  denied call left nothing in the trail at all, since the gate raises
  before the guarded read/write ever runs; a permission-denial review is
  now a real query against this log, not a gap you have to explain away.
- Does NOT prove: that a human actually read and approved a
  `user_approved: "Approved"` write — that field is a detection signal
  set by the calling domain after its own confirm stage, not
  independently verified by the connector. A `confirmation_token` match
  (on a double-confirm write) proves recency/consistency of the rendered
  facts, never that a human said yes to them.
- **Secondary cross-check, recommended for a real statutory audit:**
  enable `track_changes: 1` on every doctype this skill writes to, so
  Frappe's own `Version` doctype independently captures field diffs
  server-side. Periodically reconcile `Version` against `Qkeee Bot Audit
  Log` (same doctype + name + timestamp window) to catch anything either
  logger missed — this skill doesn't automate that reconciliation itself,
  but an auditor should be told it's available.

## Procedure

1. Identify which functional domain(s) the audit actually concerns
   (accounts, fixed-assets, system-admin, etc.) and latch that domain's
   reference alongside this one — this file supplies the audit-trail
   query pattern, not the underlying business-doctype knowledge. **Done
   when:** the functional domain is named and its reference is latched
   alongside this one.
2. Pull the relevant `Qkeee Bot Audit Log` rows via
   `core.client.query_resource()`, filtered by `reference_doctype`,
   `environment_tag`, and a date range on `timestamp`. Report `status`
   distribution (how many `Attempted` rows never resolved to `Success`/
   `Failure` — a crash signature worth flagging on its own) alongside the
   substantive findings. **Done when:** the `status` distribution is
   reported alongside the substantive findings, not separately or
   omitted.
3. **Redaction is already applied at write time**, not something to
   redo at query time: `core.client.redact_pii()` scrubbed
   `approval_note`/`channel_metadata` free text before it was ever
   written. Never re-introduce raw PII into an audit report by pulling it
   from a source outside the audit log (a linked record's own fields, a
   chat transcript) without applying the same redaction. **Done when:**
   no field pulled from outside the audit log lands in the report without
   the same redaction applied.
4. **State plainly which GRC guarantees are live vs. still aspirational**
   when a compliance-minded user asks — never imply a control is enforced
   in code before confirming it against this skill's actual
   `core/client.py`. Honesty about what's aspirational vs. live is itself
   a GRC property this skill should model, not undermine. **Done when:**
   every guarantee named in the reply is confirmed live against
   `core/client.py`, or explicitly marked aspirational.
5. **Segregation-of-duties questions** ("did the same person both approve
   and execute this write") map to `requested_by` (who asked) vs. whoever
   is actually operating this skill (the bot account, always) — this
   skill cannot itself certify that the human named in `requested_by`
   is different from whoever is prompting it right now; say so if asked,
   rather than implying a guarantee the architecture doesn't provide.
   **Done when:** the architecture's actual limit is stated, not implied
   away, when this question is asked.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Audit trail pull for a doctype/period | Every logged write, with requester/diff | Best-effort — flag any orphaned `Attempted` rows |
| Read-access review | Who read what, when | Every read is logged, unconditionally — including User/Role/DocType business reads |
| Permission-denial review | Who was refused, on what, and why | `status: "Failure"` + `response_payload.gate_check: true` |
| Segregation-of-duties question | `requested_by` vs. acting bot identity clarified | Cannot certify human identity beyond what's logged |
| GRC guarantee status check | Honest live-vs-planned answer | Never imply a control is enforced before confirming it in code |

## Relationships

Cross-cutting over every functional domain — always paired with one of
them in an actual conversation, never invoked alone. Depends on
`scripts/init_bot.py` having provisioned `Qkeee Bot Audit Log` on the
target instance; if it hasn't, say so rather than reporting an empty
trail as "clean."
