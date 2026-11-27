# qkeee-erp: fixes from the QKEEE LLP HRMS demo build

Status: decisions D1–D5 taken 2026-10-22 (nikhil.sharma@qkeee.ai); split into `issues/01`–`17` on 2026-10-22 (06 merged into 02). ready-for-agent: 01–05, 07–12. ready-for-human: 13–17 (17 starts with a live check on demo_erp).
Raised: 2026-10-22, after a review of the full HRMS demo-data build on demo_erp (dev-erp)
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (`plugins/qkeee-erp`, Profile skill docs, `config.yaml`, `docker/terminal`), `agents` (ADR 0003 and a new ADR), `hermes-agent` and Jev (upstream only)
Evidence:
- `erpnext-hermes/logs/demo-hrms-22-oct-run.log`: 216 messages, cards T1–T10.
- `erpnext-hermes/logs/demo-hrms-22-oct-run-part2.log`: three concatenated requests.
  - Request A: 243 messages, payroll fix to recruitment.
  - Request B: 108 messages, onboarding, exit and the final check. It repeats the tail of A after a mid-turn compaction.
  - Request C: the background skill review. The log stops after `skills_list`.
- Model: `deepseek/deepseek-v4.1-flash` through OpenRouter, platform google_chat.

## Goal

The next multi-step ERP build (a demo data set, an onboarding, a payroll run) on an Instance:

- needs fewer user round trips, and no confirmation expires before the user replies;
- reports a write as done only when the record in ERPNext shows it;
- uses each tool correctly the first time;
- follows a known build order, so no step fails on a rule ERPNext always applies;
- spends less context on results the agent does not use.

## Outcome of the run

The demo data set was built and verified, with four items left open.

| Area | Result |
|---|---|
| Company | QKEEE LLP, chart of accounts, 13 departments created by ERPNext, Branch, Holiday List and its assignment |
| Employees | Arjun Menon `HR-EMP-00008`; Priya Nair `HR-EMP-00009` (Left); Rahul Verma `HR-EMP-00010` |
| Leave | Leave Policy, 6 allocations, 3 Leave Applications approved and submitted |
| Attendance | 102 rows for Aug–Sep 2026 (96 Work From Home, 6 On Leave) |
| Payroll | 2 Salary Structures, 2 assignments, 2 Payroll Entries, 4 Salary Slips, all submitted |
| Recruitment | Requisition, Opening, 2 Applicants, Interview, Feedback, Job Offer |
| Onboarding and exit | Templates, Onboarding, Separation submitted; Exit Interview and Full and Final Statement drafts |

Left open:
- Draft Interview `HR-INT-2026-0001`: the bot has no delete permission.
- Exit Interview `HR-EXIT-INT-00001`: no role on the instance grants submit.
- Full and Final Statement `HR-FNF-2026-00001`: HRMS refuses submit until payables and receivables are settled.
- The 15 Kanban cards: they stayed in triage. Fixed by commit `931dfba`, which is not deployed yet.

## What went right (keep)

- **Autonomy request:** the agent refused "all 15 tasks without interruption" and "one blanket approval" honestly, explained the token gate and the SOUL review rule, and offered batching instead.
- **Read-back:** it checked results on the record: "success on the API is not success on the record" (part 2 A[95]). This caught the Payroll Entry that stayed a draft.
- **Self-correction:** it admitted its own sequencing mistake (attendance before leave) and fixed it end to end.
- **Use of what exists:** it reused the departments that Company create made, found the Payroll Payable account by name, and noticed a cost centre from the wrong company.
- **Gates held:**
  - The requester gate refused Exit Interview submit for a requester without the right.
  - The domain gate refused writes through the wrong operation.
  - The self-escalation rule was never tested.
- **Noise ignored:** it ignored an irrelevant `jev-browser-use` skill suggestion.

## Run statistics

| Measure | Part 1 | Part 2 (A + B) |
|---|---|---|
| Messages | 216 | 351 (B repeats part of A) |
| Write renders / executes | 68 / 23 | about the same ratio |
| Expired confirmations (whole batch re-rendered) | 3 | 1 |
| `tool_call` wrapper shape errors | 7 (+2 missing argument) | 18 (+1 missing argument) |
| Wrong operation key (refused by the domain gate) | 0 | 8 |
| `erp_query` HTTP 500 `KeyError: 1` | 0 | 6 |
| Agent reasoning, total characters | 172K | 220K; one block 17K |
| Largest tool result | 26 KB (Holiday List create) | 27.8 KB (Attendance list), twice |
| Fixed context | 31K system prompt + 34K compaction summary | same |

## Findings

### Confirmation and approval

| # | Finding | Evidence |
|---|---|---|
| F1 | The confirmation lifetime (`DEFAULT_TOKEN_TTL_SECONDS = 900`) is shorter than the user's reply time (about 21 minutes, measured at part 1 [120]). Whole batches expired four times. The agent ran `date` in the terminal to check the clock. That caused two Tirith approval prompts, and one prompt timed out and blocked the turn. | P1 [68], [112], [70], [89]; P2 A[165] |
| F2 | Each write needs its own code, including drafts that cannot be submitted (Department, Branch, Leave Type, Salary Component). The user typed up to 9 codes per reply and asked twice for one approval for the whole job. | P1 [6], [65]; P2 A[221] |
| F3 | A batch that first changes a record and then submits it fails on its own change. The submit was rendered with the record's old `modified`, and the earlier step changed it (Interview Feedback repoint, then submit). | P2 B[24] |

### Tool interface

| # | Finding | Evidence |
|---|---|---|
| F4 | The Hermes `tool_call` wrapper rejects `name` inside `arguments` and a stringified `calls`. The model made this error 25 times, with five loop warnings in a row. **This repeats item-run-review F9 (issue 07, upstream, still open).** | P1 [84], [110]; P2 A[3], B[85]–[89] |
| F5 | `erp_query` sends `filters` to ERPNext as given. The schema says array, but a dict (`{"company": "X"}`) passes the wrapper, and ERPNext v16 returns HTTP 500 `KeyError: 1`. The agent misread this as "filtered queries are broken". | P2 A[41], B[75]–[79] |
| F6 | `erp_query` has no `order_by` and no count. To count 102 Attendance rows, the agent pulled 27.8 KB twice. A create returns the full document, so the Holiday List create returned all 59 rows (26 KB). | P2 A[49], A[126], B[94]; P1 [8] |
| F7 | The agent picked the wrong operation 8 times: Leave Application, Employee Onboarding and Employee Separation through `unscoped.generic`, and Interview Type through `hr_payroll.generic`. The gate refused each one correctly, but each cost a render round. | P2 A[55]–[57], A[130], A[234], A[236] |
| F8 | **A submit reported success, but the record stayed a draft.** HRMS processes Payroll Entry submit as a background job. On failure it leaves `docstatus 0`, `status Failed` and an `error_message`. The batch said "succeeded". Only the agent's own read-back found it. | P2 A[74]–[81] |
| F9 | A render that showed `request.name` made the agent conclude "Solved", and the execute then failed. Fixed in `25ffd46`: the name is now in the body that the render shows. The general lesson still applies: a render shows the request; only an execute proves it. | P1 [101], [125] |

### Permissions

| # | Finding | Evidence |
|---|---|---|
| F10 | Company create needs read on `GST Settings` (India Compliance reads it during validate). Preflight said `bot_ready: true`, and the create returned 403. Preflight cannot see reads that the server does inside its own checks. | P1 [10]–[14] |
| F11 | Leave Application submit needs `status = Approved`. `status` is a permlevel-1 field. `init-bot` copies only permlevel-0 rows (ADR 0003, 2026-10-21), so an admin added the permlevel-1 row by hand. (Interview submit also needs `status` Cleared, but that update worked at permlevel 0.) | P2 A[54], A[67], A[72] |
| F12 | The bot has no delete (by design, ADR 0003). A draft made by mistake (Interview with a wrong, set-once date) cannot be removed, so the demo keeps a stray draft. | P2 B[22] |
| F13 | The requester had no submit right on Exit Interview, so the requester gate refused it. This is correct, but the agent learned it only at execute, not at render. | P2 B[71] |

### Process and domain knowledge

| # | Finding | Evidence |
|---|---|---|
| F14 | The spec the agent wrote had wrong facts: "Creates need no token" (true only for domain `generic` creates) and "Designation HR Executive exists" (it did not). | P1 [30], [95] |
| F15 | The agent predicted document names (`HR-HIREQ-0001`, `HR-APP-2026-00001`) to build one batch with links. ERPNext gave `HR-HIREQ-00001`, and Job Applicant is named by email. Links in later steps broke. | P2 A[152], A[181] |
| F16 | ERPNext/HRMS rules that cost rework (see "HR build order and rules" below). | many |

### Context and cost

| # | Finding | Evidence |
|---|---|---|
| F17 | Fixed context is 31K characters of system prompt plus a 34K compaction summary. A mid-turn compaction re-sent the whole history (part 2 B repeats A). Memory is 97% full (2,143 of 2,200). | P1 [0], [1], [15] |
| F18 | Jev skill suggestions are added to most user turns, including "load `qkeee-erp-associate`" when it is already loaded, and `jev-browser-use` for API work. **This repeats item-run-review F10.** | P1 [140]; P2 A[19] |
| F19 | The background skill review (request C) runs on the aux model with a 30K system prompt. The log stops after `skills_list`, so there is no evidence of what it wrote. The skill tree is mounted read-only (README). Only the learned skill can take its updates. | P2 C[27]–[29] |

### Kanban (fixed, not deployed)

| # | Finding | Fix |
|---|---|---|
| F20 | Chat had no `kanban_*` tools, so the agent ran `hermes kanban` in the terminal sidecar. The board landed in the sidecar's Hermes home, cards stayed in triage, and `kanban_hooks` never ran. It then tried `hermes dashboard`, `npm install` and `sudo`. | `931dfba`: top-level `toolsets: [hermes-cli, kanban]`, setup item P3, guard rule `hermes_cli`, sidecar without Hermes entry points, prompt rules |

## HR build order and rules (input for `domains/hr-payroll.md`)

The order that works, with the rule each step depends on:

1. **Company.** Creating it also creates 13 departments (`<name> - <abbr>`). Query departments before you create one. The bot needs read on `GST Settings` when India Compliance is installed.
2. **Holiday List**, then **Holiday List Assignment** (submit it). A Holiday List on the Employee alone is not enough on HRMS 16.
3. **Masters:** Designation, Branch, Employee Grade, Shift Type, Leave Type, Leave Period, Leave Policy, Salary Component.
   - Employee Grade, Shift Type, Salary Structure, Payroll Period, Income Tax Slab and Staffing Plan are named by the user. Pass the name as the create's `name` argument.
   - Check that a referenced Designation exists; do not trust the spec.
4. **Employees.** `date_of_birth` is mandatory. Run `preflight` first.
5. **Leave Policy Assignment** (submit). It creates Leave Allocations, pro-rated from the joining date. Say so in the report.
6. **Leave Applications, before any Attendance.** A leave over a date with Attendance fails with `AttendanceAlreadyMarkedError`. Set `status = Approved` (a permlevel-1 field), then submit; "Only Leave Applications with status 'Approved' and 'Rejected' can be submitted".
7. **Attendance Request** (submit). It skips approved leave and holidays.
8. **Salary Structure** (submit), then **Salary Structure Assignment** (submit). A draft assignment does not apply.
9. **Payroll Entry.**
   - Set `employees` (child `Payroll Employee Detail`, only `employee` is needed) and `cost_center` explicitly. Through the API nothing fills them, and the cost centre default can belong to another company.
   - The payroll payable account needs `account_type = Payable`.
   - After submit, read `docstatus`, `status` and `error_message`. The submit can fail in the background.
10. **Salary Slips** (submit).
11. **Recruitment.**
    - Skill first; Interview Type needs at least one `expected_skill_set` row.
    - Job Applicant is named by email.
    - Interview `scheduled_on` is set once at create, so choose a past date if feedback will be submitted now.
    - Interview Feedback cannot be submitted before the scheduled date.
    - Interview submit needs `status` Cleared or Rejected.
12. **Onboarding and exit.**
    - A template without activities never creates the Employee or sets the status.
    - Create the Employee directly; set `status = Left` and `relieving_date` by hand.
    - Exit Interview needs `relieving_date` on the Employee.
    - Full and Final Statement submit needs settled payables and receivables.

General rules:

- Read a created record's name from the result; never predict it.
- A render shows the request. Only an execute, followed by a read-back, proves the write.
- For a count, ask for a count (issue 04), not every row.

## Decisions

Taken 2026-10-22 by nikhil.sharma@qkeee.ai.

| # | Question | Decision |
|---|---|---|
| D1 | Confirmation lifetime | 60 minutes on non-production tags, 15 minutes on production. Rules below. |
| D2 | One code per batch | Yes. A batch render returns one code for all its steps. Rules below. New ADR. |
| D3 | Permlevel-1 rights for the bot | Yes. `init-bot` also copies a template role's permlevel-1 row, only for the fields in a code list of status fields that gate submit (today: Leave Application `status`). ADR 0003 amendment. |
| D4 | Delete for the bot | Yes, only for a draft the bot created in the same session, behind the guardrails below. ADR 0003 amendment. |
| D5 | Wrong operation key | Yes. Add an `auto` operation key that routes to the owning domain's operation, or to `unscoped.generic` when no domain owns the doctype. |

### D1: confirmation lifetime

- New per-tag setting `QKEEE_ERP_<TAG>_ENVIRONMENT`: `production` or `non-production`. **Unset means production** (fail closed).
- Lifetime: 900 seconds on production; 3600 seconds on non-production. No other value is accepted, so a typo cannot stretch the window.
- Every render and batch render returns `expires_at` (ISO 8601, UTC) and `ttl_minutes`. The agent shows "expires at HH:MM UTC" from it and never runs a clock command in the terminal.
- `health` reports the tag's environment and lifetime, so the user sees which rule applies.

### D2: one code per batch

- `erp_execute_write` phase `render` with `batch: [...]` returns **one** token and **one** confirmation code. The token binds, in order, each step's operation, doctype, action, name, body, confirmed facts and requester, plus the batch length.
- The user's reply must contain that one code. Execute refuses a batch whose steps differ from the render in any way: added, removed, reordered or changed.
- The render lists every step in plain words. A submit, cancel or delete step is marked as one, so "confirm-first human review" in SOUL still holds: the user reviews each submit in the list they approve.
- At most 25 steps per batch.
- A step that changes a record a later step uses (F3): the later step's `expected_modified` is bound as "the result of step k", not as a timestamp. Execute takes it from step k's result.
- Execution stops at the first failure. Steps after it are reported `not_attempted`; a new render covers them.
- The lifetime from D1 applies to the whole batch.
- The Audit Log records the batch code on each step's row, so each step traces back to the one approval.
- Mixed batches stay allowed. A step that needs no code (a domain `generic` draft) runs inside the batch without asking anything extra.

### D4: delete of the bot's own drafts

**Purpose:** remove a draft the bot made by mistake in the current session (for example an Interview with a wrong, set-once date). It is not a clean-up tool for anything else.

**Who and what can be deleted.** Every condition must hold, checked live at render **and** again at execute:

1. **Draft only:** `docstatus = 0`. A submitted or cancelled record is never deleted.
2. **Made by the bot:** the record's `owner` is the Bot Account's user.
3. **Nobody else touched it:** `modified_by` is the Bot Account's user.
4. **Same session:** the Audit Log has a successful Create row for this exact doctype and name, with the current `session_id`. The session id comes from the gateway session context or, for a Kanban worker, its task id. It is never taken from a tool argument.
5. **Same requester:** that Create row's `requested_by` is the current requester. Another user in the same chat cannot delete it.
6. **Recent:** the Create row is at most 8 hours old.
7. **Allowed doctype:** never a doctype in `UNSCOPED_DENY` or `admin_key_doctypes()`, and never the Audit Log.
8. **Not referenced:** ERPNext's own link check refuses a delete that other records link to (`LinkExistsError`). The plugin reports that refusal as is and never unlinks records to get past it.

**How it runs:**

- A dedicated operation, `delete_own_draft`, with the bot credential. Every delete on a bot-credential `generic` operation (`<domain>.generic`, `unscoped.generic`) is refused with a pointer to it, so there is no second path for business records. The existing `system_admin` deletes (Admin Account, its own doctypes such as Custom Field) are out of scope and unchanged.
- Always token-gated with the user's code. In a batch, the render marks the step "delete (your own draft from this session)".
- At most 10 deletes per session. The 11th is refused.
- The Audit Log row records the delete, the Create row it relies on, and the full record as it was before the delete.
- The self-escalation rule still applies. `delete_own_draft` never uses the Admin Account key.

**Defence in ERPNext itself, for a leaked bot key:**

- `init-bot` grants `delete` on `Qkeee Bot` only as an **"Only If Creator" (`if_owner = 1`) row**, and only on doctypes the bot may create. ERPNext then refuses, for a direct API call with the bot key, a delete of any record the bot did not create.
- ERPNext keeps a copy of every deleted record in Deleted Document. The Audit Log is separate and is never deletable by the bot.
- **Remaining risk:** a leaked bot key could still delete a bot-created **cancelled** record, because ERPNext allows deleting cancelled records. ERPNext's Deleted Document copy and the Audit Log keep the evidence. Recorded in the ADR.

**Live check first (issue 17, step 1).** Confirm on demo_erp that Role Permission Manager keeps a separate `if_owner = 1` delete row next to the normal permlevel-0 row without restricting read, create and write. If ERPNext cannot hold both rows, stop and come back for a decision. Do not move the delete to the Admin Account key instead.

### D5: `auto` operation key

- `op: "auto"` resolves, at render, to the domain whose `ALLOWED_WRITE_DOCTYPES` holds the doctype, or to `unscoped.generic`. The render shows the resolved operation, and the token binds it, so execute cannot change it.
- All existing gates (allowlist, ownership, deny list, token policy) apply to the resolved operation unchanged.
- The cookbook gains a short domain-ownership table for the HR doctypes.

## Work items

These become `issues/NN-<slug>.md` files when the decisions are settled.

| # | Item | Findings | Kind | Depends on |
|---|---|---|---|---|
| 01 | `QKEEE_ERP_<TAG>_ENVIRONMENT` (unset = production); lifetime 15/60 min; `expires_at` and `ttl_minutes` in render results; `health` shows both; agent rule: never use the terminal for the clock | F1 | plugin code, skill text | D1 |
| 02 | One confirmation code per batch (binding, 25-step cap, `expected_modified` from step k, batch code on Audit Log rows); new ADR "one confirmation per batch" | F2, F3 | ADR, plugin code | D2 |
| 03 | Read-back after submit and cancel: report `submitted: false` when `docstatus` did not change; include `status` and `error_message` when the doctype has them | F8 | plugin code | — |
| 04 | `erp_query`: turn dict filters into list form (or refuse with a clear message); add `order_by`; add `count_only` | F5, F6 | plugin code | — |
| 05 | Shorter write results: `name`, `docstatus`, `modified` and the fields the payload set; full document only on request | F6 | plugin code | — |
| 06 | (Merged into 02: `expected_modified` bound to an earlier step's result) | F3 | — | D2 |
| 07 | `init-bot` permlevel-1 status fields (code list; today Leave Application `status`); ADR 0003 amendment | F11 | plugin code, ADR amendment | D3 |
| 08 | Preflight: per-app map of reads that ERPNext does inside its own checks (Company + India Compliance → GST Settings) | F10 | plugin code | — |
| 09 | Render: check the requester's right for the action (submit, cancel) at render, not only at execute | F13 | plugin code | — |
| 10 | `domains/hr-payroll.md`: the build order and rules above | F16 | skill text | — |
| 11 | Spec rules: cite a live check for "exists" and "needs no code"; never predict a name; a render is not proof | F9, F14, F15 | skill text | — |
| 12 | `op: "auto"` resolved at render and bound by the token; domain ownership table in the cookbook | F7 | plugin code, skill text | D5 |
| 13 | Context: consolidate memory, cap the compaction summary, avoid a compaction in the middle of a turn | F17 | config | — |
| 14 | Turn Jev skill suggestions off for this profile; take the `tool_call` wrapper fix to `hermes-agent` again, with these counts | F4, F18 | config, upstream | — |
| 15 | Background review: check where its skill updates land and that they go to `qkeee-erp-learned` | F19 | check | — |
| 16 | Deploy `931dfba` (kanban) on dev-erp and check that a new board goes through `kanban_create` | F20 | Operator | — |
| 17 | `delete_own_draft`. Steps: (1) live check of an `if_owner = 1` delete row next to the permlevel-0 row on demo_erp, and stop if ERPNext cannot hold both; (2) `init-bot` grants that row on doctypes the bot may create; (3) the operation with conditions 1–8, re-checked at execute, 10 per session cap, Audit Log link to the Create row; (4) refuse delete on bot-credential `generic` operations; (5) ADR 0003 amendment with the remaining risk; (6) tests for every refusal | F12 | live check, plugin code, ADR amendment | D4 |

## Already done

| Commit | Change |
|---|---|
| `968bfd4` | `init-bot` copies each domain's template roles (HR Manager and others) onto `Qkeee Bot` |
| `de5fb5a` | `init-bot` plans only doctypes that have a DocType record (Payment Gateway 404) |
| `25ffd46` | A create's `name` goes in the body as `name` and `__newname` (doctypes named by the user) |
| `931dfba` | Kanban through the gateway tools; terminal guard and sidecar without the Hermes command line; setup item P3 |

## Acceptance

A repeat of this build on a fresh company meets all of these:

1. On a non-production tag, a reply within 60 minutes never meets an expired confirmation. A tag without `QKEEE_ERP_<TAG>_ENVIRONMENT` keeps 15 minutes. The agent never runs `date` in the terminal.
2. One user reply per batch, with one code. Execute refuses a batch whose steps differ from the render in any way.
3. No step reports success while the record in ERPNext did not change state.
4. No `erp_query` HTTP 500 from the shape of a filter.
5. No write refused for a wrong operation key more than once per doctype.
6. No step fails on a rule listed in "HR build order and rules".
7. No tool result above 10 KB unless the agent asked for full rows.
8. A Kanban board for the build is visible in the gateway's `hermes kanban boards`.
9. The bot deletes a draft it created in the session, for the same requester. Tests show a refusal for each of these:
   - a submitted or cancelled record;
   - a record another user created or edited;
   - a record created in another session, or more than 8 hours ago;
   - a different requester in the same chat;
   - a doctype in `UNSCOPED_DENY`, `admin_key_doctypes()` or the Audit Log;
   - an 11th delete in one session;
   - a delete through any bot-credential `generic` operation;
   - a `session_id` or requester passed as a tool argument.
10. A direct API delete with the bot key, outside the plugin, of a record the bot did not create is refused by ERPNext (the `if_owner` row).
