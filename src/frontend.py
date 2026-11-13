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

APP_NAME = "ColdRoute AI"
APP_TAGLINE = "Helping every delivery arrive fresh, safe and on time."

st.set_page_config(
    page_title=APP_NAME,
    page_icon=":material/local_shipping:",
    layout="wide",
)

from src import chat_store
from src.auth import init_db as init_auth_db
from src.ui.auth import render_account_menu, require_user


@st.cache_resource
def _init_storage():
    chat_store.init_db()
    init_auth_db()
    return True


_init_storage()

user = require_user(APP_NAME, APP_TAGLINE)  
user_id = user["username"]
is_admin = user["is_admin"]
if st.session_state.get("user_id") not in (None, user_id):
   
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
    st.markdown(f"#### :material/ac_unit: {APP_NAME}")
    st.caption(f"Cold-chain operations · :material/memory: `{llm_name}`")
    render_account_menu(user_id)

page.run()
