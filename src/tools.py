import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_pinecone import PineconeVectorStore

from src.ui_common import agent_engine
from src.sql_guard import UnsafeQueryError, validate_select

load_dotenv(dotenv_path=project_root / ".env")
PREVIEW_ROWS = 10
COUNT_LIMIT = 5000

DEFAULT_INDEX = {
    "LOCAL": "index-local",
    "GEMINI": "index-gemini",
    "OPENAI": "index-openai",
}


@dataclass(frozen=True)
class EmbeddingSettings:
    provider: str
    model: str
    index_name: str


def embedding_settings() -> EmbeddingSettings:
    provider = os.getenv("Embeddings_model", "LOCAL").strip().upper()
    if provider not in DEFAULT_INDEX:
        raise ValueError(f"Embeddings_model must be one of {sorted(DEFAULT_INDEX)}, got {provider!r}")

    if provider == "GEMINI":
        model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    elif provider == "OPENAI":
        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    else:
        legacy = os.getenv("LOCAL_EM_MODEL")
        model = os.getenv("Local_Embedding_Model") or legacy or "Qwen/Qwen3-Embedding-4B"

    index_name = os.getenv("PINECONE_INDEX") or DEFAULT_INDEX[provider]
    return EmbeddingSettings(provider=provider, model=model, index_name=index_name)


def _local_device() -> str:
    device = os.getenv("EMBEDDING_DEVICE", "auto").strip().lower()
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def resolve_local_model(name: str) -> str:
    if Path(name).exists():
        return name
    try:
        from huggingface_hub import try_to_load_from_cache
        # modules.json marks a sentence-transformers model; its folder is the cached snapshot.
        marker = try_to_load_from_cache(name, "modules.json")
        if isinstance(marker, str):
            folder = Path(marker).parent
            if (folder / "config.json").exists() and any(folder.glob("*.safetensors")):
                return str(folder)
    except Exception:
        pass
    return name


def build_embeddings(settings: EmbeddingSettings | None = None):
    settings = settings or embedding_settings()

    if settings.provider == "GEMINI":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Embeddings_model=GEMINI but GEMINI_API_KEY is empty in .env")
        return GoogleGenerativeAIEmbeddings(model=settings.model, google_api_key=api_key)

    if settings.provider == "OPENAI":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=settings.model)

    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=resolve_local_model(settings.model),
        model_kwargs={"device": _local_device()},
        encode_kwargs={"normalize_embeddings": True},
    )


def _load_embeddings(settings):
    try:
        import streamlit as st
        if st.runtime.exists():
            return st.cache_resource(show_spinner=False)(build_embeddings)(settings)
    except ImportError:
        pass
    return build_embeddings(settings)


@lru_cache(maxsize=1)
def get_retriever():
    if not os.getenv("PINECONE_API_KEY"):
        raise ValueError("PINECONE_API_KEY must be set in the .env file")
    settings = embedding_settings()
    print(f"Embeddings: {settings.provider} [{settings.model}] -> Pinecone index [{settings.index_name}]")
    vector_store = PineconeVectorStore(index_name=settings.index_name, embedding=_load_embeddings(settings))
    return vector_store.as_retriever(search_kwargs={"k": 2})


def format_rows(columns, rows) -> str:
    if not rows:
        return "No records matched the query criteria."

    if len(rows) > COUNT_LIMIT:
        summary = f"ROWS MATCHED: more than {COUNT_LIMIT:,} (showing first {PREVIEW_ROWS})"
    elif len(rows) > PREVIEW_ROWS:
        summary = f"ROWS MATCHED: {len(rows):,} (showing first {PREVIEW_ROWS})"
    else:
        summary = f"ROWS MATCHED: {len(rows):,}"

    lines = [summary, f"COLUMNS: {', '.join(columns)}"]
    lines += [str(tuple(row)) for row in rows[:PREVIEW_ROWS]]
    return "\n".join(lines) + "\n"


@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Runs ONE read-only T-SQL SELECT against the view llm_views.Active_FLEET (no other tables).
    Columns available:
    Timestamp, Latitude, Longitude, Current_Temperature_C, Cargo_Condition_Code,
    Risk_Classification, Delay_Probability, Port_Congestion_Level, Route_Risk_Index.
    The result shows how many rows matched in total and the first 10 rows.
    For counts, averages or rankings use COUNT, AVG, GROUP BY or TOP in the query itself.
    """
    try:
        safe_sql = validate_select(sql_query)
    except UnsafeQueryError as e:
        return f"QUERY REJECTED: {e}"

    try:
        with agent_engine().connect() as conn:
            result = conn.exec_driver_sql(safe_sql)
            columns = list(result.keys())
            rows = result.fetchmany(COUNT_LIMIT + 1)
    except Exception as e:
        return f"Database Error: {e}"

    return format_rows(columns, rows)

_SEVERE_CODES = {
    45: "fog", 48: "freezing fog", 56: "freezing drizzle", 57: "freezing drizzle",
    65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
_HIGH_CODES = {66, 67, 75, 86, 95, 96, 99}


def weather_disruption(gusts_kmh: float, precip_mm: float, code: int) -> tuple[str, list[str]]:
    reasons = []
    if gusts_kmh >= 40:
        reasons.append(f"gusts {gusts_kmh:.0f} km/h")
    if precip_mm >= 4:
        reasons.append(f"precipitation {precip_mm:.1f} mm")
    if code in _SEVERE_CODES:
        reasons.append(_SEVERE_CODES[code])

    if gusts_kmh >= 60 or code in _HIGH_CODES:
        level = "High"
    elif reasons:
        level = "Moderate"
    else:
        level = "Low"
    return level, reasons


@tool
def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    """
    Fetches live weather at the given GPS coordinates (Open-Meteo): temperature, wind, gusts,
    precipitation and conditions, plus a rule-based weather disruption level (Low/Moderate/High).
    This is weather only. It does NOT measure traffic or port congestion; use
    Port_Congestion_Level from query_telemetry_db for congestion.
    """
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return "Invalid coordinates: latitude must be -90..90 and longitude -180..180."
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,weather_code",
                "timezone": "UTC",
            },
            timeout=6,
        )
        response.raise_for_status()
        current = response.json().get("current", {})
    except Exception as e:
        return f"Weather API failure: {e}"

    temp = current.get("temperature_2m")
    wind = float(current.get("wind_speed_10m") or 0.0)
    gusts = float(current.get("wind_gusts_10m") or 0.0)
    precip = float(current.get("precipitation") or 0.0)
    code = int(current.get("weather_code") or 0)
    level, reasons = weather_disruption(gusts, precip, code)
    why = f" ({'; '.join(reasons)})" if reasons else " (no adverse conditions)"

    return (
        "--- LIVE CORRIDOR WEATHER (Open-Meteo) ---\n"
        f"Location: {latitude}, {longitude} | Observed: {current.get('time', 'N/A')} UTC\n"
        f"Air temp: {temp}°C | Wind: {wind:.0f} km/h | Gusts: {gusts:.0f} km/h | "
        f"Precipitation: {precip:.1f} mm | WMO code: {code}\n"
        f"Weather disruption estimate: {level}{why}\n"
        "Note: weather only, not a traffic or congestion measurement.\n"
        "------------------------------------------"
    )


@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.
    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
    """
    try:
        matched_docs = get_retriever().invoke(query)
        if not matched_docs:
            return "No matching compliance clauses found."

        formatted_context = "\n\n".join(
            [f"[Source: {doc.metadata.get('source_file', 'SOP')} | Format: {doc.metadata.get('file_format', 'RAW')}]\n{doc.page_content}" for doc in matched_docs]
        )
        return f"--- COMPLIANCE SOP CONTEXT ---\n{formatted_context}\n------------------------------"
    except Exception as e:
        return f"Vector Store Retrieval Error: {str(e)}"


if __name__ == "__main__":
    print("\n--- Testing Tool 1: SQL Telemetry View ---")
    print(query_telemetry_db.invoke("SELECT TOP 2 Latitude, Longitude, Current_Temperature_C FROM llm_views.Active_FLEET"))

    print("\n--- Testing Tool 2: Live Corridor API ---")
    print(fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19}))

    print("\n--- Testing Tool 3: Pinecone Vector Retrieval ---")
    print(search_compliance_sop.invoke("What are the temperature rules for fresh perishables?"))
