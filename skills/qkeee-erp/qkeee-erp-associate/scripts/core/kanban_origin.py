"""Kanban task origin: which Requester asked for a Kanban task.

A Kanban worker runs with every HERMES_SESSION_* var cleared by the
dispatcher, so it cannot see who asked for its task. The `qkeee-erp`
plugin (erpnext-hermes/plugins/qkeee-erp) records each task's origin when
`kanban_create` runs in a gateway session, from the gateway's per-turn
session context, never from tool arguments. This module is that store and
its resolution, shared by the plugin and core/client.py.

Resolution (requester identity binding, issue 09):
  1. the task's own row in the origin store;
  2. else the task's `created` event payload `from_decompose_of` (set by
     the auto-decomposer), then that root's origin, repeated for nested
     decomposition;
  3. else no origin: the caller blocks the task as needs_input.

Parent links are never walked. The decomposer links each child -> root,
and dependency parents can belong to another Requester.

Stdlib only, like the rest of core/.
"""

import json
import os
import sqlite3
import time

PLUGIN_NAME = "qkeee-erp"
STORE_FILENAME = "kanban_origin.db"
ORIGIN_FIELDS = ("platform", "user_id", "user_id_alt", "user_name", "chat_id")
# Nested decomposition deeper than this is treated as no origin.
MAX_DECOMPOSE_DEPTH = 16

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS task_origin ("
    " task_id TEXT PRIMARY KEY,"
    " platform TEXT, user_id TEXT, user_id_alt TEXT, user_name TEXT, chat_id TEXT,"
    " source_task_id TEXT,"
    " created_at INTEGER NOT NULL)"
)


def plugin_data_dir(hermes_home: str = None) -> str:
    """`<HERMES_HOME>/plugin-data/qkeee-erp`, the Hermes per-plugin data
    convention (plugins/plugin_storage.py). Gateway-side state: the origin
    store and the ERPNext credentials file."""
    base = hermes_home or os.environ.get("HERMES_HOME") or os.getcwd()
    return os.path.join(base, "plugin-data", PLUGIN_NAME)


def default_store_path(hermes_home: str = None) -> str:
    return os.path.join(plugin_data_dir(hermes_home), STORE_FILENAME)


def _connect(store_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
    conn = sqlite3.connect(store_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def record_origin(store_path: str, task_id: str, origin: dict, source_task_id: str = None) -> bool:
    """Write the origin row for `task_id`. First write wins: an existing row
    is never replaced. Returns True when a row was written."""
    if not task_id:
        return False
    row = {k: (origin or {}).get(k) or None for k in ORIGIN_FIELDS}
    conn = _connect(store_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO task_origin (task_id, platform, user_id, user_id_alt, "
                "user_name, chat_id, source_task_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, row["platform"], row["user_id"], row["user_id_alt"], row["user_name"],
                 row["chat_id"], source_task_id, int(time.time())))
        return cur.rowcount == 1
    finally:
        conn.close()


def get_origin(store_path: str, task_id: str):
    """The stored origin row for `task_id` as a dict, or None. A missing
    store file is no origin, not an error."""
    if not task_id or not os.path.exists(store_path):
        return None
    conn = _connect(store_path)
    try:
        row = conn.execute("SELECT * FROM task_origin WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def resolve_origin(store_path: str, kanban_db_path: str, task_id: str):
    """The origin for `task_id` (see module docstring), as the stored row
    plus `origin_task_id` (the task the row belongs to), or None."""
    tid, seen = task_id, set()
    while tid and tid not in seen and len(seen) <= MAX_DECOMPOSE_DEPTH:
        seen.add(tid)
        row = get_origin(store_path, tid)
        if row:
            return dict(row, origin_task_id=tid)
        tid = _decomposed_from(kanban_db_path, tid)
    return None


def _decomposed_from(kanban_db_path: str, task_id: str):
    """The root task id in `task_id`'s `created` event (`from_decompose_of`),
    or None. Read-only; an unreadable board is no origin."""
    if not kanban_db_path or not os.path.exists(kanban_db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{kanban_db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'created' "
            "ORDER BY id LIMIT 1", (task_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    try:
        payload = json.loads(row[0]) if row and row[0] else {}
    except (TypeError, ValueError):
        return None
    root = payload.get("from_decompose_of") if isinstance(payload, dict) else None
    return root if isinstance(root, str) and root else None
