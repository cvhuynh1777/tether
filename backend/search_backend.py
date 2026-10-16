"""Retrieval backend built on Tether's own Azure AI Search index.

Flow for each question:
  1. embed the question (Azure OpenAI)
  2. hybrid search: keyword (BM25) + vector, fused by Azure AI Search
  3. give the top chunks to gpt-5-mini as numbered sources
  4. keep only the sources the answer actually cites ([n] markers)

Unlike the Foundry agent, citations here carry page numbers, and every chunk
already holds its catalog metadata (symbol, year, type, country).

Auth: API keys if set (AZURE_SEARCH_QUERY_KEY, AZURE_OPENAI_API_KEY), otherwise
DefaultAzureCredential, i.e. `az login` locally and the managed identity on Azure.
"""

import os
import re
import threading
import uuid
from collections import OrderedDict

from backend.agent_client import AgentReply, Citation

TOP_K = 6
HISTORY_TURNS = 3          # previous Q/A pairs kept for follow-up questions
MAX_CONVERSATIONS = 500    # in-memory; oldest are dropped first

SYSTEM_PROMPT = """You are Tether, a research assistant for official UN space sustainability documents.

Rules:
- Answer only from the numbered sources provided with the question. Never use outside knowledge.
- After every claim, cite its source with its number in square brackets, e.g. [2]. Cite several if needed: [1][4].
- If the sources don't answer the question, say "The documents I have don't cover that." and stop.
- If sources disagree, or a later document updates an earlier one, say so and give the years.
- The LTS guidelines are voluntary and non-binding; never describe them as legal requirements.
- Be concise and plain. No preamble."""

SELECT_FIELDS = ["id", "content", "source", "page", "title", "symbol", "year", "doc_type", "submitted_by", "url"]
CITE_PATTERN = re.compile(r"\[(\d{1,2})\]")


class SearchRetriever:
    def __init__(self, index_name: str, search_client=None, openai_client=None):
        self.index_name = index_name
        self.search = search_client or self._make_search_client(index_name)
        self.openai = openai_client or self._make_openai_client()
        # Chat can live on a different Azure OpenAI resource than embeddings.
        if openai_client is None and os.environ.get("AZURE_OPENAI_CHAT_ENDPOINT"):
            self.chat_client = self._make_openai_client(
                os.environ["AZURE_OPENAI_CHAT_ENDPOINT"], os.environ.get("AZURE_OPENAI_CHAT_API_KEY")
            )
        else:
            self.chat_client = self.openai
        self.embed_deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small")
        self.chat_deployment = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5-mini")
        self._history: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
        self._lock = threading.Lock()

    # -- clients ----------------------------------------------------------
    @staticmethod
    def _make_search_client(index_name: str):
        from azure.search.documents import SearchClient

        endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        key = os.environ.get("AZURE_SEARCH_QUERY_KEY")
        if key:
            from azure.core.credentials import AzureKeyCredential

            return SearchClient(endpoint, index_name, AzureKeyCredential(key))
        from azure.identity import DefaultAzureCredential

        return SearchClient(endpoint, index_name, DefaultAzureCredential())

    @staticmethod
    def _make_openai_client(endpoint: str | None = None, key: str | None = None):
        from openai import AzureOpenAI

        if endpoint is None:
            endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
            key = os.environ.get("AZURE_OPENAI_API_KEY")
        common = dict(azure_endpoint=endpoint, api_version="2024-10-21", max_retries=4)
        if key:
            return AzureOpenAI(api_key=key, **common)
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        return AzureOpenAI(azure_ad_token_provider=token, **common)

    # -- retrieval --------------------------------------------------------
    def retrieve(self, query: str, filter_expr: str | None = None, top: int = TOP_K) -> list[dict]:
        from azure.search.documents.models import VectorizedQuery

        vector = self.openai.embeddings.create(model=self.embed_deployment, input=[query]).data[0].embedding
        results = self.search.search(
            search_text=query,                       # keyword (BM25) half of hybrid search
            vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=top * 3, fields="embedding")],
            filter=filter_expr,
            select=SELECT_FIELDS,
            top=top,
        )
        return [dict(r) for r in results]

    # -- answering --------------------------------------------------------
    @staticmethod
    def _format_sources(chunks: list[dict]) -> str:
        blocks = []
        for i, c in enumerate(chunks, start=1):
            label = ", ".join(str(x) for x in (c.get("symbol"), c.get("year"), f"p. {c.get('page')}") if x)
            blocks.append(f"[{i}] {c.get('title')} ({label})\n{c.get('content', '').strip()}")
        return "\n\n".join(blocks)

    def ask(self, message: str, conversation_id: str | None = None, filter_expr: str | None = None) -> AgentReply:
        with self._lock:
            if conversation_id is None or conversation_id not in self._history:
                if conversation_id is not None:
                    raise ValueError("Invalid conversation id")
                conversation_id = f"conv_{uuid.uuid4().hex}"
                self._history[conversation_id] = []
            history = list(self._history[conversation_id])

        # Fold the previous question into retrieval so follow-ups like
        # "what about Canada?" still find the right passages.
        search_query = f"{history[-1][0]} {message}" if history else message
        chunks = self.retrieve(search_query, filter_expr)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for q, a in history[-HISTORY_TURNS:]:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        if chunks:
            user_content = f"Sources:\n\n{self._format_sources(chunks)}\n\nQuestion: {message}"
        else:
            user_content = f"Sources: (none found)\n\nQuestion: {message}"
        messages.append({"role": "user", "content": user_content})

        completion = self.chat_client.chat.completions.create(
            model=self.chat_deployment,
            messages=messages,
            reasoning_effort="low",
        )
        answer = (completion.choices[0].message.content or "").strip()

        with self._lock:
            turns = self._history.setdefault(conversation_id, [])
            turns.append((message, answer))
            self._history.move_to_end(conversation_id)
            while len(self._history) > MAX_CONVERSATIONS:
                self._history.popitem(last=False)

        return AgentReply(answer=answer, conversation_id=conversation_id, citations=self._cited(answer, chunks))

    @staticmethod
    def _cited(answer: str, chunks: list[dict]) -> list[Citation]:
        """Only the sources the answer actually references, in order of first use."""
        citations, seen = [], set()
        for match in CITE_PATTERN.finditer(answer):
            n = int(match.group(1))
            if not 1 <= n <= len(chunks) or n in seen:
                continue
            seen.add(n)
            c = chunks[n - 1]
            citations.append(Citation(
                filename=c.get("source"),
                file_id=c.get("id"),
                quote=(c.get("content") or "")[:280].strip() or None,
                page=c.get("page"),
                ref=n,
            ))
        return citations

    def close(self) -> None:
        for client in {id(c): c for c in (self.search, self.openai, self.chat_client)}.values():
            close = getattr(client, "close", None)
            if close:
                close()
