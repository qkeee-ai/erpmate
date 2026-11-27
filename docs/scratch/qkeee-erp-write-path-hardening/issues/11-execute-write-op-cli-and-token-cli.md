# Expose every op through execute_write.py --op and one token/render CLI

Status: resolved (branch `fix/write-path-hardening-p0-p1`, tip fb0a77c, merged into erpnext-hermes `develop`; verified 2026-10-08)
Type: task
Phase: P2
Blocked by: 08, 09, 10
Blocks: 17
Closes: W08 (code side)

## Change

1. **`execute_write.py`**
   - Replace the `--domain` / `--doctype` / `--action` dispatch with `--op <key>` plus an op-specific `--args '<json>'`.
   - Keep `--doctype`, `--action`, `--payload` and `--name` as sugar for `*.generic` ops.
   - Keep the existing context flags and their WARN behaviour.
   - Make `--channel-metadata`, `--session-id` and `--latest-prompt` mandatory for every op whose policy is not `none`. Every gated write is then fully attributed. Today these flags only WARN.
   - Remove `--kyc` and `--kyc-waiver-confirmed` as top-level flags. They move into `--args` for `procurement.generic` Supplier create.
   - `--list-ops` prints each registered op: key, policy, owned pairs and its args schema.
2. **`confirm_token.py`**
   - Replace `advisory-token` with `render --op <key> --args ... --requested-by ...`.
   - It calls `prepare_only`, then prints:
     - the prepared request, in human-readable form
     - the schema-mapping notes
     - `confirmation_token`
     - `issued_at`
     - `confirmation_code`
   - The agent shows the user this render. This replaces the `render_*.py` scripts the docs reference but which never existed.
3. **Exit codes:** 0 on success, 2 on argparse errors, 3 when a gate refuses, 4 on a transport error with an unknown outcome (from 14). The agent can then branch without parsing the text.

## Acceptance

- [x] Every op in the registry is runnable through `execute_write.py --op`, and `--list-ops` shows it.
- [x] A CLI-level test runs `confirm_token.py render` and then `execute_write.py --op` with the printed token and a confirmation text containing the code. The write succeeds against a mocked transport.
- [x] The README and SKILL.md command blocks use only these two scripts for writes.

## Comments

**Implemented 2026-10-06.**

- `execute_write.py` gains `--op`/`--args` and `--list-ops`.
- The generic shorthand is kept, and the KYC, purchase-sourced,
  staged-fields and expected-modified flags fold into args.
- For gated operations the context flags are mandatory (exit 2).
- Exit codes are 0/1/2/3/4.
- `confirm_token.py render` replaces `advisory-token`.
- `--issued-at` is accepted as a string and coerced by the pipeline.

**Acceptance checked 2026-10-06 (P4 audit):** all boxes verified against the code and tests. Suite: 305 passed, 861 subtests.
