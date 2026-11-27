# Generate servers/qkeee-erp/* wrapper modules from the erp_* tool registry

Status: needs-triage
Type: task
Blocked by: 01

## Problem

The code-execution pattern needs each `erp_*` tool exposed as a typed function on a filesystem (`servers/qkeee-erp/get_item.ts`, `create_purchase_receipt.ts`, …), not as a direct tool-call definition in the model's context. The agent should discover servers by listing a directory and read only the wrapper files it needs, rather than loading every `erp_*` tool definition upfront.

## Change

- Generate one wrapper file per registered `erp_*` tool, from the same registry the Plugin uses to register tools today, so the two never drift.
- Each wrapper calls through to the real tool exactly as a direct call would: Requester Gate check, audit log write, then the ERPNext call.
- A directory listing (`servers/qkeee-erp/index`) that names each wrapper without its full schema, for progressive discovery; the full typed signature lives in the file itself.

## Done when

An agent on the `cli` toolset can list `servers/qkeee-erp/`, read one wrapper file, and call it from generated code, with the Requester Gate and audit log firing exactly as they do for a direct `erp_*` tool call today.

## Comments
