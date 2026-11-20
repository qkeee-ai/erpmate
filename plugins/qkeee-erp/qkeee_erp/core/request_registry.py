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

Issue 03 (requester-vs-input-provider): `open_in_session()` is the other
lookup direction — requests already linked to a (platform, chat_id,
thread_id) room, regardless of sender. There is no separate "participant"
table: a request's Requester is always just its `sender_email`; anyone
else whose session resolves to the same `request_id` (via `open_in_session()`
on a shared Chat thread, or a future email Message-ID index) is an
input-provider for that request's lifetime — a role derived at read time,
never stored.

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
    "CREATE TABLE IF NOT EXISTS request_event ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " request_id TEXT NOT NULL, action TEXT NOT NULL, sender_email TEXT NOT NULL,"
    " note TEXT, created_at INTEGER NOT NULL);"
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


def open_in_session(store_path: str, platform: str, chat_id: str, thread_id: str = None,
                    limit: int = 10) -> list:
    """Open/in_progress requests already linked to this exact (platform,
    chat_id, thread_id) room — regardless of WHO sent this call's turn
    (agents issue 03: a Google Chat space/thread already shares one
    session across participants, so this is how a second person's turn
    finds a request the first person already opened there). Never auto-
    acted on by the caller — same surfacing-only contract as
    `open_for_sender()`.

    Requires a real `thread_id` on both sides — deliberately does NOT
    match two requests that both happen to have no `thread_id` (adversarial
    review, issue 03: a busy Google Chat space with threading turned OFF
    gives every message in it `thread_id=None`, so matching on "both
    null" would surface every unrelated conversation in that space to
    anyone posting there — a cross-conversation leak, not a feature).
    Email's `chat_id` is already the sender's own address (never shared
    across people) so this exclusion costs it nothing it could use anyway."""
    if not platform or not chat_id or not thread_id or not os.path.exists(store_path):
        return []
    conn = _connect(store_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT rs.request_id FROM request_session rs JOIN request r "
            "ON r.request_id = rs.request_id WHERE rs.platform = ? AND rs.chat_id = ? "
            "AND rs.thread_id = ? AND r.agent_status != 'done' ORDER BY r.updated_at DESC LIMIT ?",
            (platform, chat_id, thread_id, int(limit))).fetchall()
        return [_row(conn, r["request_id"]) for r in rows]
    finally:
        conn.close()


def log_event(store_path: str, request_id: str, action: str, sender_email: str,
             note: str = None) -> None:
    """Append-only forensic trail (adversarial review, issue 03: `erp_request`
    changes state with no record anywhere of who did it or why, unlike
    every ERPNext write tool's audit log). One row per `open`/`link`/
    `status` call — never read back by any lookup here; a later report
    (issue 11) or an incident review queries this table directly."""
    conn = _connect(store_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO request_event (request_id, action, sender_email, note, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (request_id, action, sender_email, note, _now()))
    finally:
        conn.close()
