# Governance and status

Operator/maintainer material — read once at install or when reviewing
this skill's own health, not needed for a normal ERPNext session.
Relocated out of `SKILL.md` (2026-09-13 writing pass, batch 2 / C6) to
keep the router thin; nothing here changed meaning, only location.

## Governance: this skill is externally-owned, not curator-managed

This shipped skill must stay closed to Hermes' autonomous background-
review pass (which may otherwise "improve" its audit/RBAC/redaction
logic unsupervised), while the `qkeee-erp-learned/*` satellite skills
stay open to that same evolution — that's the whole point of the split.
The mechanism is `skills.external_dirs` in `config.yaml` (**a config
entry, not a frontmatter flag** — skill_usage.py deliberately keeps this
kind of policy out of user-authored SKILL.md content), which the
background-review write guard (`_background_review_write_guard`) checks
first and refuses ANY autonomous `edit`/`patch`/`delete`/`write_file`/
`remove_file` against, unconditionally — "external skills are read-only
to the curator." This repo's own `config.yaml` lists `skills/qkeee-erp`
under `skills.external_dirs`, which covers this skill (a subdirectory of
it). A foreground, user-directed edit is unaffected — the guard only
blocks the *autonomous* curator pass. Complementary belt-and-suspenders
option for whoever operates the live profile: `hermes curator pin
qkeee-erp-associate` additionally blocks `skill_manage(action="delete")`
itself, not just autonomous writes — this requires a live profile/CLI,
not a repo-side config change.

## Status note (read this before assuming a capability is fully live)

`scripts/core/client.py` and the domain modules with a write path are
real, tested code, including RBAC-every-environment and always-on read
audit logging (see `00-conventions.md`'s GRC baseline).

Two distinct things sit under "advisory-first draft," and only one of
them is code-enforced today:

- **The double-confirm GATE on submit/cancel/delete** (never let a write
  through without a fresh, exact-match confirmation over what was just
  shown to the user) **is code-enforced**, uniformly, via
  `core.client.mutate_resource()`'s `DOMAIN_TOKEN_GATED_ACTIONS` registry
  (`register_domain_token_gate()`) for accounts/hr-payroll/sales/
  procurement/inventory's submit/cancel, and via each domain's own
  bespoke token scheme for fixed-assets' depreciation/disposal and
  system-admin's destructive/permission/config actions. Compute the token
  via `scripts/core/confirm_token.py`'s `advisory-token` CLI (or a
  domain's own token constructor) over the exact facts confirmed — never
  hand-construct one.
- **Composing the draft's actual content** — a Journal Entry's balance
  check and narration, a cancel's impact statement, a Quotation's
  presentation — still depends on the `render_*.py` scripts several
  domain files describe, which are **not yet present in this skill's
  `scripts/` directory**. The gate above will refuse an unconfirmed
  submit/cancel either way, but nothing yet code-assists producing the
  draft itself; that part is still prompt discipline.

Don't claim a capability is fully enforced in code without confirming it
in `scripts/` — say what's live vs. planned plainly, the same discipline
`references/domains/grc-audit.md` asks of any GRC-framed conversation.

## Doc claims vs. actual config

Verify `config.yaml` before repeating a doc claim about it — a profile
can drift into having the claim without the config backing it. Two
confirmed cases: `profile.md` states local skill writes are gated by
`skills.write_approval` and reviewed before landing; that's only true
if `skills.write_approval: true` is actually set (the key defaults off,
`tools/write_approval.py`). `curator.consolidate` (default off,
`agent/curator.py`) is the mechanism that would otherwise merge
overlapping agent-created skills back into this one — it doesn't run
unless explicitly turned on. Neither is this skill's own code to
enforce; flag a mismatch to the operator if ever discovered, same as the
`external_dirs` check above.
