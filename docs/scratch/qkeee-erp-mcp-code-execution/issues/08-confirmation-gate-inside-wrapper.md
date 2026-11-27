# Run the unscoped-write confirmation gate inside the wrapper, not the model

Status: needs-triage
Type: task
Blocked by: 02

## Problem

The demo procurement domain-less decision confirmation-gates every unscoped Item/Item Group/Purchase Receipt create. Today the model needs the full record in context to evaluate whether the gate should fire, which is itself a token cost the earlier fix attempt couldn't remove without breaking tests (per that decision's notes).

## Change

- Move the gate check into the wrapper call for `create_item`/`create_item_group`/`create_purchase_receipt`: the wrapper evaluates the gate against the record it already has, and returns only a verdict (approved / needs-confirmation + reason) to the model.
- Confirm this doesn't change the gate's behavior, only where it runs — re-run whatever test previously broke when the domain-less fix was attempted, to check whether moving the check (rather than removing it) avoids that regression.

## Done when

The gate fires on the same cases as today, the model no longer needs the full record in context to see a gate decision, and the previously-breaking test passes.

## Comments
