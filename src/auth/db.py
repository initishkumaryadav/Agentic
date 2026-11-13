
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from src import chat_store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    pw_salt    BLOB NOT NULL,
    pw_hash    BLOB NOT NULL,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Only SHA-256 hashes of tokens and codes are stored, never the values themselves.
CREATE TABLE IF NOT EXISTS remember_tokens (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remember_user ON remember_tokens(username);

CREATE TABLE IF NOT EXISTS password_resets (
    username   TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    return chat_store._connect()


def init_db() -> None:
    with closing(connect()) as conn, conn:
        conn.executescript(_SCHEMA)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return utc_iso(datetime.now(timezone.utc))


def iso_from_now(delta: timedelta) -> str:
    return utc_iso(datetime.now(timezone.utc) + delta)
