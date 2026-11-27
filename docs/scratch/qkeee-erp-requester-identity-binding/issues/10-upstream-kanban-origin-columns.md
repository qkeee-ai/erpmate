# Upstream: origin identity columns on kanban tasks + optional forced triage

Status: needs-triage (decide whether to raise with NousResearch hermes-agent)
Type: task
Blocked by: 09 (ship the plugin first; upstream replaces it)
Repo: `hermes-agent` (upstream PR)

## Change

- Add `origin_platform`, `origin_user_id`, `origin_user_id_alt`, `origin_user_name` and `origin_chat_id` to `tasks`.
- `kanban_create` fills them from the session ContextVar, only when session context is engaged. It never takes them from arguments, and not from `hermes kanban create` in a terminal, where the env can be overridden inline.
- `inherit_creator_origin` copies them, which covers decomposer children and worker-created children.
- The dispatcher exports them to the worker as `HERMES_SESSION_USER_ID`, `_ALT`, `_USER_NAME` and an origin-platform var. It does not export `HERMES_SESSION_PLATFORM` or `CHAT_ID`; reply routing stays with notify subscriptions.
- `kanban_show` shows the origin.
- Config `kanban.force_triage_from_gateway` (default false) forces `triage=true` for gateway-originated creates.

## Why upstream

The plugin in issue 09 keeps a side store and walks parent links on every lookup. Core columns make the origin durable, visible on the board, and available to any skill. After this lands, remove the plugin and the parent-walk in `client.py`.
