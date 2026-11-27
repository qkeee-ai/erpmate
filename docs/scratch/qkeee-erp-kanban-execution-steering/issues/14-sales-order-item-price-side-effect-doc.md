# Sales Order creation silently auto-creates Item Price rows; undocumented

Status: ready-for-agent
Blocked by: none

## What happened

29-Oct live run: creating Sales Orders auto-created 4 `Item Price` rows outside the planned write set. Nothing in the spec, the domain reference, or the preflight check named this side effect. The agent had to detect the extra records after the fact and fold their deletion into the rollback batch by querying creation timestamps — a reconstruction, not something the plan anticipated.

## Fix

Document the side effect in `skills/qkeee-erp/qkeee-erp-associate/references/domains/sales.md`: Sales Order creation can auto-create `Item Price` rows (name the triggering condition if known — e.g. a price list without an existing rate for the item/UOM). A spec that creates Sales Orders and may need a rollback should name this in its Rollback section up front, not discover it via timestamp queries after the fact.

## Depends on

Nothing.
