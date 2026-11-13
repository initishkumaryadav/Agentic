
import streamlit as st

from src.auth import AuthError, config, passwords, users
from src.ui.auth import cookies, session

SIGN_IN, SIGN_UP, RESET = "Sign in", "Sign up", "Reset password"


def render_login_page(title: str, tagline: str) -> None:
    
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.space("large")
        st.title(f":material/ac_unit: {title}", text_alignment="center")
        st.caption(tagline, text_alignment="center")

        modes = [SIGN_IN, SIGN_UP, RESET] if config.ALLOW_SIGNUP else [SIGN_IN, RESET]
        mode = st.segmented_control("Mode", modes, default=SIGN_IN, label_visibility="collapsed", width="stretch")

        if mode == SIGN_UP:
            _sign_up_form()
        elif mode == RESET:
            _reset_form()
        else:
            _sign_in_form()
    cookies.render_pending()
    st.stop()


def _error(message: str) -> None:
    st.error(message, icon=":material/error:")


def _sign_in_form() -> None:
    with st.form("signin"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        remember_me = (
            st.checkbox(f"Remember me for {config.REMEMBER_DAYS} days") if config.REMEMBER_DAYS else False
        )
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
    if submitted:
        user = users.verify_user(username, password)
        if user is None:
            _error("Incorrect username or password.")
        else:
            session.sign_in(user, remember_me)


def _sign_up_form() -> None:
    with st.form("signup"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm password", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("Create account", type="primary", width="stretch")
    if submitted:
        try:
            user = users.create_user(username, password, confirm)
        except AuthError as e:
            _error(str(e))
        else:
            session.sign_in(user)


def _reset_form() -> None:
    st.caption("Ask an admin for a one-time reset code, then choose a new password.")
    with st.form("reset"):
        username = st.text_input("Username", autocomplete="username")
        code = st.text_input("Reset code", placeholder="XXXXX-XXXXX", autocomplete="one-time-code")
        password = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("Reset password", type="primary", width="stretch")
    if submitted:
        try:
            user = passwords.reset_password(username, code, password, confirm)
        except AuthError as e:
            _error(str(e))
        else:
            session.flash("Password reset. You're signed in.")
            session.sign_in(user)
