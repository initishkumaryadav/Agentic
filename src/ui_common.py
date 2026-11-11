"""Shared helpers for the Streamlit pages: DB access, audit logging, fleet data and SOP rules."""
import ast
import re
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.db import agent_engine, audit_engine

projectRoot = Path(__file__).resolve().parent.parent

CSV_PATH = projectRoot / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

TEMP_BREACH_C = 4.0
PORT_CONGESTION_LIMIT = 7.0
ESCALATION_DELAY_PROB = 0.65

RISK_COLORS = {
    "High Risk": "#D64545CC",
    "Moderate Risk": "#E0912FCC",
    "Low Risk": "#2E9E6ACC",
}


TOOL_LABELS = {
    "query_telemetry_db": ("Fleet telemetry", ":material/database:"),
    "fetch_corridor_conditions": ("Corridor conditions", ":material/route:"),
    "search_compliance_sop": ("Compliance SOPs", ":material/menu_book:"),
}


def tool_label(name):
    return TOOL_LABELS.get(name, (name.replace("_", " ").capitalize(), ":material/build:"))


_audit_warning_shown = False


def write_audit_log(session_id, user_id, node_name, tool_name, content):
    """Appends one agent step to the audit table using the insert-only audit login."""
    global _audit_warning_shown
    engine = audit_engine()
    if engine is None:
        if not _audit_warning_shown:
            print("Audit log disabled: set SQL_AUDIT_USER and SQL_AUDIT_PASSWORD in .env")
            _audit_warning_shown = True
        return
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO llm_views.AgentAuditLog (SessionID, UserID, NodeExecuted, ToolName, Content)
                VALUES (:session_id, :user_id, :node_name, :tool_name, :content)
            """), {
                "session_id": session_id,
                "user_id": user_id,
                "node_name": node_name,
                "tool_name": tool_name,
                "content": content,
            })
            conn.commit()
    except Exception as e:
        print(f"Audit log write failed: {e}")




_CSV_TO_VIEW = {
    "timestamp": "Timestamp",
    "vehicle_gps_latitude": "Latitude",
    "vehicle_gps_longitude": "Longitude",
    "iot_temperature": "Current_Temperature_C",
    "cargo_condition_status": "Cargo_Condition_Code",
    "risk_classification": "Risk_Classification",
    "delay_probability": "Delay_Probability",
    "port_congestion_level": "Port_Congestion_Level",
    "route_risk_level": "Route_Risk_Index",
}


@st.cache_data(ttl=600, show_spinner="Loading fleet telemetry…")
def load_fleet():
    """Reads the same view the agent queries. Falls back to the raw CSV when the database is unreachable."""
    try:
        with agent_engine().connect() as conn:
            df = pd.read_sql(text("SELECT * FROM llm_views.Active_FLEET"), conn)
        source = "database"
    except Exception as e:
        print(f"Fleet view unavailable, using CSV: {e}")
        df = pd.read_csv(CSV_PATH, usecols=list(_CSV_TO_VIEW)).rename(columns=_CSV_TO_VIEW)
        source = "csv"

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    for col in ["Latitude", "Longitude", "Current_Temperature_C", "Cargo_Condition_Code",
                "Delay_Probability", "Port_Congestion_Level", "Route_Risk_Index"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
    return df, source


def sop_flags(df):
    """Adds one boolean column per SOP rule plus a readable summary of the rules that fired."""
    out = df.copy()
    out["Temp_Breach"] = out["Current_Temperature_C"] > TEMP_BREACH_C
    out["Port_Diversion"] = out["Port_Congestion_Level"] > PORT_CONGESTION_LIMIT
    out["Escalate"] = (out["Risk_Classification"] == "High Risk") & (out["Delay_Probability"] > ESCALATION_DELAY_PROB)
    labels = {
        "Temp_Breach": "Temperature breach",
        "Port_Diversion": "Port diversion",
        "Escalate": "Tier 2 escalation",
    }
    out["Rules"] = out[list(labels)].apply(
        lambda r: [labels[k] for k, hit in r.items() if hit], axis=1
    )
    return out




_DATETIME_RE = re.compile(r"datetime\.datetime\(([^)]*)\)")
_DECIMAL_RE = re.compile(r"Decimal\('([^']*)'\)")


def _datetime_literal(match):
    parts = [p.strip() for p in match.group(1).split(",")]
    try:
        nums = [int(p) for p in parts]
        return repr(str(pd.Timestamp(*nums)))
    except Exception:
        return repr(match.group(0))


def telemetry_row_summary(output):
    """The 'ROWS MATCHED: ...' line from query_telemetry_db output, without the prefix, or None."""
    if output and output.startswith("ROWS MATCHED:"):
        return output.splitlines()[0][len("ROWS MATCHED:"):].strip()
    return None


def parse_telemetry_output(output):
    """Turns query_telemetry_db's 'COLUMNS: ...' + tuple rows into a DataFrame, or None if it can't."""
    if not output:
        return None
    lines = output.strip().splitlines()
    if lines and lines[0].startswith("ROWS MATCHED:"):
        lines = lines[1:]
    if not lines or not lines[0].startswith("COLUMNS:"):
        return None
    columns = [c.strip() for c in lines[0][len("COLUMNS:"):].split(",")]
    rows = []
    try:
        for line in lines[1:]:
            line = _DECIMAL_RE.sub(r"\1", _DATETIME_RE.sub(_datetime_literal, line))
            row = ast.literal_eval(line)
            rows.append(row if isinstance(row, tuple) else (row,))
        return pd.DataFrame(rows, columns=columns)
    except Exception:
        return None
