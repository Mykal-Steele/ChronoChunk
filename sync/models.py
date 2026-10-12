"""
The model calls the sync makes: embeddings, page descriptions and translations. Every
call is priced and added up, and the run stops before it passes its spending limit.
Results go into the cache, so the same text is never paid for twice.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Sequence

from src.course_store import DIMENSIONS
from sync.cache import Cache, sha
from sync.settings import CHAT_MODEL, EMBEDDING_MODEL, PRICES, SPEND_CAP

# Texts per embedding request, and the most characters one request may carry. The
# deployment allows 150,000 tokens a minute, and one request has to fit well inside that.
EMBED_BATCH = 100
EMBED_BATCH_CHARS = 120_000
# Room for the descriptions of one request's pages
DESCRIBE_MAX_TOKENS = 4000
TRANSLATE_MAX_TOKENS = 3000
CALLS_AT_ONCE = 5

TRANSLATE_PROMPT = (
    "Translate this course material into English. Keep technical terms, names, numbers and code as they are. "
    "Give only the translation.\n\n{text}"
)


class SpendCapReached(RuntimeError):
    """The run has spent what it may. What it finished is cached, so the next run continues from there."""


def make_client() -> Any:
    """The bot's Azure resource, from the same two settings the bot reads."""
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(".env")
    return OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.environ["AZURE_OPENAI_KEY"],
                  max_retries=8, timeout=180)


class Models:
    def __init__(self, cache: Cache, client: Any = None, cap: float = SPEND_CAP):
        self.cache = cache
        self.client = client if client is not None else make_client()
        self.cap = cap
        self.spent = 0.0
        self.calls = 0
        self._lock = threading.Lock()
        self._embedding_key = f"{EMBEDDING_MODEL}:{DIMENSIONS}"

    def _check(self) -> None:
        with self._lock:
            if self.spent >= self.cap:
                raise SpendCapReached(f"spent ${self.spent:.2f} of the ${self.cap:.2f} this run may spend")

    def _charge(self, model: str, usage: Any) -> None:
        input_price, output_price = PRICES.get(model, PRICES[CHAT_MODEL])
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        with self._lock:
            self.spent += (tokens_in * input_price + tokens_out * output_price) / 1e6
            self.calls += 1

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """One embedding per text, in order. Only texts the cache does not hold are sent."""
        known = self.cache.vectors(self._embedding_key, texts)
        missing = list(dict.fromkeys(text for text in texts if text not in known))
        batch: List[str] = []
        size = 0
        batches: List[List[str]] = []
        for text in missing:
            if batch and (len(batch) >= EMBED_BATCH or size + len(text) > EMBED_BATCH_CHARS):
                batches.append(batch)
                batch, size = [], 0
            batch.append(text)
            size += len(text)
        if batch:
            batches.append(batch)
        for batch in batches:
            self._check()
            reply = self.client.embeddings.create(model=EMBEDDING_MODEL, input=batch, dimensions=DIMENSIONS)
            vectors = [item.embedding for item in sorted(reply.data, key=lambda item: item.index)]
            self._charge(EMBEDDING_MODEL, reply.usage)
            self.cache.put_vectors(self._embedding_key, batch, vectors)
            known.update(zip(batch, vectors, strict=True))
        return [known[text] for text in texts]

    def _chat(self, content: Any, max_tokens: int) -> str:
        self._check()
        reply = self.client.chat.completions.create(
            model=CHAT_MODEL, reasoning_effort="none", max_completion_tokens=max_tokens,
            messages=[{"role": "user", "content": content}])
        self._charge(CHAT_MODEL, reply.usage)
        return (reply.choices[0].message.content or "").strip()

    def describe(self, prompt: str, images: Sequence[str]) -> str:
        """What a set of page pictures shows. images are data URLs."""
        content = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": image}} for image in images]
        return self._chat(content, DESCRIBE_MAX_TOKENS)

    def translate(self, texts: Sequence[str]) -> List[str]:
        """Each text in English, in order. A text that cannot be translated comes back as it was."""
        def one(text: str) -> str:
            cached: Optional[str] = self.cache.get("translation", sha(text))
            if cached is not None:
                return cached
            translated = self._chat(TRANSLATE_PROMPT.format(text=text), TRANSLATE_MAX_TOKENS)
            if not translated:
                return text
            self.cache.put("translation", sha(text), translated)
            return translated

        with ThreadPoolExecutor(CALLS_AT_ONCE) as pool:
            return list(pool.map(one, texts))
