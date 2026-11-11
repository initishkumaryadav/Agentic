"""One place that decides which embedding model and Pinecone index to use.

Both scripts/ingest_sop_pinecone.py (writing vectors) and src/tools.py (querying them) call this,
so documents and questions are always embedded by the same model into the same index.

Embeddings_model   LOCAL (default) | GEMINI | OPENAI
Each provider has its own index, because vectors from different models can't be compared.
PINECONE_INDEX overrides the index name.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

projectRoot = Path(__file__).resolve().parent.parent
load_dotenv(projectRoot / ".env")

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
    """Path to the model if it's already on disk, so loading needs no network.
    Otherwise the Hub name, and the model is downloaded on first use."""
    if Path(name).exists():
        return name
    try:
        from huggingface_hub import try_to_load_from_cache
        # modules.json marks a sentence-transformers model; its folder is the cached snapshot.
        # (snapshot_download(local_files_only=True) is stricter and rejects snapshots missing
        # files embeddings never use, such as .gitattributes.)
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
        # A cached model loads from its folder: no Hub check, so no hang when huggingface.co is unreachable.
        model_name=resolve_local_model(settings.model),
        model_kwargs={"device": _local_device()},
        encode_kwargs={"normalize_embeddings": True},
    )
