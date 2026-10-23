"""Music requests are capped before an expensive lookup starts."""
from unittest.mock import AsyncMock, MagicMock, call

from src.music_manager import MusicManager, Song


def _queue(manager, guild_id, count, requested_by_id="other"):
    state = manager.get_guild_music_state(guild_id)
    state.queue = [Song(f"https://example.test/{i}", f"song {i}", 60, "Someone", requested_by_id)
                   for i in range(count)]
    return state


async def test_a_guild_queue_has_a_ceiling(monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "MUSIC_MAX_QUEUE_PER_GUILD", 2)
    manager = MusicManager()
    manager.extract_info = AsyncMock()
    _queue(manager, 1, 2)
    ok, message = await manager.play(1, "a song", "Kruskal", "42")
    assert not ok and "queue is full" in message
    manager.extract_info.assert_not_called()


async def test_one_user_cannot_fill_the_queue(monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "MUSIC_MAX_SONGS_PER_USER", 2)
    manager = MusicManager()
    manager.extract_info = AsyncMock()
    _queue(manager, 1, 2, requested_by_id="42")
    ok, message = await manager.play(1, "a song", "Kruskal", "42")
    assert not ok and "already have 2 songs" in message
    manager.extract_info.assert_not_called()


def test_tls_verification_is_not_disabled():
    manager = MusicManager()
    assert manager.yt_dlp_options["nocheckcertificate"] is False


async def test_a_music_request_counts_against_the_person_and_the_guild():
    from src.slash_commands import SlashCommandManager

    limiter = MagicMock()
    bot = MagicMock()
    bot.rate_limiter = limiter
    manager = SlashCommandManager(
        bot, game_manager=MagicMock(), user_data_manager=MagicMock(), ai_handler=MagicMock(),
        message_handler=MagicMock(), music_manager=MagicMock())
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.guild_id = 7

    assert await manager._music_allowed(interaction) is True
    limiter.check_rate_limit.assert_has_calls([
        call("42", "music"),
        call("guild:7", "music_guild"),
    ])


async def test_a_rejected_music_request_does_not_spend_the_guild_budget():
    from src.exceptions import RateLimitError
    from src.slash_commands import SlashCommandManager

    limiter = MagicMock()
    limiter.check_rate_limit.side_effect = RateLimitError(5)
    bot = MagicMock()
    bot.rate_limiter = limiter
    manager = SlashCommandManager(
        bot, game_manager=MagicMock(), user_data_manager=MagicMock(), ai_handler=MagicMock(),
        message_handler=MagicMock(), music_manager=MagicMock())
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.guild_id = 7
    interaction.response.send_message = AsyncMock()

    assert await manager._music_allowed(interaction) is False
    limiter.check_rate_limit.assert_called_once_with("42", "music")
