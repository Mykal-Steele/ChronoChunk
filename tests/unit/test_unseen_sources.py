"""A "-# Source:" line only stays when the bot really saw that link."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai_response_handler import AIResponseHandler, drop_unseen_sources

F1 = "https://en.wikipedia.org/wiki/List_of_Formula_One_World_Drivers%27_Champions"
MADE_UP = "https://www.formula1.com/en/results.html/2025/drivers.html"


def test_a_source_line_with_a_link_nobody_saw_is_removed():
    # Seen live: with no tools, it answered from memory and cited a page it never opened
    text = f"max verstappen won it\n\n-# Source: [2025 F1 Standings](<{MADE_UP}>)"
    assert drop_unseen_sources(text, []) == "max verstappen won it"


def test_a_source_line_from_a_search_result_stays():
    text = f"lando norris won it\n\n-# Source: [Champions - Wikipedia](<{F1}>)"
    results = f"=== WEB SEARCH RESULTS ===\n1. List of Formula One World Drivers' Champions\n   {F1}\n   ..."
    assert drop_unseen_sources(text, [results]) == text


def test_a_made_up_link_is_removed_even_after_a_search():
    text = f"lando norris won it\n-# Source: [Wikipedia](<{F1}>)\n-# Source: [F1](<{MADE_UP}>)"
    results = f"1. Champions\n   {F1}"
    assert drop_unseen_sources(text, [results]) == f"lando norris won it\n-# Source: [Wikipedia](<{F1}>)"


def test_a_link_they_posted_themselves_stays():
    text = f"that page says norris\n-# Source: [their link](<{MADE_UP}>)"
    assert drop_unseen_sources(text, [], given=[f"sum up {MADE_UP}"]) == text


def test_a_source_line_with_no_link_stays_only_when_something_was_looked_up():
    text = "norris won\n-# Source: formula1.com"
    assert drop_unseen_sources(text, []) == "norris won"
    assert drop_unseen_sources(text, ["1. F1 results\n   https://www.formula1.com/en/results"]) == text


def test_links_in_the_answer_itself_are_left_alone():
    text = f"check {MADE_UP} for the table"
    assert drop_unseen_sources(text, []) == text


def test_a_reply_without_source_lines_is_unchanged():
    text = "yo what's good\n-# Heads up, u used 80% of ur ai for today"
    assert drop_unseen_sources(text, []) == text


def _message(content):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        h = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                              api_key="fake_key", deployment="gpt-5-mini")
    h.ai_client = mock_openai_client
    return h


async def test_a_reply_with_no_tools_never_shows_a_made_up_source(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(f"max verstappen won it\n\n-# Source: [2025 F1 Standings](<{MADE_UP}>)"),
    ]
    reply = await handler.generate_reply("who won the last f1 championship", "", "Mint", None)
    assert reply.text == "max verstappen won it"


async def test_a_reply_with_no_tools_is_told_it_cannot_look_things_up(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [_message("not sure, might be out of date")]
    await handler.generate_reply("who won the last f1 championship", "", "Mint", None)
    system = mock_openai_client.chat.completions.create.call_args_list[-1][1]["messages"][0]["content"]
    assert "cant search the web" in system
    assert "never write a Source line" in system


def test_course_source_links_count_as_seen():
    # course_sources maps a passage number to (name, link). Only the numbers were passed at first,
    # which would have removed every course citation
    from src.ai_response_handler import seen_material
    from src.tools.base import Turn
    link = "https://kmutt.sharepoint.com/sites/csc340/slides.pdf#page=44"
    turn = Turn(query="what is a* search", username="K", history="", attached="", images=[])
    turn.course_sources = {"10": ("Search slides, page 44", link)}
    text = f"a* adds the heuristic [10]\n-# Source: [Search slides, page 44](<{link}>)"
    assert drop_unseen_sources(text, seen_material(turn, [])) == text
