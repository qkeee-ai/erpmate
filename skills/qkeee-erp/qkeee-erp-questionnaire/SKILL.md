---
name: qkeee-erp-questionnaire
description: "Turn an ERPNext data gap only someone else can fill (supplier KYC, HR detail, a doc-extraction low-confidence field) into a questionnaire to hand them."
metadata:
  hermes:
    tags: [ERPNext, Questionnaire, Data-Gap, Procurement, HR, Doc-Extraction]
    related_skills: [qkeee-erp-associate]
---

# qkeee-erp-questionnaire

A write is blocked on data only a *different* person holds — a
supplier's GSTIN a purchase invoice never carried, an HR field
`doc-extraction`'s staged report flagged `confidence: "low"`/`value:
null`, a KYC gap `procurement.py` refuses to proceed past
(`IncompleteSupplierKYCError`). This skill turns that gap into a
**questionnaire**: a markdown document the requester forwards to
whoever actually knows the answer.

**Not for** a question the requester themselves can answer right now in
this chat — that's just asking. Reach for this only when the gap crosses
to a different person.

**Grill the send, not the subject.** Interview the requester only about
who the questionnaire goes to and what's needed back — they can always
answer that. The questionnaire's own questions target the gap between
what the recipient knows and what the record needs; don't interview the
requester about facts they've already said they don't have.

## Procedure

1. **Identify the gap.** Name the exact missing field(s) and the record
   they belong to — from `doc-extraction`'s staged report (a `low`/
   `null` field), a domain's own completeness refusal (procurement's
   KYC gate, or similar), or wherever else the block surfaced. **Done
   when:** the missing fields are named exactly, not summarized as
   "more info needed."
2. **Who's it going to?** The actual supplier's own contact, an HR
   contact, the requester's manager — ask if it isn't already obvious
   from context. **Done when:** the recipient's role/relationship to the
   record is known.
3. **What's needed back?** Tie each question to the doctype's real field
   name — confirm via `discover.py meta` (per `01-connectivity.md`) if
   this session hasn't already resolved the live schema — so the
   recipient's answer maps cleanly onto a payload later instead of
   needing re-interpretation. **Done when:** every open field maps to a
   real, confirmed field name on the target doctype.
4. **Write it** using the template below. Save to
   `./qkeee-erp-specs/questionnaire-<slug>-<YYYYMMDD-HHMM>.md` — same
   working-directory placement rule as a task spec
   (`00-conventions.md`'s naming table, `03-spec-driven-execution.md`'s
   step 3) — and report the path. **Done when:** the file exists at that
   path and every field from step 1 is covered by a question.
5. **An answered questionnaire is raw input, not an approved draft.**
   Route the recipient's answers through the same present-confirm-write
   discipline as any other domain write (`00-conventions.md`
   Non-negotiable 5) — never write a questionnaire's answers straight
   into a business record without that review step, and never write a
   collected PII/sensitive field in raw form (Non-negotiable 6).

## Template

```markdown
# Questionnaire: <short title>

**Purpose:** <the write/task this unblocks — one sentence>
**From:** <requester>, **To:** <recipient>
**Record:** <doctype/name, or "not yet created">

## Missing fields

| Field | Doctype | Why it's needed |
| --- | --- | --- |
| <field> | <doctype> | <one line> |

## Questions

1. **<field/topic>**: <the actual question, plain language, not the raw
   field name>
```

## Relationships

Consumes `domains/doc-extraction.md`'s staged report (a `low`/`null`
field is the most common trigger) and `references/domains/procurement.md`'s
KYC gate. Feeds back into whichever domain's write the gap was blocking —
this skill's job ends at the questionnaire file; the receiving domain
still owns its own Confirm → Execute steps.
