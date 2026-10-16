"""Tether ingestion pipeline: PDFs -> page chunks + catalog metadata -> Azure AI Search.

    python ingest.py                          # default collection (lts); safe to rerun
    python ingest.py --collection debris      # another collection
    python ingest.py --recreate               # drop and rebuild the index first
    python ingest.py --dry-run                # parse and chunk only; no Azure calls

For a collection <id>, PDFs are read from data/<id>/ (or data/ for the
default collection if that folder doesn't exist), metadata comes from the
collection's sources_file in backend/collections.json, and chunks go to the
collection's search_index (or AZURE_SEARCH_INDEX when that's null).

Each chunk carries its page number and the document's catalog metadata
(UN symbol, year, type, submitting country, official URL), so the app can
cite pages and filter by year, type or country.
"""

import argparse
import hashlib
import json
import logging
import os
import pathlib
import re
import time

from dotenv import load_dotenv
from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)  # hide font warnings

ROOT = pathlib.Path(__file__).parent
DEFAULT_COLLECTION = "lts"

# Set by configure() from backend/collections.json.
COLLECTION = DEFAULT_COLLECTION
DATA_DIR = ROOT / "data"
SOURCES_FILE = ROOT / "backend" / "sources" / f"{DEFAULT_COLLECTION}.json"
INDEX_NAME: str | None = None
OTHER_COLLECTIONS: set[str] = set()

CHUNK_SIZE = 3000      # characters
CHUNK_OVERLAP = 400
BATCH_SIZE = 16


def configure(collection_id: str) -> None:
    """Point the pipeline at one collection from backend/collections.json."""
    global COLLECTION, DATA_DIR, SOURCES_FILE, INDEX_NAME, OTHER_COLLECTIONS
    registry = json.loads((ROOT / "backend" / "collections.json").read_text())
    entry = next((c for c in registry if c["id"] == collection_id), None)
    if entry is None:
        raise SystemExit(f"No collection '{collection_id}' in backend/collections.json")
    if not entry.get("sources_file"):
        raise SystemExit(f"Collection '{collection_id}' has no sources_file yet.")

    COLLECTION = collection_id
    SOURCES_FILE = ROOT / "backend" / entry["sources_file"]
    own_dir = ROOT / "data" / collection_id
    if own_dir.is_dir():
        DATA_DIR = own_dir
    elif collection_id == DEFAULT_COLLECTION:
        DATA_DIR = ROOT / "data"
    else:
        raise SystemExit(f"Put this collection's PDFs in {own_dir.relative_to(ROOT)}/")
    INDEX_NAME = entry.get("search_index")
    OTHER_COLLECTIONS = {c["id"] for c in registry if c["id"] != collection_id}


# ---------------------------------------------------------------------------
# Extract + transform
# ---------------------------------------------------------------------------
def load_catalog() -> dict[str, dict]:
    """Catalog metadata keyed by lowercase filename."""
    entries = json.loads(SOURCES_FILE.read_text())
    return {e["filename"].lower(): e for e in entries if e.get("filename")}


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    step = size - overlap
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), step)]


def chunk_id(filename: str, page: int, n: int) -> str:
    # Deterministic, so reruns overwrite instead of duplicating.
    return hashlib.md5(f"{filename}-{page}-{n}".encode()).hexdigest()


def clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_chunks(catalog: dict[str, dict]) -> tuple[list[dict], list[str]]:
    chunks, warnings = [], []
    seen_files: dict[str, pathlib.Path] = {}

    for path in sorted(DATA_DIR.rglob("*.pdf")):
        # When the default collection reads data/ directly, skip other collections' folders.
        if path.relative_to(DATA_DIR).parts[0] in OTHER_COLLECTIONS:
            continue
        key = path.name.lower()
        if key in seen_files:
            warnings.append(f"duplicate filename skipped: {path} (already read {seen_files[key]})")
            continue
        seen_files[key] = path

        meta = catalog.get(key)
        if meta is None:
            warnings.append(f"not in {SOURCES_FILE.name}, skipped: {path.name}")
            continue

        try:
            reader = PdfReader(path)
        except Exception as exc:
            warnings.append(f"unreadable, skipped: {path.name} ({exc})")
            continue

        empty_pages = 0
        for page_num, page in enumerate(reader.pages, start=1):
            text = clean(page.extract_text() or "")
            if not text:
                empty_pages += 1
                continue
            for n, piece in enumerate(chunk(text)):
                chunks.append({
                    "id": chunk_id(path.name, page_num, n),
                    "content": piece,
                    "source": path.name,
                    "page": page_num,
                    "title": meta.get("title"),
                    "symbol": meta.get("symbol"),
                    "year": meta.get("year"),
                    "doc_type": meta.get("type"),
                    "submitted_by": meta.get("submitted_by"),
                    "url": meta.get("url"),
                    "collection": COLLECTION,
                })
        if empty_pages == len(reader.pages):
            warnings.append(f"no extractable text (scanned?): {path.name}")

    missing = sorted(set(catalog) - set(seen_files))
    for name in missing:
        warnings.append(f"in catalog but no PDF found in {DATA_DIR.name}/: {catalog[name]['filename']}")
    return chunks, warnings


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def index_schema(name: str, dimensions: int):
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    def tag(field_name):  # exact-match metadata: filter and count by it
        return SimpleField(name=field_name, type=SearchFieldDataType.String,
                           filterable=True, facetable=True)

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="page", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        tag("symbol"),
        SimpleField(name="year", type=SearchFieldDataType.Int32,
                    filterable=True, sortable=True, facetable=True),
        tag("doc_type"),
        tag("submitted_by"),
        tag("collection"),
        SimpleField(name="url", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="hnsw-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


def with_retry(fn, *args, attempts=5, **kwargs):
    from azure.core.exceptions import HttpResponseError, ServiceRequestError

    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except (ServiceRequestError, HttpResponseError) as exc:
            if attempt == attempts:
                raise
            wait = 2 ** attempt
            print(f"  {exc.__class__.__name__}, retrying in {wait}s...")
            time.sleep(wait)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="collection id from collections.json")
    parser.add_argument("--recreate", action="store_true", help="drop and rebuild the index first")
    parser.add_argument("--dry-run", action="store_true", help="parse and chunk only")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    configure(args.collection)
    print(f"collection '{COLLECTION}': PDFs from {DATA_DIR.relative_to(ROOT)}/, metadata from {SOURCES_FILE.name}")
    chunks, warnings = build_chunks(load_catalog())
    for w in warnings:
        print(f"warning: {w}")
    print(f"{len(chunks)} chunks from {len({c['source'] for c in chunks})} PDFs")
    if not chunks:
        raise SystemExit("No text extracted.")
    if args.dry_run:
        sample = chunks[0]
        print("sample:", {k: v for k, v in sample.items() if k != "content"})
        return

    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import ResourceNotFoundError
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from openai import AzureOpenAI

    aoai = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version="2024-10-21",
        max_retries=6,
    )
    deployment = os.environ["AZURE_OPENAI_EMBED_DEPLOYMENT"]
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    index_name = INDEX_NAME or os.environ["AZURE_SEARCH_INDEX"]
    credential = AzureKeyCredential(os.environ["AZURE_SEARCH_ADMIN_KEY"])

    # Match the index's vector size to whatever the embedding model returns.
    dimensions = len(aoai.embeddings.create(model=deployment, input=["dimension probe"]).data[0].embedding)

    indexes = SearchIndexClient(endpoint, credential)
    if args.recreate:
        try:
            indexes.delete_index(index_name)
            print(f"deleted index '{index_name}'")
        except ResourceNotFoundError:
            pass
    indexes.create_or_update_index(index_schema(index_name, dimensions))
    print(f"index '{index_name}' ready ({dimensions}-dim vectors)")

    search = SearchClient(endpoint, index_name, credential)
    failed: list[str] = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        vectors = aoai.embeddings.create(model=deployment, input=[c["content"] for c in batch]).data
        for c, v in zip(batch, vectors):
            c["embedding"] = v.embedding
        results = with_retry(search.merge_or_upload_documents, batch)
        failed += [r.key for r in results if not r.succeeded]
        print(f"uploaded {i + len(batch)}/{len(chunks)}")

    if failed:
        print(f"FAILED {len(failed)} chunks: {failed[:10]}{' ...' if len(failed) > 10 else ''}")
    print("done. Counts can lag a few seconds; recheck with get_document_count().")


if __name__ == "__main__":
    main()