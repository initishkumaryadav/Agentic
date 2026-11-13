import sqlite3
from contextlib import closing

from src.auth import config, crypto, db
from src.auth.errors import AuthError
from src.auth.models import User


def normalize(username: str) -> str:
    return (username or "").strip().lower()


def validate_new_password(password: str, confirm: str) -> None:
    if len(password) < config.MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {config.MIN_PASSWORD_LEN} characters.")
    if password != confirm:
        raise AuthError("Passwords do not match.")


def create_user(username: str, password: str, confirm: str) -> User:
    username = normalize(username)
    if not config.USERNAME_RE.match(username):
        raise AuthError("Username must be 3–32 characters: letters, numbers, dot, dash or underscore.")
    validate_new_password(password, confirm)
    salt = crypto.new_salt()
    with closing(db.connect()) as conn, conn:
        # The very first account becomes the admin.
        first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            conn.execute(
                "INSERT INTO users (username, pw_salt, pw_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, salt, crypto.hash_password(password, salt), int(first), db.now_iso()),
            )
        except sqlite3.IntegrityError:
            raise AuthError("That username is already taken.") from None
    return User(username=username, is_admin=first)


def verify_user(username: str, password: str) -> User | None:
    """Returns the user when the password is right, otherwise None."""
    username = normalize(username)
    with closing(db.connect()) as conn:
        row = conn.execute(
            "SELECT username, pw_salt, pw_hash, is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row is None:
        crypto.burn_password_check(password)
        return None
    if not crypto.password_matches(password, row["pw_salt"], row["pw_hash"]):
        return None
    return User(username=row["username"], is_admin=bool(row["is_admin"]))


def user_exists(conn: sqlite3.Connection, username: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def store_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    """Writes a new password hash. Callers decide what else to revoke."""
    salt = crypto.new_salt()
    conn.execute(
        "UPDATE users SET pw_salt = ?, pw_hash = ? WHERE username = ?",
        (salt, crypto.hash_password(password, salt), username),
    )
