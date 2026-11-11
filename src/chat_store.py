
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

projectRoot = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("CHAT_DB_PATH", projectRoot / "data" / "chat_sessions.db"))


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def list_sessions(limit=50):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(session_id):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT role, content, steps FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return [
        {"role": r["role"], "content": r["content"], "steps": json.loads(r["steps"]) if r["steps"] else []}
        for r in rows
    ]


def add_message(session_id, role, content, steps=None):
    """Appends a message, creating the session (titled from its first message) if needed."""
    now = _now()
    title = " ".join(content.split())[:60] or "New chat"
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
            (session_id, title, now, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, steps, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(steps) if steps else None, now),
        )


def delete_session(session_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
