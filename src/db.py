"""SQL Server connections. Each job gets its own login so a leak in one can't be used for another:

- agent login  (SQL_AGENT_USER):  SELECT on llm_views.Active_FLEET only. Used by the SQL tool and dashboard.
- audit login  (SQL_AUDIT_USER):  INSERT on llm_views.AgentAuditLog only. Used to write the audit trail.
- admin login  (SQL_ADMIN_USER):  typed in on the Audit log page to read the trail.

Permissions are created by scripts/security.sql.
"""
import os
import urllib.parse
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

projectRoot = Path(__file__).resolve().parent.parent
load_dotenv(projectRoot / ".env")

DB_HOST = os.getenv("SQL_SERVER_HOST", "localhost")
DB_PORT = os.getenv("SQL_SERVER_PORT", "1433")
DB_NAME = os.getenv("DB_NAME", "master")

# Seconds a single statement may run before the driver cancels it.
QUERY_TIMEOUT_S = int(os.getenv("SQL_QUERY_TIMEOUT", "20"))


def odbc_url(user: str, password: str) -> str:
    params = urllib.parse.quote_plus(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={DB_HOST},{DB_PORT};"
        f"DATABASE={DB_NAME};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=no;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=8;"
    )
    return f"mssql+pyodbc:///?odbc_connect={params}"


def _make_engine(user: str, password: str) -> Engine:
    engine = create_engine(odbc_url(user, password), pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _set_query_timeout(dbapi_conn, _record):
        dbapi_conn.timeout = QUERY_TIMEOUT_S  # pyodbc: per-statement timeout in seconds

    return engine


@lru_cache(maxsize=1)
def agent_engine() -> Engine:
    """Read-only engine for the agent's SQL tool and the fleet dashboard."""
    return _make_engine(os.getenv("SQL_AGENT_USER", "fde_agent_ro"), os.getenv("SQL_AGENT_PASSWORD", ""))


@lru_cache(maxsize=1)
def audit_engine() -> Engine | None:
    """Insert-only engine for the audit trail, or None when no audit login is configured."""
    user = os.getenv("SQL_AUDIT_USER")
    password = os.getenv("SQL_AUDIT_PASSWORD")
    if not user or not password:
        return None
    return _make_engine(user, password)


def admin_engine(user: str, password: str) -> Engine:
    return _make_engine(user, password)
