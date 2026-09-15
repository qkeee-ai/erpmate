# CLI cookbook: worked examples for `core/client.py` and `execute_write.py`

Copy-paste call shapes only — the mechanics behind why each shape is
correct (auth, query cost, `qkeee-erp.env`, `discover.py`) live in
`01-connectivity.md`; read that first if a call here doesn't make sense
on its own. Split out (2026-09-13 writing pass, batch 2 / C7) so a
single-domain read-only lookup doesn't have to load worked-example
prose it won't use — nothing here changed meaning, only location.

## Subcommand list

`core/client.py` and each `domains/<slug>.py` module are runnable
directly for manual/ad hoc use. See `core/client.py`'s own `_cli()` for
the full subcommand list (`health`, `list-envs`, `query`, `get`, `report`,
`roles`, `mutate`, `gated-mutate`).

**`core/client.py`'s own `mutate`/`gated-mutate` subcommands are read-only-safe to explore but not the write entry point — use `execute_write.py` for every actual write.** `mutate --domain <slug>` looks like the domain-scoped write path, but `core/client.py` never imports any `domains/*.py` module itself — run standalone in a fresh process, `--domain procurement` 404s with "domain has no registered ALLOWED_WRITE_DOCTYPES" even though `domains/procurement.py` genuinely declares one (`register_domain_allowlist()` only runs at that module's *own* import time). `scripts/execute_write.py` imports every `domains/*.py` module up front specifically so this isn't a trap, and it's the one write entry point regardless of whether the target doctype belongs to a named domain (`--domain <slug>` → `mutate_resource()`) or not (omit `--domain`, pass `--confirmation-token`/`--issued-at` → `gated_mutate_resource()`). See its own module docstring — this is also where a real bug got fixed: hand-writing a fresh one-off Python script per write is exactly how `session_id`/`channel_metadata`/`latest_prompt` kept getting left blank, and `execute_write.py` WARNs loudly on stderr, before the write fires, if any of those three are missing — don't route around that warning by constructing the underlying `mutate_resource()`/`gated_mutate_resource()` call directly in a hand-written script instead.

**Exact path — don't guess it.** The script lives at
`<profile>/skills/qkeee-erp/qkeee-erp-associate/scripts/core/client.py`
(the `qkeee-erp-associate/` segment is easy to drop — `skills/qkeee-erp/
scripts/...` is a real, previously-observed wrong guess that costs a
wasted round trip). `cd` into `.../qkeee-erp-associate/scripts` first, or
prefix every command with the full path — verify with one `search_files`/
listing at the start of a session if there's any doubt, rather than
guessing and retrying on failure.

## Read-only calls

**Copy these verbatim, substitute values, don't hand-construct flags
from memory.** A malformed `--filters`/`--fields` argument is a
previously-observed wasted round trip (`usage: client.py [-h] [--tag
TAG]...` argparse error) — these four cover the overwhelming majority of
read-only lookups:

```
# Connectivity + auth check — run first, every session
python core/client.py --tag <tag> health

# List configured environment tags
python core/client.py --tag <tag> list-envs

# Filtered, field-scoped list query — the default shape for "fetch X
# where Y" asks. filters is a JSON list of [field, operator, value]
# triples; fields is a JSON list of field names (see 01-connectivity.md's
# "Query cost" section for why to always scope fields).
python core/client.py --tag <tag> query <DocType> \
  --filters '[["supplier", "=", "<value>"], ["company", "=", "<value>"], ["docstatus", "=", 0]]' \
  --fields '["name", "supplier", "company", "posting_date", "grand_total", "status"]' \
  --limit 20

# Single-resource GET — full doc including child tables (line items,
# etc.) — use only when child-table data is actually needed, see
# 01-connectivity.md's "Query cost" section.
python core/client.py --tag <tag> get <DocType> <name>
```

`docstatus`: `0` = Draft, `1` = Submitted, `2` = Cancelled — use this in
`--filters` for any "draft"/"submitted"/"cancelled" phrasing in the
request rather than a status-name guess.

## Write calls

For a write, see the matching `domains/<slug>.md` file for the exact
payload shape and required fields — don't freehand a payload from this
cookbook alone. Fire the write itself through `execute_write.py` (see
above), never a hand-written script that reconstructs
`mutate_resource()`/`gated_mutate_resource()` inline:

```
# Domain-scoped write — Supplier belongs to procurement's
# ALLOWED_WRITE_DOCTYPES. Supplier 'create' additionally requires --kyc
# (creates the linked Address/Contact in the same call — see
# domains/procurement.md's "Supplier KYC write order") or, only when the
# user explicitly confirmed proceeding without it, --kyc-waiver-confirmed.
python execute_write.py --tag <tag> --mode read-write \
  --requested-by <requester-email> --doctype Supplier --action create \
  --domain procurement \
  --payload '{"supplier_name": "<value>", "supplier_type": "Company", ...}' \
  --kyc '{"address": {"address_line1": "<value>", "<tax-id field confirmed via discover.py meta \"Address\">": "<value>", ...}}' \
  --session-id <this-logical-session-id> \
  --channel-metadata '{"space": "<platform-space-id>", "thread": "<platform-thread-id>"}' \
  --prompt-summary "<one-line paraphrase>" \
  --latest-prompt "<the user's literal most-recent message>"

# Domain-less write — Item belongs to no domain's allowlist yet. For an
# item sourced from a purchase document (PO, purchase invoice, GRN), add
# --purchase-sourced-item: defaults is_purchase_item=1/is_sales_item=0
# (nothing in a purchase document supports "the org resells this") and
# refuses a bare standard_rate key (that auto-creates a Standard SELLING
# Item Price from what was actually a purchase cost; see
# item_write_helpers.py for the buying-side alternative). Omit --domain,
# supply the advisory-draft token, AND the user's own literal reply
# containing confirm_token.py's printed
# confirmation_code (show them the code in the rendered draft first —
# never construct this string yourself, see confirmation_code()'s
# docstring for why that defeats the point).
python execute_write.py --tag <tag> --mode read-write \
  --requested-by <requester-email> --doctype Item --action create \
  --payload '{"item_code": "<value>", ...}' \
  --confirmation-token <from confirm_token.py> --issued-at <same> \
  --user-confirmation-text "<the user's actual reply, e.g. 'yes 284D51'>" \
  --session-id <this-logical-session-id> \
  --channel-metadata '{"space": "<platform-space-id>", "thread": "<platform-thread-id>"}' \
  --prompt-summary "<one-line paraphrase>" \
  --latest-prompt "<the user's literal most-recent message>"
```

Resolve `--session-id`/`--channel-metadata`/`--latest-prompt` **once**, at
the start of the logical session, and reuse the same values across every
write in it — don't re-derive them per call, and don't leave them out
because the write "feels routine." An audit row with `session` blank or
`channel_metadata` absent is exactly as unauditable as a write that never
happened, even though the write itself succeeded — see this skill's own
GRC baseline (`00-conventions.md`).
