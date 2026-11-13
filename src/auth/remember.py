import sqlite3
from contextlib import closing
from datetime import timedelta

from src.auth import config, crypto, db
from src.auth.models import User


def _valid(token) -> bool:
    return isinstance(token, str) and bool(token)


def issue_token(username: str) -> str:
    token = crypto.new_token()
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM remember_tokens WHERE expires_at <= ?", (db.now_iso(),))
        conn.execute(
            "INSERT INTO remember_tokens (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (crypto.sha256_hex(token), username, db.now_iso(),
             db.iso_from_now(timedelta(days=config.REMEMBER_DAYS))),
        )
    return token


def user_for_token(token: str | None) -> User | None:
    if not _valid(token) or config.REMEMBER_DAYS == 0:
        return None
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT u.username, u.is_admin FROM remember_tokens t JOIN users u ON u.username = t.username "
            "WHERE t.token_hash = ? AND t.expires_at > ?",
            (crypto.sha256_hex(token), db.now_iso()),
        ).fetchone()
    if row is None:
        return None
    return User(username=row["username"], is_admin=bool(row["is_admin"]))


def revoke_token(token: str | None) -> None:
    if not _valid(token):
        return
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM remember_tokens WHERE token_hash = ?", (crypto.sha256_hex(token),))


def revoke_all(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM remember_tokens WHERE username = ?", (username,))
