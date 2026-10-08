"""kanban_create hooks: force triage, record the Requester origin
(requester identity binding, issue 09).

- pre_tool_call: a kanban_create from a gateway session (a chat user's
  prompt, not a dispatcher worker) is rewritten to `triage=true`, so the
  specifier/decomposer runs and a human can review it before work starts.
  `initial_status` is reset to the default, so it cannot park the task.
- post_tool_call: after a successful create, write the task's origin to the
  plugin's store (core/kanban_origin.py). A gateway create takes it from the
  per-turn session context. A worker create copies its own task's origin.
  Tool arguments and the card text are never a source.

Workers are detected by HERMES_KANBAN_TASK in the process environment: the
dispatcher sets it per worker process; a gateway turn never has it.

Limit: the store is per Hermes home. A worker reads the store under its
assignee profile's HERMES_HOME. A task assigned to a different profile than
the gateway that created it finds no origin and blocks as needs_input
(fails closed). This profile assigns its tasks to itself.
"""

import json
import logging
import os

from core import kanban_origin

logger = logging.getLogger(__name__)

TOOL = "kanban_create"
# "running" is _handle_create's default: "modify" args are shallow-merged into
# the call, so this resets an agent-chosen "blocked" rather than removing it.
_TRIAGE_ARGS = {"triage": True, "initial_status": "running"}


class KanbanOriginHooks:
    """`session_env(name, default="")` reads the gateway session context
    (gateway.session_context.get_session_env); `store_path()` returns the
    origin store path; `environ` is the process environment."""

    def __init__(self, session_env, store_path, environ=None):
        self._session_env = session_env
        self._store_path = store_path
        self._environ = os.environ if environ is None else environ

    def _worker_task(self) -> str:
        return (self._environ.get("HERMES_KANBAN_TASK") or "").strip()

    def _gateway_platform(self) -> str:
        if self._worker_task():
            return ""
        return (self._session_env("HERMES_SESSION_PLATFORM", "") or "").strip()

    def pre_tool_call(self, tool_name=None, args=None, **_):
        if tool_name != TOOL or not self._gateway_platform():
            return None
        return {"action": "modify", "args": dict(_TRIAGE_ARGS)}

    def post_tool_call(self, tool_name=None, args=None, result=None, **_):
        if tool_name != TOOL:
            return None
        task_id = _created_task_id(result)
        if not task_id:
            return None
        try:
            if self._worker_task():
                self._copy_worker_origin(task_id)
            elif self._gateway_platform():
                origin = {"platform": self._gateway_platform()}
                for field, name in (("user_id", "HERMES_SESSION_USER_ID"),
                                    ("user_id_alt", "HERMES_SESSION_USER_ID_ALT"),
                                    ("user_name", "HERMES_SESSION_USER_NAME"),
                                    ("chat_id", "HERMES_SESSION_CHAT_ID")):
                    origin[field] = self._session_env(name, "")
                kanban_origin.record_origin(self._store_path(), task_id, origin)
        except Exception:  # observer hook: never fail the tool, but say so
            logger.exception("qkeee-erp: could not record kanban origin for %s", task_id)
        return None

    def _copy_worker_origin(self, task_id: str) -> None:
        origin = kanban_origin.resolve_origin(self._store_path(),
                                              self._environ.get("HERMES_KANBAN_DB") or "",
                                              self._worker_task())
        if origin:
            kanban_origin.record_origin(self._store_path(), task_id, origin,
                                        source_task_id=origin["origin_task_id"])


def _created_task_id(result):
    try:
        payload = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    task_id = payload.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None
