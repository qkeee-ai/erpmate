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
`roles`). All of them are read-only.

**`core/client.py` has no write subcommand. Use `execute_write.py` for every write.** It imports every `domains/*.py` module up front, so every domain's allowlist and operations are registered before the write runs, and it runs a named operation (`--op`, or the `--domain`/`--doctype`/`--action` shorthand for a `<domain>.generic` operation — `unscoped.generic` when `--domain` is omitted). See "Write calls" below. Never route around it by calling the pipeline or a domain function from a hand-written script. In Python (tests, other skills' scripts), the write API is `core/operations.py`: `run_operation(op_key, args, WriteContext(...))`, `prepare_only()` to render, and `call_generic()` as the keyword-style spelling for a generic operation — `core/client.py` has no write function.

**Exact path — don't guess it.** Every command below is written
relative to the skill directory (`scripts/...`). Run it with the skill
directory prefixed — the absolute path, e.g.
`python <profile>/skills/qkeee-erp/qkeee-erp-associate/scripts/core/client.py`
(SKILL.md gives it as `${HERMES_SKILL_DIR}`). **Never `cd` into `scripts/`:**
the terminal keeps its working directory between calls, so the next
`scripts/...` path becomes `scripts/scripts/...` and fails (observed live,
2026-10-06). Don't drop the `qkeee-erp-associate/` segment either —
`skills/qkeee-erp/scripts/...` is another observed wrong guess.

## Read-only calls

**Copy these verbatim, substitute values, don't hand-construct flags
from memory.** A malformed `--filters`/`--fields` argument is a
previously-observed wasted round trip (`usage: client.py [-h] [--tag
TAG]...` argparse error) — these four cover the overwhelming majority of
read-only lookups:

```
# Connectivity + auth check — run first, every session
python scripts/core/client.py --tag <tag> health

# List configured environment tags
python scripts/core/client.py --tag <tag> list-envs

# Filtered, field-scoped list query — the default shape for "fetch X
# where Y" asks. filters is a JSON list of [field, operator, value]
# triples; fields is a JSON list of field names (see 01-connectivity.md's
# "Query cost" section for why to always scope fields).
python scripts/core/client.py --tag <tag> query <DocType> \
  --filters '[["supplier", "=", "<value>"], ["company", "=", "<value>"], ["docstatus", "=", 0]]' \
  --fields '["name", "supplier", "company", "posting_date", "grand_total", "status"]' \
  --limit 20

# Single-resource GET — full doc including child tables (line items,
# etc.) — use only when child-table data is actually needed, see
# 01-connectivity.md's "Query cost" section.
python scripts/core/client.py --tag <tag> get <DocType> <name>
```

`docstatus`: `0` = Draft, `1` = Submitted, `2` = Cancelled — use this in
`--filters` for any "draft"/"submitted"/"cancelled" phrasing in the
request rather than a status-name guess.

## Write calls

Every write is a named **operation**, run through one pipeline:

1. mode
2. requester
3. allowlist
4. ownership
5. preconditions
6. confirmation token
7. RBAC
8. audit
9. send

`execute_write.py` is the only write CLI. Never hand-write a script that
calls the pipeline or a domain function directly. For
the exact payload shape and required fields, see the matching
`domains/<slug>.md`; don't freehand a payload from this cookbook alone.

```
# Every operation, its arguments, whether it needs a confirmation, and a
# ready-to-edit worked example (`example_args`) for each one
python scripts/execute_write.py --list-ops
```

Start every write from that operation's `example_args`: replace the
values, keep the keys. They are the same examples the test suite drives
through every gate, so they stay valid as the code changes.

**Which writes need a confirmation:**

- Every bespoke operation, such as `system_admin.disable_user` or
  `fixed_assets.scrap`.
- `submit`/`cancel`/`delete` on a domain's `<domain>.generic` operation.
- Every action on `unscoped.generic` and on `system_admin.generic`
  (it sends with the admin key).

`create`/`update` on any other `<domain>.generic` operation are drafts and
need no token. `--list-ops` shows each operation's rule.

### The confirmed write: render, show, execute

1. **Render.** This prints `request` (exactly what will be sent),
   `args` (pass back UNCHANGED), `confirmation_token`, `issued_at` and
   `confirmation_code`:

   ```
   python scripts/core/confirm_token.py render --op <op> --args '<json>' \
     --tag <tag> --requested-by <requester-email>
   ```

2. **Show.** Show the user `request` and the code, for example "reply
   `yes 3F0A9C` to confirm". Wait for their reply in a later turn.
3. **Execute.** Pass the printed values and the user's own reply:

   ```
   python scripts/execute_write.py --tag <tag> --mode read-write \
     --requested-by <requester-email> --op <op> --args '<printed args>' \
     --confirmation-token <printed> --issued-at <printed> \
     --user-confirmation-text "<the user's actual reply>" \
     --session-id <session> --channel-metadata '<json>' \
     --prompt-summary "<one line>" --latest-prompt "<the user's literal message>"
   ```

**Refusals on execute:**

- Any change between render and execute is refused: args, record,
  requester, or 15 minutes elapsed. The record counts as changed when its
  `modified` timestamp moves after a submit/cancel/delete render.
  Re-render and re-confirm.
- Never construct the reply text yourself.
- For a confirmed write, `--session-id`, `--channel-metadata` and
  `--latest-prompt` are mandatory. The CLI refuses without them.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | ERPNext rejected it |
| 2 | usage error, including malformed `--args` (nothing was sent) |
| 3 | refused by a gate, nothing was sent |
| 4 | outcome unknown or partial — re-read the record before any retry |

`core/client.py` reads use the same codes: 3 when a gate (requester, RBAC,
missing doctype) refuses the read, 1 for any other error.

**Deletes on an instance provisioned before 2026-10-06** fail with
`LinkExistsError` ("linked with Qkeee Bot Audit Log") until an admin
re-runs `init_bot.py` there. Its audit log still links to every record
it describes, which blocks the delete. Tell the user the delete needs
that one-time migration; for a draft, offer to disable or leave it
instead. Never retry the delete.

### Worked examples

```
# Draft create on a domain doctype — shorthand for --op sales.generic
python scripts/execute_write.py --tag <tag> --mode read-write --requested-by <email> \
  --domain sales --doctype "Sales Order" --action create \
  --payload '{"customer": "<value>", "items": [...]}' \
  --session-id <s> --channel-metadata '<json>' --latest-prompt "<msg>"

# Submit it later: render binds the record's current `modified`
python scripts/core/confirm_token.py render --op sales.generic \
  --args '{"doctype": "Sales Order", "action": "submit", "name": "SAL-ORD-0001"}' \
  --tag <tag> --requested-by <email>
# ...show, get the reply, then execute with the printed args/token/issued_at

# Supplier create — KYC is mandatory: an address WITH a tax ID (gstin/
# tax_id/pan), or kyc_waiver_confirmed only when the user explicitly
# waived it. Linked Address/Contact are created in the same operation and
# the Supplier is rolled back if they fail. See domains/procurement.md.
python scripts/execute_write.py --tag <tag> --mode read-write --requested-by <email> \
  --domain procurement --doctype Supplier --action create \
  --payload '{"supplier_name": "<value>", "supplier_type": "Company"}' \
  --kyc '{"address": {"address_line1": "<v>", "city": "<v>", "country": "<v>", "gstin": "<v>"}}' \
  --session-id <s> --channel-metadata '<json>' --latest-prompt "<msg>"

# Doctype no domain owns (e.g. Item) — unscoped.generic: every action is
# confirmed. For an item sourced from a purchase document add
# "purchase_sourced_item": true (is_purchase_item=1/is_sales_item=0; a
# bare standard_rate is refused — see item_write_helpers.py).
python scripts/core/confirm_token.py render --op unscoped.generic \
  --args '{"doctype": "Item", "action": "create", "purchase_sourced_item": true, "payload": {"item_code": "<v>", ...}}' \
  --tag <tag> --requested-by <email>

# A bespoke gated operation
python scripts/core/confirm_token.py render --op system_admin.disable_user \
  --args '{"name": "<user email>", "reason": "<stated reason>"}' \
  --tag <tag> --requested-by <email>
```

Resolve `--session-id`/`--channel-metadata`/`--latest-prompt` **once**, at
the start of the logical session, and reuse the same values across every
write in it — don't re-derive them per call, and don't leave them out
because the write "feels routine." An audit row with `session` blank or
`channel_metadata` absent is exactly as unauditable as a write that never
happened, even though the write itself succeeded — see this skill's own
GRC baseline (`00-conventions.md`).
