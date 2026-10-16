"""Core Tether logic, shared by the FastAPI server and the Streamlit app.

- With the FastAPI server (local dev, containers): main.py wraps this in HTTP.
- On Streamlit Community Cloud (no separate server): the Streamlit app calls
  this module directly.
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from backend.agent_client import AgentClient

BACKEND_DIR = Path(__file__).parent

# Per-visitor rate limit for the public demo.
MAX_REQUESTS = 5
WINDOW_SECONDS = 60


class ServiceError(Exception):
    """An error with an HTTP-style status code and a message safe to show users."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Collections registry
# ---------------------------------------------------------------------------
def _load_catalog(sources_file: str | None) -> list[dict]:
    """Every document in a collection's metadata file, ingested or not."""
    if not sources_file:
        return []
    return json.loads((BACKEND_DIR / sources_file).read_text())


def load_collections() -> dict[str, dict]:
    raw = json.loads((BACKEND_DIR / "collections.json").read_text())
    collections = {}
    for c in raw:
        c["_catalog"] = _load_catalog(c.get("sources_file"))
        # Citation lookup: readable titles and official links, keyed by filename.
        c["_sources"] = {
            e["filename"].lower(): e for e in c["_catalog"] if e.get("filename")
        }
        collections[c["id"]] = c
    return collections


COLLECTIONS = load_collections()
DEFAULT_COLLECTION = next(cid for cid, c in COLLECTIONS.items() if c["status"] == "live")

PUBLIC_FIELDS = ("id", "name", "description", "status", "retrieval", "examples")


def public_collections() -> list[dict]:
    """Collection info that's safe to send to the browser."""
    return [{k: c[k] for k in PUBLIC_FIELDS} for c in COLLECTIONS.values()]


CATALOG_FIELDS = ("symbol", "year", "type", "submitted_by", "title", "url")


def catalog(collection_id: str) -> dict:
    """The documents a collection answers from."""
    collection = COLLECTIONS.get(collection_id)
    if collection is None:
        raise ServiceError(404, f"Unknown collection '{collection_id}'.")
    docs = [{k: d.get(k) for k in CATALOG_FIELDS} for d in collection["_catalog"]]
    docs.sort(key=lambda d: (-(d["year"] or 0), d["title"]))  # newest first
    return {
        "collection": collection["id"],
        "name": collection["name"],
        "total": len(docs),
        "documents": docs,
    }


def get_live_collection(collection_id: str) -> dict:
    collection = COLLECTIONS.get(collection_id)
    if collection is None:
        raise ServiceError(404, f"Unknown collection '{collection_id}'.")
    if collection["status"] != "live":
        raise ServiceError(400, f"'{collection['name']}' isn't available yet.")
    return collection


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def check_rate_limit(visitor: str) -> None:
    now = time.monotonic()
    with _hits_lock:
        hits = _hits[visitor]
        while hits and now - hits[0] > WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= MAX_REQUESTS:
            raise ServiceError(
                429, f"Demo limit is {MAX_REQUESTS} questions per minute. Try again shortly."
            )
        hits.append(now)


# ---------------------------------------------------------------------------
# Retrieval backends
# ---------------------------------------------------------------------------
class AgentPool:
    """One client per retrieval target (Foundry agent or search index), created on first use."""

    def __init__(self, endpoint: str | None = None):
        self._endpoint = endpoint or os.environ.get("PROJECT_ENDPOINT")
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    def get(self, agent_name: str) -> AgentClient:
        with self._lock:
            key = f"agent:{agent_name}"
            if key not in self._clients:
                if not self._endpoint:
                    raise ServiceError(500, "PROJECT_ENDPOINT isn't set.")
                self._clients[key] = AgentClient(self._endpoint, agent_name)
            return self._clients[key]

    def get_retriever(self, index_name: str):
        with self._lock:
            key = f"search:{index_name}"
            if key not in self._clients:
                from backend.search_backend import SearchRetriever

                self._clients[key] = SearchRetriever(index_name)
            return self._clients[key]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()


def _odata(value: str) -> str:
    return value.replace("'", "''")


def build_filter(filters: dict | None) -> str | None:
    """Turn {year_from, year_to, doc_type, submitted_by} into an OData filter."""
    if not filters:
        return None
    parts = []
    if filters.get("year_from"):
        parts.append(f"year ge {int(filters['year_from'])}")
    if filters.get("year_to"):
        parts.append(f"year le {int(filters['year_to'])}")
    for field in ("doc_type", "submitted_by"):
        if filters.get(field):
            parts.append(f"{field} eq '{_odata(str(filters[field]))}'")
    return " and ".join(parts) or None


def _enrich(citation, sources: dict[str, dict]) -> dict:
    meta = sources.get((citation.filename or "").lower(), {})
    url = meta.get("url")
    # Deep-link straight to the cited page when the source is a PDF.
    if url and citation.page and url.lower().endswith(".pdf"):
        url = f"{url}#page={citation.page}"
    return {
        **vars(citation),
        "title": meta.get("title"),
        "symbol": meta.get("symbol"),
        "year": meta.get("year"),
        "url": url,
    }


def chat(
    pool: AgentPool,
    message: str,
    collection_id: str = DEFAULT_COLLECTION,
    conversation_id: str | None = None,
    visitor: str = "unknown",
    filters: dict | None = None,
) -> dict:
    """Answer one question. Returns {answer, collection, conversation_id, citations}."""
    message = (message or "").strip()
    if not message or len(message) > 2000:
        raise ServiceError(400, "Questions must be between 1 and 2,000 characters.")

    collection = get_live_collection(collection_id)
    check_rate_limit(visitor)

    try:
        backend = collection["backend"]
        if backend == "azure_ai_search":
            index = collection.get("search_index") or os.environ.get("AZURE_SEARCH_INDEX")
            if not index:
                raise ServiceError(500, "No search index configured for this collection.")
            reply = pool.get_retriever(index).ask(message, conversation_id, build_filter(filters))
        elif backend == "foundry_agent":
            reply = pool.get(collection["agent_name"]).ask(message, conversation_id)
        else:
            raise ServiceError(501, f"Backend '{backend}' isn't built yet.")
    except ServiceError:
        raise
    except Exception as exc:
        text = str(exc).lower()
        if "rate limit" in text or "429" in text:
            raise ServiceError(429, "The model is busy. Wait a minute and retry.")
        if "invalid conversation id" in text:
            raise ServiceError(
                400, "Unknown conversation_id. Omit it (or send null) to start a new conversation."
            )
        raise ServiceError(502, f"Retrieval or model call failed: {exc}")

    return {
        "answer": reply.answer,
        "collection": collection["id"],
        "conversation_id": reply.conversation_id,
        "citations": [_enrich(c, collection["_sources"]) for c in reply.citations],
    }
