
import hashlib
import hmac
import secrets

from src.auth import config
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16

_DUMMY_SALT = secrets.token_bytes(_SALT_BYTES)


def new_salt() -> bytes:
    return secrets.token_bytes(_SALT_BYTES)


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def password_matches(password: str, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected)


def burn_password_check(password: str) -> None:
    
    hash_password(password, _DUMMY_SALT)


def sha256_hex(value: str) -> str:
    
    return hashlib.sha256(value.encode()).hexdigest()


def same_digest(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_reset_code() -> str:
    return "".join(secrets.choice(config.RESET_CODE_ALPHABET) for _ in range(config.RESET_CODE_LEN))
