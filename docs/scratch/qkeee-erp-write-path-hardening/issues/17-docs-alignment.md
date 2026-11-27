# Align SKILL.md and references with the single write entry point

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P4
Blocked by: 11
Closes: W08, W20 (docs side)

## Interim (do now, alongside P0)

Remove the false "single entry point" claim until 11 lands. Add a short "Known gaps" note to `references/domains/system-admin.md` and `fixed-assets.md`. The note says the bespoke functions have no CLI yet, and must not be reached by hand-written scripts until 11 lands. If the user needs one of these operations, route it to a human.

## Full rewrite (after 11)

- `SKILL.md`, `00-conventions.md`, `01-connectivity.md` and `cli-cookbook.md` give one write flow:

  ```text
  confirm_token.py render --op ...  →  show user  →  user replies with code  →  execute_write.py --op ...
  ```

  Each also gets one worked example per op key, generated from `execute_write.py --list-ops`, so the docs can't drift.
- `references/domains/*.md`:
  - replace function names (`destructive_mutate()`, `create_user()`, `call_whitelisted_method()`, ...) with op keys
  - delete every `render_*.py` reference
  - remove `existing_roles` and `elevated_roles_acknowledged`, or describe the real precondition from 08
  - fix the `restore_asset` wording to "GL-reversing, token-gated"
- `governance.md`: state the token's guarantees honestly. A token proves the request matches what was rendered, the confirmation code proves a user reply referenced it, and RBAC per D1.
- `CHANGELOG.md`: add one entry per phase.

## Acceptance

- [x] `grep -rn "render_.*\.py\|destructive_mutate\|create_user(\|call_whitelisted_method\|existing_roles\|elevated_roles_acknowledged" references/ SKILL.md` returns nothing.
- [x] Each op key in `--list-ops` appears in at least one domain doc.

## Comments

**Interim part done 2026-10-06** (during P0): a "Known gaps" section was added to `references/domains/system-admin.md` and `fixed-assets.md`, covering the missing CLI for the gated functions and the missing render scripts. The `existing_roles` sentence in system-admin.md was corrected. The full rewrite still waits on 11.

**Full rewrite done 2026-10-06** (pulled forward from P4).

P2 removed the functions the docs named, so leaving the docs would have
broken the agent. Updated:

- `cli-cookbook.md` now has the single write flow and examples.
- `00-conventions.md`, `governance.md` and `SKILL.md`.
- Every domain doc. Fixed-assets and system-admin each got an operation
  table.
- `docs/qkeee-erpnext-feature-inventory.md`, the env example (ADMIN,
  KYC tax-ID and audit-mask variables), and `CHANGELOG.md`.

The acceptance grep returns nothing in `references/`, `SKILL.md` and
`docs/`.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
