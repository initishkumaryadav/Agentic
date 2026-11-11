import uuid

import altair as alt
import pandas as pd
import streamlit as st

from src.ui_common import (
    ESCALATION_DELAY_PROB,
    PORT_CONGESTION_LIMIT,
    RISK_COLORS,
    TEMP_BREACH_C,
    load_fleet,
    sop_flags,
)

WINDOWS = {"24 hours": 1, "7 days": 7, "30 days": 30, "90 days": 90}
RISK_ORDER = ["High Risk", "Moderate Risk", "Low Risk"]
MAP_POINTS = 1500
TABLE_ROWS = 500


def ask_agent_about(row):
    """Open a fresh chat with a question about one reading and switch to Dispatch."""
    question = (
        f"Investigate the shipment at {row['Latitude']:.4f}, {row['Longitude']:.4f} "
        f"recorded at {row['Timestamp']:%Y-%m-%d %H:%M}. Its cargo temperature is "
        f"{row['Current_Temperature_C']:.1f}°C, risk is {row['Risk_Classification']}, delay probability "
        f"{row['Delay_Probability']:.2f} and port congestion {row['Port_Congestion_Level']:.1f}. "
        "Check corridor conditions there and tell me what the SOP requires."
    )
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.ui_messages = []
    st.session_state.pending_prompt = question
    st.switch_page(st.session_state.dispatch_page)


def bucket_counts(frame, column, freq):
    return frame.set_index("Timestamp")[column].resample(freq).sum().tolist()


df_all, source = load_fleet()


with st.container(horizontal=True, vertical_alignment="bottom"):
    st.header("Fleet overview", width="content")
    st.space("stretch")
    if st.button("Refresh", icon=":material/refresh:", type="tertiary"):
        load_fleet.clear()
        st.rerun()

latest = df_all["Timestamp"].max()
source_note = (
    ":material/database: Live from `llm_views.Active_FLEET`"
    if source == "database"
    else ":orange[:material/cloud_off:] Database unreachable, showing the raw CSV sample"
)
st.caption(f"{source_note} · latest reading {latest:%d %b %Y, %H:%M}")

with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
    window = st.segmented_control(
        "Time window", list(WINDOWS), default="7 days", required=True, label_visibility="collapsed"
    )
    risks = st.pills(
        "Risk", RISK_ORDER, selection_mode="multi", default=RISK_ORDER, label_visibility="collapsed"
    )

days = WINDOWS[window]
start = latest - pd.Timedelta(days=days)
prev_start = start - pd.Timedelta(days=days)
risk_mask = df_all["Risk_Classification"].isin(risks or RISK_ORDER)

df = sop_flags(df_all[(df_all["Timestamp"] > start) & risk_mask])
prev = sop_flags(df_all[(df_all["Timestamp"] > prev_start) & (df_all["Timestamp"] <= start) & risk_mask])
freq = "h" if days == 1 else "D"

if df.empty:
    st.info("No readings in this window for the selected risk levels.", icon=":material/filter_alt_off:")
    st.stop()


def delta(now_val, prev_val):
    return None if prev.empty else now_val - prev_val


with st.container(horizontal=True, gap="medium"):
    st.metric(
        "Readings",
        f"{len(df):,}",
        delta=delta(len(df), len(prev)),
        border=True,
        chart_data=df.set_index("Timestamp")["Latitude"].resample(freq).count().tolist(),
        chart_type="bar",
        help="Telemetry rows in the selected window, compared with the window before it.",
    )
    st.metric(
        "Temperature breaches",
        f"{int(df['Temp_Breach'].sum()):,}",
        delta=delta(int(df["Temp_Breach"].sum()), int(prev["Temp_Breach"].sum())),
        delta_color="inverse",
        border=True,
        chart_data=bucket_counts(df, "Temp_Breach", freq),
        chart_type="bar",
        help=f"Readings above {TEMP_BREACH_C:.1f}°C (SOP section 1).",
    )
    st.metric(
        "Tier 2 escalations",
        f"{int(df['Escalate'].sum()):,}",
        delta=delta(int(df["Escalate"].sum()), int(prev["Escalate"].sum())),
        delta_color="inverse",
        border=True,
        chart_data=bucket_counts(df, "Escalate", freq),
        chart_type="bar",
        help=f"High Risk with delay probability above {ESCALATION_DELAY_PROB} (SOP section 3).",
    )
    avg_cong = df["Port_Congestion_Level"].mean()
    st.metric(
        "Avg port congestion",
        f"{avg_cong:.1f} / 10",
        delta=None if prev.empty else round(avg_cong - prev["Port_Congestion_Level"].mean(), 2),
        delta_color="inverse",
        border=True,
        chart_data=df.set_index("Timestamp")["Port_Congestion_Level"].resample(freq).mean().round(2).tolist(),
        help=f"Above {PORT_CONGESTION_LIMIT:.0f} triggers diversion to the overflow depot (SOP section 2).",
    )


map_col, mix_col = st.columns([3, 2], gap="medium")

with map_col:
    with st.container(border=True):
        st.markdown("**:material/map: Where the fleet is**")
        points = df.tail(MAP_POINTS).assign(color=lambda d: d["Risk_Classification"].map(RISK_COLORS))
        st.map(points, latitude="Latitude", longitude="Longitude", color="color", size=6000, height=360)
        st.caption(
            f"Latest {min(len(df), MAP_POINTS):,} readings · "
            ":red[●] High · :orange[●] Moderate · :green[●] Low"
        )

with mix_col:
    with st.container(border=True):
        st.markdown("**:material/donut_small: Risk mix**")
        mix = df["Risk_Classification"].value_counts().reindex(RISK_ORDER).fillna(0).reset_index()
        mix.columns = ["Risk", "Readings"]
        bars = alt.Chart(mix).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("Readings:Q", title=None),
            y=alt.Y("Risk:N", sort=RISK_ORDER, title=None),
            color=alt.Color(
                "Risk:N",
                scale=alt.Scale(domain=RISK_ORDER, range=[RISK_COLORS[r][:7] for r in RISK_ORDER]),
                legend=None,
            ),
            tooltip=["Risk", alt.Tooltip("Readings:Q", format=",")],
        ).properties(height=150)
        st.altair_chart(bars)

        st.markdown("**:material/rule: SOP rules triggered**")
        rule_counts = pd.DataFrame({
            "Rule": ["Temperature breach", "Port diversion", "Tier 2 escalation"],
            "Share": [df["Temp_Breach"].mean(), df["Port_Diversion"].mean(), df["Escalate"].mean()],
        })
        st.dataframe(
            rule_counts,
            hide_index=True,
            column_config={
                "Share": st.column_config.ProgressColumn("Share of readings", format="percent", min_value=0, max_value=1),
            },
        )


with st.container(border=True):
    st.markdown("**:material/thermostat: Cargo temperature**")
    trend = (
        df.set_index("Timestamp")["Current_Temperature_C"]
        .resample(freq)
        .agg(["mean", "max"])
        .rename(columns={"mean": "Average", "max": "Highest"})
        .reset_index()
        .melt("Timestamp", var_name="Series", value_name="Temperature")
    )
    lines = alt.Chart(trend).mark_line(point=days <= 7).encode(
        x=alt.X("Timestamp:T", title=None),
        y=alt.Y("Temperature:Q", title="°C"),
        color=alt.Color("Series:N", legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Timestamp:T", format="%d %b %H:%M"), "Series", alt.Tooltip("Temperature:Q", format=".1f")],
    )
    limit = alt.Chart(pd.DataFrame({"y": [TEMP_BREACH_C]})).mark_rule(strokeDash=[4, 4], color="#D64545").encode(y="y:Q")
    st.altair_chart((lines + limit).properties(height=260))
    st.caption(f"Dashed line: {TEMP_BREACH_C:.0f}°C breach threshold for fresh perishables.")

st.subheader("Needs attention")
flagged = (
    df[df["Rules"].str.len() > 0]
    .sort_values(["Escalate", "Current_Temperature_C", "Timestamp"], ascending=False)
    .head(TABLE_ROWS)
    .reset_index(drop=True)
)
st.caption(
    f"{int((df['Rules'].str.len() > 0).sum()):,} readings break at least one SOP rule. "
    f"Showing the {len(flagged):,} most urgent. Select a row to hand it to the agent."
)

selection = st.dataframe(
    flagged,
    key="attention_table",
    on_select="rerun",
    selection_mode="single-row",
    hide_index=True,
    height=380,
    column_order=[
        "Timestamp", "Rules", "Risk_Classification", "Current_Temperature_C",
        "Delay_Probability", "Port_Congestion_Level", "Route_Risk_Index", "Latitude", "Longitude",
    ],
    column_config={
        "Timestamp": st.column_config.DatetimeColumn("Time", format="DD MMM, HH:mm"),
        "Rules": st.column_config.ListColumn("Rules broken", width="medium"),
        "Risk_Classification": st.column_config.TextColumn("Risk"),
        "Current_Temperature_C": st.column_config.NumberColumn("Temp", format="%.1f °C"),
        "Delay_Probability": st.column_config.ProgressColumn("Delay prob.", format="percent", min_value=0, max_value=1),
        "Port_Congestion_Level": st.column_config.ProgressColumn("Port congestion", format="%.1f", min_value=0, max_value=10),
        "Route_Risk_Index": st.column_config.NumberColumn("Route risk", format="%.1f"),
        "Latitude": st.column_config.NumberColumn("Lat", format="%.3f"),
        "Longitude": st.column_config.NumberColumn("Lon", format="%.3f"),
    },
)

selected_rows = selection.selection.rows
with st.container(horizontal=True, vertical_alignment="center"):
    if st.button(
        "Ask the agent about this reading",
        icon=":material/support_agent:",
        type="primary",
        disabled=not selected_rows,
    ):
        ask_agent_about(flagged.iloc[selected_rows[0]])
    st.download_button(
        "Download CSV",
        data=flagged.assign(Rules=flagged["Rules"].str.join("; ")).to_csv(index=False),
        file_name=f"fleet-attention-{latest:%Y%m%d}.csv",
        mime="text/csv",
        icon=":material/download:",
        on_click="ignore",
    )
