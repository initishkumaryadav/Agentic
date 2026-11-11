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


AUTH_ENABLED = auth_configured()
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "false").strip().lower() in {"1", "true", "yes"}
DEV_USER_ID = "local-dev"

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

# Stable identity used to scope chats and stamp the audit log.
user_id = (st.user.get("email") or st.user.get("sub")) if AUTH_ENABLED else DEV_USER_ID
if st.session_state.get("user_id") not in (None, user_id):
    # Different user in the same browser session: drop the previous user's open chat.
    for key in ("thread_id", "ui_messages", "pending_prompt"):
        st.session_state.pop(key, None)
st.session_state.user_id = user_id

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

page = st.navigation([dispatch_page, fleet_page, audit_page], position="top")

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
