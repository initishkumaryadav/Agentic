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

from src import auth_store, chat_store


@st.cache_resource
def _init_chat_store():
    chat_store.init_db()
    auth_store.init_users()
    return True


_init_chat_store()

# Set ALLOW_SIGNUP=false in .env to stop new registrations once your team has accounts.
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").strip().lower() not in {"0", "false", "no"}


def sign_out():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def auth_page():
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.space("large")
        st.title(":material/ac_unit: FDE Dispatch", text_alignment="center")
        st.caption("Cold-chain operations assistant", text_alignment="center")

        tabs = ["Sign in", "Sign up"] if ALLOW_SIGNUP else ["Sign in"]
        mode = st.segmented_control("Mode", tabs, default="Sign in", label_visibility="collapsed", width="stretch")

        if mode == "Sign up":
            with st.form("signup"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="new-password")
                confirm = st.text_input("Confirm password", type="password", autocomplete="new-password")
                submitted = st.form_submit_button("Create account", type="primary", width="stretch")
            if submitted:
                try:
                    user = auth_store.create_user(username, password, confirm)
                except auth_store.AuthError as e:
                    st.error(str(e), icon=":material/error:")
                else:
                    st.session_state.auth_user = user
                    st.rerun()
        else:
            with st.form("signin"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
            if submitted:
                user = auth_store.verify_user(username, password)
                if user is None:
                    st.error("Incorrect username or password.", icon=":material/error:")
                else:
                    st.session_state.auth_user = user
                    st.rerun()
    st.stop()


if "auth_user" not in st.session_state:
    auth_page()

user_id = st.session_state.auth_user["username"]
is_admin = st.session_state.auth_user["is_admin"]

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
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f":material/account_circle: {user_id}")
        st.button("Sign out", icon=":material/logout:", type="tertiary", on_click=sign_out)

page.run()
