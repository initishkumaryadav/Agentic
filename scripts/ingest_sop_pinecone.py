import hashlib
import json
import os
import sys
import time
from numbers import Integral
from pathlib import Path

import pandas as pd
import pypdf
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_pinecone import PineconeVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pinecone import Pinecone, ServerlessSpec

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"

script_dir = Path(__file__).resolve().parent
projectRoot = script_dir.parent
if str(projectRoot) not in sys.path:
    sys.path.insert(0, str(projectRoot))
load_dotenv(projectRoot / ".env")
from src.tools import build_embeddings, embedding_settings

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
if not PINECONE_API_KEY:
    raise ValueError("pinecone api key missing.")

cache_dir = projectRoot / "data" / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
HASH_CACHE_FILE = cache_dir / "ingestion_hash_cache.json"


def load_cache() -> dict:
    if not HASH_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(HASH_CACHE_FILE.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("_format") != 2:
        print("Hash cache is from an older version; every file will be re-ingested once.")
        return {}
    return data.get("indexes", {})


def save_cache(indexes: dict) -> None:
    HASH_CACHE_FILE.write_text(json.dumps({"_format": 2, "indexes": indexes}, indent=4))


settings = embedding_settings()
INDEX_NAME = settings.index_name
print(f"Embeddings: {settings.provider} [{settings.model}] -> Pinecone index [{INDEX_NAME}]")
embeddings = build_embeddings(settings)

TARGET_DIMENSION = len(embeddings.embed_query("dimension probe"))
print(f"Embedding dimension detected: {TARGET_DIMENSION}")

all_caches = load_cache()
index_cache = all_caches.get(INDEX_NAME, {})
if index_cache.get("model") != settings.model:
    if index_cache:
        print(f"Embedding model changed ({index_cache.get('model')} -> {settings.model}); re-ingesting all files.")
    index_cache = {"model": settings.model, "files": {}}
hash_cache = index_cache["files"]

def pinecone_call(fn, what, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            reason = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
            if attempt == attempts:
                print(f"\nERROR: could not {what} after {attempts} attempts ({reason}).\n"
                      "Check your internet connection (api.pinecone.io must be reachable) and run the script again.")
                sys.exit(1)
            wait = 5 * attempt
            print(f"  Pinecone unreachable while trying to {what} ({reason}); retrying in {wait}s...")
            time.sleep(wait)


print(f"Connecting to Pinecone index [{INDEX_NAME}]")
pc = Pinecone(api_key=PINECONE_API_KEY)
existing_indexes = pinecone_call(lambda: pc.list_indexes().names(), "list Pinecone indexes")

if INDEX_NAME in existing_indexes:
    desc = pinecone_call(lambda: pc.describe_index(INDEX_NAME), f"describe index {INDEX_NAME}")
    if desc.dimension != TARGET_DIMENSION:
        print(f"Index has {desc.dimension} dimensions, model has {TARGET_DIMENSION}; recreating the index...")
        pc.delete_index(INDEX_NAME)
        existing_indexes = [name for name in existing_indexes if name != INDEX_NAME]
        hash_cache.clear()

if INDEX_NAME not in existing_indexes:
    print(f"Creating index {INDEX_NAME} ({TARGET_DIMENSION} dim)..")
    pc.create_index(
        name=INDEX_NAME,
        dimension=TARGET_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    hash_cache.clear()  

index_client = pc.Index(INDEX_NAME)
vector_store = PineconeVectorStore(index=index_client, embedding=embeddings)


def chunk_id_prefix(file_name: str) -> str:
    return f"{file_name}-chunk-"


def delete_file_vectors(file_name: str) -> None:
    try:
        for id_batch in index_client.list(prefix=chunk_id_prefix(file_name)):
            if id_batch:
                index_client.delete(ids=list(id_batch))
    except Exception as list_error:
        try:
            index_client.delete(filter={"source_file": {"$eq": file_name}})
        except Exception as filter_error:
            raise RuntimeError(f"could not delete old vectors: {list_error}; {filter_error}") from None


def parse_and_chunk_document(doc_path: Path) -> list[Document]:
    ext = doc_path.suffix.lower()
    raw_chunks: list[Document] = []
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)

    if ext == ".md":
        headers_to_split_on = [("#", "Header_1"), ("##", "Header_2"), ("###", "Header_3")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        raw_text = doc_path.read_text(encoding="utf-8")
        header_docs = md_splitter.split_text(raw_text)
        raw_chunks = text_splitter.split_documents(header_docs)

    elif ext == ".txt":
        raw_text = doc_path.read_text(encoding="utf-8")
        raw_docs = [Document(page_content=raw_text)]
        raw_chunks = text_splitter.split_documents(raw_docs)

    elif ext == ".pdf":
        pdf_docs = []
        try:
            with open(doc_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for page_num, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        pdf_docs.append(Document(page_content=page_text, metadata={"page": page_num}))
            raw_chunks = text_splitter.split_documents(pdf_docs)
        except Exception as e:
            print(f"Error parsing PDF {doc_path.name}: {e}")
            return []

    elif ext in [".csv", ".xlsx"]:
        try:
            df = pd.read_csv(doc_path) if ext == ".csv" else pd.read_excel(doc_path)
        except Exception as e:
            print(f"Error reading table {doc_path.name}: {e}")
            return []
        for idx, row in df.iterrows():
            row_items = [
                f"{col}: {val}"
                for col, val in row.to_dict().items()
                if pd.notna(val) and str(val).strip() != ""
            ]
            if row_items:
                row_text = " | ".join(row_items)
                row_index = int(idx) if isinstance(idx, Integral) else str(idx)
                raw_chunks.append(Document(page_content=row_text, metadata={"row_index": row_index}))

    valid_chunks = []
    for chunk in raw_chunks:
        clean_text = chunk.page_content.strip()
        if clean_text:
            chunk.page_content = clean_text
            valid_chunks.append(chunk)
    return valid_chunks


policy_dir = projectRoot / "data" / "policy"
policy_dir.mkdir(parents=True, exist_ok=True)

target_patterns = ["*.md", "*.txt", "*.pdf", "*.csv", "*.xlsx"]
current_files = {}
for pattern in target_patterns:
    for file_path in policy_dir.glob(pattern):
        current_files[file_path.name] = file_path
print(f"found {len(current_files)} policy file(s) in {policy_dir}...")

updated_files = {}
cache_modified = False

for deleted_file in set(hash_cache) - set(current_files):
    print(f"Detected deleted file: {deleted_file}. Purging from Pinecone...")
    try:
        delete_file_vectors(deleted_file)
        cache_modified = True
    except Exception as e:
        print(f"Failed to purge {deleted_file}: {e}")
        updated_files[deleted_file] = hash_cache[deleted_file]  # retry next run

for file_name, file_path in current_files.items():
    file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()

    if hash_cache.get(file_name) == file_hash:
        print(f"Skipped (Unchanged): {file_name}")
        updated_files[file_name] = file_hash
        continue

    print(f"Processing updates: {file_name}....")
    cache_modified = True

    try:
        delete_file_vectors(file_name)

        chunks = parse_and_chunk_document(file_path)
        if not chunks:
            print(f"no valid text chunks extracted from {file_name}.")
            continue

        ext = file_path.suffix.lower()
        explicit_ids = []
        for idx, chunk in enumerate(chunks):
            chunk.metadata["source_file"] = file_name
            chunk.metadata["file_format"] = ext.lstrip(".")
            chunk.metadata["document_type"] = "table_row" if ext in [".csv", ".xlsx"] else "policy_text"
            explicit_ids.append(f"{chunk_id_prefix(file_name)}{idx}")

        batch_size = 100
        print(f"upserting {len(chunks)} chunk(s) in batches of {batch_size}....")
        for i in range(0, len(chunks), batch_size):
            vector_store.add_documents(
                documents=chunks[i:i + batch_size],
                ids=explicit_ids[i:i + batch_size],
            )
        updated_files[file_name] = file_hash 

    except Exception as e:
        print(f"Error during ingestion of {file_name}: {e}")

if cache_modified or index_cache.get("files") != updated_files:
    all_caches[INDEX_NAME] = {"model": settings.model, "files": updated_files}
    save_cache(all_caches)
    print("Ingestion & cache update complete.")
else:
    print("Index is already up to date.")
