"""Thin wrapper around the Foundry agent (lts-agent).

Keeps one authenticated client for the life of the app and turns the raw
Responses API output into a clean {answer, citations} shape for the API layer.
"""

import os
from dataclasses import dataclass, field

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


@dataclass
class Citation:
    filename: str | None
    file_id: str | None
    quote: str | None = None


@dataclass
class AgentReply:
    answer: str
    conversation_id: str
    citations: list[Citation] = field(default_factory=list)


class AgentClient:
    def __init__(self, endpoint: str, agent_name: str):
        # DefaultAzureCredential uses `az login` locally and the managed
        # identity once deployed, so no keys live in code or config.
        self._credential = DefaultAzureCredential()
        self._project = AIProjectClient(
            endpoint=endpoint,
            credential=self._credential,
            allow_preview=True,
        )
        self._openai = self._project.get_openai_client(agent_name=agent_name)

    @classmethod
    def from_env(cls) -> "AgentClient":
        endpoint = os.environ["PROJECT_ENDPOINT"]
        agent_name = os.environ["AGENT_NAME"]
        return cls(endpoint, agent_name)

    def ask(self, message: str, conversation_id: str | None = None) -> AgentReply:
        # A conversation holds the chat history server-side, so follow-up
        # questions ("what about guideline B.2?") keep their context.
        if conversation_id is None:
            conversation_id = self._openai.conversations.create().id

        response = self._openai.responses.create(
            conversation=conversation_id,
            input=[{"role": "user", "content": message}],
        )

        return AgentReply(
            answer=response.output_text,
            conversation_id=conversation_id,
            citations=self._extract_citations(response),
        )

    @staticmethod
    def _extract_citations(response) -> list[Citation]:
        """Pull file-search citations out of the response annotations.

        Read defensively with getattr: annotation fields vary slightly
        across SDK versions.
        """
        seen: set[str] = set()
        citations: list[Citation] = []

        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", None) or []:
                for ann in getattr(part, "annotations", None) or []:
                    if getattr(ann, "type", None) != "file_citation":
                        continue
                    file_id = getattr(ann, "file_id", None)
                    filename = getattr(ann, "filename", None)
                    key = file_id or filename
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    citations.append(
                        Citation(
                            filename=filename,
                            file_id=file_id,
                            quote=getattr(ann, "quote", None),
                        )
                    )
        return citations

    def close(self) -> None:
        self._project.close()
        self._credential.close()