"""Unit tests for picking the AI provider and models from env, and shaping calls for Vertex."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai_provider import (
    GEMINI_SAFETY_CATEGORIES, load_settings, make_client, prepare_chat, vertex_token_provider,
)

AZURE_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://chrono.openai.azure.com/openai/v1",
    "AZURE_OPENAI_KEY": "azure-key",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-6-luna",
    "AZURE_OPENAI_BACKGROUND_DEPLOYMENT": "gpt-5-nano",
}


# ── which provider and models ────────────────────────────────────────────────

def test_without_ai_provider_the_old_azure_settings_still_work():
    s = load_settings(AZURE_ENV)
    assert s.provider == "azure"
    assert s.base_url == "https://chrono.openai.azure.com/openai/v1"
    assert s.api_key == "azure-key"
    assert s.chat_model == "gpt-6-luna"
    assert s.background_model == "gpt-5-nano"


def test_switching_back_to_azure_is_only_the_provider_name():
    s = load_settings({**AZURE_ENV, "AI_PROVIDER": "azure", "VERTEX_PROJECT": "adorio"})
    assert s.provider == "azure"
    assert s.chat_model == "gpt-6-luna"


def test_vertex_builds_the_global_openai_compatible_url_from_the_project():
    s = load_settings({"AI_PROVIDER": "vertex", "VERTEX_PROJECT": "adorio"})
    assert s.provider == "vertex"
    assert s.base_url == (
        "https://aiplatform.googleapis.com/v1/projects/adorio/locations/global/endpoints/openapi")


def test_vertex_in_a_region_uses_the_regional_host():
    s = load_settings({"AI_PROVIDER": "vertex", "VERTEX_PROJECT": "adorio", "VERTEX_LOCATION": "us-central1"})
    assert s.base_url == (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/adorio/locations/us-central1/endpoints/openapi")


def test_vertex_defaults_to_the_cheapest_gemini_for_chat():
    s = load_settings({"AI_PROVIDER": "vertex", "VERTEX_PROJECT": "adorio"})
    assert s.chat_model == "gemini-3.1-flash-lite"
    assert s.background_model == ""
    assert s.heavy_model == ""


def test_the_generic_model_settings_win_over_the_azure_names():
    s = load_settings({**AZURE_ENV, "AI_CHAT_MODEL": "gpt-5-mini", "AI_BACKGROUND_MODEL": "x",
                       "AI_HEAVY_MODEL": "gpt-5.6-luna"})
    assert (s.chat_model, s.background_model, s.heavy_model) == ("gpt-5-mini", "x", "gpt-5.6-luna")


def test_any_openai_compatible_api_works_from_a_url_and_key():
    s = load_settings({"AI_PROVIDER": "openai", "AI_BASE_URL": "https://api.example/v1",
                       "AI_API_KEY": "k", "AI_CHAT_MODEL": "some-model"})
    assert (s.provider, s.base_url, s.api_key, s.chat_model) == ("openai", "https://api.example/v1", "k", "some-model")


def test_vertex_without_a_project_says_which_setting_is_missing():
    with pytest.raises(ValueError, match="VERTEX_PROJECT"):
        load_settings({"AI_PROVIDER": "vertex"})


def test_an_unknown_provider_is_refused_at_startup():
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        load_settings({"AI_PROVIDER": "bedrock"})


# ── shaping each call ────────────────────────────────────────────────────────

VERTEX = load_settings({"AI_PROVIDER": "vertex", "VERTEX_PROJECT": "adorio"})
AZURE = load_settings(AZURE_ENV)


def test_vertex_calls_turn_every_adjustable_safety_filter_off():
    sent = prepare_chat(VERTEX, {"model": "gemini-3.1-flash-lite", "messages": []})
    safety = sent["extra_body"]["extra_body"]["google"]["safety_settings"]
    assert {s["category"] for s in safety} == set(GEMINI_SAFETY_CATEGORIES)
    assert all(s["threshold"] == "OFF" for s in safety)
    assert len(GEMINI_SAFETY_CATEGORIES) >= 5


def test_vertex_calls_name_the_publisher():
    assert prepare_chat(VERTEX, {"model": "gemini-3.8-flash"})["model"] == "google/gemini-3.8-flash"
    assert prepare_chat(VERTEX, {"model": "google/gemini-3.8-flash"})["model"] == "google/gemini-3.8-flash"


@pytest.mark.parametrize("asked, sent", [("none", "low"), ("minimal", "low"), ("low", "low"), ("medium", "medium")])
def test_vertex_gets_the_lightest_reasoning_it_accepts(asked, sent):
    # gemini-3.8-flash answers 400 to "none" and "minimal" (probe of 2026-11-17 in run.md)
    assert prepare_chat(VERTEX, {"model": "m", "reasoning_effort": asked})["reasoning_effort"] == sent


def test_vertex_keeps_other_extra_body_fields():
    sent = prepare_chat(VERTEX, {"model": "m", "extra_body": {"extra_body": {"google": {"thinking_config": {"a": 1}}}, "x": 2}})
    assert sent["extra_body"]["x"] == 2
    assert sent["extra_body"]["extra_body"]["google"]["thinking_config"] == {"a": 1}
    assert "safety_settings" in sent["extra_body"]["extra_body"]["google"]


def test_azure_calls_go_out_unchanged():
    kwargs = {"model": "gpt-6-luna", "reasoning_effort": "none", "messages": []}
    assert prepare_chat(AZURE, dict(kwargs)) == kwargs


# ── the client ───────────────────────────────────────────────────────────────

async def test_the_vertex_token_is_refreshed_only_when_it_has_expired():
    creds = SimpleNamespace(valid=False, token=None, refreshed=0)

    def refresh(_request):
        creds.refreshed += 1
        creds.valid, creds.token = True, f"token-{creds.refreshed}"

    creds.refresh = refresh
    token = vertex_token_provider(creds)
    assert await token() == "token-1"
    assert await token() == "token-1"
    creds.valid = False
    assert await token() == "token-2"


async def test_the_client_shapes_each_chat_call_for_the_provider(monkeypatch):
    inner = MagicMock()
    inner.chat.completions.create = AsyncMock(return_value=SimpleNamespace(model="google/gemini-3.1-flash-lite", usage=None))
    monkeypatch.setattr("src.ai_provider.AsyncOpenAI", lambda **kw: inner)
    monkeypatch.setattr("src.ai_provider.vertex_token_provider", lambda creds=None: AsyncMock(return_value="t"))
    client = make_client(VERTEX)
    await client.chat.completions.create(model="gemini-3.1-flash-lite", messages=[], reasoning_effort="none")
    sent = inner.chat.completions.create.call_args.kwargs
    assert sent["model"] == "google/gemini-3.1-flash-lite"
    assert sent["reasoning_effort"] == "low"
    assert "safety_settings" in sent["extra_body"]["extra_body"]["google"]


# ── the background model ─────────────────────────────────────────────────────

def test_a_leftover_azure_background_deployment_is_ignored_on_vertex():
    from config.config import background_model
    env = {"AI_PROVIDER": "vertex", "VERTEX_PROJECT": "adorio", "AZURE_OPENAI_BACKGROUND_DEPLOYMENT": "gpt-5-nano"}
    assert background_model(env) == ""
    assert load_settings(env).background_model == ""


def test_the_azure_background_deployment_still_works_on_azure():
    from config.config import background_model
    assert background_model(AZURE_ENV) == "gpt-5-nano"
    assert background_model({**AZURE_ENV, "AI_PROVIDER": "azure"}) == "gpt-5-nano"


def test_ai_background_model_works_for_any_provider():
    from config.config import background_model
    assert background_model({"AI_PROVIDER": "vertex", "AI_BACKGROUND_MODEL": "gemini-3.1-flash-lite"}) == "gemini-3.1-flash-lite"
