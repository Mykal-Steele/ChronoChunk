"""
The chat model's side of Uniloom: one small tool that loads the rest, reads that run
straight away, and changes that wait for the person's OK (docs/uniloom-plan.md).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools import Turn
from src.tools.uniloom import OpenUniloom, UniloomTool, mentions_uniloom
from src.uniloom.service import NotConnected, ToolSpec
from tests.unit.test_ai_response_handler import _message, _tool_call

SPECS = [
    ToolSpec("list_projects", "The projects you belong to", {"type": "object", "properties": {}}, True),
    ToolSpec("save_item", "Create or update an item",
             {"type": "object", "properties": {"title": {"type": "string"},
                                               "parent": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
              "required": ["title"]}, False),
]


@pytest.fixture
def uniloom():
    service = MagicMock()
    service.enabled = True
    service.is_connected = MagicMock(return_value=True)
    service.tools = AsyncMock(return_value=(SPECS, "Keys, not ids."))
    service.call = AsyncMock(return_value="TG: Tracker")
    return service


def _turn(user_id="760"):
    return Turn("whats on my board", "Oakar", "", "", [], user_id=user_id)


async def test_someone_not_connected_gets_the_connect_button(uniloom):
    uniloom.is_connected.return_value = False
    turn = _turn()
    result = await OpenUniloom(uniloom).run({}, turn)
    assert turn.uniloom.connect is True
    assert "Connect Uniloom" in result.text
    uniloom.tools.assert_not_awaited()


async def test_loading_adds_the_tools_and_hands_over_how_to_use_them(uniloom):
    turn = _turn()
    result = await OpenUniloom(uniloom).run({}, turn)
    assert [tool.name for tool in turn.added_tools] == ["uniloom_list_projects", "uniloom_save_item"]
    assert "Keys, not ids." in result.text
    uniloom.tools.assert_awaited_once_with("760")


async def test_loading_twice_adds_nothing_new(uniloom):
    turn = _turn()
    tool = OpenUniloom(uniloom)
    await tool.run({}, turn)
    turn.added_tools.clear()
    await tool.run({}, turn)
    assert turn.added_tools == []


async def test_a_message_from_no_one_cannot_use_anyones_uniloom(uniloom):
    turn = _turn(user_id=None)
    result = await OpenUniloom(uniloom).run({}, turn)
    assert turn.added_tools == [] and "cant" in result.text


def test_it_is_only_offered_when_set_up(uniloom):
    assert OpenUniloom(uniloom).available()
    uniloom.enabled = False
    assert not OpenUniloom(uniloom).available()


async def test_a_read_runs_with_the_asker_and_its_result_reaches_the_model(uniloom):
    turn = _turn("222")
    result = await UniloomTool(SPECS[0], uniloom).run({}, turn)
    uniloom.call.assert_awaited_once_with("222", "list_projects", {})
    assert result.text == "TG: Tracker" and turn.uniloom.used


async def test_a_change_waits_for_the_person(uniloom):
    turn = _turn()
    result = await UniloomTool(SPECS[1], uniloom).run({"title": "Login page", "parent": "TG-4"}, turn)
    uniloom.call.assert_not_awaited()
    (write,) = turn.uniloom.writes
    assert write.tool == "save_item" and write.arguments == {"title": "Login page", "parent": "TG-4"}
    assert "Login page" in write.summary and "TG-4" in write.summary
    assert "not done yet" in result.text


async def test_a_reply_can_only_queue_so_many_changes(uniloom):
    turn = _turn()
    tool = UniloomTool(SPECS[1], uniloom)
    for n in range(30):
        await tool.run({"title": f"t{n}"}, turn)
    assert len(turn.uniloom.writes) == 10


async def test_not_connected_any_more_is_said_plainly(uniloom):
    uniloom.call.side_effect = NotConnected(expired=True)
    turn = _turn()
    result = await UniloomTool(SPECS[0], uniloom).run({}, turn)
    assert turn.uniloom.connect and "ran out" in result.text


def test_the_schema_the_model_gets_is_the_gemini_safe_one(uniloom):
    definition = UniloomTool(SPECS[1], uniloom).definition["function"]
    assert definition["name"] == "uniloom_save_item"
    assert definition["parameters"]["properties"]["parent"] == {"type": "string", "description": "null clears it"}


@pytest.mark.parametrize("text,expected", [
    ("whats left on my uniloom board", True),
    ("move TG-12 to in progress", True),
    ("what is covid-19", False),
    ("hey whats up", False),
])
def test_a_message_about_uniloom_is_spotted(text, expected):
    assert mentions_uniloom(text) is expected


# ── in the reply loop ────────────────────────────────────────────────────────

@pytest.fixture
def handler(mock_openai_client, uniloom):
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.ai_response_handler import AIResponseHandler
        h = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1", api_key="fake_key",
                              deployment="gpt-5-mini")
    h.ai_client = mock_openai_client
    h.tools = [OpenUniloom(uniloom)]
    return h


async def test_after_loading_the_next_round_offers_the_uniloom_tools(handler, mock_openai_client, uniloom):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("uniloom", {})]),
        _message(tool_calls=[_tool_call("uniloom_list_projects", {}, "call_2")]),
        _message(content="u have TG"),
    ]
    reply = await handler.generate_reply("/hey whats going on", "", "Oakar", "760", allow_files=True, uniloom=True)
    assert reply.text == "u have TG"
    first, second, _ = mock_openai_client.chat.completions.create.call_args_list
    assert [t["function"]["name"] for t in first[1]["tools"]] == ["uniloom"]
    assert "uniloom_save_item" in [t["function"]["name"] for t in second[1]["tools"]]
    uniloom.call.assert_awaited_once_with("760", "list_projects", {})


async def test_a_message_about_uniloom_gets_the_tools_from_the_start(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [_message(content="ok")]
    await handler.generate_reply("/whats on my uniloom board", "", "Oakar", "760", allow_files=True, uniloom=True)
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert "uniloom_list_projects" in [t["function"]["name"] for t in call["tools"]]
    assert "Keys, not ids." in call["messages"][0]["content"]


async def test_a_uniloom_reply_gets_more_rounds_than_chat(handler, mock_openai_client):
    from config.config import Config
    calls = [_message(tool_calls=[_tool_call("uniloom_list_projects", {}, f"c{n}")]) for n in range(20)]
    mock_openai_client.chat.completions.create.side_effect = calls + [_message(content="done")]
    await handler.generate_reply("/plan it in uniloom", "", "Oakar", "760", allow_files=True, uniloom=True)
    used = mock_openai_client.chat.completions.create.call_count
    assert used == Config.UNILOOM_MAX_TOOL_ROUNDS + 1 > Config.AI_CHAT_MAX_TOOL_ROUNDS + 1


async def test_queued_changes_and_the_connect_button_reach_the_reply(handler, mock_openai_client, uniloom):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("uniloom_save_item", {"title": "Login page"})]),
        _message(content="ill make it once u press do it"),
    ]
    reply = await handler.generate_reply("/add a login slice in uniloom", "", "Oakar", "760", allow_files=True, uniloom=True)
    assert reply.uniloom is not None and reply.uniloom.owner_id == "760"
    assert [w.tool for w in reply.uniloom.writes] == ["save_item"]


async def test_a_reply_that_touched_uniloom_is_never_cached(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [_message(content="u have TG")] * 2
    await handler.generate_reply("/whats on my uniloom board", "", "Oakar", "760", allow_files=True, uniloom=True)
    assert handler.response_cache == {}


async def test_a_reply_that_cant_show_buttons_gets_no_uniloom(handler, mock_openai_client):
    # /chat and /tldr answer through the interaction and show no do it button, so they get no Uniloom
    mock_openai_client.chat.completions.create.side_effect = [_message(content="ok")]
    await handler.generate_reply("/whats on my uniloom board", "", "Oakar", "760", allow_files=True)
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert "uniloom" not in [t["function"]["name"] for t in call.get("tools") or []]
