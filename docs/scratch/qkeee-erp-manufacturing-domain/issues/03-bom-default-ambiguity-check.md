# BOM default-marking ambiguity check

Status: partially done (erpnext-hermes@eb99887) — code + unit tests shipped, live round trip against demo_erp still open

Blocked by: 01

## Problem

`references/domains/manufacturing.md`'s procedure step 2 (currently
"proposed, needs live confirmation") says: query existing BOMs for an
item before assuming none exists, and flag — don't silently accept —
a BOM with no default marked, or more than one active default for the
same item.

## Proposed fix

Build this as a `preconditions`/`enrich` hook on `manufacturing.generic`
(or a BOM-specific named op if the create/update shape warrants one,
matching how other domains split a generic fallback from named ops for
well-known patterns): before a BOM create/update that sets
`is_default`, query sibling BOMs for the same item and refuse (with a
clear message naming the conflicting BOM(s)) if the result would leave
zero or more than one active default. Live-validate against demo_erp:
create two BOMs for the same item, confirm the check actually fires,
then promote `manufacturing.md`'s "proposed, needs live confirmation"
language for this specific claim to confirmed (or correct it if
ERPNext's own behavior differs from the hypothesis).
