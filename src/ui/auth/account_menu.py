
import streamlit as st

from src.auth import AuthError, passwords
from src.ui.auth import cookies, session


def render_account_menu(username: str) -> None:
    with st.popover(username, icon=":material/account_circle:", type="tertiary"):
        if st.button("Change password", icon=":material/password:", type="tertiary"):
            _change_password_dialog(username)
        st.button("Sign out", icon=":material/logout:", type="tertiary", on_click=session.sign_out)
    cookies.render_pending()
    session.show_flash()


@st.dialog("Change password", icon=":material/password:")
def _change_password_dialog(username: str) -> None:
    with st.form("change_password", border=False):
        current = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("Update password", type="primary", width="stretch")
    if not submitted:
        return
    try:
        passwords.change_password(username, current, new, confirm)
    except AuthError as e:
        st.error(str(e), icon=":material/error:")
        return
    session.keep_remembered_after_password_change(username)
    session.flash("Password updated. Other remembered browsers were signed out.")
    st.rerun()
