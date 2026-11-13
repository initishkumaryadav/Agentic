
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

projectRoot = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("CHAT_DB_PATH", projectRoot / "data" / "chat_sessions.db"))


class ChatOwnershipError(PermissionError):


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_MIGRATIONS = {
    "messages": {"feedback": "INTEGER", "elapsed_ms": "INTEGER"},
    
    "sessions": {"owner": "TEXT"},
}


def init_db():
    with closing(_connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                steps      TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
        """)
        for table, columns in _MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, col_type in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner, updated_at)")


def list_sessions(owner, limit=100):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions "
            "WHERE owner = ? ORDER BY updated_at DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(session_id, owner):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT m.id, m.role, m.content, m.steps, m.feedback, m.elapsed_ms, m.created_at "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.session_id = ? AND s.owner = ? ORDER BY m.id",
            (session_id, owner),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "steps": json.loads(r["steps"]) if r["steps"] else [],
            "feedback": r["feedback"],
            "elapsed_ms": r["elapsed_ms"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def add_message(session_id, owner, role, content, steps=None, elapsed_ms=None):
    if not owner:
        raise ValueError("owner is required")
    now = _now()
    title = " ".join(content.split())[:60] or "New chat"
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, owner) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at WHERE sessions.owner = excluded.owner",
            (session_id, title, now, now, owner),
        )
        row = conn.execute("SELECT owner FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row["owner"] != owner:
            raise ChatOwnershipError("This chat belongs to another user.")
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, steps, elapsed_ms, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(steps, default=str) if steps else None, elapsed_ms, now),
        )
        return cur.lastrowid


def set_feedback(message_id, owner, value):
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE messages SET feedback = ? WHERE id = ? "
            "AND session_id IN (SELECT id FROM sessions WHERE owner = ?)",
            (value, message_id, owner),
        )


def rename_session(session_id, owner, title):
    title = " ".join(title.split())[:80]
    if not title:
        return
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ? AND owner = ?", (title, session_id, owner))


def delete_session(session_id, owner):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE id = ? AND owner = ?", (session_id, owner))


def feedback_summary():
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT SUM(feedback = 1) AS up, SUM(feedback = 0) AS down, COUNT(*) AS answers "
            "FROM messages WHERE role = 'assistant'"
        ).fetchone()
    return {"up": row["up"] or 0, "down": row["down"] or 0, "answers": row["answers"] or 0}
