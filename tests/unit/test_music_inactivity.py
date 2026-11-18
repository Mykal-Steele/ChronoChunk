"""When the bot leaves voice on its own: nothing playing for a while, or nobody left to listen."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.music_manager import MusicManager

IDLE = 600
ALONE = 600
NOW = 1_000_000.0


def _manager(monkeypatch, idle=IDLE, alone=ALONE):
    from config.config import Config
    monkeypatch.setattr(Config, "MUSIC_IDLE_SECONDS", idle, raising=False)
    monkeypatch.setattr(Config, "MUSIC_ALONE_SECONDS", alone, raising=False)
    manager = MusicManager()
    manager.leave_voice_channel = AsyncMock(return_value=True)
    return manager


def _connect(manager, guild_id=1, channel_id=10, playing=False):
    state = manager.get_guild_music_state(guild_id)
    voice = MagicMock()
    voice.is_connected.return_value = True
    voice.is_playing.return_value = playing
    voice.channel.id = channel_id
    state.voice_client = voice
    return state


def _member(bot=False):
    return SimpleNamespace(bot=bot)


def _voice_state(channel):
    return SimpleNamespace(channel=channel)


def _channel(channel_id, members=()):
    return SimpleNamespace(id=channel_id, members=list(members))


async def test_a_long_song_keeps_playing_with_no_commands(monkeypatch):
    # A 15 minute song started 11 minutes ago, and nobody typed anything since
    manager = _manager(monkeypatch)
    state = _connect(manager, playing=True)
    state.last_activity = NOW - 11 * 60
    await manager.check_inactive(NOW)
    manager.leave_voice_channel.assert_not_awaited()


async def test_it_leaves_after_the_idle_time_with_nothing_playing(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, playing=False)
    state.last_activity = NOW - IDLE - 1
    await manager.check_inactive(NOW)
    manager.leave_voice_channel.assert_awaited_once_with(1)


async def test_it_stays_while_the_idle_time_has_not_passed(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, playing=False)
    state.last_activity = NOW - IDLE + 30
    await manager.check_inactive(NOW)
    manager.leave_voice_channel.assert_not_awaited()


async def test_it_leaves_when_alone_too_long_even_while_playing(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, playing=True)
    state.last_activity = NOW
    state.alone_since = NOW - ALONE - 1
    await manager.check_inactive(NOW)
    manager.leave_voice_channel.assert_awaited_once_with(1)


async def test_the_timeouts_come_from_the_settings(monkeypatch):
    manager = _manager(monkeypatch, idle=120, alone=60)
    idle = _connect(manager, guild_id=1, playing=False)
    idle.last_activity = NOW - 121
    alone = _connect(manager, guild_id=2, playing=True)
    alone.last_activity = NOW
    alone.alone_since = NOW - 61
    await manager.check_inactive(NOW)
    assert sorted(c.args[0] for c in manager.leave_voice_channel.await_args_list) == [1, 2]


async def test_everyone_leaving_starts_the_alone_timer(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, channel_id=10)
    left = _channel(10, members=[_member(bot=True)])
    await manager.on_voice_state_update(_member(), _voice_state(left), _voice_state(None))
    assert state.alone_since is not None


async def test_someone_joining_the_bots_channel_stops_the_alone_timer(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, channel_id=10)
    state.alone_since = NOW - 300
    joined = _channel(10, members=[_member(bot=True), _member()])
    await manager.on_voice_state_update(_member(), _voice_state(None), _voice_state(joined))
    assert state.alone_since is None


async def test_someone_moving_into_the_bots_channel_stops_the_alone_timer(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, channel_id=10)
    state.alone_since = NOW - 300
    await manager.on_voice_state_update(
        _member(), _voice_state(_channel(20)), _voice_state(_channel(10, members=[_member()])))
    assert state.alone_since is None


async def test_joining_some_other_channel_does_not_stop_the_alone_timer(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, channel_id=10)
    state.alone_since = NOW - 300
    await manager.on_voice_state_update(_member(), _voice_state(None), _voice_state(_channel(20, [_member()])))
    assert state.alone_since == NOW - 300


async def test_another_bot_joining_does_not_stop_the_alone_timer(monkeypatch):
    manager = _manager(monkeypatch)
    state = _connect(manager, channel_id=10)
    state.alone_since = NOW - 300
    await manager.on_voice_state_update(
        _member(bot=True), _voice_state(None), _voice_state(_channel(10, [_member(bot=True)])))
    assert state.alone_since == NOW - 300


def test_the_voice_timeouts_default_to_ten_minutes():
    from config.config import music_limit
    assert music_limit({}, "MUSIC_IDLE_SECONDS") == 600
    assert music_limit({}, "MUSIC_ALONE_SECONDS") == 600


@pytest.mark.parametrize("bad", ["0", "-5", "soon", ""])
def test_a_broken_voice_timeout_falls_back_to_the_default(bad):
    from config.config import music_limit
    assert music_limit({"MUSIC_IDLE_SECONDS": bad}, "MUSIC_IDLE_SECONDS") == 600


def test_the_voice_timeouts_can_be_changed_from_env():
    import os
    import subprocess
    import sys
    env = {**os.environ, "MUSIC_IDLE_SECONDS": "900", "MUSIC_ALONE_SECONDS": "120"}
    code = "from config.config import Config as C; print(C.MUSIC_IDLE_SECONDS, C.MUSIC_ALONE_SECONDS)"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["900", "120"]
