"""Unit tests for the message processor: splitting long answers, sending them and shutting down."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import discord

from src.ai_response_handler import Reply
from src.message_handler import MessageHandler
from src.message_processor import MessageProcessor, give_first_use_role


def test_short_text_is_one_chunk():
    assert MessageProcessor._split_text("hello") == ["hello"]


def test_empty_text_is_no_chunks():
    assert MessageProcessor._split_text("") == []


def test_long_text_splits_under_the_discord_limit():
    chunks = MessageProcessor._split_text("word " * 1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == "word" * 1000


def test_split_prefers_line_breaks():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(40))
    chunks = MessageProcessor._split_text(text)
    assert all(not chunk.endswith("x" * 5 + " ") for chunk in chunks)
    assert all(chunk.splitlines()[-1].startswith("line ") for chunk in chunks)


def test_split_keeps_code_blocks_closed_in_every_chunk():
    text = "here:\n```python\n" + "value = compute(1, 2)\n" * 300 + "```\ndone"
    chunks = MessageProcessor._split_text(text)
    assert len(chunks) > 1
    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)
    assert all(len(chunk) <= 2000 for chunk in chunks)


def forbidden() -> discord.Forbidden:
    response = MagicMock(status=403, reason="Forbidden")
    return discord.Forbidden(response, {"code": 50013, "message": "Missing Permissions"})


async def test_a_server_without_the_first_use_role_is_warned_about_once(caplog):
    from src import message_processor
    message_processor._warned_no_role.clear()
    guild = MagicMock(id=77, roles=[])
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await give_first_use_role(guild, 1)
    assert sum("role not found" in record.message for record in caplog.records) == 1


async def test_a_channel_the_bot_cannot_post_in_gets_no_more_tries(caplog):
    ai = MagicMock()
    ai.generate_reply = AsyncMock(return_value=Reply("hello there"))
    bot = MagicMock(guilds=[])
    processor = MessageProcessor(bot=bot, message_handler=MessageHandler(), ai_response_handler=ai,
                                 user_data_manager=MagicMock())
    message = MagicMock()
    message.guild = None
    message.reply = AsyncMock(side_effect=forbidden())
    message.channel.send = AsyncMock(side_effect=forbidden())
    message.channel.typing = MagicMock()

    with caplog.at_level(logging.WARNING):
        await processor._handle_ai_response(message, "1", "someone", "c", "hi", "")

    # The reply, then the same text as a plain message. Nothing after that can get through either.
    assert message.reply.await_count == 1
    assert message.channel.send.await_count == 1
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_a_stop_signal_closes_the_bot_instead_of_stopping_its_loop():
    from src import bot as bot_module

    async def scenario():
        closed = asyncio.Event()
        fake = MagicMock()
        fake.close = AsyncMock(side_effect=lambda: closed.set())
        bot_module._running_bot = fake
        bot_module.shutdown_bot()
        await asyncio.wait_for(closed.wait(), 1)

    try:
        # A stopped loop would make this raise "Event loop stopped before Future completed"
        asyncio.run(scenario())
    finally:
        bot_module._running_bot = None


async def test_a_command_answer_in_a_channel_the_bot_cannot_post_in_gets_no_more_tries(caplog):
    commands = MagicMock()
    commands.handle_command = AsyncMock(return_value=Reply("the recap"))
    processor = MessageProcessor(bot=MagicMock(guilds=[]), message_handler=MessageHandler(),
                                 ai_response_handler=MagicMock(), user_data_manager=MagicMock(),
                                 command_handler=commands)
    message = MagicMock()
    message.content = "/tldr 20"
    message.reply = AsyncMock(side_effect=forbidden())
    message.channel.send = AsyncMock(side_effect=forbidden())
    message.channel.typing = MagicMock()

    with caplog.at_level(logging.WARNING):
        await processor._handle_command_message(message, "1", "someone", "c", {}, False)

    assert message.channel.send.await_count == 1
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
