"""
The client for the spend-capped Gemini proxy that Uniloom replies run on. The bot speaks
the OpenAI chat format and the proxy speaks Gemini's own API, so these check both
directions of the translation and how the proxy's answers come back.
"""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.gemini_proxy import (
    GeminiProxyClient, ProxyError, SKIP_SIGNATURE, from_gemini, to_gemini,
)

TOOL = {"type": "function", "function": {
    "name": "uniloom_list_projects", "description": "The projects you belong to",
    "parameters": {"type": "object", "properties": {}}}}


# ── the request ──────────────────────────────────────────────────────────────

def test_system_messages_become_the_system_instruction():
    model, body = to_gemini({"model": "gemini-3.8-flash", "messages": [
        {"role": "system", "content": "be chrono"},
        {"role": "user", "content": "hi"},
    ], "max_completion_tokens": 100})
    assert model == "gemini-3.8-flash"
    assert body["systemInstruction"] == {"parts": [{"text": "be chrono"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert body["generationConfig"]["maxOutputTokens"] == 100


def test_the_vertex_model_prefix_is_dropped():
    model, _ = to_gemini({"model": "google/gemini-3.8-flash", "messages": [{"role": "user", "content": "hi"}]})
    assert model == "gemini-3.8-flash"


def test_images_go_as_inline_data():
    _, body = to_gemini({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0K"}},
    ]}]})
    assert body["contents"][0]["parts"] == [
        {"text": "what is this"}, {"inlineData": {"mimeType": "image/png", "data": "iVBORw0K"}}]


def test_tools_and_a_forced_tool_are_translated():
    _, body = to_gemini({"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": [TOOL],
                         "tool_choice": {"type": "function", "function": {"name": "uniloom_list_projects"}}})
    assert body["tools"] == [{"functionDeclarations": [{
        "name": "uniloom_list_projects", "description": "The projects you belong to",
        "parameters": {"type": "object", "properties": {}}}]}]
    assert body["toolConfig"] == {"functionCallingConfig": {
        "mode": "ANY", "allowedFunctionNames": ["uniloom_list_projects"]}}


def test_a_tool_call_and_its_result_keep_their_names_and_signature():
    _, body = to_gemini({"model": "m", "messages": [
        {"role": "user", "content": "whats on my board"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "uniloom_list_projects", "arguments": "{}"},
             "extra_content": {"google": {"thought_signature": "sig-1"}}},
            {"id": "c2", "type": "function", "function": {"name": "uniloom_get_item", "arguments": '{"key": "TG-1"}'}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "TG: Tracker"},
        {"role": "tool", "tool_call_id": "c2", "content": "TG-1: Login"},
    ]})
    model_turn, results = body["contents"][1], body["contents"][2]
    assert model_turn == {"role": "model", "parts": [
        {"functionCall": {"name": "uniloom_list_projects", "args": {}}, "thoughtSignature": "sig-1"},
        {"functionCall": {"name": "uniloom_get_item", "args": {"key": "TG-1"}}},
    ]}
    # Both results answer the one model turn, so they go back together
    assert results == {"role": "user", "parts": [
        {"functionResponse": {"name": "uniloom_list_projects", "response": {"result": "TG: Tracker"}}},
        {"functionResponse": {"name": "uniloom_get_item", "response": {"result": "TG-1: Login"}}},
    ]}


def test_a_call_made_on_another_provider_gets_the_documented_stand_in_signature():
    # A reply that loaded Uniloom on the bot's own model has calls with no Gemini signature,
    # and Gemini 3 answers 400 to a function call without one
    _, body = to_gemini({"model": "m", "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "uniloom", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "loaded"},
    ]})
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == SKIP_SIGNATURE


def test_text_from_the_model_turn_is_kept():
    _, body = to_gemini({"model": "m", "messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "again"}]})
    assert body["contents"][1] == {"role": "model", "parts": [{"text": "yo"}]}


def test_two_user_messages_in_a_row_become_one_turn():
    _, body = to_gemini({"model": "m", "messages": [
        {"role": "user", "content": "material"}, {"role": "user", "content": "question"}]})
    assert body["contents"] == [{"role": "user", "parts": [{"text": "material"}, {"text": "question"}]}]


@pytest.mark.parametrize("effort, level", [("none", "low"), ("minimal", "low"), ("low", "low"),
                                           ("medium", "medium"), ("high", "high")])
def test_reasoning_effort_becomes_a_thinking_level(effort, level):
    _, body = to_gemini({"model": "m", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": effort})
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": level}


def test_safety_filters_are_off_like_the_vertex_path():
    from src.ai_provider import GEMINI_SAFETY_CATEGORIES
    _, body = to_gemini({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert body["safetySettings"] == [{"category": c, "threshold": "OFF"} for c in GEMINI_SAFETY_CATEGORIES]


# ── the answer ───────────────────────────────────────────────────────────────

USAGE = {"promptTokenCount": 1200, "candidatesTokenCount": 30, "thoughtsTokenCount": 70,
         "cachedContentTokenCount": 1000}


def test_a_text_answer_reads_like_an_openai_one_and_skips_thoughts():
    resp = from_gemini({"candidates": [{"content": {"role": "model", "parts": [
        {"text": "planning...", "thought": True}, {"text": "u have "}, {"text": "TG"}]},
        "finishReason": "STOP"}], "usageMetadata": USAGE}, "gemini-3.8-flash")
    message = resp.choices[0].message
    assert message.content == "u have TG"
    assert message.tool_calls is None
    assert resp.model == "gemini-3.8-flash"
    # Thinking is billed as output
    assert (resp.usage.prompt_tokens, resp.usage.completion_tokens) == (1200, 100)
    assert resp.usage.prompt_tokens_details.cached_tokens == 1000


def test_function_calls_come_back_as_tool_calls_with_their_signature():
    resp = from_gemini({"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": "uniloom_get_item", "args": {"key": "TG-1"}}, "thoughtSignature": "sig-9"},
        {"functionCall": {"name": "uniloom_list_projects", "args": {}}},
    ]}}], "usageMetadata": USAGE}, "m")
    first, second = resp.choices[0].message.tool_calls
    assert first.function.name == "uniloom_get_item"
    assert json.loads(first.function.arguments) == {"key": "TG-1"}
    assert first.extra_content == {"google": {"thought_signature": "sig-9"}}
    assert first.id != second.id
    assert second.extra_content is None


def test_an_answer_with_no_candidate_is_an_empty_message():
    resp = from_gemini({"usageMetadata": {"promptTokenCount": 5}}, "m")
    assert resp.choices[0].message.content == ""
    assert resp.choices[0].message.tool_calls is None


def test_signatures_survive_the_round_trip():
    from src.ai_response_handler import _echo_tool_call
    resp = from_gemini({"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "uniloom", "args": {}}, "thoughtSignature": "sig-3"}]}}]}, "m")
    echoed = _echo_tool_call(resp.choices[0].message.tool_calls[0])
    _, body = to_gemini({"model": "m", "messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": [echoed]},
        {"role": "tool", "tool_call_id": echoed["id"], "content": "ok"}]})
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "sig-3"


# ── talking to the proxy ─────────────────────────────────────────────────────

async def _serve(answer):
    """A stand-in proxy on localhost. answer(request, body) returns (status, json)."""
    seen = {}

    async def handle(request: web.Request):
        seen["path"] = request.path
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = await request.json()
        status, data = answer(seen["body"])
        return web.json_response(data, status=status)

    app = web.Application()
    app.router.add_post("/{tail:.*}", handle)
    server = TestServer(app)
    await server.start_server()
    return server, seen


async def test_it_posts_to_generate_content_with_the_key_header():
    server, seen = await _serve(lambda body: (200, {"candidates": [{"content": {"parts": [{"text": "hey"}]}}]}))
    try:
        client = GeminiProxyClient(str(server.make_url("")).rstrip("/"), "friend-key")
        resp = await client.chat.completions.create(
            model="gemini-3.8-flash", messages=[{"role": "user", "content": "hi"}], max_completion_tokens=50)
    finally:
        await server.close()
    assert seen["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert seen["key"] == "friend-key"
    assert seen["body"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert resp.choices[0].message.content == "hey"


@pytest.mark.parametrize("status, refused", [(402, True), (403, True), (401, True), (404, True), (429, True), (400, False), (500, False)])
async def test_a_failed_request_raises_with_its_status(status, refused):
    server, _ = await _serve(lambda body: (status, {"error": {"message": "Spending limit reached"}}))
    try:
        client = GeminiProxyClient(str(server.make_url("")).rstrip("/"), "friend-key")
        with pytest.raises(ProxyError) as caught:
            await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    finally:
        await server.close()
    assert caught.value.status_code == status
    assert caught.value.refused is refused
    assert "Spending limit reached" in str(caught.value)
