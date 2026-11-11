import os
import hashlib
import json
from numbers import Integral
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import pypdf

from pinecone import Pinecone, ServerlessSpec
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_pinecone import PineconeVectorStore
from langchain_core.documents import Document

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"

script_dir = Path(__file__).resolve().parent
projectRoot = script_dir.parent
load_dotenv(projectRoot / ".env")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

if not PINECONE_API_KEY:
    raise ValueError("pinecone api key missing.")

cache_dir = projectRoot / "data" / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
HASH_CACHE_FILE = cache_dir / "ingestion_hash_cache.json"

hash_cache = {}
if HASH_CACHE_FILE.exists():
    try:
        with open(HASH_CACHE_FILE, "r") as f:
            hash_cache = json.load(f)
    except Exception:
        hash_cache = {}

EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

if EMBEDDINGS_MODEL_SETTING == "OPENAI":
    print("we are using openai flagship embedding models..")
    embeddings = OpenAIEmbeddings()
    INDEX_NAME = "index-cloud"
else:
    
    import torch
    from langchain_huggingface import HuggingFaceEmbeddings

    local_model_target = os.getenv("LOCAL_EM_MODEL", "Qwen/Qwen3-Embedding-4B")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"local em model working. Launching [{local_model_target}] on [{device}]")
    embeddings = HuggingFaceEmbeddings(
        model_name=local_model_target,
        model_kwargs={"device": device},
        encode_kwargs={"batch_size": 8, "normalize_embeddings": True},
    )
    INDEX_NAME = "index-local"


TARGET_DIMENSION = len(embeddings.embed_query("dimension probe"))
print(f"Embedding dimension detected: {TARGET_DIMENSION}")

print(f"Connecting to Pinecone Index Target: [{INDEX_NAME}]")
pc = Pinecone(api_key=PINECONE_API_KEY)
existing_indexes = pc.list_indexes().names()

if INDEX_NAME in existing_indexes:
    desc = pc.describe_index(INDEX_NAME)
    if desc.dimension != TARGET_DIMENSION:
        print(f"fixing tracking: purging mismatched {desc.dimension} dim index...")
        pc.delete_index(INDEX_NAME)
        existing_indexes = [name for name in existing_indexes if name != INDEX_NAME]
        hash_cache = {} 

if INDEX_NAME not in existing_indexes:
    print(f"Creating isolated target index: {INDEX_NAME} ({TARGET_DIMENSION} dim)..")
    pc.create_index(
        name=INDEX_NAME,
        dimension=TARGET_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )

index_client = pc.Index(INDEX_NAME)
vector_store = PineconeVectorStore(index=index_client, embedding=embeddings)


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

updated_cache = {}
cache_modified = False

deleted_files = set(hash_cache.keys()) - set(current_files.keys())

for deleted_file in deleted_files:
    print(f"Detected deleted file: {deleted_file}. Purging from Pinecone...")
    try:
        index_client.delete(filter={"source_file": {"$eq": deleted_file}})
        cache_modified = True
    except Exception as e:
        print(f"Failed to purge {deleted_file}: {e}")
        updated_cache[deleted_file] = hash_cache[deleted_file]  

for file_name, file_path in current_files.items():
    file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
    updated_cache[file_name] = file_hash

    if hash_cache.get(file_name) == file_hash:
        print(f"Skipped (Unchanged): {file_name}")
        continue

    print(f"Processing updates: {file_name}....")
    cache_modified = True

    try:
        try:
            index_client.delete(filter={"source_file": {"$eq": file_name}})
        except Exception:
            pass 

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
            explicit_ids.append(f"{file_name}-chunk-{idx}")

        batch_size = 100
        total_chunks = len(chunks)
        print(f"upserting {total_chunks} chunk(s) in batches of {batch_size}....")

        for i in range(0, total_chunks, batch_size):
            vector_store.add_documents(
                documents=chunks[i:i + batch_size],
                ids=explicit_ids[i:i + batch_size],
            )

    except Exception as e:
        print(f"Error during ingestion of {file_name}: {e}")
        updated_cache.pop(file_name, None)  

if cache_modified:
    with open(HASH_CACHE_FILE, "w") as f:
        json.dump(updated_cache, f, indent=4)
    print("Ingestion & cache update complete.")
else:
    print("Index is already up to date.")
