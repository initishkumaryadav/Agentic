
import re
import sqlite3
from contextlib import closing
from datetime import timedelta

from src.auth import config, crypto, db, remember, users
from src.auth.errors import AuthError
from src.auth.models import User

_BAD_CODE = "That reset code is invalid or has expired. Ask an admin for a new one."


def _apply_new_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    users.store_password(conn, username, password)
    remember.revoke_all(conn, username)
    conn.execute("DELETE FROM password_resets WHERE username = ?", (username,))


def change_password(username: str, current: str, new: str, confirm: str) -> None:
    """For a signed-in user who knows their current password."""
    if users.verify_user(username, current) is None:
        raise AuthError("Current password is incorrect.")
    users.validate_new_password(new, confirm)
    if new == current:
        raise AuthError("New password must be different from the current one.")
    with closing(db.connect()) as conn, conn:
        _apply_new_password(conn, users.normalize(username), new)


def issue_reset_code(username: str) -> str:
    """Admin action. Replaces any earlier code for this user."""
    username = users.normalize(username)
    code = crypto.new_reset_code()
    with closing(db.connect()) as conn, conn:
        if not users.user_exists(conn, username):
            raise AuthError(f"No account named '{username}'.")
        conn.execute(
            "INSERT INTO password_resets (username, code_hash, expires_at, attempts) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(username) DO UPDATE SET code_hash = excluded.code_hash, "
            "expires_at = excluded.expires_at, attempts = 0",
            (username, crypto.sha256_hex(code), db.iso_from_now(timedelta(minutes=config.RESET_CODE_MINUTES))),
        )
    return code


def format_code(code: str) -> str:
    
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def _clean_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code or "").upper()


def reset_password(username: str, code: str, new: str, confirm: str) -> User:
    
    username = users.normalize(username)
    users.validate_new_password(new, confirm)
    with closing(db.connect()) as conn, conn:
        row = conn.execute(
            "SELECT r.code_hash, r.expires_at, r.attempts, u.is_admin FROM password_resets r "
            "JOIN users u ON u.username = r.username WHERE r.username = ?",
            (username,),
        ).fetchone()
        if row is None:
            raise AuthError(_BAD_CODE)

        if row["expires_at"] <= db.now_iso() or row["attempts"] >= config.RESET_MAX_ATTEMPTS:
            conn.execute("DELETE FROM password_resets WHERE username = ?", (username,))
            conn.commit()  
            raise AuthError(_BAD_CODE)

        if not crypto.same_digest(crypto.sha256_hex(_clean_code(code)), row["code_hash"]):
            conn.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE username = ?", (username,))
            conn.commit()
            raise AuthError(_BAD_CODE)

        _apply_new_password(conn, username, new)
    return User(username=username, is_admin=bool(row["is_admin"]))
