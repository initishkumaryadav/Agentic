import streamlit as st

from src.auth import User, config, remember
from src.ui.auth import cookies

USER_KEY = "auth_user"
_TOKEN_KEY = "_auth_remember_token"
_FLASH_KEY = "_auth_flash"


def current_user() -> User | None:
    return st.session_state.get(USER_KEY)


def restore_from_cookie() -> User | None:
    
    token = cookies.read_token()
    user = remember.user_for_token(token)
    if user:
        st.session_state[USER_KEY] = user
        st.session_state[_TOKEN_KEY] = token
    elif token:
        cookies.queue_clear()
    return user


def sign_in(user: User, remember_me: bool = False) -> None:
    st.session_state[USER_KEY] = user
    if remember_me and config.REMEMBER_DAYS:
        _remember(user["username"])
    st.rerun()


def sign_out() -> None:
    
    remember.revoke_token(st.session_state.get(_TOKEN_KEY))
    remember.revoke_token(cookies.read_token())
    st.session_state.clear()
    cookies.queue_clear()


def keep_remembered_after_password_change(username: str) -> None:
   
    if st.session_state.get(_TOKEN_KEY):
        _remember(username)


def _remember(username: str) -> None:
    token = remember.issue_token(username)
    st.session_state[_TOKEN_KEY] = token
    cookies.queue_set(token)


def flash(message: str) -> None:
    
    st.session_state[_FLASH_KEY] = message


def show_flash() -> None:
    if message := st.session_state.pop(_FLASH_KEY, None):
        st.toast(message, icon=":material/check_circle:")
