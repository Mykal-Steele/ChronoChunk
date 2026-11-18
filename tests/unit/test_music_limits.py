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


# ── how generous the limits are (raised 2026-11-18, the old ones were too tight) ──

def test_music_limits_default_to_the_raised_numbers():
    # Reads an empty env on purpose, so a MUSIC_* value in the real env or .env cannot change it (review on #46)
    from config.config import music_limit
    defaults = {"MUSIC_REQUESTS_PER_USER": 15, "MUSIC_REQUESTS_PER_GUILD": 40, "MUSIC_CONTROLS_PER_USER": 60,
                "MUSIC_MAX_QUEUE_PER_GUILD": 25, "MUSIC_MAX_SONGS_PER_USER": 8}
    assert {name: music_limit({}, name) for name in defaults} == defaults


def test_a_zero_negative_or_broken_music_limit_falls_back_to_the_default():
    # 0 used to reach the rate limiter and raise IndexError instead of a rate limit message
    from config.config import music_limit
    for bad in ("0", "-3", "abc", ""):
        assert music_limit({"MUSIC_REQUESTS_PER_USER": bad}, "MUSIC_REQUESTS_PER_USER") == 15


def test_config_uses_the_music_limits():
    import os
    from config.config import Config, music_limit
    assert Config.RATE_LIMITS["music"] == (music_limit(os.environ, "MUSIC_REQUESTS_PER_USER"), 600)
    assert Config.MUSIC_MAX_QUEUE_PER_GUILD == music_limit(os.environ, "MUSIC_MAX_QUEUE_PER_GUILD")


def test_every_music_limit_can_be_changed_from_env():
    import json
    import os
    import subprocess
    import sys
    env = {**os.environ, "MUSIC_REQUESTS_PER_USER": "7", "MUSIC_REQUESTS_PER_GUILD": "70",
           "MUSIC_CONTROLS_PER_USER": "77", "MUSIC_MAX_QUEUE_PER_GUILD": "33", "MUSIC_MAX_SONGS_PER_USER": "11"}
    code = ("import json; from config.config import Config as C; print(json.dumps([C.RATE_LIMITS['music'], "
            "C.RATE_LIMITS['music_guild'], C.RATE_LIMITS['music_control'], C.MUSIC_MAX_QUEUE_PER_GUILD, "
            "C.MUSIC_MAX_SONGS_PER_USER]))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [[7, 600], [70, 600], [77, 600], 33, 11]
