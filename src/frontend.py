import os
import sys
import uuid
import json
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from langchain_core.messages import HumanMessage, ToolMessage

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

from src.orchestrator import agent

db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_AGENT_USER", "USR_FDE_RO")
db_password = os.getenv("SQL_AGENT_PASSWORD")


def _odbc_url(user, password):
    params = urllib.parse.quote_plus(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={db_host},{db_port};"
        "DATABASE=master;"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=no;"
        "TrustServerCertificate=yes;"
    )
    return f"mssql+pyodbc:///?odbc_connect={params}"


@st.cache_resource
def get_log_engine():
    return create_engine(_odbc_url(db_user, db_password))


def write_audit_log(session_id, node_name, tool_name, content):
    """Writes agent execution traces to the SQL audit table using agent permissions."""
    try:
        with get_log_engine().connect() as conn:
            conn.execute(text("""
                INSERT INTO llm_views.AgentAuditLog (SessionID, NodeExecuted, ToolName, Content)
                VALUES (:session_id, :node_name, :tool_name, :content)
            """), {
                "session_id": session_id,
                "node_name": node_name,
                "tool_name": tool_name,
                "content": content,
            })
            conn.commit()
    except Exception as e:
        print(f"Audit log write failed: {e}")


# Plain-language labels for the agent's tools.
TOOL_LABELS = {
    "query_telemetry_db": ("Fleet telemetry", ":material/database:"),
    "fetch_corridor_conditions": ("Corridor conditions", ":material/route:"),
    "search_compliance_sop": ("Compliance SOPs", ":material/menu_book:"),
}

SUGGESTIONS = [
    "Which reefer units are running above their set temperature?",
    "What are the current conditions near 40.71, -74.00?",
    "What is the SOP for a temperature excursion on frozen goods?",
]


def tool_label(name):
    return TOOL_LABELS.get(name, (name.replace("_", " ").capitalize(), ":material/build:"))


def render_steps(steps):
    """One collapsed row per tool call: what was asked and what came back."""
    for step in steps:
        label, icon = tool_label(step["name"])
        with st.expander(label, icon=icon):
            if step.get("args"):
                st.caption("Request")
                st.code(json.dumps(step["args"], indent=2), language="json")
            if step.get("output") is not None:
                st.caption("Response")
                st.code(step["output"], language="text")


if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "ui_messages" not in st.session_state:
    st.session_state.ui_messages = []
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

thread_config = {"configurable": {"thread_id": st.session_state.thread_id}}

llm_provider = os.getenv("Agent_llm", "OLLAMA").strip().upper()
llm_name = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b") if llm_provider == "OLLAMA" else llm_provider.lower()

with st.sidebar:
    st.markdown("#### FDE Dispatch")
    st.caption("Cold-chain operations")

    app_mode = st.radio(
        "View",
        ["Dispatch", "Audit log"],
        captions=["Ask about the fleet", "Admin only"],
        label_visibility="collapsed",
    )

    st.space("medium")
    st.caption(f"Session `{st.session_state.thread_id[:8]}`  \nModel `{llm_name}`")
    if st.button("New session", icon=":material/restart_alt:", width="stretch"):
        st.session_state.ui_messages = []
        st.session_state.thread_id = str(uuid.uuid4())
        st.rerun()


if app_mode == "Dispatch":
    st.header("Cold-chain incidents")
    st.caption("Ask about fleet telemetry, corridor conditions or compliance procedures.")

    for entry in st.session_state.ui_messages:
        with st.chat_message(entry["role"]):
            if entry.get("steps"):
                render_steps(entry["steps"])
            st.markdown(entry["content"])

    if not st.session_state.ui_messages:
        st.space("small")
        cols = st.columns(len(SUGGESTIONS))
        for col, text_ in zip(cols, SUGGESTIONS):
            if col.button(text_, width="stretch", type="tertiary"):
                st.session_state.pending_prompt = text_
                st.rerun()

    typed = st.chat_input("Ask about a shipment, route or procedure")
    user_input = typed or st.session_state.pending_prompt
    st.session_state.pending_prompt = None

    if user_input:
        st.session_state.ui_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            final_response = ""
            steps = {}  # tool_call_id -> {name, args, output}
            error = None

            with st.status("Thinking…", expanded=False) as status:
                try:
                    events = agent.stream(
                        {"messages": [HumanMessage(content=user_input)]},
                        config=thread_config,
                        stream_mode="updates",
                    )
                    for event in events:
                        for node_name, node_state in event.items():
                            if node_name == "reasoner":
                                latest_msg = node_state["messages"][-1]

                                for tool_call in getattr(latest_msg, "tool_calls", None) or []:
                                    label, _ = tool_label(tool_call["name"])
                                    status.update(label=f"Checking {label.lower()}…")
                                    steps[tool_call["id"]] = {
                                        "name": tool_call["name"],
                                        "args": tool_call["args"],
                                        "output": None,
                                    }
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        node_name="reasoner",
                                        tool_name=tool_call["name"],
                                        content=json.dumps(tool_call["args"]),
                                    )

                                if latest_msg.content:
                                    final_response = latest_msg.content
                                    status.update(label="Writing answer…")
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        node_name="reasoner_final",
                                        tool_name="LLM Text Synthesis",
                                        content=final_response,
                                    )

                            elif node_name == "tools":
                                for msg in node_state.get("messages", []):
                                    if isinstance(msg, ToolMessage):
                                        step = steps.setdefault(
                                            msg.tool_call_id,
                                            {"name": msg.name, "args": None, "output": None},
                                        )
                                        step["output"] = msg.content
                                        write_audit_log(
                                            session_id=st.session_state.thread_id,
                                            node_name="tools",
                                            tool_name=msg.name,
                                            content=msg.content,
                                        )
                except Exception as e:
                    error = e

                step_list = list(steps.values())
                if error:
                    status.update(label="Request failed", state="error")
                elif step_list:
                    sources = ", ".join(dict.fromkeys(tool_label(s["name"])[0].lower() for s in step_list))
                    status.update(label=f"Checked {sources}", state="complete")
                else:
                    status.update(label="Answered without lookups", state="complete")

                if step_list:
                    render_steps(step_list)

            if error:
                st.error(f"The agent could not finish this request: {error}", icon=":material/error:")
            elif final_response:
                st.markdown(final_response)
                st.session_state.ui_messages.append({
                    "role": "assistant",
                    "content": final_response,
                    "steps": step_list,
                })
            else:
                st.warning("No answer came back. Try rephrasing the question.", icon=":material/warning:")


elif app_mode == "Audit log":
    st.header("Audit log")
    st.caption("Every tool call and answer the agent produced, read from `llm_views.AgentAuditLog`.")

    with st.form("admin_auth_form", border=True):
        col1, col2 = st.columns(2)
        input_user = col1.text_input("Admin user", value=os.getenv("SQL_ADMIN_USER", ""))
        input_pass = col2.text_input("Password", type="password")
        submit_admin = st.form_submit_button("Sign in", icon=":material/lock_open:")

    if submit_admin:
        if input_user == os.getenv("SQL_ADMIN_USER") and input_pass == os.getenv("SQL_ADMIN_PASSWORD"):
            try:
                admin_engine = create_engine(_odbc_url(input_user, input_pass))
                with admin_engine.connect() as conn:
                    st.session_state.audit_df = pd.read_sql("""
                        SELECT LogID, Timestamp, SessionID, NodeExecuted, ToolName, Content
                        FROM llm_views.AgentAuditLog
                        ORDER BY Timestamp DESC
                    """, conn)
            except Exception as e:
                st.error(f"Could not read the audit table: {e}", icon=":material/error:")
        else:
            st.error("Wrong user name or password.", icon=":material/error:")

    df = st.session_state.get("audit_df")
    if df is not None:
        if df.empty:
            st.caption("Nothing logged yet. Ask something in Dispatch first.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Entries", f"{len(df):,}", border=True)
            m2.metric("Sessions", f"{df['SessionID'].nunique():,}", border=True)
            m3.metric("Tool calls", f"{(df['NodeExecuted'] == 'tools').sum():,}", border=True)

            search = st.text_input("Filter", placeholder="Session, tool or text", label_visibility="collapsed")
            view = df
            if search:
                mask = df[["SessionID", "ToolName", "Content"]].astype(str).apply(
                    lambda c: c.str.contains(search, case=False, regex=False)
                ).any(axis=1)
                view = df[mask]

            st.dataframe(
                view,
                column_config={
                    "LogID": st.column_config.NumberColumn("ID", format="%d", width="small"),
                    "Timestamp": st.column_config.DatetimeColumn("Time", format="DD MMM YYYY, HH:mm:ss"),
                    "SessionID": st.column_config.TextColumn("Session", width="small"),
                    "NodeExecuted": "Step",
                    "ToolName": "Tool",
                    "Content": st.column_config.TextColumn("Payload", width="large"),
                },
                hide_index=True,
                width="stretch",
                height=560,
            )
