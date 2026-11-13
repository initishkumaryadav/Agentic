
from src.auth import User
from src.ui.auth import session
from src.ui.auth.account_menu import render_account_menu
from src.ui.auth.login_page import render_login_page


def require_user(title: str, tagline: str) -> User:
    user = session.current_user() or session.restore_from_cookie()
    if user is None:
        render_login_page(title, tagline)
    return user


__all__ = ["render_account_menu", "require_user"]
