"""Unit tests for AIResponseHandler — Gemini client is mocked."""
import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.ai_response_handler import _study_gap
from src.study import StudyRequest


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_response_handler.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.ai_response_handler import AIResponseHandler
        h = AIResponseHandler(
            endpoint="https://fake.openai.azure.com/openai/v1",
            api_key="fake_key",
            deployment="gpt-5-mini",
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


async def test_a_rate_limit_is_named_as_one_with_when_to_try_again(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception(
        "Error code: 429 - {'error': {'message': 'Your requests to gpt-6-luna have exceeded token rate limit.', "
        "'code': 'rate_limit_exceeded'}}")
    replies = {await handler.generate_response(f"test {number}", "", "TestUser", "12345") for number in range(4)}
    # Worded a few ways, and every one says what happened and that waiting fixes it
    assert len(replies) > 1
    for reply in replies:
        assert "rate limit" in reply and ("sec" in reply or "min" in reply)


async def test_a_spent_quota_is_not_called_a_rate_limit(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception(
        "Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': 'You exceeded your current quota'}}")
    reply = await handler.generate_response("test", "", "TestUser", "12345")
    assert "quota" in reply and "rate limit" not in reply


async def test_any_other_failure_says_it_is_on_the_bots_side(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception("connection refused")
    reply = await handler.generate_response("test", "", "TestUser", "12345")
    assert "something broke on my end" in reply and "try again" in reply


async def test_error_replies_are_kept_out_of_later_context(handler, mock_openai_client):
    from src.message_handler import MessageHandler
    for error in ("429 rate limit", "429 insufficient_quota", "connection refused"):
        mock_openai_client.chat.completions.create.side_effect = Exception(error)
        reply = await handler.generate_response(f"test {error}", "", "TestUser", "12345")
        assert any(marker in reply.lower() for marker in MessageHandler._EXCLUDED_BOT_PATTERNS), reply


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

def test_format_keeps_dollar_signs_inside_a_link(handler):
    result = handler._format_ai_response("see [the page](<https://example.test/a$b$c>)")
    assert "https://example.test/a$b$c" in result


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
    from src.tools import documents
    from src.document_builder import BuiltFile
    document = AsyncMock(return_value=BuiltFile("proposal.pdf", b"%PDF"))
    diagram = AsyncMock(return_value=BuiltFile("flow.png", b"\x89PNG"))
    monkeypatch.setattr(documents, "build_document", document)
    monkeypatch.setattr(documents, "build_diagram", diagram)
    return document, diagram


def test_the_debug_log_gets_the_context_and_not_just_the_one_line(handler):
    from src.ai_response_handler import Reply
    from src.tools import Turn
    handler.reply_log = MagicMock()
    request = Turn("what dev tool", "Kruskal", "Alex: earlier", "file: notes.md", ["img"],
                   user_id="42", channel_id="99", routing={"course": False})
    request.course_sources = {"10": ("Week 9", "https://example.test/9")}
    request.calls = 2
    request.cost = 0.0123
    handler._log_reply(request, Reply("it is Teams"))
    fields = handler.reply_log.record.call_args.kwargs
    assert fields["query"] == "what dev tool"
    assert fields["conversation_history"] == "Alex: earlier"
    assert fields["attached_context"] == "file: notes.md"
    assert fields["channel_id"] == "99" and fields["user_id"] == "42"
    assert fields["routing"] == {"course": False}
    assert fields["sources"]["10"][0] == "Week 9"
    assert fields["model_calls"] == 2 and fields["cost"] == 0.0123


async def test_each_reply_logs_what_it_read_and_what_it_cost(handler, mock_openai_client, caplog):
    from types import SimpleNamespace
    response = _message(content="its a cat")
    response.usage = SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0, prompt_tokens_details=None)
    mock_openai_client.chat.completions.create.side_effect = [response]
    attached = "=== MESSAGE THEY REPLIED TO (sent by Alex) ===\nfile: notes.md\n--- start of notes.md ---\nhi\n=== END ==="
    with caplog.at_level("INFO", logger="src.ai_response_handler"):
        await handler.generate_reply("/what is this", "", "Kruskal", "1", attached_context=attached,
                                     images=["data:image/png;base64,AAAA"])
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Reply for Kruskal"))
    assert "read 1 images and 1 files" in line
    assert "1 model calls, about $0.2500, sent 0 files" in line
    assert re.search(r"took \d+\.\ds \(\d+\.\ds waiting for the model, \d+\.\ds in tools\)$", line)


async def test_tools_are_only_offered_when_files_are_allowed(handler, mock_openai_client):
    await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert "tools" not in mock_openai_client.chat.completions.create.call_args[1]
    await handler.generate_reply("/make a pdf", "", "Kruskal", "1", allow_files=True)
    offered = mock_openai_client.chat.completions.create.call_args[1]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {"create_document", "create_diagram", "open_url"}


# ── the web ───────────────────────────────────────────────────────────────────

@pytest.fixture
def web(monkeypatch):
    """Replace the real search and page reader, and pretend a search key is configured."""
    from config.config import Config
    from src.tools import web as web_tools_module
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-test")
    search = AsyncMock(return_value='=== WEB SEARCH RESULTS for "x" ===\n1. Result\n   https://a.example/1\n=== END ===')
    page = AsyncMock(return_value="=== WEB PAGE https://a.example/1 ===\nthe page text\n=== END ===")
    monkeypatch.setattr(web_tools_module, "search_web", search)
    monkeypatch.setattr(web_tools_module, "read_page", page)
    return search, page


async def test_search_is_offered_only_when_its_key_is_set(handler, mock_openai_client, web):
    await handler.generate_reply("/whats new", "", "Kruskal", "1", allow_files=True)
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert {tool["function"]["name"] for tool in call["tools"]} == {
        "create_document", "create_diagram", "open_url", "web_search"}
    system = call["messages"][0]["content"]
    assert "web_search" in system and "today is" in system


async def test_system_prompt_leaves_search_out_without_a_key(handler, mock_openai_client):
    await handler.generate_reply("/read this link", "", "Kruskal", "1", allow_files=True)
    system = mock_openai_client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "open_url" in system and "web_search" not in system


async def test_search_results_go_back_to_the_model(handler, mock_openai_client, web):
    search, _ = web
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("web_search", {"query": "latest python", "time_range": "week"})]),
        _message(content="its 3.14 <https://a.example/1>"),
    ]
    reply = await handler.generate_reply("/latest python version?", "", "Kruskal", "1", allow_files=True)
    search.assert_awaited_once_with("latest python", None, "week")
    assert reply.text == "its 3.14 <https://a.example/1>"
    tool_turn = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]
    assert tool_turn["role"] == "tool" and "WEB SEARCH RESULTS" in tool_turn["content"]


async def test_a_link_is_opened_and_its_text_goes_back_to_the_model(handler, mock_openai_client, web):
    _, page = web
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("open_url", {"url": "https://a.example/1"})]),
        _message(content="it says the page text"),
    ]
    await handler.generate_reply("/sum up https://a.example/1", "", "Kruskal", "1", allow_files=True)
    page.assert_awaited_once_with("https://a.example/1")
    assert "the page text" in mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]


async def test_answers_from_the_web_are_not_cached(handler, mock_openai_client, web):
    def answers():
        return [_message(tool_calls=[_tool_call("web_search", {"query": "score"})]), _message(content="2-1")]
    mock_openai_client.chat.completions.create.side_effect = answers() + answers()
    await handler.generate_reply("/score?", "", "Kruskal", "1", allow_files=True)
    await handler.generate_reply("/score?", "", "Kruskal", "1", allow_files=True)
    assert mock_openai_client.chat.completions.create.call_count == 4


async def test_web_lookups_per_reply_are_limited(handler, mock_openai_client, web):
    from src.tools.web import MAX_WEB_CALLS_PER_REPLY
    search, _ = web
    many = [_tool_call("web_search", {"query": f"q{n}"}, call_id=f"call_{n}") for n in range(MAX_WEB_CALLS_PER_REPLY + 2)]
    mock_openai_client.chat.completions.create.side_effect = [_message(tool_calls=many), _message(content="done")]
    await handler.generate_reply("/search a lot", "", "Kruskal", "1", allow_files=True)
    assert search.await_count == MAX_WEB_CALLS_PER_REPLY
    last = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]
    assert last.startswith("error: this reply has used all its web lookups")


async def test_a_failed_lookup_is_reported_to_the_model(handler, mock_openai_client, web):
    from src.web_tools import WebError
    _, page = web
    page.side_effect = WebError("that address is not on the public internet")
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("open_url", {"url": "http://localhost/"})]),
        _message(content="cant open that one"),
    ]
    reply = await handler.generate_reply("/open http://localhost/", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "cant open that one"
    last = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]
    assert last == "error: that address is not on the public internet"


async def test_a_crash_in_a_lookup_does_not_lose_the_reply(handler, mock_openai_client, web):
    search, _ = web
    search.side_effect = RuntimeError("boom")
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("web_search", {"query": "x"})]),
        _message(content="search broke"),
    ]
    reply = await handler.generate_reply("/search x", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "search broke"


async def test_the_document_writer_sees_what_was_looked_up(handler, mock_openai_client, built_files, web):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("open_url", {"url": "https://a.example/1"})]),
        _message(tool_calls=[_tool_call("create_document", {"format": "pdf", "title": "Summary", "brief": "Sum it up"})]),
        _message(content="# Summary\n\nText."),
        _message(content="here"),
    ]
    reply = await handler.generate_reply("/make a pdf of https://a.example/1", "", "Kruskal", "1", allow_files=True)
    assert len(reply.files) == 1
    writer_call = mock_openai_client.chat.completions.create.call_args_list[2][1]
    assert "LOOKED UP FOR THIS REQUEST" in writer_call["messages"][1]["content"]
    assert "the page text" in writer_call["messages"][1]["content"]


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
    from src.document_builder import BuiltFile, DiagramSyntaxError
    _, diagram = built_files
    diagram.side_effect = [DiagramSyntaxError("the mermaid source has a syntax error: Parse error on line 2"),
                           BuiltFile("flow.png", b"\x89PNG")]
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A[oops"})]),
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"}, "call_2")]),
        _message(content="fixed it"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "fixed it" and len(reply.files) == 1
    calls = mock_openai_client.chat.completions.create.call_args_list
    tool_result = next(m for m in calls[1][1]["messages"] if m["role"] == "tool")["content"]
    assert "syntax error" in tool_result
    assert "Line 2 of your source is: A[oops" in tool_result
    # the model has to draw it again, it cannot answer in text until a diagram has rendered
    assert "tool_choice" not in calls[0][1]
    assert calls[1][1]["tool_choice"]["function"]["name"] == "create_diagram"
    assert "tool_choice" not in calls[2][1]


async def test_diagram_that_never_parses_ends_with_an_honest_message(handler, mock_openai_client, built_files):
    from src.ai_response_handler import MAX_TOOL_ROUNDS
    from src.document_builder import DiagramSyntaxError
    _, diagram = built_files
    diagram.side_effect = DiagramSyntaxError("the mermaid source has a syntax error: Parse error on line 2")
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A[oops"}, f"call_{n}")])
        for n in range(MAX_TOOL_ROUNDS)
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True)
    assert reply.files == [] and "couldnt get that diagram to render" in reply.text
    # the model is not asked for a closing line, it would say the diagram was sent
    assert mock_openai_client.chat.completions.create.call_count == MAX_TOOL_ROUNDS


async def test_a_diagram_that_failed_to_parse_does_not_use_up_the_file_limit(handler, mock_openai_client, built_files):
    from src.document_builder import BuiltFile, DiagramSyntaxError
    _, diagram = built_files
    diagram.side_effect = [DiagramSyntaxError("the mermaid source has a syntax error: Parse error on line 2"),
                           BuiltFile("flow.png", b"\x89PNG")]
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A[oops"})]),
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"}, "call_2")]),
        _message(content="fixed it"),
    ]
    counted = []
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True,
                                         file_gate=lambda: counted.append(1))
    assert len(reply.files) == 1 and len(counted) == 1


async def test_diagram_over_the_file_limit_is_not_attached(handler, mock_openai_client, built_files):
    from src.exceptions import RateLimitError

    def gate():
        raise RateLimitError(retry_after=7200)

    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})]),
        _message(content="u hit ur file limit for today"),
    ]
    reply = await handler.generate_reply("/draw the flow", "", "Kruskal", "1", allow_files=True, file_gate=gate)
    assert reply.files == []
    tool_result = next(m for m in mock_openai_client.chat.completions.create.call_args[1]["messages"] if m["role"] == "tool")
    assert "limit" in tool_result["content"]


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
    from src.ai_response_handler import MAX_TOOL_ROUNDS
    from src.tools.documents import MAX_FILES_PER_REPLY
    looping = [_message(tool_calls=[_tool_call("create_diagram", {"title": "Flow", "mermaid": "flowchart TD\n  A --> B"})])
               for _ in range(MAX_TOOL_ROUNDS)]
    mock_openai_client.chat.completions.create.side_effect = looping + [_message(content="ok thats enough")]
    reply = await handler.generate_reply("/draw", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "ok thats enough"
    assert len(reply.files) == min(MAX_TOOL_ROUNDS, MAX_FILES_PER_REPLY)
    assert "tools" not in mock_openai_client.chat.completions.create.call_args[1]


async def test_a_request_for_five_files_gets_enough_rounds_to_make_them(handler, mock_openai_client, built_files):
    from src.tools.documents import MAX_FILES_PER_REPLY
    _, diagram = built_files
    calls = [
        _message(tool_calls=[_tool_call(
            "create_diagram",
            {"title": f"Flow {n}", "mermaid": "flowchart TD\n  A --> B"},
            f"call_{n}",
        )])
        for n in range(MAX_FILES_PER_REPLY)
    ]
    mock_openai_client.chat.completions.create.side_effect = calls + [_message(content="five diagrams attached")]
    reply = await handler.generate_reply("/make five diagrams", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "five diagrams attached"
    assert len(reply.files) == MAX_FILES_PER_REPLY
    assert diagram.await_count == MAX_FILES_PER_REPLY


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
    from src.tools.documents import page_limit_from
    assert page_limit_from("write a one page proposal as a pdf") == 1
    assert page_limit_from("a 2-page report on sales") == 2
    assert page_limit_from("keep it to three pages") == 3
    assert page_limit_from("single page cv") == 1
    assert page_limit_from("เขียนรายงาน 2 หน้า") == 2


def test_page_limit_is_not_invented():
    from src.tools.documents import page_limit_from
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


# ── per-user limits ───────────────────────────────────────────────────────────

async def test_web_lookups_stop_at_the_persons_daily_limit(handler, mock_openai_client, web):
    from src.exceptions import RateLimitError
    search, _ = web

    def gate():
        raise RateLimitError(retry_after=5 * 3600)
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("web_search", {"query": "news"})]),
        _message(content="u are out of lookups today"),
    ]
    reply = await handler.generate_reply("/search news", "", "Kruskal", "1", allow_files=True, web_gate=gate)
    assert reply.text == "u are out of lookups today"
    search.assert_not_awaited()
    told = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]
    assert "used all their web lookups for today" in told and "about 5 hours" in told


async def test_a_person_over_their_ai_limit_is_told_when_it_resets(handler, mock_openai_client):
    from datetime import date, datetime, timedelta, timezone
    from src.usage_guard import BudgetExceededError
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    mock_openai_client.chat.completions.create.side_effect = BudgetExceededError("user_daily", tomorrow)
    reply = await handler.generate_reply("/yo", "", "Kruskal", "1")
    assert reply.text.startswith("u used up ur ai for today, back in about ") and "/usage" in reply.text
    mock_openai_client.chat.completions.create.side_effect = BudgetExceededError("user_weekly", date(2026, 10, 15))
    reply = await handler.generate_reply("/yo again", "", "Kruskal", "1")
    assert "limit for the week" in reply.text and "Oct 15" in reply.text


async def test_the_reply_carries_a_heads_up_once_near_the_daily_limit(handler, mock_openai_client):
    from src import usage_context
    from src.usage_guard import get_usage_guard
    guard = get_usage_guard()
    guard.user_daily_budget = 1.0
    usage_context._current.set(("77", "chat"))
    guard.record(_message().usage or __import__("types").SimpleNamespace(prompt_tokens=0, completion_tokens=450_000,
                                                                            prompt_tokens_details=None))
    first = await handler.generate_reply("/yo", "", "Kruskal", "77")
    second = await handler.generate_reply("/yo again", "", "Kruskal", "77")
    usage_context._current.set(("", "chat"))
    assert "80% of ur ai for today" in first.text and first.text.splitlines()[-1].startswith("-# ")
    assert "80%" not in second.text



# ── how a reply is typed ──────────────────────────────────────────────────────

LONG = ("a linked list is a chain of nodes, where each node holds a value and a pointer to the next one. "
        "the nodes dont sit next to each other in memory like an array. to reach item number n you walk from the "
        "first node, so lookup is O(n). adding or removing a node is O(1) once you are at that spot.")


def test_day_to_day_talk_is_left_in_small_letters(handler):
    assert handler._format_ai_response("yo not much. what's good with u?") == "yo not much. what's good with u?"
    assert handler._format_ai_response("nah. thats cooked\nu good tho?") == "nah. thats cooked\nu good tho?"


def test_capitals_the_model_added_to_day_to_day_talk_are_taken_off(handler):
    assert handler._format_ai_response("Yo, not much. What's good with u?") == "yo, not much. what's good with u?"
    assert handler._format_ai_response("Bro what. It is an ai class\nWrong class my guy") == "bro what. it is an ai class\nwrong class my guy"


def test_taking_capitals_off_leaves_real_capitals_alone(handler):
    assert handler._format_ai_response("Nah. I think TCP is fine. O(n) is slow. I'm sure") == "nah. I think TCP is fine. O(n) is slow. I'm sure"
    assert handler._format_ai_response("Its on the syllabus.\n-# Source: [CSC345 syllabus](<https://a.example/s.pdf>)") == (
        "its on the syllabus.\n-# Source: [CSC345 syllabus](<https://a.example/s.pdf>)")
    assert handler._format_ai_response("Use `Foo.bar()`. FastAPI does that. iPhone too") == "use `Foo.bar()`. FastAPI does that. iPhone too"


def test_a_long_explanation_gets_a_capital_at_every_sentence(handler):
    from src.ai_response_handler import CAPITALS_MIN_CHARS
    assert len(LONG) >= CAPITALS_MIN_CHARS
    sent = handler._format_ai_response(LONG)
    assert sent.startswith("A linked list is a chain of nodes")
    assert ". The nodes dont sit" in sent and ". To reach item" in sent and ". Adding or removing" in sent
    assert sent.lower() == LONG.lower()


def test_the_source_line_and_code_do_not_make_a_short_answer_long(handler):
    from src.ai_response_handler import CAPITALS_MIN_CHARS
    link = "https://uni.example/sites/" + "x" * CAPITALS_MIN_CHARS + "/deck.pdf"
    reply = f"the syllabus only lists exam 1. ask the teacher for exam 2.\n-# Source: [CSC345 syllabus](<{link}>)"
    assert handler._format_ai_response(reply) == reply
    code = "here, look\n```js\n" + "items.map(f)\n" * 40 + "```\nok !!!"
    assert handler._format_ai_response(code).startswith("here, look") and handler._format_ai_response(code).endswith("ok !!!")


def test_every_sentence_starts_with_a_capital_and_nothing_else_changes():
    from src.ai_response_handler import capitalize_sentences
    assert capitalize_sentences("nah. i think john is right?? yeah") == "Nah. I think john is right?? Yeah"
    assert capitalize_sentences("first idea\nsecond idea") == "First idea\nSecond idea"
    assert capitalize_sentences("**bold** start. *wow* ok\n- one\n-# source: x") == "**Bold** start. *Wow* ok\n- One\n-# Source: x"


def test_capitals_leave_trailing_thoughts_short_forms_and_brand_names_alone():
    from src.ai_response_handler import capitalize_sentences
    assert capitalize_sentences("idk... maybe. lol") == "Idk... maybe. Lol"
    assert capitalize_sentences("some of them, e.g. this one, on p. 4 vs. that") == "Some of them, e.g. this one, on p. 4 vs. that"
    assert capitalize_sentences("ok. iPhone is mid") == "Ok. iPhone is mid"


def test_capitals_never_touch_code_links_or_addresses():
    from src.ai_response_handler import capitalize_sentences
    text = "`getInstance()` makes it. `foo` too. [the deck. page 4](<https://a.example/x.pdf>). https://a.example/b. done"
    assert capitalize_sentences(text) == text[:-4] + "Done"


def test_a_star_after_a_single_letter_does_not_open_italics(handler):
    assert handler._format_ai_response("for A*, the score is **f(n)**. see *this* too") == "for A\\*, the score is **f(n)**. see *this* too"


def test_an_answer_the_model_wrote_twice_is_sent_once(handler):
    answer = "in csc319, singleton is a creational pattern that ensures a class has one instance and a shared access point. "
    doubled = answer + "it uses a private constructor. burugburu\n" + answer + "it uses a private constructor and a static method."
    assert handler._format_ai_response(doubled) == answer + "it uses a private constructor and a static method."
    once = answer + "it uses a private constructor."
    assert handler._format_ai_response(once) == once


# What gpt-6-luna really returned for "/list all the ms teams u have", each a first copy and the copy kept
_TEAMS = "CSC102, CSC219, CSC302, CSC319, CSC340, CSC345, CSC491, CSC535, and LNG320"
_TEAM_LIST = ("- CSC102 Introduction To Programming\n- CSC219 Modern Java\n- CSC319 Object-Oriented Design\n"
              "- CSC340 Artificial Intelligence\n- CSC345 Data Science\n- LNG320 Content-based Language Learning\n\n")
_DOUBLED = {
    "said again in other words": (
        f"i have these teams’ course files and recordings: {_TEAMS}. i dont have the teams channels though.\n",
        f"i have the course files and recordings for {_TEAMS}. i dont have their channels though."),
    "a list": (
        f"i have these teams:\n\n{_TEAM_LIST}i have their files and lecture recordings, not their channels or posts.\n",
        f"i have these teams:\n\n{_TEAM_LIST}i have their files and lecture recordings, not their channels."),
    "a list without its first line": (
        f"the teams i have course files and recordings for are:\n{_TEAM_LIST}i have their files and recordings, "
        "not the teams’ channels or posts.\n",
        f"{_TEAM_LIST}i have their files and recordings, not their channels or posts."),
    "stray text between the copies": (
        f"i have these course teams: {_TEAMS}. i have their files and lecture recordings, not the channels. \n"
        "આર્ચassistantლება \n-vesm\n-# Source: [CSC102 course material](<>)\n",
        f"i have {_TEAMS}. i have the course files and lecture recordings, but not the teams’ channels."),
    "made-up small print after the first copy": (
        f"i have files and lecture recordings from these teams: {_TEAMS}. i don’t have their channels or posts. \n"
        "-# newest material is synced through 13 Oct 2026 at 14:06 Bangkok time. \n-# source: course team data\n",
        f"i have files and lecture recordings from {_TEAMS}. i don’t have the teams’ channels or posts."),
}


@pytest.mark.parametrize("case", _DOUBLED)
def test_a_second_copy_on_a_new_line_is_the_one_sent(handler, case):
    first, second = _DOUBLED[case]
    assert handler._format_ai_response(first + second) == handler._format_ai_response(second)


@pytest.mark.parametrize("reply", [
    "csc340 has its lecture on tuesday at 9 in the morning and the slides go up the same day\n"
    "csc345 has its lecture on tuesday at 1 in the afternoon and the slides go up the day after",
    "the teams i have are:\n- CSC102 Introduction To Programming\n- CSC219 Modern Java\n- CSC319 Object-Oriented Design\n"
    "- CSC340 Artificial Intelligence\n\ni have their files and lecture recordings, not their channels",
    "tcp sets up a connection first, numbers every segment and sends a lost one again, so the data arrives whole and in order.\n"
    "udp just sends datagrams with no connection and no resend, which is why games and calls use it.\n"
    "so tcp for whole and in order, udp for fast",
    "yeah i got u\nyeah i got u bro",
])
def test_a_reply_with_lines_that_look_alike_is_not_cut(handler, reply):
    assert handler._format_ai_response(reply).lower() == reply.lower()


def test_none_of_the_example_replies_is_cut(handler):
    from config.ai_config import load_voice_examples
    from src.ai_response_handler import _without_repeat
    replies = [example.reply for example in load_voice_examples()]
    assert len(replies) > 10
    for reply in replies:
        assert _without_repeat(reply) == reply


def test_a_reply_that_uses_its_opening_words_again_is_not_cut(handler):
    opening = "the singleton pattern in csc319 is about one instance of a class"
    reply = (f"{opening}, and the slides spend three pages on it with a diagram of the class and its members.\n"
             "they also cover the observer pattern, the decorator pattern and the factory method in the weeks after.\n"
             f"so if the exam asks, {opening} and nothing more than that.")
    assert handler._format_ai_response(reply).lower() == reply.lower()


def test_a_link_the_model_changed_is_put_back():
    from src.ai_response_handler import repair_links
    real = "https://uni.example/sites/1-2569_CSC340ARTIFICIALINTELLIGENCECSC209PreCSC210Pre/Shared%20Documents/rec.mp4"
    other = "https://uni.example/sites/1-2569_CSC340ARTIFICIALINTELLIGENCECSC209PreCSC210Pre/Class%20Materials/6th--Search.pdf"
    sources = [f"=== COURSE MATERIAL ===\nlink: {real}\ntext\nlink: {other}\n=== END ==="]
    changed = real.replace("CSC210Pre", "")
    reply = f"-# Source: [Search slides, page 44](<{other}>), [AI lecture](<{changed}>)."
    assert repair_links(reply, sources) == f"-# Source: [Search slides, page 44](<{other}>), [AI lecture](<{real}>)."


def test_links_from_somewhere_else_are_left_alone():
    from src.ai_response_handler import repair_links
    sources = ["link: https://uni.example/sites/a/deck.pdf"]
    reply = "see <https://docs.python.org/3/library/re.html> and https://uni.example/sites/a/deck.pdf."
    assert repair_links(reply, sources) == reply
    assert repair_links(reply, []) == reply


def test_a_link_to_another_page_of_the_same_site_is_not_swapped():
    from src.ai_response_handler import repair_links
    sources = ["link: https://docs.python.org/3/library/re.html"]
    reply = "that one is in [the os docs](<https://docs.python.org/3/library/os.html>)"
    assert repair_links(reply, sources) == reply


async def test_every_reply_is_told_the_current_date_and_time_in_bangkok(handler, mock_openai_client, monkeypatch):
    from datetime import datetime, timezone
    import src.ai_response_handler as module
    # 14:05 UTC is 21:05 in Bangkok
    monkeypatch.setattr(module, "_now", lambda: datetime(2026, 10, 13, 14, 5, tzinfo=timezone.utc))
    await handler.generate_response("what day is it", "", "TestUser", "12345")
    call = mock_openai_client.chat.completions.create.call_args[1]
    asked = call["messages"][1]["content"]
    assert "(right now it is Tuesday 13 Oct 2026, 21:05 Bangkok time)" in asked
    # It goes with the message, so the system prompt stays the same from one minute to the next
    assert "right now it is" not in call["messages"][0]["content"]
    assert asked.rstrip().endswith('[TestUser]: "what day is it"')


async def test_a_kept_reply_is_only_served_for_a_few_minutes(handler, mock_openai_client):
    # Every prompt carries the time, so yesterday's answer to "what day is it" is wrong today
    assert handler.cache_seconds == 300
    await handler.generate_response("what day is it", "", "TestUser", "12345")
    await handler.generate_response("what day is it", "", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.call_count == 1
    handler.cache_seconds = 0
    await handler.generate_response("what day is it", "", "TestUser", "12345")
    assert mock_openai_client.chat.completions.create.call_count == 2


async def test_an_error_that_only_has_the_letters_rate_in_it_is_not_called_a_rate_limit(handler, mock_openai_client):
    for error in ("Failed to generate a completion", "invalid separate tool_call arguments", "moderate load, connection reset"):
        mock_openai_client.chat.completions.create.side_effect = Exception(error)
        reply = await handler.generate_response(f"test {error}", "", "TestUser", "12345")
        assert "something broke on my end" in reply and "rate limit" not in reply, error
    for error in ("Error code: 429 - too many", "Rate limit reached for requests", "rate-limited, retry later", "Too Many Requests"):
        mock_openai_client.chat.completions.create.side_effect = Exception(error)
        reply = await handler.generate_response(f"test {error}", "", "TestUser", "12345")
        assert "rate limit" in reply, error


def test_a_source_line_the_model_wrote_at_the_end_of_a_sentence_goes_on_a_line_of_its_own(handler):
    reply = "microsoft has not rewritten windows in rust. -# Source: [Windows Latest](<https://www.windowslatest.com/a>)"
    assert handler._format_ai_response(reply) == (
        "microsoft has not rewritten windows in rust.\n-# Source: [Windows Latest](<https://www.windowslatest.com/a>)")


def test_a_line_that_is_only_a_stray_token_is_taken_out(handler):
    assert handler._format_ai_response("phase two is the first round of models\n^(finish)\nsee you") == (
        "phase two is the first round of models\nsee you")


def test_a_stray_token_line_inside_code_is_kept(handler):
    reply = "the pattern is\n```\n^(finish)\n```"
    assert handler._format_ai_response(reply) == reply


# ── study requests ────────────────────────────────────────────────────────────

def _quiz(number):
    """One numbered multiple-choice question, different enough that no repeat check drops it."""
    return f"{number}. What does topic {number} do in case {number}?\nA. answer {number}a\nB. answer {number}b\nC. answer {number}c\nD. answer {number}d"


def _quiz_reply(first, last):
    questions = "\n\n".join(_quiz(number) for number in range(first, last + 1))
    key = "\n".join(f"{number}. A. because case {number} says so." for number in range(first, last + 1))
    return questions + "\n\n## Answer key\n" + key


async def test_a_study_request_gets_the_study_prompt_and_room(handler, mock_openai_client):
    from config.config import Config
    mock_openai_client.chat.completions.create.return_value = _message(content=_quiz_reply(1, 30))
    await handler.generate_reply("make 30 multiple choice for my ai exam", "", "Kruskal", "1")
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert call["max_completion_tokens"] == Config.AI_STUDY_MAX_TOKENS
    assert call["reasoning_effort"] == Config.AI_STUDY_REASONING_EFFORT
    system = call["messages"][0]["content"]
    assert "STUDY MATERIAL" in system and "exactly 30 questions" in system
    assert "ChronoChunk" in system  # the personality is still there


async def test_normal_chat_does_not_get_the_study_rules(handler, mock_openai_client):
    from config.config import Config
    await handler.generate_reply("yo", "", "Kruskal", "1")
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert call["max_completion_tokens"] == Config.AI_CHAT_MAX_TOKENS
    assert "STUDY MATERIAL" not in call["messages"][0]["content"]


async def test_a_short_answer_request_asks_for_model_answers_not_options(handler, mock_openai_client):
    questions = "\n".join(f"{number}. Explain topic {number}." for number in range(1, 11))
    answers = "\n".join(f"{number}. It is the key point for topic {number}." for number in range(1, 11))
    mock_openai_client.chat.completions.create.return_value = _message(
        content=questions + "\n\n## Model answers\n" + answers)
    await handler.generate_reply("make 10 short answer questions on deadlocks", "", "Kruskal", "1")
    system = mock_openai_client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "short-answer theory practice" in system and "No multiple-choice options" in system
    assert "Model answers" in system


def test_the_study_gap_fills_holes_instead_of_counting_them():
    text = "1. One\n2. Two\n4. Four\n\n## Answer key\n1. A\n2. B\n4. D"
    gap = _study_gap(text, StudyRequest("quiz", 5))
    assert "Write only the missing questions: 3, 5" in gap
    assert "Continue from question 4" not in gap


def test_the_short_answer_gap_asks_for_model_answers_without_letters():
    text = "1. Explain one.\n2. Explain two.\n\n## Model answers\n1. One is ..."
    gap = _study_gap(text, StudyRequest("short", 2))
    assert "model answers are missing entries" in gap
    assert "letter" not in gap


def test_a_study_guide_ends_on_its_completion_marker():
    done = "Topic one.\n\n=== GUIDE COMPLETE ==="
    assert _study_gap(done, StudyRequest("guide", 0)) == ""
    gap = _study_gap("Topic one.", StudyRequest("guide", 0))
    assert "Continue the study guide" in gap and "=== GUIDE COMPLETE ===" in gap


async def test_a_guide_completion_marker_is_not_sent(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.return_value = _message(
        content="Topic one.\n\n=== GUIDE COMPLETE ===")
    reply = await handler.generate_reply("make a study guide for ai", "", "Kruskal", "1")
    assert "GUIDE COMPLETE" not in reply.text
    assert "topic one." in reply.text


async def test_a_study_reply_that_stops_short_is_continued_without_repeating(handler, mock_openai_client):
    full_key = "\n".join(f"{number}. A. because case {number} says so." for number in range(1, 31))
    mock_openai_client.chat.completions.create.side_effect = [
        _message(content=_quiz_reply(1, 12)),
        _message(content=_quiz_reply(13, 30)),
        _message(content="## Answer key\n" + full_key),
    ]
    reply = await handler.generate_reply("make 30 mcqs for ai", "", "Kruskal", "1")
    calls = mock_openai_client.chat.completions.create.call_args_list
    assert len(calls) == 3
    assert "You wrote 12 of the 30 questions" in calls[1][1]["messages"][-1]["content"]
    assert "full answer key" in calls[2][1]["messages"][-1]["content"]
    last_assistant = [message for message in calls[2][1]["messages"] if message["role"] == "assistant"][-1]["content"]
    assert "13. What does topic 13" in last_assistant
    assert "1. What does topic 1" not in last_assistant
    assert "13. What does topic 13" in reply.text and "30. What does topic 30" in reply.text
    assert reply.text.count("## Answer key") == 1
    assert "1. A. Because case 1" in reply.text and "30. A. Because case 30" in reply.text


async def test_a_complete_study_reply_is_not_continued(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.return_value = _message(content=_quiz_reply(1, 30))
    reply = await handler.generate_reply("make 30 mcqs for ai", "", "Kruskal", "1")
    assert mock_openai_client.chat.completions.create.call_count == 1
    assert "30. What does topic 30" in reply.text


async def test_a_study_reply_is_not_served_from_the_plain_chat_cache(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.return_value = _message(content=_quiz_reply(1, 10))
    query = "make 10 mcqs for ai"
    await handler.generate_reply(query, "", "Kruskal", "1")
    await handler.generate_reply(query, "", "Kruskal", "1")
    assert mock_openai_client.chat.completions.create.call_count == 2


async def test_a_detail_request_gets_room_without_a_study_shape(handler, mock_openai_client):
    from config.config import Config
    mock_openai_client.chat.completions.create.return_value = _message(content="a full answer")
    await handler.generate_reply("explain deadlock in detail", "", "Kruskal", "1")
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert call["max_completion_tokens"] == Config.AI_DETAIL_MAX_TOKENS
    system = call["messages"][0]["content"]
    assert "FULL ANSWER" in system and "STUDY MATERIAL" not in system
