import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src import chat_store
from src.orchestrator import agent, message_text
from src.ui_common import parse_telemetry_output, telemetry_row_summary, tool_label, write_audit_log

SUGGESTIONS = {
    ":material/thermostat: Reefers above 4 °C": "Which reefer units are running above 4°C right now?",
    ":material/priority_high: Shipments to escalate": "Which high-risk shipments have a delay probability above 0.65 and need Tier 2 escalation?",
    ":material/air: Conditions near Long Beach": "What are the current corridor conditions near 33.77, -118.19?",
    ":material/menu_book: Temperature breach SOP": "What is the SOP for a cold-chain temperature breach on fresh perishables?",
}

CAPABILITIES = [
    (":material/database:", "Fleet telemetry", "Cargo temperature, risk, delay and congestion for every vehicle."),
    (":material/route:", "Corridor conditions", "Live weather and wind for any GPS position on the route."),
    (":material/menu_book:", "Compliance SOPs", "Thresholds, escalation paths and diversion rules."),
]

ASSISTANT_AVATAR = ":material/support_agent:"
OWNER = st.session_state.user_id  # set by frontend.py after sign-in
USER_AVATAR = ":material/person:"


# --- Chat state ----------------------------------------------------------------

def open_chat(session_id=None):
    """Switch to an existing chat, or start a new one when session_id is None."""
    st.session_state.thread_id = session_id or str(uuid.uuid4())
    st.session_state.ui_messages = chat_store.get_messages(session_id, OWNER) if session_id else []
    st.session_state.pending_prompt = None


def history_for_agent(thread_config, past_messages):
    """The agent keeps context in memory only. After an app restart, rebuild it from saved history."""
    state = agent.get_state(thread_config)
    if state.values.get("messages"):
        return []
    return [
        HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"])
        for m in past_messages
    ]


def date_group(iso_ts, now):
    try:
        ts = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return "Older"
    days = (now.date() - ts.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return "Previous 7 days"
    if days < 30:
        return "Previous 30 days"
    return "Older"


def chat_as_markdown(messages, title):
    lines = [f"# {title}", ""]
    for m in messages:
        who = "Dispatcher" if m["role"] == "user" else "Agent"
        lines += [f"**{who}:**", "", m["content"], ""]
    return "\n".join(lines)


def save_feedback(message_id, key):
    value = st.session_state.get(key)
    chat_store.set_feedback(message_id, OWNER, value)
    for m in st.session_state.ui_messages:
        if m.get("id") == message_id:
            m["feedback"] = value
    if value is not None:
        st.toast("Thanks for the feedback", icon=":material/check:")



def render_step(step):
    """One collapsed row per tool call: what was asked and what came back."""
    name = step["name"]
    label, icon = tool_label(name)
    args = step.get("args") or {}
    output = step.get("output")

    with st.expander(label, icon=icon):
        if name == "query_telemetry_db" and args.get("sql_query"):
            st.code(args["sql_query"], language="sql", wrap_lines=True)
        elif name == "fetch_corridor_conditions" and {"latitude", "longitude"} <= args.keys():
            try:
                point = pd.DataFrame([{"lat": float(args["latitude"]), "lon": float(args["longitude"])}])
                st.map(point, zoom=6, height=180, size=4000)
            except (TypeError, ValueError):
                st.code(json.dumps(args, indent=2), language="json")
        elif name == "search_compliance_sop" and args.get("query"):
            st.caption(f":material/search: {args['query']}")
        elif args:
            st.code(json.dumps(args, indent=2), language="json")

        if output is None:
            return
        table = parse_telemetry_output(output) if name == "query_telemetry_db" else None
        if table is not None:
            summary = telemetry_row_summary(output)
            if summary:
                st.caption(f":material/table_rows: Rows matched: {summary}")
            st.dataframe(table, hide_index=True)
        elif name == "search_compliance_sop" and "COMPLIANCE SOP CONTEXT" in output:
            body = "\n".join(l for l in output.splitlines() if not l.startswith("---"))
            with st.container(border=True):
                st.markdown(body)
        else:
            st.code(output, language="text", wrap_lines=True)


def render_answer_footer(entry):
    """Sources used, response time and a feedback control under each answer."""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        for source in dict.fromkeys(tool_label(s["name"])[0] for s in entry.get("steps") or []):
            st.badge(source, color="gray")
        if entry.get("elapsed_ms"):
            st.caption(f":material/timer: {entry['elapsed_ms'] / 1000:.1f}s")
        st.space("stretch")
        if entry.get("id"):
            key = f"feedback_{entry['id']}"
            st.feedback(
                "thumbs",
                key=key,
                default=entry.get("feedback"),
                on_change=save_feedback,
                args=(entry["id"], key),
            )


def render_message(entry):
    avatar = USER_AVATAR if entry["role"] == "user" else ASSISTANT_AVATAR
    with st.chat_message(entry["role"], avatar=avatar):
        for step in entry.get("steps") or []:
            render_step(step)
        st.markdown(entry["content"])
        if entry["role"] == "assistant":
            render_answer_footer(entry)


def use_suggestion():
    picked = st.session_state.get("suggestion_pick")
    if picked:
        st.session_state.pending_prompt = SUGGESTIONS[picked]
    st.session_state.suggestion_pick = None


def render_empty_state():
    st.space("medium")
    st.title("What's happening on the road?", text_alignment="center")
    st.caption(
        "Ask about cargo temperatures, risky routes or what the SOP says. "
        "The agent looks up live data before it answers.",
        text_alignment="center",
    )
    st.space("small")
    with st.container(horizontal=True, gap="medium"):
        for icon, title, desc in CAPABILITIES:
            with st.container(border=True):
                st.markdown(f"**{icon} {title}**")
                st.caption(desc)
    st.space("small")
    st.pills(
        "Try asking",
        list(SUGGESTIONS),
        key="suggestion_pick",
        on_change=use_suggestion,
        width="stretch",
    )




sessions = chat_store.list_sessions(OWNER)
current = next((c for c in sessions if c["id"] == st.session_state.thread_id), None)

with st.sidebar:
    if st.button("New chat", icon=":material/edit_square:", type="primary", width="stretch"):
        open_chat()
        st.rerun()

    if sessions:
        search = st.text_input(
            "Search chats",
            placeholder="Search chats",
            icon=":material/search:",
            label_visibility="collapsed",
        )
        shown = [c for c in sessions if search.lower() in c["title"].lower()] if search else sessions

        with st.container(height=420, border=False):
            now = datetime.now(timezone.utc)
            last_group = None
            for chat in shown:
                group = date_group(chat["updated_at"], now)
                if group != last_group:
                    st.caption(group)
                    last_group = group
                is_current = chat["id"] == st.session_state.thread_id
                if st.button(
                    chat["title"],
                    key=f"chat_{chat['id']}",
                    type="secondary" if is_current else "tertiary",
                    width="stretch",
                    help=chat["title"],
                ):
                    open_chat(chat["id"])
                    st.rerun()
            if not shown:
                st.caption("No chats match that search.")

    if current:
        with st.popover("This chat", icon=":material/tune:", width="stretch"):
            with st.form("rename_chat", border=False):
                new_title = st.text_input("Title", value=current["title"])
                if st.form_submit_button("Rename", icon=":material/edit:"):
                    chat_store.rename_session(current["id"], OWNER, new_title)
                    st.rerun()
            st.download_button(
                "Export as Markdown",
                data=chat_as_markdown(st.session_state.ui_messages, current["title"]),
                file_name=f"chat-{current['id'][:8]}.md",
                mime="text/markdown",
                icon=":material/download:",
                on_click="ignore",
                width="stretch",
            )
            if st.button("Delete chat", icon=":material/delete:", width="stretch"):
                chat_store.delete_session(current["id"], OWNER)
                open_chat()
                st.toast("Chat deleted", icon=":material/delete:")
                st.rerun()


# --- Main area -----------------------------------------------------------------------

thread_config = {"configurable": {"thread_id": st.session_state.thread_id}}

if st.session_state.ui_messages:
    st.subheader(current["title"] if current else "Cold-chain incidents")
    for entry in st.session_state.ui_messages:
        render_message(entry)
elif not st.session_state.pending_prompt:
    render_empty_state()

typed = st.chat_input("Ask about a shipment, route or procedure", submit_mode="disable")
user_input = typed or st.session_state.pending_prompt
st.session_state.pending_prompt = None

if user_input:
    past_messages = list(st.session_state.ui_messages)
    try:
        user_msg_id = chat_store.add_message(st.session_state.thread_id, OWNER, "user", user_input)
    except chat_store.ChatOwnershipError:
        st.error("That chat isn't yours. Starting a new chat instead.", icon=":material/lock:")
        open_chat()
        st.stop()
    st.session_state.ui_messages.append({"id": user_msg_id, "role": "user", "content": user_input})
    render_message(st.session_state.ui_messages[-1])

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        final_response = ""
        steps = {}  
        error = None
        started = time.perf_counter()

        with st.status("Thinking…", expanded=False) as status:
            try:
                events = agent.stream(
                    {"messages": [*history_for_agent(thread_config, past_messages), HumanMessage(content=user_input)]},
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
                                    user_id=OWNER,
                                    node_name="reasoner",
                                    tool_name=tool_call["name"],
                                    content=json.dumps(tool_call["args"]),
                                )

                            reply = message_text(latest_msg).strip()
                            if reply:
                                final_response = reply
                                status.update(label="Writing answer…")
                                write_audit_log(
                                    session_id=st.session_state.thread_id,
                                    user_id=OWNER,
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
                                    step["output"] = message_text(msg)
                                    write_audit_log(
                                        session_id=st.session_state.thread_id,
                                        user_id=OWNER,
                                        node_name="tools",
                                        tool_name=msg.name,
                                        content=message_text(msg),
                                    )
            except Exception as e:
                error = e

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            step_list = list(steps.values())
            if error:
                status.update(label="Request failed", state="error")
            elif step_list:
                sources = ", ".join(dict.fromkeys(tool_label(s["name"])[0].lower() for s in step_list))
                status.update(label=f"Checked {sources} in {elapsed_ms / 1000:.1f}s", state="complete")
            else:
                status.update(label="Answered without lookups", state="complete")

            for step in step_list:
                render_step(step)

        if error:
            st.error(
                f"The agent could not finish this request: {error}\n\nSend the question again to retry.",
                icon=":material/error:",
            )
        elif final_response:
            st.markdown(final_response)
            message_id = chat_store.add_message(
                st.session_state.thread_id, OWNER, "assistant", final_response, step_list, elapsed_ms
            )
            entry = {
                "id": message_id,
                "role": "assistant",
                "content": final_response,
                "steps": step_list,
                "elapsed_ms": elapsed_ms,
                "feedback": None,
            }
            st.session_state.ui_messages.append(entry)
            render_answer_footer(entry)
        else:
            st.warning("No answer came back. Try rephrasing the question.", icon=":material/warning:")

    if not past_messages and not error and final_response:
        st.rerun()
