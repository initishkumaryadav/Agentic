import altair as alt
import pandas as pd
import streamlit as st

from src import chat_store
from src.ui_common import audit_reader_engine

STEP_LABELS = {
    "reasoner": "Tool request",
    "tools": "Tool result",
    "reasoner_final": "Final answer",
}


def load_audit():
    engine = audit_reader_engine()
    if engine is None:
        raise RuntimeError("Set SQL_AUDIT_READER_USER and SQL_AUDIT_READER_PASSWORD in .env.")
    with engine.connect() as conn:
        df = pd.read_sql("""
            SELECT LogID, Timestamp, SessionID, UserID, NodeExecuted, ToolName, Content
            FROM llm_views.AgentAuditLog
            ORDER BY Timestamp DESC
        """, conn)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df["Step"] = df["NodeExecuted"].map(STEP_LABELS).fillna(df["NodeExecuted"])
    return df


st.header("Audit log")
st.caption("Every tool call and answer the agent produced, read from `llm_views.AgentAuditLog`.")

# frontend.py only lists this page for admins; check again in case it is reached some other way.
if not st.session_state.get("is_admin"):
    st.error("The audit log is only available to administrators.", icon=":material/block:")
    st.stop()

with st.container(horizontal=True, vertical_alignment="center"):
    st.badge(f"Admin: {st.session_state.user_id}", icon=":material/verified_user:", color="green")
    st.space("stretch")
    reload = st.button("Reload", icon=":material/refresh:", type="tertiary")

if reload or "audit_df" not in st.session_state:
    try:
        st.session_state.audit_df = load_audit()
    except Exception as e:
        print(f"Audit log read failed: {e}")
        st.error("Could not read the audit table. Check the server log for details.", icon=":material/error:")
        st.stop()

df = st.session_state.get("audit_df")
if df is None or df.empty:
    st.caption("Nothing logged yet. Ask something in Dispatch first.")
    st.stop()

fb = chat_store.feedback_summary()
rated = fb["up"] + fb["down"]
with st.container(horizontal=True, gap="medium"):
    st.metric("Entries", f"{len(df):,}", border=True)
    st.metric("Users", f"{df['UserID'].nunique():,}", border=True)
    st.metric("Sessions", f"{df['SessionID'].nunique():,}", border=True)
    st.metric("Tool calls", f"{(df['NodeExecuted'] == 'reasoner').sum():,}", border=True)
    st.metric(
        "Helpful answers",
        f"{fb['up'] / rated:.0%}" if rated else "—",
        border=True,
        help=f"{fb['up']} thumbs up, {fb['down']} thumbs down across {fb['answers']} answers saved on this server.",
    )

# --- Charts ------------------------------------------------------------------------------
span_days = (df["Timestamp"].max() - df["Timestamp"].min()).days
time_unit = "yearmonthdatehours" if span_days <= 3 else "yearmonthdate"

act_col, tool_col = st.columns([3, 2], gap="medium")
with act_col:
    with st.container(border=True):
        st.markdown("**:material/timeline: Activity**")
        activity = alt.Chart(df).mark_bar().encode(
            x=alt.X(f"{time_unit}(Timestamp):T", title=None),
            y=alt.Y("count():Q", title="Entries"),
            color=alt.Color("Step:N", legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip(f"{time_unit}(Timestamp):T", title="Time"), "Step", alt.Tooltip("count():Q", title="Entries")],
        ).properties(height=220)
        st.altair_chart(activity)

with tool_col:
    with st.container(border=True):
        st.markdown("**:material/build: Tool usage**")
        usage = df[df["NodeExecuted"] == "reasoner"]["ToolName"].value_counts().reset_index()
        usage.columns = ["Tool", "Calls"]
        if usage.empty:
            st.caption("No tool calls yet.")
        else:
            st.altair_chart(
                alt.Chart(usage).mark_bar(cornerRadiusEnd=3).encode(
                    x=alt.X("Calls:Q", title=None),
                    y=alt.Y("Tool:N", sort="-x", title=None),
                    tooltip=["Tool", "Calls"],
                ).properties(height=220)
            )

# --- Filters and table ---------------------------------------------------------------------
with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
    steps = st.pills(
        "Step", list(STEP_LABELS.values()), selection_mode="multi",
        default=list(STEP_LABELS.values()), label_visibility="collapsed",
    )
    sessions = ["All sessions", *df["SessionID"].drop_duplicates().tolist()]
    session = st.selectbox(
        "Session", sessions, label_visibility="collapsed", width=260,
        format_func=lambda s: s if s == "All sessions" else f"Session {s[:8]}…",
    )
    search = st.text_input(
        "Filter", placeholder="Search user, tool or payload", icon=":material/search:", label_visibility="collapsed"
    )

view = df[df["Step"].isin(steps or list(STEP_LABELS.values()))]
if session != "All sessions":
    view = view[view["SessionID"] == session]
if search:
    mask = view[["SessionID", "UserID", "ToolName", "Content"]].astype(str).apply(
        lambda c: c.str.contains(search, case=False, regex=False)
    ).any(axis=1)
    view = view[mask]
view = view.reset_index(drop=True)

st.caption(f"{len(view):,} of {len(df):,} entries · select a row to read the full payload")
selection = st.dataframe(
    view,
    key="audit_table",
    on_select="rerun",
    selection_mode="single-row",
    column_order=["LogID", "Timestamp", "UserID", "SessionID", "Step", "ToolName", "Content"],
    column_config={
        "LogID": st.column_config.NumberColumn("ID", format="%d", width="small"),
        "Timestamp": st.column_config.DatetimeColumn("Time", format="DD MMM YYYY, HH:mm:ss"),
        "UserID": st.column_config.TextColumn("User", width="small"),
        "SessionID": st.column_config.TextColumn("Session", width="small"),
        "Step": "Step",
        "ToolName": "Tool",
        "Content": st.column_config.TextColumn("Payload", width="large"),
    },
    hide_index=True,
    height=460,
)

rows = selection.selection.rows
if rows:
    item = view.iloc[rows[0]]
    with st.container(border=True):
        st.markdown(f"**Entry {item['LogID']}** · {item['Step']} · `{item['ToolName']}`")
        st.caption(f"{item['Timestamp']:%d %b %Y, %H:%M:%S} · user `{item['UserID']}` · session `{item['SessionID']}`")
        if item["NodeExecuted"] == "reasoner_final":
            st.markdown(item["Content"])
        else:
            st.code(item["Content"], language="json" if item["NodeExecuted"] == "reasoner" else "text", wrap_lines=True)

st.download_button(
    "Download filtered CSV",
    data=view.to_csv(index=False),
    file_name="agent-audit-log.csv",
    mime="text/csv",
    icon=":material/download:",
    on_click="ignore",
)
