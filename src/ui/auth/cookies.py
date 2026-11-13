import json

import streamlit as st

from src.auth import config

COOKIE_NAME = "coldroute_remember"
_PENDING_KEY = "_auth_cookie_pending"


def read_token() -> str | None:
    """The token the browser sent when this session connected."""
    value = st.context.cookies.get(COOKIE_NAME)
    return value if isinstance(value, str) and value else None


def queue_set(token: str) -> None:
    st.session_state[_PENDING_KEY] = token


def queue_clear() -> None:
    st.session_state[_PENDING_KEY] = ""


def render_pending() -> None:
    if _PENDING_KEY not in st.session_state:
        return
    token = st.session_state.pop(_PENDING_KEY)
    max_age = config.REMEMBER_DAYS * 86400 if token else 0
    secure = "; Secure" if (st.context.url or "").startswith("https") else ""
    cookie = f"{COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; SameSite=Strict{secure}"
    st.html(f"<script>document.cookie = {json.dumps(cookie)};</script>", unsafe_allow_javascript=True)
