# Kanban tasks: start in triage, and carry the requester to workers (plugin)

Status: implemented (erpnext-hermes `feat/discovery-write-readiness` ab5ef65, 2026-10-08); dev-erp retest pending
Type: task
Blocked by: none
Repos: `erpnext-hermes` (new plugin, `client.py`, skill text). No `hermes-agent` change.
Raised: 2026-10-07, from a dev-erp test: a Google Chat prompt asked the agent to post work to the kanban board

## Problem

**(a) Tasks skip triage.** The agent created the user's request as a `ready` task. A user prompt for kanban should land in `triage`, so the specifier/decomposer runs and a human can review it before work starts.

- `kanban_create` takes `triage: bool`. It defaults to false, so the task lands `ready` (or `todo` under unfinished parents). See `hermes-agent/tools/kanban_tools.py` `_handle_create` and `hermes_cli/kanban_db.py` `create_task`.
- No config forces triage. It is the agent's choice.
- **Check first:** task `t_439cacf9` shows `created by auto-decomposer, from_decompose_of t_60848faf`. The decomposer runs only on triage tasks. Read root `t_60848faf`'s events. If it started in `triage`, the cards seen in `ready` were children promoted from `todo`, and (a) needs restating.

**(b) Workers have no requester.** A worker that runs an ERPNext task cannot resolve `requested_by`. It blocks with `needs_input`, which is correct behaviour but means no work gets done.

- The dispatcher deliberately clears every `HERMES_SESSION_*` var before it spawns a worker, and sets only `HERMES_SESSION_SOURCE=kanban` (`hermes_cli/kanban_db_dispatch.py`, about lines 2186–2208).
- `kanban_create` reads the sender (`HERMES_SESSION_USER_ID`, `_ALT`) from the session ContextVar, but only to build a notify subscription (`_resolve_notify_target`). The `tasks` table has no requester column.
- Children inherit only `session_id` and notify subscriptions (`inherit_creator_origin`, `kanban_db_graph.py`). Decomposer children go through the same path.
- The worker found only the display name ("Nikhil Sharma") on the card. The skill forbids using it.

## Rejected: agent writes `requested_by` into the task body

The agent writes the body. A worker cannot tell a real `Requested-by:` line from an invented or stale one. This is the same weakness issue 01 closed for chat sessions. The identity must be captured by code from the gateway's session context at creation time.

Also rejected as the source:

- **`sessions.user_id`** (state.db) via the task's `session_id`. In a group space this is the session's user, not the person who asked for this card.
- **Notify subscriptions.** They exist for delivery, can hold several rows (explicit subscribes), and depend on `kanban.auto_subscribe_on_create`.

## Approach: `qkeee-erp` plugin in erpnext-hermes (kanban-origin part)

The image is `FROM nousresearch/hermes-agent:latest`, so a core patch means a fork. Hermes plugin hooks run in the gateway process: `pre_tool_call` can rewrite arguments or block a call and fails closed (`hermes_cli/plugins.py` `_dispatch_pre_tool_call_hooks`), and `post_tool_call` sees the result.

1. **Force triage (a).** `pre_tool_call` on `kanban_create`: when the call comes from an engaged gateway session (not a dispatcher worker, that is, no `HERMES_KANBAN_TASK`), set `triage=true` and drop `initial_status`. Worker-created child tasks are unchanged.
2. **Record the origin (b).** `post_tool_call` on `kanban_create`: write `task_id → {platform, user_id (email), user_id_alt, user_name, chat_id, created_at}` to a plugin-owned SQLite file under `$HERMES_HOME`. Values come from `gateway.session_context.get_session_env`, the per-turn ContextVar, never from tool arguments. When the creator is a worker (`HERMES_KANBAN_TASK` set), copy the origin of that task instead.
3. **Resolve in `client.py`.** When there is no session sender email but `HERMES_KANBAN_TASK` is set:
   - look the task up in the plugin store;
   - if it is missing (decomposer children are not created through the tool), walk parent links in the kanban DB (`HERMES_KANBAN_DB`, `task_links`) up to the first task that has an origin;
   - use that email as the session sender. Mismatch refusal, the bot-identity guard and the gate all apply unchanged.
   - `whoami` also prints `HERMES_SESSION_SOURCE`, `HERMES_KANBAN_TASK` and the resolved origin task id.
4. **Skill text.** `SKILL.md` step 3: on `source=kanban` with no resolvable origin, `kanban_block(kind="needs_input")` naming the gap. Do not ask for an email in a comment and do not take it from the card text.
5. **Cleanup.** Delete the dev-erp worker memory entry "Kanban-dispatched ERPNext tasks carry no sender identity…". It becomes wrong once this ships.

## Tests

- Hook unit tests: gateway-origin create gets `triage=true`; worker create unchanged; origin row written from ContextVars, not args; worker-created child copies the parent's origin.
- `client.py`: origin lookup direct, origin via parent walk (decomposer child), no origin → no sender, mismatch with the origin email refused.
- dev-erp retest: from Google Chat, ask for a kanban task that creates an ERPNext record.
  - Expect the root in `triage`.
  - Expect the decomposed children's workers to show the sender email in `whoami`.
  - Expect the write's audit row to carry that email.

## Residual risk

- A worker terminal can override `HERMES_KANBAN_TASK` inline or edit the SQLite files. This is the same class as issues 04 and 06.
- Tasks created from the CLI or dashboard have no origin. They block as `needs_input`, which is correct.
- Issue 10 tracks the cleaner upstream fix.

## Comments

**Decided 2026-10-08** (grill-with-docs). Plugin approach approved. Package it as the shared `qkeee-erp` plugin with issue 04 (one origin resolver).

**Finding (a) confirmed** from `hermes kanban show t_60848faf --json` on dev-erp: the `created` event has `status: "ready"`, `creator_task_id: null`. A later `status -> triage` event (actor not recorded, about 3.5 min after create) let the decomposer run. Step 1 (force triage) stays.

**Correction to step 3: do not walk parent links.** The decomposer links each child -> root (`kanban_db_graph.py:142-143`), so the root lists its children as *parents*; walking parents from a child never reaches the root. Parent links are also dependencies that may belong to another Requester, so a walk could resolve the wrong person. Resolve instead:
1. the task's own row in the plugin origin store;
2. else the task's `created` event payload `from_decompose_of` (`kanban_db_graph.py:211`), then that root's origin (repeat for nested decomposition);
3. else no origin -> `needs_input`.

Update the `client.py` test "origin via parent walk" to "origin via `from_decompose_of`", and add: a child whose dependency parent has a *different* origin resolves to its own root's origin, not the parent's.

**Implemented 2026-10-08** (ab5ef65). Steps 1–4 done. Step 5 (delete the dev-erp worker memory entry) and the dev-erp retest are still open. Limit: the origin store is per Hermes home, so a task assigned to another profile finds no origin and blocks as `needs_input`.
