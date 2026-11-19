"""
Turns a text into an embedding, for recall by meaning (memory plan, layer 2).

Vertex's OpenAI-compatible endpoint answers 500 to embeddings, so on Vertex this calls
Google's own embedContent API, signed in as the VM like the chat calls. Only the global
region has gemini-embedding-2 (probe of 2026-11-19 in run.md). Other providers embed
through the OpenAI embeddings API.

AI_MEMORY_EMBEDDING_MODEL picks the model and AI_MEMORY_EMBEDDING_LOCATION the Vertex
region. Changing the model is safe: memories embedded by another model are embedded
again when the bot starts, and until then they are found by their words.
"""
import asyncio
import os
from types import SimpleNamespace
from typing import Callable, List, Mapping, Optional, Tuple

from src.ai_provider import CLOUD_PLATFORM_SCOPE, AiSettings
from src.usage_guard import UsageGuard, get_usage_guard

# How many numbers each embedding has. Both Gemini models and text-embedding-3 can shorten theirs.
DIMENSIONS = 768
VERTEX_TASKS = {"query": "RETRIEVAL_QUERY", "document": "RETRIEVAL_DOCUMENT"}
_TIMEOUT_SECONDS = 20


def _vertex_host(location: str) -> str:
    return "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"


class VertexEmbedder:
    def __init__(self, project: str, location: str, model: str, post: Optional[Callable] = None):
        self.project = project
        self.location = location
        self.model = model
        # A function like requests' post. Made on first use, so nothing signs in until it is needed.
        self._post = post

    def _session_post(self) -> Callable:
        if self._post is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
            self._post = AuthorizedSession(credentials).post
        return self._post

    @property
    def url(self) -> str:
        return (f"https://{_vertex_host(self.location)}/v1/projects/{self.project}/locations/{self.location}"
                f"/publishers/google/models/{self.model}:embedContent")

    async def embed(self, text: str, task: str = "query") -> Tuple[List[float], float]:
        """The embedding of text and what it cost. task is "query" for a question, "document" for a memory."""
        guard = get_usage_guard()
        guard.check()
        body = {"content": {"parts": [{"text": text}]}, "taskType": VERTEX_TASKS.get(task, "RETRIEVAL_QUERY"),
                "outputDimensionality": DIMENSIONS}
        post = self._session_post()
        response = await asyncio.to_thread(post, self.url, json=body, timeout=_TIMEOUT_SECONDS)
        if not response.ok:
            raise RuntimeError(f"Vertex embedding failed with {response.status_code}: {str(response.text)[:200]}")
        data = response.json()
        tokens = int((data.get("usageMetadata") or {}).get("promptTokenCount") or 0)
        cost = guard.record(SimpleNamespace(prompt_tokens=tokens, completion_tokens=0), self.model)
        return list(data["embedding"]["values"]), cost


class OpenAIEmbedder:
    def __init__(self, client, model: str):
        # A GuardedClient, so the budget is checked and the cost recorded like any other call
        self.client = client
        self.model = model

    async def embed(self, text: str, task: str = "query") -> Tuple[List[float], float]:
        response = await self.client.embeddings.create(model=self.model, input=[text], dimensions=DIMENSIONS,
                                                       encoding_format="float")
        return list(response.data[0].embedding), UsageGuard.cost_of(getattr(response, "usage", None), self.model)


def make_embedder(settings: AiSettings, client, env: Optional[Mapping[str, str]] = None):
    """The embedder for the provider in settings. client is the bot's guarded client, used outside Vertex."""
    env = os.environ if env is None else env
    model = (env.get("AI_MEMORY_EMBEDDING_MODEL") or "").strip()
    if settings.provider == "vertex":
        location = (env.get("AI_MEMORY_EMBEDDING_LOCATION") or "").strip() or "global"
        return VertexEmbedder(project=(env.get("VERTEX_PROJECT") or "").strip(), location=location,
                              model=model or "gemini-embedding-2")
    return OpenAIEmbedder(client, model or "text-embedding-3-small")
