"""/help music: how to play music, every music command, and the real limits, from the settings."""
import re

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.command_handler import CommandHandler
from src.help_menu import build_help_embed, build_music_help_embed
from src.rate_limiter import RateLimiter
from src.slash_commands import SlashCommandManager

MUSIC_COMMANDS = ("/music", "/relate", "/queue", "/skip", "/pause", "/resume", "/volume", "/stop")


def _text(embed) -> str:
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


def test_the_music_page_lists_every_music_command():
    text = _text(build_music_help_embed())
    for command in MUSIC_COMMANDS:
        assert f"`{command}" in text, command


def test_the_music_page_states_the_limits_from_the_settings(monkeypatch):
    from config.config import Config
    monkeypatch.setitem(Config.RATE_LIMITS, "music", (13, 600))
    monkeypatch.setitem(Config.RATE_LIMITS, "music_guild", (37, 600))
    monkeypatch.setitem(Config.RATE_LIMITS, "music_control", (51, 600))
    monkeypatch.setattr(Config, "MUSIC_MAX_QUEUE_PER_GUILD", 21)
    monkeypatch.setattr(Config, "MUSIC_MAX_SONGS_PER_USER", 6)
    monkeypatch.setattr(Config, "MUSIC_IDLE_SECONDS", 900, raising=False)
    monkeypatch.setattr(Config, "MUSIC_ALONE_SECONDS", 120, raising=False)
    text = _text(build_music_help_embed())
    assert "13 songs per person and 37 per server every 10 min" in text
    assert "51 controls per person every 10 min" in text
    assert "21 songs in the queue, 6 from one person" in text
    assert "after 2 min with nobody else in the channel" in text
    assert "after 15 min with nothing playing" in text


def test_the_music_page_says_who_can_control_playback():
    text = _text(build_music_help_embed())
    assert "Manage Server" in text


def test_the_music_page_says_chat_cannot_play_songs():
    # The bot used to claim it queued songs from chat. The help says how it really works.
    assert "cant start songs from chat" in _text(build_music_help_embed())


def test_the_music_page_fits_discord_and_stays_clean():
    embed = build_music_help_embed()
    text = _text(embed)
    assert not re.search(r"\b(shit|fuck\w*|bitch|damn|ass|hell|crap|wtf|piss\w*)\b", text, re.IGNORECASE)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(text) <= 6000


def test_the_main_help_points_to_the_music_page():
    assert "`/help music`" in _text(build_help_embed())


def test_the_main_help_still_fits_discord():
    embed = build_help_embed()
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(_text(embed)) <= 6000


@pytest.fixture
async def help_command(mock_bot_user):
    registered = {}

    def command(**kwargs):
        def register(func):
            registered[kwargs["name"]] = func
            return func
        return register

    bot = MagicMock()
    bot.user = mock_bot_user
    bot.tree.command = command
    manager = SlashCommandManager(bot=bot, game_manager=MagicMock(), user_data_manager=MagicMock(),
                                  ai_handler=MagicMock(), message_handler=MagicMock(), music_manager=MagicMock())
    await manager._register_help_command()
    return registered["help"]


def _interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_slash_help_with_the_music_topic_shows_the_music_page(help_command):
    interaction = _interaction()
    await help_command(interaction, "music")
    embed = interaction.response.send_message.call_args[1]["embed"]
    assert embed.title == build_music_help_embed().title


async def test_slash_help_with_no_topic_shows_everything(help_command):
    interaction = _interaction()
    await help_command(interaction)
    embed = interaction.response.send_message.call_args[1]["embed"]
    assert embed.title == build_help_embed().title


async def test_typed_help_music_shows_the_music_page():
    handler = CommandHandler(bot=None, rate_limiter=RateLimiter())
    embed = await handler._handle_help(["music"], MagicMock(), "1")
    assert embed is not None and embed.title == build_music_help_embed().title


async def test_typed_help_with_other_words_is_still_chat():
    handler = CommandHandler(bot=None, rate_limiter=RateLimiter())
    assert await handler._handle_help(["me", "write", "this"], MagicMock(), "1") is None
