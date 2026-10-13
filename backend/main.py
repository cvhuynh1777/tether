"""Tether API: a small FastAPI service in front of one or more Foundry agents.

Each document collection (see collections.json) has its own retrieval backend.
Today every live collection is a Foundry agent with file search; the
`backend` field is where a future Azure AI Search retriever plugs in.

Run locally (from the tether/ folder):
    python -m uvicorn backend.main:app --reload
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.agent_client import AgentClient

load_dotenv()

BACKEND_DIR = Path(__file__).parent

# Simple per-visitor rate limit for the public demo.
MAX_REQUESTS = 5
WINDOW_SECONDS = 60
_hits: dict[str, deque] = defaultdict(deque)


# ---------------------------------------------------------------------------
# Collections registry
# ---------------------------------------------------------------------------
def _load_sources(sources_file: str | None) -> dict[str, dict]:
    """Readable titles and official links for a collection's PDFs, keyed by filename."""
    if not sources_file:
        return {}
    entries = json.loads((BACKEND_DIR / sources_file).read_text())
    return {e["filename"].lower(): e for e in entries if e.get("filename")}


def _load_collections() -> dict[str, dict]:
    raw = json.loads((BACKEND_DIR / "collections.json").read_text())
    collections = {}
    for c in raw:
        c["_sources"] = _load_sources(c.get("sources_file"))
        collections[c["id"]] = c
    return collections


COLLECTIONS = _load_collections()
DEFAULT_COLLECTION = next(cid for cid, c in COLLECTIONS.items() if c["status"] == "live")


# ---------------------------------------------------------------------------
# Retrieval backends
# ---------------------------------------------------------------------------
class AgentPool:
    """One authenticated client per Foundry agent, created on first use."""

    def __init__(self, endpoint: str):
        self._endpoint = endpoint
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


def answer(pool: AgentPool, collection: dict, message: str, conversation_id: str | None):
    """Route a question to the collection's retrieval backend."""
    backend = collection["backend"]
    if backend == "foundry_agent":
        return pool.get(collection["agent_name"]).ask(message, conversation_id)
    # Future: elif backend == "azure_ai_search": return search_retriever.ask(...)
    raise NotImplementedError(f"Backend '{backend}' isn't built yet.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agents = AgentPool(os.environ["PROJECT_ENDPOINT"])
    yield
    app.state.agents.close()


app = FastAPI(title="Tether API", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    collection: str = DEFAULT_COLLECTION
    conversation_id: str | None = None

    # Pre-fills the /docs page with a valid request.
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "What does guideline B.1 say?", "collection": "lts", "conversation_id": None}
            ]
        }
    }


class CitationOut(BaseModel):
    filename: str | None
    file_id: str | None
    quote: str | None = None
    title: str | None = None
    symbol: str | None = None
    year: int | None = None
    url: str | None = None


class ChatResponse(BaseModel):
    answer: str
    collection: str
    conversation_id: str
    citations: list[CitationOut]


class CollectionOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    retrieval: str
    examples: list[list[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    hits = _hits[client_ip]
    while hits and now - hits[0] > WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Demo limit is {MAX_REQUESTS} questions per minute. Try again shortly.",
        )
    hits.append(now)


def _enrich(citation, sources: dict[str, dict]) -> CitationOut:
    meta = sources.get((citation.filename or "").lower(), {})
    return CitationOut(
        **vars(citation),
        title=meta.get("title"),
        symbol=meta.get("symbol"),
        year=meta.get("year"),
        url=meta.get("url"),
    )


def _get_live_collection(collection_id: str) -> dict:
    collection = COLLECTIONS.get(collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=f"Unknown collection '{collection_id}'.")
    if collection["status"] != "live":
        raise HTTPException(status_code=400, detail=f"'{collection['name']}' isn't available yet.")
    return collection


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/collections", response_model=list[CollectionOut])
def list_collections():
    return [
        CollectionOut(**{k: c[k] for k in CollectionOut.model_fields})
        for c in COLLECTIONS.values()
    ]


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    collection = _get_live_collection(req.collection)
    _check_rate_limit(request.client.host if request.client else "unknown")

    try:
        # The Azure SDK call is blocking, so run it off the event loop.
        reply = await run_in_threadpool(
            answer, request.app.state.agents, collection, req.message, req.conversation_id
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        text = str(exc).lower()
        if "rate limit" in text or "429" in text:
            raise HTTPException(status_code=429, detail="The model is busy. Wait a minute and retry.")
        if "invalid conversation id" in text:
            raise HTTPException(
                status_code=400,
                detail="Unknown conversation_id. Omit it (or send null) to start a new conversation.",
            )
        raise HTTPException(status_code=502, detail=f"Agent call failed: {exc}")

    return ChatResponse(
        answer=reply.answer,
        collection=collection["id"],
        conversation_id=reply.conversation_id,
        citations=[_enrich(c, collection["_sources"]) for c in reply.citations],
    )