"""The check that decides whether a new message still belongs to the chat before it."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai_response_handler import AIResponseHandler
from src.conversation_link import ConversationLink, LINK_CHECK_TOOL, link_prompt, link_question, parse_link


def _message(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _tool_call(name, arguments):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(id="call_1", function=function)


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        h = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                              api_key="fake_key", deployment="gpt-5-mini")
    h.ai_client = mock_openai_client
    return h


# ── reading the judgment ──────────────────────────────────────────────────────

def test_a_clear_answer_is_read():
    link = parse_link('{"related": false, "topic": "They ask for the bot version."}')
    assert link == ConversationLink(False, "They ask for the bot version.")


def test_a_continuing_message_is_read():
    assert parse_link('{"related": true, "topic": "They follow up on the Go joke."}').related is True


def test_malformed_output_is_no_judgment():
    assert parse_link("not json") is None
    assert parse_link('{"topic": "no related field"}') is None
    assert parse_link('{"related": "yes"}') is None


def test_a_missing_topic_is_allowed():
    assert parse_link('{"related": true}') == ConversationLink(True, "")


def test_the_prompt_judges_meaning_not_shared_words():
    text = link_prompt("5 hours")
    assert "Shared wording alone is not a link" in text
    assert "5 hours" in text


def test_the_question_shows_the_earlier_chat_and_the_new_message():
    asked = link_question(">>> YOU: dont go is Go with a breakup arc", "/what is your version")
    assert "breakup arc" in asked
    assert 'NEW MESSAGE:\n"/what is your version"' in asked


# ── inside a reply ────────────────────────────────────────────────────────────

async def test_a_stale_unrelated_message_never_reaches_the_answer(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call(LINK_CHECK_TOOL, {
            "related": False, "topic": "They ask for the bot version."})]),
        _message(content="v3.21.0"),
    ]
    await handler.generate_reply("/what is your version", ">>> YOU: dont go is Go with a breakup arc",
                                 "Kruskal", "1", history_age=timedelta(hours=7))
    answer = mock_openai_client.chat.completions.create.call_args_list[-1][1]
    joined = "\n".join(str(m["content"]) for m in answer["messages"])
    assert "breakup arc" not in joined
    assert "what is your version" in joined


async def test_a_stale_message_that_continues_the_chat_keeps_the_history(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call(LINK_CHECK_TOOL, {
            "related": True, "topic": "They ask what Go is better than."})]),
        _message(content="still google"),
    ]
    await handler.generate_reply("so what about go then", ">>> YOU: dont go is Go with a breakup arc",
                                 "Kruskal", "1", history_age=timedelta(hours=2))
    answer = mock_openai_client.chat.completions.create.call_args_list[-1][1]
    joined = "\n".join(str(m["content"]) for m in answer["messages"])
    assert "breakup arc" in joined


async def test_a_fresh_history_skips_the_check(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.return_value = _message(content="v3.21.0")
    await handler.generate_reply("/what is your version", ">>> YOU: hey", "Kruskal", "1",
                                 history_age=timedelta(minutes=10))
    assert mock_openai_client.chat.completions.create.await_count == 1
