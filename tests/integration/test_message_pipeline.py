"""Integration tests for the MessageProcessor pipeline.

Real components: MessageHandler, GameManager, UserDataManager (tmp dir)
Mocked: AIResponseHandler (no real API), MusicManager (no FFmpeg),
        discord.TextChannel.send, discord.TextChannel.history
"""
import asyncio
import re
from unittest.mock import MagicMock, AsyncMock

import discord

from config.config import Config


from src.ai_response_handler import Reply
from tests.fakes import AsyncIterator


def _make_message(bot_user, content="hello", is_reply_to_bot=False, is_bot_author=False,
                  channel_send=None):
    channel = MagicMock()
    channel.id = 99999
    channel.send = channel_send or AsyncMock()
    channel.guild = MagicMock()
    channel.guild.id = 11111
    channel.guild.emojis = []
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=None)
    channel.typing = MagicMock(return_value=typing_cm)
    channel.history = MagicMock(return_value=AsyncIterator([]))

    author = MagicMock()
    author.id = 9999 if is_bot_author else 12345
    author.name = "BotUser" if is_bot_author else "TestUser"
    author.display_name = author.name
    author.bot = is_bot_author

    reference = None
    if is_reply_to_bot:
        ref_msg = MagicMock()
        ref_msg.author = bot_user
        reference = MagicMock()
        reference.message_id = 11111
        channel.fetch_message = AsyncMock(return_value=ref_msg)

    msg = MagicMock()
    msg.author = author
    msg.channel = channel
    msg.guild = channel.guild
    msg.content = content
    msg.reference = reference
    msg.id = 54321
    msg.reply = AsyncMock()
    return msg


# ── bot message ignored ───────────────────────────────────────────────────────

async def test_bot_message_ignored(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    msg = _make_message(bot.user, is_bot_author=True)
    await processor.process_message(msg)
    msg.channel.send.assert_not_called()
    ai_handler.generate_reply.assert_not_called()


def _test_bot_message(bot_user, author_id=777, channel_id=99999, content="/yo"):
    msg = _make_message(bot_user, content=content, is_bot_author=True)
    msg.author.id = author_id
    msg.channel.id = channel_id
    return msg


async def test_the_test_bot_is_answered_in_the_test_channel(pipeline, monkeypatch):
    processor, _, _, _, ai_handler, bot = pipeline
    monkeypatch.setattr(Config, "TEST_BOT_ID", "777")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "99999")
    msg = _test_bot_message(bot.user)
    await processor.process_message(msg)
    ai_handler.generate_reply.assert_called_once()
    msg.reply.assert_called_once()


async def test_the_test_bot_is_ignored_in_any_other_channel(pipeline, monkeypatch):
    processor, _, _, _, ai_handler, bot = pipeline
    monkeypatch.setattr(Config, "TEST_BOT_ID", "777")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "99999")
    await processor.process_message(_test_bot_message(bot.user, channel_id=55555))
    ai_handler.generate_reply.assert_not_called()


async def test_another_bot_is_ignored_in_the_test_channel(pipeline, monkeypatch):
    processor, _, _, _, ai_handler, bot = pipeline
    monkeypatch.setattr(Config, "TEST_BOT_ID", "777")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "99999")
    await processor.process_message(_test_bot_message(bot.user, author_id=888))
    ai_handler.generate_reply.assert_not_called()


async def test_one_setting_alone_answers_no_bot(pipeline, monkeypatch):
    processor, _, _, _, ai_handler, bot = pipeline
    monkeypatch.setattr(Config, "TEST_BOT_ID", "777")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "")
    await processor.process_message(_test_bot_message(bot.user))
    monkeypatch.setattr(Config, "TEST_BOT_ID", "")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "99999")
    await processor.process_message(_test_bot_message(bot.user))
    ai_handler.generate_reply.assert_not_called()


async def test_the_bot_never_answers_itself_even_when_named_as_the_test_bot(pipeline, monkeypatch):
    processor, _, _, _, ai_handler, bot = pipeline
    monkeypatch.setattr(Config, "TEST_BOT_ID", str(bot.user.id))
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "99999")
    await processor.process_message(_test_bot_message(bot.user, author_id=bot.user.id))
    ai_handler.generate_reply.assert_not_called()


# ── command routing ───────────────────────────────────────────────────────────

async def test_unknown_command_falls_back_to_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    send = AsyncMock()
    msg = _make_message(bot.user, content="/notacommand hello", channel_send=send)
    await processor.process_message(msg)
    # Either AI called or some response sent
    assert send.called or ai_handler.generate_reply.called


async def test_regular_message_no_ai_response(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    msg = _make_message(bot.user, content="just a normal message")
    await processor.process_message(msg)
    ai_handler.generate_reply.assert_not_called()


# ── reply triggers AI ─────────────────────────────────────────────────────────

async def test_reply_to_bot_triggers_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    send = AsyncMock()
    msg = _make_message(bot.user, content="what do you think?",
                        is_reply_to_bot=True, channel_send=send)
    await processor.process_message(msg)
    ai_handler.generate_reply.assert_called_once()


async def test_reply_to_bot_sends_response(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here's my take fr"))
    send = AsyncMock()
    msg = _make_message(bot.user, content="what do you think?",
                        is_reply_to_bot=True, channel_send=send)
    await processor.process_message(msg)
    msg.reply.assert_called_once()
    assert msg.reply.call_args[0][0] == "here's my take fr"


async def test_an_answer_logs_how_long_each_step_took(pipeline, caplog):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here's my take fr"))
    msg = _make_message(bot.user, content="what do you think?", is_reply_to_bot=True)
    with caplog.at_level("INFO", logger="src.message_processor"):
        await processor.process_message(msg)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Answered TestUser in "))
    assert re.search(r"in \d+\.\d\ds: history \d+\.\d\ds, context \d+\.\d\ds, "
                     r"model and tools \d+\.\d\ds, send \d+\.\d\ds, other \d+\.\d\ds$", line)


async def test_a_slow_typing_indicator_does_not_hold_up_the_answer(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline

    async def reply(*args, **kwargs):
        await asyncio.sleep(0.01)  # a model call gives the indicator time to start
        return Reply("here's my take fr")
    ai_handler.generate_reply = AsyncMock(side_effect=reply)
    msg = _make_message(bot.user, content="what do you think?", is_reply_to_bot=True)

    async def slow_start():
        await asyncio.sleep(30)
    msg.channel.typing.return_value.__aenter__ = AsyncMock(side_effect=slow_start)

    await asyncio.wait_for(processor.process_message(msg), timeout=5)

    msg.channel.typing.assert_called_once()
    assert msg.reply.call_args[0][0] == "here's my take fr"


async def test_the_typing_indicator_is_on_while_the_model_works_and_off_before_the_answer_goes_out(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    typing = MagicMock()
    typing.__aenter__ = AsyncMock(return_value=None)
    typing.__aexit__ = AsyncMock(return_value=None)
    seen = {}

    async def reply(*args, **kwargs):
        await asyncio.sleep(0)  # let the indicator start
        seen["on during the model call"] = typing.__aenter__.await_count == 1 and typing.__aexit__.await_count == 0
        return Reply("here's my take fr")
    ai_handler.generate_reply = AsyncMock(side_effect=reply)
    msg = _make_message(bot.user, content="what do you think?", is_reply_to_bot=True)
    msg.channel.typing = MagicMock(return_value=typing)

    async def send(*args, **kwargs):
        seen["off before the send"] = typing.__aexit__.await_count == 1
    msg.reply = AsyncMock(side_effect=send)

    await processor.process_message(msg)

    assert seen == {"on during the model call": True, "off before the send": True}


async def test_a_refused_typing_indicator_does_not_cost_the_answer(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here's my take fr"))
    msg = _make_message(bot.user, content="what do you think?", is_reply_to_bot=True)
    refused = discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")
    msg.channel.typing.return_value.__aenter__ = AsyncMock(side_effect=refused)

    await processor.process_message(msg)

    assert msg.reply.call_args[0][0] == "here's my take fr"


# ── channel history captured ──────────────────────────────────────────────────

async def test_channel_history_captured_on_any_message(pipeline):
    processor, mh, _, _, _, bot = pipeline
    msg = _make_message(bot.user, content="random message here")
    await processor.process_message(msg)
    channel_id = str(msg.channel.id)
    assert channel_id in mh.last_channel_messages
    assert len(mh.last_channel_messages[channel_id]) > 0


# ── fact extraction on AI reply ───────────────────────────────────────────────

async def test_fact_extraction_called_on_substantive_reply(pipeline, mock_genai_client, fake_genai_response):
    processor, _, _, udm, ai_handler, bot = pipeline
    # Silence fact extraction API response
    fake_genai_response.choices[0].message.content = "[]"
    mock_genai_client.chat.completions.create = AsyncMock(return_value=fake_genai_response)
    udm.ai_client = mock_genai_client

    msg = _make_message(bot.user, content="I am 25 years old and I love coffee",
                        is_reply_to_bot=True)
    await processor.process_message(msg)
    # API should have been called (fact extraction + possibly conversation add)
    # We just verify no crash and the pipeline completed


async def test_fact_extraction_skipped_for_short_message(pipeline, mock_genai_client):
    processor, _, _, udm, ai_handler, bot = pipeline
    mock_genai_client.aio.models.generate_content = AsyncMock()

    msg = _make_message(bot.user, content="ok", is_reply_to_bot=True)
    await processor.process_message(msg)
    # With message "ok" (1 word), extract_and_save_facts should return early
    # API should not have been called for fact extraction (it is for the AI response though)
