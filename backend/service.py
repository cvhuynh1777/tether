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
def _load_sources(sources_file: str | None) -> dict[str, dict]:
    """Readable titles and official links for a collection's PDFs, keyed by filename."""
    if not sources_file:
        return {}
    entries = json.loads((BACKEND_DIR / sources_file).read_text())
    return {e["filename"].lower(): e for e in entries if e.get("filename")}


def load_collections() -> dict[str, dict]:
    raw = json.loads((BACKEND_DIR / "collections.json").read_text())
    collections = {}
    for c in raw:
        c["_sources"] = _load_sources(c.get("sources_file"))
        collections[c["id"]] = c
    return collections


COLLECTIONS = load_collections()
DEFAULT_COLLECTION = next(cid for cid, c in COLLECTIONS.items() if c["status"] == "live")

PUBLIC_FIELDS = ("id", "name", "description", "status", "retrieval", "examples")


def public_collections() -> list[dict]:
    """Collection info that's safe to send to the browser."""
    return [{k: c[k] for k in PUBLIC_FIELDS} for c in COLLECTIONS.values()]


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
    """One authenticated client per Foundry agent, created on first use."""

    def __init__(self, endpoint: str | None = None):
        self._endpoint = endpoint or os.environ["PROJECT_ENDPOINT"]
        self._clients: dict[str, AgentClient] = {}
        self._lock = threading.Lock()

    def get(self, agent_name: str) -> AgentClient:
        with self._lock:
            if agent_name not in self._clients:
                self._clients[agent_name] = AgentClient(self._endpoint, agent_name)
            return self._clients[agent_name]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()


def _enrich(citation, sources: dict[str, dict]) -> dict:
    meta = sources.get((citation.filename or "").lower(), {})
    return {
        **vars(citation),
        "title": meta.get("title"),
        "symbol": meta.get("symbol"),
        "year": meta.get("year"),
        "url": meta.get("url"),
    }


def chat(
    pool: AgentPool,
    message: str,
    collection_id: str = DEFAULT_COLLECTION,
    conversation_id: str | None = None,
    visitor: str = "unknown",
) -> dict:
    """Answer one question. Returns {answer, collection, conversation_id, citations}."""
    message = (message or "").strip()
    if not message or len(message) > 2000:
        raise ServiceError(400, "Questions must be between 1 and 2,000 characters.")

    collection = get_live_collection(collection_id)
    check_rate_limit(visitor)

    try:
        if collection["backend"] == "foundry_agent":
            reply = pool.get(collection["agent_name"]).ask(message, conversation_id)
        # Future: elif collection["backend"] == "azure_ai_search": ...
        else:
            raise ServiceError(501, f"Backend '{collection['backend']}' isn't built yet.")
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
        raise ServiceError(502, f"Agent call failed: {exc}")

    return {
        "answer": reply.answer,
        "collection": collection["id"],
        "conversation_id": reply.conversation_id,
        "citations": [_enrich(c, collection["_sources"]) for c in reply.citations],
    }
