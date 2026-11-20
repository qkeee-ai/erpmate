"""Request Registry: one durable row per distinct Requester ask, linked
across channels (agents/.scratch/qkeee-erp-email-channel-reliability,
issue 02, U3).

Each channel's gateway session is keyed by platform + chat_id (and
thread_id), so the same Requester emailing then chatting about the same
ask looks like two unrelated sessions. This store gives that one ask a
durable identity (`request_id` + human `topic`) that can be linked from
more than one session, and the fields a later weekly report (issue 11)
needs (`agent_status`, `requester_confirmed`, timestamps) as a side
effect of the schema, not a separate build.

Deliberately NOT a same-task classifier: nothing here guesses that two
sessions are "the same ask". `open_for_sender()` only lists a sender's
other open requests; the caller (erp_tools.ErpTools) must have the
Requester confirm before calling `link_session()`. An ERPNext record
anchor (doctype + name) is optional — a read-only ask (an MIS/GST report)
never gets one.

Stdlib only, like the rest of core/.
"""

import os
import sqlite3
import time
import uuid

from .kanban_origin import plugin_data_dir

STORE_FILENAME = "request_registry.db"
AGENT_STATUSES = ("open", "in_progress", "done")
REQUESTER_CONFIRMATIONS = ("confirmed", "disputed")
SESSION_LINK_FIELDS = ("platform", "chat_id", "thread_id", "session_id")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS request ("
    " request_id TEXT PRIMARY KEY,"
    " sender_email TEXT NOT NULL,"
    " topic TEXT,"
    " anchor_doctype TEXT, anchor_name TEXT,"
    " agent_status TEXT NOT NULL DEFAULT 'open',"
    " requester_confirmed TEXT,"
    " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, closed_at INTEGER);"
    "CREATE TABLE IF NOT EXISTS request_session ("
    " request_id TEXT NOT NULL,"
    " platform TEXT, chat_id TEXT, thread_id TEXT, session_id TEXT,"
    " linked_at INTEGER NOT NULL,"
    " PRIMARY KEY (request_id, platform, chat_id, thread_id));"
)


def default_store_path(hermes_home: str = None) -> str:
    return os.path.join(plugin_data_dir(hermes_home), STORE_FILENAME)


def _connect(store_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
    conn = sqlite3.connect(store_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> int:
    """Epoch microseconds, not seconds: `open_for_sender()` orders by this
    column, and two calls in the same request/test can otherwise land in
    the same second and tie."""
    return time.time_ns() // 1000


def _session_row(request_id: str, link: dict) -> tuple:
    link = link or {}
    return (request_id, *(link.get(k) or None for k in SESSION_LINK_FIELDS), _now())


def _sessions_for(conn: sqlite3.Connection, request_id: str) -> list:
    rows = conn.execute(
        "SELECT platform, chat_id, thread_id, session_id, linked_at FROM request_session "
        "WHERE request_id = ? ORDER BY linked_at", (request_id,)).fetchall()
    return [dict(r) for r in rows]


def _row(conn: sqlite3.Connection, request_id: str):
    row = conn.execute("SELECT * FROM request WHERE request_id = ?", (request_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["linked_sessions"] = _sessions_for(conn, request_id)
    return out


def open_request(store_path: str, sender_email: str, topic: str, session_link: dict, *,
                 anchor_doctype: str = None, anchor_name: str = None) -> dict:
    """Start tracking a new ask: a fresh `request_id`, `agent_status` 'open',
    linked to *session_link* (the session that just opened it).

    `request_id` is a full uuid4 (agents issue 02: "not a content hash"),
    not a truncated one — a short id would trade away the exact guarantee
    (effectively no collisions) uuid4 was asked for."""
    request_id = f"REQ-{uuid.uuid4().hex.upper()}"
    now = _now()
    conn = _connect(store_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO request (request_id, sender_email, topic, anchor_doctype, "
                "anchor_name, agent_status, requester_confirmed, created_at, updated_at, "
                "closed_at) VALUES (?, ?, ?, ?, ?, 'open', NULL, ?, ?, NULL)",
                (request_id, sender_email, topic, anchor_doctype, anchor_name, now, now))
            conn.execute(
                "INSERT INTO request_session (request_id, platform, chat_id, thread_id, "
                "session_id, linked_at) VALUES (?, ?, ?, ?, ?, ?)", _session_row(request_id, session_link))
        return _row(conn, request_id)
    finally:
        conn.close()


def get_request(store_path: str, request_id: str):
    """The stored row for *request_id*, with its `linked_sessions`, or None.
    A missing store file is no request, not an error."""
    if not request_id or not os.path.exists(store_path):
        return None
    conn = _connect(store_path)
    try:
        return _row(conn, request_id)
    finally:
        conn.close()


def link_session(store_path: str, request_id: str, session_link: dict):
    """Attach *session_link* to an existing request. Returns the updated row,
    or None when *request_id* does not exist. Idempotent: linking the same
    session twice is a no-op, not an error."""
    conn = _connect(store_path)
    try:
        if conn.execute("SELECT 1 FROM request WHERE request_id = ?", (request_id,)).fetchone() is None:
            return None
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO request_session (request_id, platform, chat_id, thread_id, "
                "session_id, linked_at) VALUES (?, ?, ?, ?, ?, ?)", _session_row(request_id, session_link))
        return _row(conn, request_id)
    finally:
        conn.close()


def update_status(store_path: str, request_id: str, *, agent_status: str = None,
                  requester_confirmed: str = None):
    """Set `agent_status` and/or `requester_confirmed` (each independently —
    neither overwrites the other). Setting `agent_status='done'` stamps
    `closed_at`; any other `agent_status` clears it (a reopened request is
    no longer closed). Returns the updated row, or None when *request_id*
    does not exist."""
    if agent_status is not None and agent_status not in AGENT_STATUSES:
        raise ValueError(f"agent_status must be one of {AGENT_STATUSES}, got {agent_status!r}")
    if requester_confirmed is not None and requester_confirmed not in REQUESTER_CONFIRMATIONS:
        raise ValueError(f"requester_confirmed must be one of {REQUESTER_CONFIRMATIONS}, got "
                         f"{requester_confirmed!r}")
    conn = _connect(store_path)
    try:
        if conn.execute("SELECT 1 FROM request WHERE request_id = ?", (request_id,)).fetchone() is None:
            return None
        now = _now()
        with conn:
            if agent_status is not None:
                closed_at = now if agent_status == "done" else None
                conn.execute("UPDATE request SET agent_status = ?, closed_at = ?, updated_at = ? "
                            "WHERE request_id = ?", (agent_status, closed_at, now, request_id))
            if requester_confirmed is not None:
                conn.execute("UPDATE request SET requester_confirmed = ?, updated_at = ? "
                            "WHERE request_id = ?", (requester_confirmed, now, request_id))
        return _row(conn, request_id)
    finally:
        conn.close()


def open_for_sender(store_path: str, sender_email: str, limit: int = 10) -> list:
    """This sender's open/in_progress requests (never 'done'), most recently
    updated first, each with its `linked_sessions` — a surfacing list for
    the caller to show, never auto-matched against the current turn."""
    if not sender_email or not os.path.exists(store_path):
        return []
    conn = _connect(store_path)
    try:
        rows = conn.execute(
            "SELECT request_id FROM request WHERE sender_email = ? AND agent_status != 'done' "
            "ORDER BY updated_at DESC LIMIT ?", (sender_email, int(limit))).fetchall()
        return [_row(conn, r["request_id"]) for r in rows]
    finally:
        conn.close()
