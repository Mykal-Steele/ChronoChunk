"""
A client for the Unicorp Gemini proxy (github.com/Mykal-Steele/unicorp-gemini), so the
model calls of a Uniloom reply are paid from that capped allowance instead of the bot's
own credits.

The proxy speaks Gemini's own API (generateContent) and the rest of the bot speaks the
OpenAI chat format, so this translates both ways. It only covers what the bot sends:
text, data-URL images, function calling, the token limit and the reasoning effort.
"""
import json
from itertools import count
from types import SimpleNamespace
from typing import List, Optional, Tuple

import aiohttp

from src.ai_provider import GEMINI_SAFETY_CATEGORIES, VERTEX_REASONING
from src.usage_guard import GuardedClient, guard_client

# Gemini 3 answers 400 to a function call sent back without its thought signature. Calls
# made earlier in the reply by another provider have none, so they get the value Google
# documents for history that did not come from Gemini.
SKIP_SIGNATURE = "skip_thought_signature_validator"

# The proxy's own rules saying no: 401 bad key, 402 the allowance is used up, 403 the key is
# paused, 404 a model it does not list, 429 its daily cap or too many requests at once
REFUSED = (401, 402, 403, 404, 429)

_ids = count(1)


class ProxyError(Exception):
    """The proxy answered with an error. status_code is there so the usage guard's 429 retry works."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Gemini proxy answered {status_code}: {message}")
        self.status_code = status_code

    @property
    def refused(self) -> bool:
        """True when the allowance cannot be used at all right now, rather than a failed call."""
        return self.status_code in REFUSED


def _text_parts(content) -> List[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    parts = []
    for piece in content:
        if piece.get("type") == "text":
            parts.append({"text": piece["text"]})
        elif piece.get("type") == "image_url":
            url = piece["image_url"]["url"]
            head, _, data = url.partition(",")
            mime = head.removeprefix("data:").split(";")[0]
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return parts


def _call_part(call: dict) -> dict:
    # Broken arguments already got an error result from the bot, which follows this call, so
    # the model can see what went wrong and try again
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    part = {"functionCall": {"name": call["function"]["name"], "args": args}}
    google = (call.get("extra_content") or {}).get("google") or {}
    signature = google.get("thought_signature")
    if signature:
        part["thoughtSignature"] = signature
    return part


def _add(contents: List[dict], role: str, parts: List[dict]) -> None:
    """Gemini wants the turns to alternate, so parts for the same role in a row join one turn."""
    if not parts:
        return
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(parts)
    else:
        contents.append({"role": role, "parts": parts})


def to_gemini(kwargs: dict) -> Tuple[str, dict]:
    """The model name and generateContent body for one OpenAI-style chat completion."""
    model = (kwargs.get("model") or "").removeprefix("google/")
    system: List[dict] = []
    contents: List[dict] = []
    names = {}
    for message in kwargs.get("messages") or []:
        role = message.get("role")
        if role in ("system", "developer"):
            system.extend(_text_parts(message.get("content")))
        elif role == "user":
            _add(contents, "user", _text_parts(message.get("content")))
        elif role == "assistant":
            parts = _text_parts(message.get("content"))
            calls = [_call_part(call) for call in message.get("tool_calls") or []]
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
            if calls and not any("thoughtSignature" in part for part in calls):
                calls[0]["thoughtSignature"] = SKIP_SIGNATURE
            _add(contents, "model", parts + calls)
        elif role == "tool":
            name = names.get(message.get("tool_call_id"), "tool")
            _add(contents, "user", [{"functionResponse": {
                "name": name, "response": {"result": message.get("content") or ""}}}])

    body: dict = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": system}
    tools = kwargs.get("tools")
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {key: tool["function"][key] for key in ("name", "description", "parameters") if key in tool["function"]}
            for tool in tools]}]
    choice = kwargs.get("tool_choice")
    if isinstance(choice, dict) and choice.get("function"):
        body["toolConfig"] = {"functionCallingConfig": {
            "mode": "ANY", "allowedFunctionNames": [choice["function"]["name"]]}}

    config: dict = {}
    limit = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
    if limit:
        config["maxOutputTokens"] = limit
    effort = kwargs.get("reasoning_effort")
    if effort:
        config["thinkingConfig"] = {"thinkingLevel": VERTEX_REASONING.get(effort, effort)}
    if config:
        body["generationConfig"] = config
    body["safetySettings"] = [{"category": c, "threshold": "OFF"} for c in GEMINI_SAFETY_CATEGORIES]
    return model, body


def from_gemini(data: dict, model: str) -> SimpleNamespace:
    """A generateContent answer shaped like the OpenAI client's ChatCompletion."""
    candidates = data.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if "text" in part and not part.get("thought"))
    calls = []
    for part in parts:
        call = part.get("functionCall")
        if not call:
            continue
        signature = part.get("thoughtSignature")
        calls.append(SimpleNamespace(
            id=f"proxy_call_{next(_ids)}", type="function",
            function=SimpleNamespace(name=call["name"], arguments=json.dumps(call.get("args") or {})),
            extra_content={"google": {"thought_signature": signature}} if signature else None))

    usage = data.get("usageMetadata") or {}
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=calls or None),
            finish_reason=candidates[0].get("finishReason"))],
        usage=SimpleNamespace(
            prompt_tokens=usage.get("promptTokenCount", 0),
            # Thinking is billed as output
            completion_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=usage.get("cachedContentTokenCount", 0))))


class _Completions:
    def __init__(self, base_url: str, api_key: str, timeout: Optional[float]):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout) if timeout else None

    async def create(self, **kwargs) -> SimpleNamespace:
        model, body = to_gemini(kwargs)
        url = f"{self._base_url}/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(url, json=body, headers=headers) as response:
                text = await response.text()
                if response.status != 200:
                    try:
                        message = json.loads(text)["error"]["message"]
                    except (ValueError, KeyError, TypeError):
                        message = text[:300]
                    raise ProxyError(response.status, message)
        return from_gemini(json.loads(text), model)


class _NoEmbeddings:
    async def create(self, **kwargs):
        raise NotImplementedError("The Gemini proxy is only used for chat")


class GeminiProxyClient:
    """Stands in for AsyncOpenAI, for chat completions only."""

    def __init__(self, base_url: str, api_key: str, timeout: Optional[float] = None):
        self.chat = SimpleNamespace(completions=_Completions(base_url, api_key, timeout))
        self.embeddings = _NoEmbeddings()


def make_proxy_client(base_url: str, api_key: str, timeout: Optional[float] = None) -> GuardedClient:
    """
    The proxy client behind the same budget guard as every other call, so per-person limits
    and the reply's cost still count. The proxy keeps its own total on top of that.
    """
    return guard_client(GeminiProxyClient(base_url, api_key, timeout), rate_limit_retries=2)


# /usage has to answer Discord within 3 seconds
ALLOWANCE_TIMEOUT_SECONDS = 2.5


async def fetch_allowance(base_url: str, api_key: str) -> dict:
    """
    What the proxy says is used and left of the allowance: limitUsd, spentUsd, reservedUsd,
    remainingUsd and paused. Empty when it could not be asked.
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ALLOWANCE_TIMEOUT_SECONDS)) as session:
            async with session.get(f"{base_url.rstrip('/')}/usage", headers={"x-goog-api-key": api_key}) as response:
                if response.status != 200:
                    return {}
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
