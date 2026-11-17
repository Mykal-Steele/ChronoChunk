"""YouTube stream links expire after a few hours, so a song gets a fresh one before it plays."""
import time
from unittest.mock import AsyncMock, MagicMock

from src import music_manager
from src.music_manager import MusicManager, Song

PAGE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _link(expires_at):
    return f"https://rr1---sn-x.googlevideo.com/videoplayback?expire={expires_at}&itag=251&id=o-abc"


def test_a_song_knows_when_its_stream_link_expires():
    assert Song(_link(1795000000), "t", 200, "Kruskal").expires_at == 1795000000
    assert Song("https://example.test/audio.mp3", "t", 200, "Kruskal").expires_at is None


def test_a_link_that_ends_before_the_song_would_is_stale():
    now = 1_800_000_000
    assert Song(_link(now + 100), "t", 213, "K", page_url=PAGE).is_stale(now)
    assert not Song(_link(now + 6 * 3600), "t", 213, "K", page_url=PAGE).is_stale(now)
    assert not Song("https://example.test/audio.mp3", "t", 213, "K", page_url=PAGE).is_stale(now)


def _manager_with_voice(monkeypatch):
    manager = MusicManager()
    played = []

    class FakeAudio:
        def __init__(self, url, **kwargs):
            played.append(url)

    monkeypatch.setattr(music_manager.discord, "FFmpegPCMAudio", FakeAudio)
    monkeypatch.setattr(music_manager.discord, "PCMVolumeTransformer", lambda source, volume: source)
    state = manager.get_guild_music_state(1)
    state.voice_client = MagicMock()
    state.voice_client.is_connected.return_value = True
    state.voice_client.is_playing.return_value = False  # like a real idle voice client
    return manager, state, played


async def test_a_stale_song_gets_a_fresh_link_before_it_plays(monkeypatch):
    manager, state, played = _manager_with_voice(monkeypatch)
    fresh = _link(int(time.time()) + 6 * 3600)
    manager.extract_info = AsyncMock(return_value=(fresh, "Never Gonna Give You Up", 213, PAGE))
    state.queue = [Song(_link(int(time.time()) - 60), "Never Gonna Give You Up", 213, "K", page_url=PAGE)]
    ok, _ = await manager._play_next(1)
    assert ok
    manager.extract_info.assert_awaited_once_with(PAGE)
    assert played == [fresh]


async def test_a_fresh_song_plays_without_another_lookup(monkeypatch):
    manager, state, played = _manager_with_voice(monkeypatch)
    link = _link(int(time.time()) + 6 * 3600)
    manager.extract_info = AsyncMock()
    state.queue = [Song(link, "t", 213, "K", page_url=PAGE)]
    await manager._play_next(1)
    manager.extract_info.assert_not_called()
    assert played == [link]


async def test_if_the_new_lookup_fails_the_old_link_is_still_tried(monkeypatch):
    manager, state, played = _manager_with_voice(monkeypatch)
    old = _link(int(time.time()) - 60)
    manager.extract_info = AsyncMock(side_effect=RuntimeError("youtube said no"))
    state.queue = [Song(old, "t", 213, "K", page_url=PAGE)]
    await manager._play_next(1)
    assert played == [old]


async def test_a_queued_song_remembers_its_page(monkeypatch):
    manager = MusicManager()
    manager.extract_info = AsyncMock(return_value=(_link(1795000000), "t", 213, PAGE))
    await manager.play(1, "rick roll", "Kruskal", "42")  # not in voice, so it only queues
    assert manager.get_guild_music_state(1).queue[0].page_url == PAGE


async def test_leaving_voice_during_the_refresh_plays_nothing(monkeypatch):
    # CodeRabbit on #44: the refresh awaits, and the session can change meanwhile
    manager, state, played = _manager_with_voice(monkeypatch)
    before = state.current_song

    async def lookup_then_disconnect(url):
        state.voice_client = None
        return _link(int(time.time()) + 6 * 3600), "t", 213, PAGE

    manager.extract_info = lookup_then_disconnect
    state.queue = [Song(_link(int(time.time()) - 60), "t", 213, "K", page_url=PAGE)]
    ok, _ = await manager._play_next(1)
    assert not ok and played == [] and state.current_song is before


async def test_a_song_started_during_the_refresh_keeps_playing(monkeypatch):
    manager, state, played = _manager_with_voice(monkeypatch)
    stale = Song(_link(int(time.time()) - 60), "waited", 213, "K", page_url=PAGE)

    async def lookup_while_another_starts(url):
        state.voice_client.is_playing.return_value = True
        return _link(int(time.time()) + 6 * 3600), "waited", 213, PAGE

    manager.extract_info = lookup_while_another_starts
    state.voice_client.is_playing.return_value = False
    state.queue = [stale]
    ok, _ = await manager._play_next(1)
    assert not ok and played == []
    assert state.queue[0] is stale  # back at the front, not lost
