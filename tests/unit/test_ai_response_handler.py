"""Unit tests for AIResponseHandler — Gemini client is mocked."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_response_handler.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.ai_response_handler import AIResponseHandler
        h = AIResponseHandler(
            endpoint="https://fake.openai.azure.com/openai/v1",
            api_key="fake_key",
            deployment="gpt-5-mini",
            important_topics=["depression", "suicide", "violence"],
        )
    h.ai_client = mock_openai_client
    return h


# ── _format_ai_response ───────────────────────────────────────────────────────

def test_format_empty_returns_fallback(handler):
    result = handler._format_ai_response("")
    assert isinstance(result, str)
    assert len(result) > 0


def test_format_strips_chronochunk_prefix(handler):
    result = handler._format_ai_response("ChronoChunk: sup what's up")
    assert not result.startswith("ChronoChunk:")
    assert not result.startswith("chronochunk:")


def test_format_strips_bot_prefix(handler):
    result = handler._format_ai_response("Bot: hello there")
    assert not result.lower().startswith("bot:")


def test_format_triple_question_marks_preserved(handler):
    # The personality uses "???" for emphasis — formatter keeps it
    result = handler._format_ai_response("really???")
    assert "??" in result


def test_format_triple_exclamation_preserved(handler):
    # The personality uses "!!!" for emphasis — formatter keeps it
    result = handler._format_ai_response("wow!!!")
    assert "!" in result


def test_format_fixes_space_before_punctuation(handler):
    result = handler._format_ai_response("hello , world")
    assert " ," not in result


def test_format_long_response_not_truncated(handler):
    # Personality allows long rants — formatter does NOT truncate
    long_text = "sky is blue. sea is green. fire is hot. sun is bright. moon is round. stars are cool. wind is fast. rain is wet. snow is cold. ice is clear."
    result = handler._format_ai_response(long_text)
    # Result should still contain content from most sentences (not cut short)
    assert len(result) > 50


def test_format_preserves_text(handler):
    # Formatter should return non-empty string for normal input
    result = handler._format_ai_response("hello world this is a test message")
    assert isinstance(result, str)
    assert len(result) > 0


def test_format_returns_string(handler):
    assert isinstance(handler._format_ai_response("normal message"), str)


# ── extract_important_topics ──────────────────────────────────────────────────

def test_extract_topics_finds_depression(handler):
    # The topic list contains "depression" — must use that exact word in the message
    topics = handler.extract_important_topics("I've been dealing with depression for months")
    assert "depression" in topics


def test_extract_topics_finds_suicide(handler):
    topics = handler.extract_important_topics("i have been thinking about suicide")
    assert "suicide" in topics


def test_extract_topics_empty_message(handler):
    topics = handler.extract_important_topics("")
    assert topics == []


def test_extract_topics_none_found(handler):
    topics = handler.extract_important_topics("what's your favorite color")
    assert topics == []


def test_extract_topics_multiple(handler):
    topics = handler.extract_important_topics("depression and violence are serious topics")
    assert "depression" in topics
    assert "violence" in topics


# ── generate_response ─────────────────────────────────────────────────────────

async def test_generate_response_returns_string(handler, fake_openai_response):
    fake_openai_response.choices[0].message.content = "yo what's good"
    result = await handler.generate_response("hello", "", "TestUser", "12345")
    assert isinstance(result, str)
    assert len(result) > 0


async def test_generate_response_calls_api(handler, mock_openai_client):
    await handler.generate_response("hello there", "", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.called


async def test_generate_response_caches_result(handler, mock_openai_client):
    query = "what is the capital of france"
    await handler.generate_response(query, "", "TestUser", "12345")
    await handler.generate_response(query, "", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.call_count == 1


async def test_generate_response_cache_different_queries(handler, mock_openai_client):
    await handler.generate_response("question one", "", "TestUser", "12345")
    await handler.generate_response("question two", "", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.call_count == 2


async def test_generate_response_cache_respects_history(handler, mock_openai_client):
    await handler.generate_response("why", "USER: hi\nBOT: hello", "TestUser", "12345")
    await handler.generate_response("why", "USER: cya\nBOT: bye", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.call_count == 2


async def test_generate_response_429_returns_fallback(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception("429 quota exceeded")
    result = await handler.generate_response("test", "", "TestUser", "12345")
    assert isinstance(result, str)
    assert len(result) > 0


async def test_generate_response_generic_error_returns_fallback(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception("connection refused")
    result = await handler.generate_response("test", "", "TestUser", "12345")
    assert isinstance(result, str)
    assert "glitch" in result or "brain" in result or "try again" in result.lower()


async def test_generate_response_short_followup_with_history(handler, mock_openai_client):
    history = "TestUser: whats the best programming language\nYOU (ChronoChunk): python fr"
    await handler.generate_response("why tho", history, "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.called
    call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
    messages = call_kwargs.get("messages", [])
    user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
    # History must be present and the query must be in the user turn
    assert "python fr" in user_content and "why tho" in user_content


async def test_generate_response_cache_does_not_exceed_limit(handler, mock_openai_client):
    for i in range(60):
        handler.response_cache[f"key_{i}"] = f"value_{i}"
    await handler.generate_response("eviction test query unique xyz", "", "TestUser", "12345")
    assert len(handler.response_cache) <= 51


# ── third-person regression (prompt framing) ─────────────────────────────────

async def test_prompt_does_not_use_username_says_pattern(handler, mock_openai_client):
    """Regression: username must NOT appear as '{name} says:' — causes bot to treat user as 3rd party."""
    await handler.generate_response("how is life", "", "Kruskal", "99999")
    call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
    messages = call_kwargs.get("messages", [])
    user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
    assert "Kruskal says:" not in user_content
    assert "Kruskal says:" not in user_content


async def test_prompt_uses_direct_address_framing(handler, mock_openai_client):
    """The user turn must identify the speaker with [Name]: format — clean data, no embedded instructions."""
    await handler.generate_response("yo wassup", "", "SomeUser", "11111")
    call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
    messages = call_kwargs.get("messages", [])
    user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
    assert '[SomeUser]:' in user_content


async def test_system_prompt_has_no_hardcoded_names(handler):
    """System prompt must not contain hardcoded usernames like 'Kruskal'."""
    from src.ai_response_handler import _SYSTEM_PROMPT
    assert "Kruskal" not in _SYSTEM_PROMPT


# ── attached context and images ──────────────────────────────────────────────

def _user_content(mock_openai_client, call_index=-1):
    messages = mock_openai_client.chat.completions.create.call_args_list[call_index][1]["messages"]
    return next(m["content"] for m in messages if m["role"] == "user")


async def test_attached_context_sits_between_history_and_the_query(handler, mock_openai_client):
    block = '=== MESSAGE THEY REPLIED TO (sent by Kruskal) ===\ntext: "hey bau"\n=== END OF REPLIED-TO MESSAGE ==='
    await handler.generate_response("/read what this msg say", "Kruskal: earlier chat", "Kruskal", "1",
                                    attached_context=block)
    content = _user_content(mock_openai_client)
    assert isinstance(content, str)
    assert content.index("earlier chat") < content.index("hey bau") < content.index('[Kruskal]: "read what this msg say"')


async def test_images_are_sent_as_image_parts(handler, mock_openai_client):
    await handler.generate_response("/read", "", "Kruskal", "1",
                                    attached_context="image: proof.png", images=["data:image/png;base64,AAAA"])
    content = _user_content(mock_openai_client)
    assert content[0]["type"] == "text" and '[Kruskal]: "read"' in content[0]["text"]
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


async def test_same_query_on_different_replied_messages_is_not_served_from_cache(handler, mock_openai_client):
    await handler.generate_response("/read", "", "Kruskal", "1", attached_context='text: "first message"')
    await handler.generate_response("/read", "", "Kruskal", "1", attached_context='text: "second message"')
    assert mock_openai_client.chat.completions.create.call_count == 2


async def test_reading_material_gets_a_bigger_token_budget(handler, mock_openai_client):
    await handler.generate_response("/yo", "", "Kruskal", "1")
    plain = mock_openai_client.chat.completions.create.call_args[1]["max_completion_tokens"]
    await handler.generate_response("/summarize", "", "Kruskal", "1", attached_context="file: notes.md")
    reading = mock_openai_client.chat.completions.create.call_args[1]["max_completion_tokens"]
    assert reading > plain


async def test_bad_image_falls_back_to_a_text_only_request(handler, mock_openai_client, fake_openai_response):
    mock_openai_client.chat.completions.create.side_effect = [
        Exception("Error code: 400 - Invalid image data"),
        fake_openai_response,
    ]
    result = await handler.generate_response("/read", "", "Kruskal", "1", images=["data:image/png;base64,AAAA"])
    assert result == "test ai response"
    retry_content = _user_content(mock_openai_client)
    assert isinstance(retry_content, str)
    assert "images failed to load" in retry_content


async def test_system_prompt_covers_replies_and_staying_on_topic(handler):
    from src.ai_response_handler import _SYSTEM_PROMPT
    assert "MESSAGE THEY REPLIED TO" in _SYSTEM_PROMPT
    assert "STAY ON TOPIC" in _SYSTEM_PROMPT


def test_format_keeps_number_ranges_readable(handler):
    result = handler._format_ai_response("office hrs 14:00–16:00, weeks 1 – 3")
    assert "14:00-16:00" in result
    assert "weeks 1-3" in result


def test_format_still_strips_dashes_between_words(handler):
    result = handler._format_ai_response("nah — thats cooked")
    assert "—" not in result and "–" not in result
    assert "nah thats cooked" in result
