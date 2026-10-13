"""The tool interface: how the reply loop drives any tool, shown with a made-up one."""
import asyncio

import pytest
from unittest.mock import patch

from src.tools import Tool, ToolResult, default_tools
from tests.unit.test_ai_response_handler import _message, _tool_call


def _definition(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


class Dice(Tool):
    name = "roll_dice"
    definition = _definition("roll_dice")

    def prompt(self) -> str:
        return "\nDICE: u can roll dice with roll_dice.\n"

    async def run(self, args, turn):
        return ToolResult(f"rolled a 4 for {turn.username}")


class Broken(Dice):
    name = "broken"
    definition = _definition("broken")

    async def run(self, args, turn):
        raise RuntimeError("boom")


class Hidden(Dice):
    name = "hidden"
    definition = _definition("hidden")

    def available(self) -> bool:
        return False


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_response_handler.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.ai_response_handler import AIResponseHandler
        h = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1", api_key="fake_key", deployment="gpt-5-mini")
    h.ai_client = mock_openai_client
    h.tools = [Dice(), Broken(), Hidden()]
    return h


def test_the_bot_ships_with_its_six_tools():
    assert [tool.name for tool in default_tools()] == ["create_document", "create_diagram", "open_url", "web_search",
                                                       "search_course", "course_assignments"]
    assert all(tool.definition["function"]["name"] == tool.name for tool in default_tools())


async def test_a_new_tool_is_offered_with_its_prompt_and_its_result_reaches_the_model(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("roll_dice", {})]),
        _message(content="u got a 4"),
    ]
    reply = await handler.generate_reply("/roll", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "u got a 4"
    first, second = mock_openai_client.chat.completions.create.call_args_list
    assert [tool["function"]["name"] for tool in first[1]["tools"]] == ["roll_dice", "broken"]
    assert "DICE: u can roll dice" in first[1]["messages"][0]["content"]
    assert second[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "rolled a 4 for Kruskal"}


async def test_a_tool_that_is_not_available_is_not_offered(handler, mock_openai_client):
    await handler.generate_reply("/roll", "", "Kruskal", "1", allow_files=True)
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert "hidden" not in [tool["function"]["name"] for tool in call["tools"]]


async def test_a_bug_in_a_tool_does_not_lose_the_reply(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("broken", {})]),
        _message(content="that broke"),
    ]
    reply = await handler.generate_reply("/break", "", "Kruskal", "1", allow_files=True)
    assert reply.text == "that broke"
    last = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]
    assert last == "error: the tool failed because of an internal error"


async def test_a_call_to_a_tool_that_does_not_exist_is_answered_with_an_error(handler, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_tool_call("fly_to_moon", {})]),
        _message(content="cant do that"),
    ]
    await handler.generate_reply("/fly", "", "Kruskal", "1", allow_files=True)
    last = mock_openai_client.chat.completions.create.call_args[1]["messages"][-1]["content"]
    assert last == "error: there is no tool called fly_to_moon"


async def test_tool_time_leaves_out_the_model_calls_a_tool_makes(handler):
    from src.tools import Turn

    class Writer(Dice):
        async def run(self, args, turn):
            await asyncio.sleep(0.05)
            # As if 40 ms of that was a model call the tool made
            turn.model_seconds += 0.04
            return ToolResult("written")

    turn = Turn("q", "Kruskal", "", "", [])
    result = await handler._timed_tool({"roll_dice": Writer()}, _tool_call("roll_dice", {}), turn)

    assert result.text == "written"
    assert 0.0 < turn.tool_seconds < 0.04
