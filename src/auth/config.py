
import os
import re


def _env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no"}


# Accounts
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD_LEN = 8
# Set ALLOW_SIGNUP=false once your team has accounts, to stop new registrations.
ALLOW_SIGNUP = _env_flag("ALLOW_SIGNUP", "true")

# "Remember me": how long a browser stays signed in. 0 turns the option off.
REMEMBER_DAYS = max(0, int(os.getenv("REMEMBER_ME_DAYS", "30")))

# Password reset codes
RESET_CODE_MINUTES = 30
RESET_MAX_ATTEMPTS = 5
RESET_CODE_LEN = 10
# No 0/O/1/I/L, so codes are easy to read out or copy by hand.
RESET_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
