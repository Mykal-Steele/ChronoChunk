"""
Which AI service the bot talks to, and the one place its client is made.

Every provider here speaks the OpenAI Chat Completions API, so the rest of the bot
does not care which one is in use. Switching is an env change:

    AI_PROVIDER=azure   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY (the default, as before 2026-11)
    AI_PROVIDER=vertex  VERTEX_PROJECT, optional VERTEX_LOCATION (Gemini, signs in as the VM)
    AI_PROVIDER=openai  AI_BASE_URL, AI_API_KEY (any other OpenAI-compatible API)

AI_CHAT_MODEL, AI_BACKGROUND_MODEL and AI_HEAVY_MODEL pick the models for any provider.
For Azure the old AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_BACKGROUND_DEPLOYMENT still work.
"""
import asyncio
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Optional

from openai import AsyncOpenAI

from src.usage_guard import GuardedClient, guard_client

PROVIDERS = ("azure", "vertex", "openai")
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Every Gemini safety category that can be adjusted. The user wants the bot unfiltered
# (2026-11-17), so all of them go out as OFF. Google keeps a few protections, such as
# the one against child sexual abuse material, that no setting turns off.
GEMINI_SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
)
# gemini-3.8-flash answers 400 to "none" and "minimal" (probe of 2026-11-17 in run.md),
# so the bot's Azure-era settings are moved to the lightest level Gemini accepts.
VERTEX_REASONING = {"none": "low", "minimal": "low"}


@dataclass(frozen=True)
class AiSettings:
    provider: str
    base_url: str
    api_key: str
    chat_model: str
    # Empty means "use the chat model"
    background_model: str
    heavy_model: str


def _get(env: Mapping[str, str], *names: str, default: str = "") -> str:
    """The first of names that is set and not blank."""
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return default


def _vertex_url(project: str, location: str) -> str:
    if location == "global":
        return f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/endpoints/openapi"
    return (f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project}"
            f"/locations/{location}/endpoints/openapi")


def load_settings(env: Optional[Mapping[str, str]] = None) -> AiSettings:
    """Read the provider and models from env. Raises ValueError for a setting that cannot work."""
    env = os.environ if env is None else env
    provider = _get(env, "AI_PROVIDER", default="azure").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"AI_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}")

    background = _get(env, "AI_BACKGROUND_MODEL")
    heavy = _get(env, "AI_HEAVY_MODEL")
    if provider == "azure":
        return AiSettings(
            provider, _get(env, "AZURE_OPENAI_ENDPOINT"), _get(env, "AZURE_OPENAI_KEY"),
            _get(env, "AI_CHAT_MODEL", "AZURE_OPENAI_DEPLOYMENT", default="gpt-5-mini"),
            background or _get(env, "AZURE_OPENAI_BACKGROUND_DEPLOYMENT"), heavy)
    if provider == "vertex":
        project = _get(env, "VERTEX_PROJECT")
        if not project:
            raise ValueError("AI_PROVIDER=vertex needs VERTEX_PROJECT (the Google Cloud project id)")
        return AiSettings(
            provider, _vertex_url(project, _get(env, "VERTEX_LOCATION", default="global")), "",
            _get(env, "AI_CHAT_MODEL", default="gemini-3.1-flash-lite"), background, heavy)
    return AiSettings(
        provider, _get(env, "AI_BASE_URL"), _get(env, "AI_API_KEY"),
        _get(env, "AI_CHAT_MODEL", default="gpt-5-mini"), background, heavy)


def prepare_chat(settings: AiSettings, kwargs: dict) -> dict:
    """Shape one chat completion for the provider. Azure and plain OpenAI calls go out as they are."""
    if settings.provider != "vertex":
        return kwargs
    kwargs = dict(kwargs)
    model = kwargs.get("model") or ""
    if model and "/" not in model:
        kwargs["model"] = f"google/{model}"
    effort = kwargs.get("reasoning_effort")
    if effort in VERTEX_REASONING:
        kwargs["reasoning_effort"] = VERTEX_REASONING[effort]
    # Gemini-only fields ride in extra_body.extra_body.google (Vertex's OpenAI compatibility docs)
    body = dict(kwargs.get("extra_body") or {})
    inner = dict(body.get("extra_body") or {})
    google = dict(inner.get("google") or {})
    google["safety_settings"] = [{"category": c, "threshold": "OFF"} for c in GEMINI_SAFETY_CATEGORIES]
    inner["google"] = google
    body["extra_body"] = inner
    kwargs["extra_body"] = body
    return kwargs


def vertex_token_provider(credentials=None) -> Callable[[], Awaitable[str]]:
    """
    An async function that returns a Google access token. Tokens last an hour, so it
    refreshes one only after it has expired. On a Google VM the credentials come from
    the VM's service account, so no key is stored anywhere.
    """
    if credentials is None:
        import google.auth
        credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    lock = asyncio.Lock()

    async def token() -> str:
        async with lock:
            if not credentials.valid:
                from google.auth.transport.requests import Request
                await asyncio.to_thread(credentials.refresh, Request())
            return credentials.token

    return token


def make_client(settings: AiSettings, timeout: Optional[float] = None,
                max_retries: Optional[int] = None) -> GuardedClient:
    """The client every part of the bot uses: budget-guarded, and shaped for the provider."""
    options = {}
    if timeout is not None:
        options["timeout"] = timeout
    if max_retries is not None:
        options["max_retries"] = max_retries
    api_key = vertex_token_provider() if settings.provider == "vertex" else settings.api_key
    client = AsyncOpenAI(base_url=settings.base_url, api_key=api_key, **options)
    return guard_client(client, prepare=lambda kwargs: prepare_chat(settings, kwargs))
