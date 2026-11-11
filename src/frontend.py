import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

scriptDir = Path(__file__).resolve().parent
projectRoot = scriptDir.parent

if str(projectRoot) not in sys.path:
    sys.path.insert(0, str(projectRoot))

load_dotenv(projectRoot / ".env")

st.set_page_config(
    page_title="FDE Dispatch",
    page_icon=":material/local_shipping:",
    layout="wide",
)

from src import chat_store


@st.cache_resource
def _init_chat_store():
    chat_store.init_db()
    return True


_init_chat_store()


def auth_configured() -> bool:
    """True when an OIDC provider is set up under [auth] in .streamlit/secrets.toml."""
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def _env_set(name):
    """Comma-separated env var as a lower-cased set, e.g. ALLOWED_EMAILS=a@x.com,b@x.com."""
    return {v.strip().lower() for v in os.getenv(name, "").split(",") if v.strip()}


AUTH_ENABLED = auth_configured()
# Fail closed: sign-in is required unless REQUIRE_AUTH=false is set explicitly (local development only).
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").strip().lower() not in {"0", "false", "no"}
ALLOWED_EMAILS = _env_set("ALLOWED_EMAILS")
ALLOWED_EMAIL_DOMAINS = {d.lstrip("@") for d in _env_set("ALLOWED_EMAIL_DOMAINS")}
ADMIN_EMAILS = _env_set("ADMIN_EMAILS")
DEV_USER_ID = "local-dev"


def is_allowed(email: str) -> bool:
    """Signing in only proves who someone is; this decides whether they may use the app."""
    return email in ALLOWED_EMAILS or email.rpartition("@")[2] in ALLOWED_EMAIL_DOMAINS


def refuse(message):
    st.space("large")
    st.error(message, icon=":material/block:")
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.button("Sign out", icon=":material/logout:", on_click=st.logout)
    st.stop()

if REQUIRE_AUTH and not AUTH_ENABLED:
    st.error(
        "Sign-in is required (REQUIRE_AUTH=true) but no identity provider is configured. "
        "Add an [auth] section to .streamlit/secrets.toml.",
        icon=":material/lock:",
    )
    st.stop()

if AUTH_ENABLED and not st.user.is_logged_in:
    st.space("large")
    st.title(":material/ac_unit: FDE Dispatch", text_alignment="center")
    st.caption("Cold-chain operations assistant. Sign in with your work account to continue.", text_alignment="center")
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.button("Sign in", icon=":material/login:", type="primary", on_click=st.login)
    st.stop()

if AUTH_ENABLED:
    email = (st.user.get("email") or "").strip().lower()
    if not email or st.user.get("email_verified") is False:
        refuse("Your account has no verified email address, so access can't be checked.")
    if not (ALLOWED_EMAILS or ALLOWED_EMAIL_DOMAINS):
        refuse("Access is not configured: set ALLOWED_EMAIL_DOMAINS or ALLOWED_EMAILS in .env.")
    if not is_allowed(email):
        refuse(f"{email} is not allowed to use FDE Dispatch. Ask an administrator for access.")
    user_id = email
    is_admin = email in ADMIN_EMAILS
else:
    # Only reachable with REQUIRE_AUTH=false: a single local developer who is also the admin.
    user_id = DEV_USER_ID
    is_admin = True

# Stable identity used to scope chats and stamp the audit log.
if st.session_state.get("user_id") not in (None, user_id):
    # Different user in the same browser session: drop everything the previous user had open.
    for key in ("thread_id", "ui_messages", "pending_prompt", "audit_df"):
        st.session_state.pop(key, None)
st.session_state.user_id = user_id
st.session_state.is_admin = is_admin

st.session_state.setdefault("thread_id", str(uuid.uuid4()))
st.session_state.setdefault("ui_messages", [])
st.session_state.setdefault("pending_prompt", None)

llm_provider = os.getenv("Agent_llm", "OLLAMA").strip().upper()
if llm_provider == "OLLAMA":
    llm_name = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
elif llm_provider == "GEMINI":
    llm_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
else:
    llm_name = "gpt-4o"
st.session_state.llm_name = llm_name

PAGES_DIR = scriptDir / "app_pages"
dispatch_page = st.Page(PAGES_DIR / "dispatch.py", title="Dispatch", icon=":material/forum:", default=True)
fleet_page = st.Page(PAGES_DIR / "fleet.py", title="Fleet overview", icon=":material/monitoring:")
audit_page = st.Page(PAGES_DIR / "audit.py", title="Audit log", icon=":material/policy:")
st.session_state.dispatch_page = dispatch_page

pages = [dispatch_page, fleet_page] + ([audit_page] if is_admin else [])
page = st.navigation(pages, position="top")

with st.sidebar:
    st.markdown("#### :material/ac_unit: FDE Dispatch")
    st.caption(f"Cold-chain operations · :material/memory: `{llm_name}`")
    if AUTH_ENABLED:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption(f":material/account_circle: {st.user.get('name') or user_id}")
            st.button("Sign out", icon=":material/logout:", type="tertiary", on_click=st.logout)
    else:
        st.badge("Dev mode: sign-in not configured", icon=":material/warning:", color="orange")

page.run()
