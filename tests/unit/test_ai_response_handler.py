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


# ── code blocks, tools and the document writer ───────────────────────────────

def test_format_leaves_code_blocks_exactly_as_written(handler):
    code = "```js\nitems\n    .filter(x => x , y)\n    .map(f)\n```"
    result = handler._format_ai_response(f"here , look\n{code}\nok !!!!")
    assert code in result
    assert result.startswith("here, look")


def _message(content=None, tool_calls=None):
    """A model response holding one message, the way the OpenAI client returns it."""
    from types import SimpleNamespace
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _tool_call(name, arguments, call_id="call_1"):
    import json
    from types import SimpleNamespace
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


@pytest.fixture
def built_files(monkeypatch):
    """Replace the real file builders so no pandoc or network is needed."""
    from src import ai_response_handler
    from src.document_builder import BuiltFile
    document = AsyncMock(return_value=BuiltFile("proposal.pdf", b"%PDF"))
    diagram = AsyncMock(return_value=BuiltFile("flow.png", b"\x89PNG"))
    monkeypatch.setattr(ai_response_handler, "build_document", document)
    monkeypatch.setattr(ai_response_handler, "build_diagram", diagram)
    return document, diagram


async def test_tools_are_only_offered_when_files_are_allowed(handler, mock_openai_client):
    await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert "tools" not in mock_openai_client.chat.completions.create.call_args[1]
    await handler.generate_reply("/make a pdf", "", "Kruskal", "1", allow_files=True)
    offered = mock_openai_client.chat.completions.create.call_args[1]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {"create_document", "create_diagram"}


async def test_chat_uses_the_configured_reasoning_effort(handler, mock_openai_client):
    await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert mock_openai_client.chat.completions.create.call_args[1]["reasoning_effort"] == "low"


async def test_document_request_runs_the_writer_and_returns_the_file(handler, mock_openai_client, built_files):
    from config.ai_config import WRITER_PROMPT
    document, _ = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Proposal", "brief": "A proposal for Acme"})]),
        _message(content="# Proposal\n\nWe will rebuild the site — fast."),
        _message(content="done, check the pdf"),
    ]

    reply = await handler.generate_reply("/write a proposal for acme as a pdf", "Kruskal: earlier chat", "Kruskal", "1",
                                         attached_context="=== MESSAGE THEY REPLIED TO ===\nbudget is 5000", allow_files=True)

    assert reply.text == "done, check the pdf"
    assert [f.filename for f in reply.files] == ["proposal.pdf"]

    calls = mock_openai_client.chat.completions.create.call_args_list
    writer = calls[1][1]
    assert writer["messages"][0]["content"] == WRITER_PROMPT
    assert "A proposal for Acme" in writer["messages"][1]["content"]
    assert "budget is 5000" in writer["messages"][1]["content"]
    assert writer["messages"][1]["content"].rstrip().endswith("heading, list, table and code block.")
    assert "tools" not in writer

    # the writer's markdown reaches the builder with the dash cleaned out
    fmt, title, markdown, max_pages = document.await_args[0]
    assert (fmt, title, max_pages) == ("pdf", "Proposal", None)
    assert markdown == "# Proposal\n\nWe will rebuild the site, fast."

    # the model is told the file was made
    tool_result = next(m for m in calls[2][1]["messages"] if m["role"] == "tool")
    assert tool_result["tool_call_id"] == "call_1" and "proposal.pdf" in tool_result["content"]


async def test_writer_prompt_is_the_professional_one_not_the_chat_persona(handler):
    from config.ai_config import WRITER_PROMPT
    from src.ai_response_handler import _SYSTEM_PROMPT
    assert "Document writer" in WRITER_PROMPT and "No slang" in WRITER_PROMPT
    assert "delve" in WRITER_PROMPT          # the banned word list is loaded
    assert "ya boi" not in WRITER_PROMPT     # none of the chat persona leaks in
    assert "REAL WORK" in _SYSTEM_PROMPT


async def test_diagram_request_returns_the_image(handler, mock_openai_client, built_files):
    _, diagram = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})]),
        _message(content="there u go"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert [f.filename for f in reply.files] == ["flow.png"]
    assert diagram.await_args[0] == ("Flow", "flowchart TD\n  A --> B")


async def test_mermaid_syntax_error_goes_back_to_the_model_for_a_retry(handler, mock_openai_client, built_files):
    from src.document_builder import BuiltFile, DocumentError
    _, diagram = built_files
    diagram.side_effect = [DocumentError("the mermaid source has a syntax error: Parse error on line 2"),
                           BuiltFile("flow.png", b"\x89PNG")]
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A[oops"})]),
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"}, "call_2")]),
        _message(content="fixed it"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "fixed it" and len(reply.files) == 1
    second_request = mock_openai_client.chat.completions.create.call_args_list[1][1]["messages"]
    assert "syntax error" in next(m for m in second_request if m["role"] == "tool")["content"]


async def test_pasted_mermaid_source_is_rendered_into_an_image(handler, mock_openai_client, built_files):
    # What the live bot did: two rejected diagrams, then the source pasted into chat
    from src.document_builder import BuiltFile, DocumentError
    _, diagram = built_files
    source = "flowchart TD; A[Input Stream<T>]-->B[Output Stream<R>]"
    diagram.side_effect = [DocumentError("the mermaid source has a syntax error: Parse error on line 9"),
                           BuiltFile("diagram.png", b"\x89PNG")]
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A[oops"})]),
        _message(content=f"aight fixed it, paste this into mermaid.live or whatever: {source}"),
    ]
    reply = await handler.generate_reply("/make a mermaid diagram of that", "", "Kruskal", "1", allow_files=True)
    assert [f.filename for f in reply.files] == ["diagram.png"]
    assert diagram.await_args[0] == ("Diagram", source)
    assert reply.text == "here u go"


async def test_text_around_a_pasted_diagram_is_kept_when_it_says_something(handler, mock_openai_client, built_files):
    explanation = "each element goes through the mapper and comes out as its own stream. " * 3
    mock_openai_client.chat.completions.create.side_effect = [
        _message(content=f"{explanation}\n```mermaid\nflowchart TD\n  A --> B\n```"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert len(reply.files) == 1
    assert "its own stream" in reply.text and "flowchart TD" not in reply.text


async def test_pasted_source_stays_when_they_asked_for_the_code(handler, mock_openai_client, built_files):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(content="```mermaid\nflowchart TD\n  A --> B\n```"),
    ]
    reply = await handler.generate_reply("/give me the mermaid code for that", "", "Kruskal", "1", allow_files=True)
    assert len(reply.files) == 1 and "flowchart TD" in reply.text


async def test_pasted_source_stays_as_text_when_it_cannot_be_rendered(handler, mock_openai_client, built_files):
    from src.document_builder import DocumentError
    _, diagram = built_files
    diagram.side_effect = DocumentError("the diagram renderer (mermaid.ink) could not be reached, try again later")
    mock_openai_client.chat.completions.create.side_effect = [
        _message(content="here:\n```mermaid\nflowchart TD\n  A --> B\n```"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert reply.files == [] and "flowchart TD" in reply.text


async def test_pasted_source_counts_against_the_file_limit(handler, mock_openai_client, built_files):
    from src.exceptions import RateLimitError
    _, diagram = built_files

    def gate():
        raise RateLimitError(retry_after=7200)

    mock_openai_client.chat.completions.create.side_effect = [
        _message(content="```mermaid\nflowchart TD\n  A --> B\n```"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True, file_gate=gate)
    assert reply.files == [] and "flowchart TD" in reply.text
    diagram.assert_not_awaited()


async def test_mermaid_in_a_reply_is_left_alone_when_files_are_off(handler, mock_openai_client, built_files):
    _, diagram = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        _message(content="```mermaid\nflowchart TD\n  A --> B\n```"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1")
    assert reply.files == [] and "flowchart TD" in reply.text
    diagram.assert_not_awaited()


async def test_file_limit_stops_the_build_and_tells_the_model(handler, mock_openai_client, built_files):
    from src.exceptions import RateLimitError
    document, _ = built_files

    def gate():
        raise RateLimitError(retry_after=7200)

    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "X", "brief": "x"})]),
        _message(content="u hit ur file limit for today"),
    ]
    reply = await handler.generate_reply("/make a pdf", "", "Kruskal", "1", allow_files=True, file_gate=gate)
    assert reply.files == []
    document.assert_not_awaited()
    assert mock_openai_client.chat.completions.create.call_count == 2  # the writer was never called
    tool_result = next(m for m in mock_openai_client.chat.completions.create.call_args[1]["messages"] if m["role"] == "tool")
    assert "limit" in tool_result["content"] and "2 hours" in tool_result["content"]


async def test_model_cannot_loop_on_tools_forever(handler, mock_openai_client, built_files):
    from src.ai_response_handler import MAX_FILES_PER_REPLY, MAX_TOOL_ROUNDS
    looping = [_message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})])
               for _ in range(MAX_TOOL_ROUNDS)]
    mock_openai_client.chat.completions.create.side_effect = looping + [_message(content="ok thats enough")]
    reply = await handler.generate_reply("/draw", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "ok thats enough"
    assert len(reply.files) == MAX_FILES_PER_REPLY
    assert "tools" not in mock_openai_client.chat.completions.create.call_args[1]


async def test_same_document_is_not_built_twice_in_one_reply(handler, mock_openai_client, built_files):
    document, _ = built_files
    gate = MagicMock()
    make_pdf_call = {"format": "pdf", "title": "Proposal", "brief": "A proposal"}
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", make_pdf_call)]),
        _message(content="# Proposal\n\nBody."),
        _message(tool_calls=[_tool_call("create_document", make_pdf_call, "call_2")]),
        _message(content="its attached"),
    ]
    reply = await handler.generate_reply("/proposal as pdf", "", "Kruskal", "1", allow_files=True, file_gate=gate)
    assert len(reply.files) == 1
    assert document.await_count == 1
    assert gate.call_count == 1  # the refused call does not use up the user's file limit
    last_request = mock_openai_client.chat.completions.create.call_args[1]["messages"]
    assert "already made" in [m for m in last_request if m["role"] == "tool"][-1]["content"]


async def test_separate_diagram_is_skipped_when_a_document_was_made(handler, mock_openai_client, built_files):
    document, diagram = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        # the model asks for both at once, with the diagram listed first
        _message(tool_calls=[
            _tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"}, "call_a"),
            _tool_call("create_document", {"format": "pdf", "title": "Deploy", "brief": "Deploy steps with a diagram"}, "call_b"),
        ]),
        _message(content="# Deploy\n\nSteps."),
        _message(content="pdf attached"),
    ]
    reply = await handler.generate_reply("/deploy doc with a diagram as pdf", "", "Kruskal", "1", allow_files=True)
    assert [f.filename for f in reply.files] == ["proposal.pdf"]
    diagram.assert_not_awaited()
    results = {m["tool_call_id"]: m["content"] for m in mock_openai_client.chat.completions.create.call_args[1]["messages"] if m["role"] == "tool"}
    assert results["call_a"].startswith("skipped") and results["call_b"].startswith("done")


async def test_replies_with_files_are_not_cached(handler, mock_openai_client, built_files):
    def responses():
        return [_message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})]),
                _message(content="there u go")]
    mock_openai_client.chat.completions.create.side_effect = responses() + responses()
    await handler.generate_reply("/draw", "", "Kruskal", "1", allow_files=True)
    second = await handler.generate_reply("/draw", "", "Kruskal", "1", allow_files=True)
    assert len(second.files) == 1


async def test_budget_cap_gives_a_clear_message_and_is_not_cached(handler, mock_openai_client):
    from datetime import date
    from src.usage_guard import BudgetExceededError
    mock_openai_client.chat.completions.create.side_effect = BudgetExceededError("monthly", date(2026, 10, 14))
    reply = await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert "budget" in reply.text and "Oct 14" in reply.text
    assert handler.response_cache == {}


async def test_daily_cap_says_tomorrow(handler, mock_openai_client):
    from datetime import date
    from src.usage_guard import BudgetExceededError
    mock_openai_client.chat.completions.create.side_effect = BudgetExceededError("daily", date(2026, 10, 6))
    reply = await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert "tomorrow" in reply.text


async def test_generate_response_still_returns_plain_text(handler, fake_openai_response):
    fake_openai_response.choices[0].message.content = "yo what's good"
    assert await handler.generate_response("hello", "", "TestUser", "12345") == "yo what's good"


async def test_page_limit_reaches_the_writer_and_the_builder(handler, mock_openai_client, built_files):
    document, _ = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "CV", "brief": "A CV", "max_pages": 1})]),
        _message(content="# CV\n\nBody."),
        _message(content="done"),
    ]
    await handler.generate_reply("/one page cv as pdf", "", "Kruskal", "1", allow_files=True)
    writer_request = mock_openai_client.chat.completions.create.call_args_list[1][1]["messages"][1]["content"]
    assert "PAGE LIMIT: 1" in writer_request
    assert document.await_args[0][3] == 1


def test_chat_text_loses_stock_filler_sentences(handler):
    result = handler._format_ai_response("Dear Dr. Narin,\n\nI hope you are well. I was sick this week.")
    assert "I hope you are well" not in result
    assert "I was sick this week." in result


async def test_a_crash_while_building_a_file_does_not_lose_the_reply(handler, mock_openai_client, built_files):
    document, _ = built_files
    document.side_effect = RuntimeError("pandoc exploded")
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "X", "brief": "x"})]),
        _message(content="# X\n\nBody."),
        _message(content="couldnt make the file, something broke on my end"),
    ]
    reply = await handler.generate_reply("/make a pdf", "", "Kruskal", "1", allow_files=True)
    assert reply.files == []
    assert reply.text == "couldnt make the file, something broke on my end"
    tool_result = next(m for m in mock_openai_client.chat.completions.create.call_args[1]["messages"] if m["role"] == "tool")
    assert "internal error" in tool_result["content"]


def test_page_limit_is_read_from_plain_words():
    from src.ai_response_handler import page_limit_from
    assert page_limit_from("write a one page proposal as a pdf") == 1
    assert page_limit_from("a 2-page report on sales") == 2
    assert page_limit_from("keep it to three pages") == 3
    assert page_limit_from("single page cv") == 1
    assert page_limit_from("เขียนรายงาน 2 หน้า") == 2


def test_page_limit_is_not_invented():
    from src.ai_response_handler import page_limit_from
    assert page_limit_from("write a proposal as a pdf") is None
    assert page_limit_from("redesign the landing pages for me") is None
    assert page_limit_from("summarize page 3 of this") is None
    assert page_limit_from("") is None


async def test_page_limit_is_taken_from_the_request_when_the_model_leaves_it_out(handler, mock_openai_client, built_files):
    document, _ = built_files
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Proposal", "brief": "A proposal"})]),
        _message(content="# Proposal\n\nBody."),
        _message(content="done"),
    ]
    await handler.generate_reply("/write a one page proposal as a pdf", "", "Kruskal", "1", allow_files=True)
    assert document.await_args[0][3] == 1
    writer_request = mock_openai_client.chat.completions.create.call_args_list[1][1]["messages"][1]["content"]
    assert "PAGE LIMIT: 1" in writer_request


async def test_documents_default_to_about_a_page(handler, mock_openai_client, built_files):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Deploy", "brief": "Deploy steps"})]),
        _message(content="# Deploy\n\nBody."),
        _message(content="done"),
    ]
    await handler.generate_reply("/document our deploy process as a pdf", "", "Kruskal", "1", allow_files=True)
    writer_request = mock_openai_client.chat.completions.create.call_args_list[1][1]["messages"][1]["content"]
    assert "LENGTH: about one page" in writer_request and "PAGE LIMIT" not in writer_request


async def test_earlier_diagram_image_is_dropped_once_the_document_holds_it(handler, mock_openai_client, built_files):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})]),
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Deploy", "brief": "Deploy steps with a diagram"}, "call_2")]),
        _message(content="# Deploy\n\nSteps."),
        _message(content="pdf attached"),
    ]
    reply = await handler.generate_reply("/deploy doc with a diagram as pdf", "", "Kruskal", "1", allow_files=True)
    assert [f.filename for f in reply.files] == ["proposal.pdf"]


async def test_diagram_is_only_forced_when_the_user_asked_for_one(handler, mock_openai_client, built_files):
    def responses(brief):
        return [_message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Doc", "brief": brief})]),
                _message(content="# Doc\n\nBody."), _message(content="done")]

    def writer_request():
        return mock_openai_client.chat.completions.create.call_args_list[-2][1]["messages"][1]["content"]

    mock_openai_client.chat.completions.create.side_effect = responses("Cover the flow")
    await handler.generate_reply("/document the deploy with a diagram, as pdf", "", "Kruskal", "1", allow_files=True)
    assert "DIAGRAM: they asked for one" in writer_request()

    # the model's brief mentions a timeline and a diagram, the user did not
    mock_openai_client.chat.completions.create.side_effect = responses("Include a timeline and a diagram")
    await handler.generate_reply("/write a one page proposal as pdf", "", "Kruskal", "2", allow_files=True)
    assert "DIAGRAM: none" in writer_request()
