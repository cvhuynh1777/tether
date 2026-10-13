"""Tether API: a thin FastAPI layer over backend/service.py.

Run locally (from the tether/ folder):
    python -m uvicorn backend.main:app --reload --reload-include "*.json"
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

load_dotenv()

from backend import service  # noqa: E402  (needs the .env values loaded first)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agents = service.AgentPool()
    yield
    app.state.agents.close()


app = FastAPI(title="Tether API", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    collection: str = service.DEFAULT_COLLECTION
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/collections", response_model=list[CollectionOut])
def list_collections():
    return service.public_collections()


class DocumentOut(BaseModel):
    symbol: str
    year: int | None
    type: str | None
    submitted_by: str | None
    title: str
    url: str | None


class CatalogOut(BaseModel):
    collection: str
    name: str
    total: int
    documents: list[DocumentOut]


@app.get("/collections/{collection_id}/documents", response_model=CatalogOut)
def list_documents(collection_id: str):
    try:
        return service.catalog(collection_id)
    except service.ServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    # The Streamlit frontend sends one ID per browser session; direct API
    # callers fall back to their IP address.
    visitor = request.headers.get("x-session-id") or (
        request.client.host if request.client else "unknown"
    )
    try:
        # The Azure SDK call is blocking, so run it off the event loop.
        return await run_in_threadpool(
            service.chat,
            request.app.state.agents,
            req.message,
            req.collection,
            req.conversation_id,
            visitor,
        )
    except service.ServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)