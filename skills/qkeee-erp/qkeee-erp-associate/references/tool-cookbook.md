# Tool cookbook: worked `erp_*` calls

Copy-paste call shapes only. The calling rules (requester, audit
context, the confirmed-write flow, refusals) are in the plugin skill
`qkeee-erp:usage`; load it with `skill_view("qkeee-erp:usage")` before
the first write of a session. The mechanics behind each shape (query
cost, live metadata) are in `01-connectivity.md`. Latch this file only
once a call is about to run.

No tool takes a requester or a mode. `tag` is optional: omit it to use
the active Instance that `erp_discover` `health` reports.

## Read-only calls

**Copy these shapes, substitute values, do not build arguments from
memory.** These four cover most read-only lookups:

```
# Connectivity + auth check: run first, every session.
# Returns the active Instance's tag and base_url, and gaps[].
erp_discover(action="health")

# Filtered, field-scoped list query: the default shape for "fetch X
# where Y". filters is a list of [field, operator, value] triples;
# fields is a list of field names (01-connectivity.md, "Query cost":
# always scope fields).
erp_query(doctype="<DocType>",
          filters=[["supplier", "=", "<value>"], ["company", "=", "<value>"], ["docstatus", "=", 0]],
          fields=["name", "supplier", "company", "posting_date", "grand_total", "status"],
          limit=20, prompt_summary="<one line>")

# One record with child tables (line items, etc.). Use only when the
# child-table data is needed (01-connectivity.md, "Query cost").
erp_get(doctype="<DocType>", name="<name>", prompt_summary="<one line>")

# A built-in query report
erp_report(report_name="Accounts Receivable", filters={"company": "<value>"},
           prompt_summary="<one line>")
```

`docstatus`: `0` = Draft, `1` = Submitted, `2` = Cancelled. Use it in
`filters` for any "draft"/"submitted"/"cancelled" phrasing rather than a
status-name guess.

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

`erp_execute_write` is the only write path. For the exact payload shape
and required fields, see the matching `domains/<slug>.md`; do not
freehand a payload from this cookbook alone.

```
# Every operation, its arguments, whether it needs a confirmation, and a
# ready-to-edit worked example (`example_args`) for each one
erp_execute_write(phase="list_ops")
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
need no token. `list_ops` shows each operation's rule.

### The confirmed write: render, show, execute

1. **Render.** The result has `request` (exactly what will be sent),
   `args` (pass back UNCHANGED), `confirmation_token`, `issued_at` and
   `confirmation_code`:

   ```
   erp_execute_write(phase="render", op="<op>", args={...},
                     prompt_summary="<one line>", latest_prompt="<the user's message>")
   ```

2. **Show.** Show the user `request` and the code, for example "reply
   `yes 3F0A9C` to confirm". Wait for their reply in a later turn.
3. **Execute.** Pass the rendered values and the user's own reply:

   ```
   erp_execute_write(phase="execute", op="<op>", args=<rendered args>,
                     confirmation_token="<rendered>", issued_at=<rendered>,
                     user_confirmation_text="<the user's actual reply>",
                     prompt_summary="<one line>", latest_prompt="<the user's literal message>")
   ```

**Refusals on execute:**

- Any change between render and execute is refused: args, record,
  requester, or 15 minutes elapsed. The record counts as changed when its
  `modified` timestamp moves after a submit/cancel/delete render.
  Render again and confirm again.
- Never construct the reply text yourself.
- A write that needs confirmation is refused without `latest_prompt`.

**Results:** `ok: true` with `result`; or `ok: false` with `error` and
one of `refused` (a gate refused, nothing was sent), `write_failure`
(ERPNext rejected it and named the fields), `invalid_arguments` (nothing
was sent) or `outcome_unknown` (re-read the record before any retry).
`warnings` on a success (for example an audit-log problem) go to the
user.

**Deletes on an Instance provisioned before 2026-10-06** fail with
`LinkExistsError` ("linked with Qkeee Bot Audit Log") until an Operator
runs `hermes qkeee-erp init-bot` there again. Its audit log still links
to every record it describes, which blocks the delete. Tell the user the
delete needs that one-time migration; for a draft, offer to disable or
leave it instead. Never retry the delete.

### Worked examples

```
# Draft create on a domain doctype: sales.generic, no confirmation
erp_execute_write(phase="execute", op="sales.generic",
                  args={"doctype": "Sales Order", "action": "create",
                        "payload": {"customer": "<value>", "items": [...]}},
                  prompt_summary="<one line>", latest_prompt="<msg>")

# Submit it later: render binds the record's current `modified`
erp_execute_write(phase="render", op="sales.generic",
                  args={"doctype": "Sales Order", "action": "submit", "name": "SAL-ORD-0001"})
# ...show, get the reply, then execute with the rendered args/token/issued_at

# Supplier create: KYC is mandatory. An address WITH a tax ID (gstin/
# tax_id/pan), or kyc_waiver_confirmed only when the user explicitly
# waived it. Linked Address/Contact are created in the same operation and
# the Supplier is rolled back if they fail. See domains/procurement.md.
erp_execute_write(phase="execute", op="procurement.generic",
                  args={"doctype": "Supplier", "action": "create",
                        "payload": {"supplier_name": "<value>", "supplier_type": "Company"},
                        "kyc": {"address": {"address_line1": "<v>", "city": "<v>",
                                            "country": "<v>", "gstin": "<v>"}}},
                  prompt_summary="<one line>", latest_prompt="<msg>")

# Doctype no domain owns (e.g. Item): unscoped.generic, every action is
# confirmed. For an item sourced from a purchase document add
# "purchase_sourced_item": true (is_purchase_item=1/is_sales_item=0; a
# bare standard_rate is refused).
erp_execute_write(phase="render", op="unscoped.generic",
                  args={"doctype": "Item", "action": "create", "purchase_sourced_item": true,
                        "payload": {"item_code": "<v>", ...}})

# A bespoke gated operation
erp_execute_write(phase="render", op="system_admin.disable_user",
                  args={"name": "<user email>", "reason": "<stated reason>"})

# Several writes, stop at the first failure (each step carries its own
# confirmation fields when it needs them)
erp_execute_write(phase="execute", batch=[{"op": "hr_payroll.generic", "args": {...}}, ...],
                  prompt_summary="<one line>", latest_prompt="<msg>")
```

Pass `prompt_summary` and `latest_prompt` on every write, even one that
"feels routine". The tools take the session and channel for the audit
row from the gateway; an audit row without the user's own words is not
auditable (`00-conventions.md`, GRC baseline).

## Operator-only commands

The Operator runs the same calls from a shell on the gateway host as
`hermes -p <profile> qkeee-erp <command> --requested-by <email>` (for
example `query`, `render`, `execute-write`), plus `init-bot` to provision
an Instance. These are not for the agent: the agent has no
`--requested-by`, and its terminal cannot reach the gateway.
