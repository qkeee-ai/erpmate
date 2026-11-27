# qkeee-erp: fixes from the Media Marketing Services Item run

Status: 01, 02, 04, 05, 06 resolved 2026-10-14 (uncommitted); 03 code done, live check open (ready-for-human); 07 ready-for-human (upstream); 08 summary email: code + docs done, live check open
Raised: 2026-10-14, after a review of the first full Item create on demo_erp
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (Profile skill docs, `plugins/qkeee-erp`, `docker-compose.yaml`), `hermes-agent` and Jev (issue 07 only, upstream)
Evidence: `erpnext-hermes/logs/demo-erp-plugin/openrouter-prompt.log` (75 messages) and `media-marketing-services-item-20261014-0619.md` (the spec the agent wrote)

## Goal

The next Item create (or any master-data create) on an Instance:

- asks the questions a functional consultant asks, so a purchase service does not book to the company default expense account;
- shows the user the spec before approval, at a path the procedure expects;
- records in the Audit Log whether the approval was verified by a token or only asserted by the agent;
- loads less context before the first ERPNext call.

## What went right (keep)

- HTTP 502 handled: one diagnostic retry, "nothing changed", no workaround.
- Requester from `whoami`; Instance and mode stated before the write.
- Clarify asked the whole frontier in one round, with recommended values.
- "Item is non-submittable, live on save" said before approval.
- Token TTL checked with `date +%s` before execute; re-fetch via `erp_get` after the write.
- The code-only reply (`65AE45` instead of `yes 65AE45`) reported honestly.

## Findings → issues

| # | Finding | Issue |
|---|---|---|
| F1 | No expense account, SAC, Item Tax Template or cost center asked for a purchase service. ERPNext then uses the company `default_expense_account`. | 01 |
| F2 | `purchase_sourced_item: true` used for a typed request. The flag is for an Item whose only evidence is a purchase document. Its default `is_stock_item: 1` was only overridden because the payload set 0. | 01 |
| F3 | `user_approved: true` is agent-set. The Audit Log shows "Approved" the same way for a token-verified write and for an agent assertion. | 02 |
| F4 | The gateway enforces `HERMES_WRITE_SAFE_ROOT=/opt/data` (base image). `terminal.cwd` is `/opt/cwd`. Every spec write to `./qkeee-erp-specs/` is denied, and the fallback lands in the sidecar's `terminal-home` volume, which a Google Chat user cannot see. | 03 |
| F5 | The user approved field values before a spec existed. The spec was written in the same turn as render and was never shown before execute. | 04 |
| F6 | Spec hygiene: confirmation token persisted; rollback "disable" path not an exact call; Deviations "None" though three deviations happened; UTC slug against IST record times. | 04 |
| F7 | Docs say "reply `yes <code>`"; the gate accepts any reply that contains the code. The agent asked for "exactly `yes 65AE45`". | 04 |
| F8 | About 155k of 213k context chars were references, `meta Item` (33k), `list_ops` (14k). `meta` was redundant with `preflight`. `inventory.md` loaded but has nothing on Item master. | 05, 06 |
| F9 | 3 failed `tool_call` wrapper calls (stringified `calls`, multi-local batch) and a loop warning. | 07 |
| F10 | The Jev skill router suggested `jev-setup` (0.6) for "check the connectivity now"; the agent loaded a 6.8k unrelated skill. | 07 |

## Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Remove `user_approved` from the tool? | No. It is the only approval signal for no-token drafts (`<domain>.generic` create/update), and the doctype documents it as a detection field. Keep it, and record the basis of approval in `approval_note` (issue 02). No Audit Log schema change, so no `init-bot` rerun on Instances. |
| D2 | Where does Item master guidance live? | `domains/inventory.md` (the domain table already routes Item there). Item stays on `unscoped.generic` until it gets a domain module. |
| D3 | Spec path on a gateway session | Add `/opt/cwd` to the gateway's `HERMES_WRITE_SAFE_ROOT`. The spec then lands in the cwd mount that both the sidecar and the host Operator can see. |
| D4 | Upstream issues (F9, F10) | Write up only. `hermes-agent` and Jev are not changed from this repo. |
