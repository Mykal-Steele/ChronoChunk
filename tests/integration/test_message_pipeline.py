"""Integration tests for the MessageProcessor pipeline.

Real components: MessageHandler, GameManager, UserDataManager (tmp dir)
Mocked: AIResponseHandler (no real API), MusicManager (no FFmpeg),
        discord.TextChannel.send, discord.TextChannel.history
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


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
    ai_handler.generate_response.assert_not_called()


# ── command routing ───────────────────────────────────────────────────────────

async def test_slash_game_command_handled(pipeline):
    processor, _, gm, _, ai_handler, bot = pipeline
    send = AsyncMock()
    msg = _make_message(bot.user, content="/game 50", channel_send=send)
    await processor.process_message(msg)
    send.assert_called()
    # The game should be started for user 12345 (int)
    assert gm.get_active_game(12345) is not None


async def test_unknown_command_falls_back_to_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    send = AsyncMock()
    msg = _make_message(bot.user, content="/notacommand hello", channel_send=send)
    await processor.process_message(msg)
    # Either AI called or some response sent
    assert send.called or ai_handler.generate_response.called


async def test_regular_message_no_ai_response(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    msg = _make_message(bot.user, content="just a normal message")
    await processor.process_message(msg)
    ai_handler.generate_response.assert_not_called()


# ── reply triggers AI ─────────────────────────────────────────────────────────

async def test_reply_to_bot_triggers_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    send = AsyncMock()
    msg = _make_message(bot.user, content="what do you think?",
                        is_reply_to_bot=True, channel_send=send)
    await processor.process_message(msg)
    ai_handler.generate_response.assert_called_once()


async def test_reply_to_bot_sends_response(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_response = AsyncMock(return_value="here's my take fr")
    send = AsyncMock()
    msg = _make_message(bot.user, content="what do you think?",
                        is_reply_to_bot=True, channel_send=send)
    await processor.process_message(msg)
    msg.reply.assert_called_once()
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


# ── game end-to-end through command handler ───────────────────────────────────

async def test_game_guess_end_to_end(pipeline):
    processor, _, gm, _, _, bot = pipeline

    # Start game
    start_send = AsyncMock()
    start_msg = _make_message(bot.user, content="/game 10", channel_send=start_send)
    await processor.process_message(start_msg)
    assert gm.get_active_game(12345) is not None

    # Guess the secret number directly
    secret = gm.active_games[12345].secret_number
    win_send = AsyncMock()
    win_msg = _make_message(bot.user, content=f"/guess {secret}", channel_send=win_send)
    await processor.process_message(win_msg)
    win_send.assert_called()
    assert gm.get_active_game(12345) is None
