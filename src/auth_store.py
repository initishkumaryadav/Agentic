"""Local username/password accounts, stored in the same SQLite file as chats."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing

from src.chat_store import _connect, _now

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD_LEN = 8
# scrypt parameters (stdlib, no extra dependency).
_N, _R, _P = 2**14, 8, 1


class AuthError(ValueError):
    """Message is safe to show to the user."""


def init_users():
    with closing(_connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username   TEXT PRIMARY KEY,
                pw_salt    BLOB NOT NULL,
                pw_hash    BLOB NOT NULL,
                is_admin   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def normalize(username: str) -> str:
    return (username or "").strip().lower()


def create_user(username: str, password: str, confirm: str) -> dict:
    username = normalize(username)
    if not USERNAME_RE.match(username):
        raise AuthError("Username must be 3–32 characters: letters, numbers, dot, dash or underscore.")
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if password != confirm:
        raise AuthError("Passwords do not match.")
    salt = secrets.token_bytes(16)
    with closing(_connect()) as conn, conn:
        # The very first account becomes the admin.
        first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            conn.execute(
                "INSERT INTO users (username, pw_salt, pw_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, salt, _hash(password, salt), int(first), _now()),
            )
        except sqlite3.IntegrityError:
            raise AuthError("That username is already taken.") from None
    return {"username": username, "is_admin": first}


# Used to spend the same time on unknown usernames, so they can't be probed by timing.
_DUMMY_SALT = secrets.token_bytes(16)


def verify_user(username: str, password: str) -> dict | None:
    username = normalize(username)
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT username, pw_salt, pw_hash, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row is None:
        _hash(password, _DUMMY_SALT)
        return None
    if not hmac.compare_digest(_hash(password, row["pw_salt"]), row["pw_hash"]):
        return None
    return {"username": row["username"], "is_admin": bool(row["is_admin"])}
