# Load less before the first ERPNext call: preflight before meta, lazy references

Status: resolved
Type: task
Blocked by: none
Findings: F8

## Problem

Of 213k context chars in the run, about 155k were references and tool output the decision did not need:

| Item | Chars | Needed? |
|---|---|---|
| `00-conventions.md` | 33k | partly |
| `erp_discover meta Item` | 33k | no: `preflight` returned mandatory, conditional, naming and workflow |
| `list_ops` (all ops) | 14k | no: cookbook had the Item example; one op needed |
| `01-connectivity.md` | 13k | partly |
| `03-spec-driven-execution.md` | 13k | yes |
| `inventory.md` | 7k | no: nothing on Item master (fixed by 01) |

Every later LLM call resends all of it.

## Change

- `SKILL.md` activation step 4 and `03` step 2: for a create, run `erp_discover preflight` with the draft payload first (a partial payload is fine: it lists each mandatory field with `in_payload`). Use `meta` only to look up a field `preflight` does not cover.
- `SKILL.md` domain table: Item row says "Item master: `unscoped.generic`, see inventory.md 'Item master'".
- `SKILL.md`: for the first write, `list_ops` with `op=<key>` (issue 06) instead of the full list.

## Acceptance

- The three doc changes above. Measured on the next live run (human): no `meta` call when `preflight` covers the create.

## Comments

- Implemented 2026-10-14: `SKILL.md` step 4 (load references when their step starts), Domain table (`list_ops` with `domain`/`op`; preflight before meta; Item row points at "Item master"); `03` step 2 (preflight early, meta only for gaps). Live measurement still to do on the next run.
