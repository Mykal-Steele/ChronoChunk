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


class _RecordingYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL and keeps the options the bot passed."""
    seen: list = []
    urls: list = []

    def __init__(self, options):
        _RecordingYoutubeDL.seen.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        _RecordingYoutubeDL.urls.append(url)
        return {"entries": [{"url": _link(1795000000), "title": "first", "duration": 200,
                             "webpage_url": PAGE}]}


async def test_a_playlist_link_only_looks_up_its_first_song(monkeypatch):
    # A 210 song playlist took 168s, because yt-dlp resolved every song before the bot took the first
    _RecordingYoutubeDL.seen = []
    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", _RecordingYoutubeDL)
    url, title, _, _ = await MusicManager().extract_info(
        "https://www.youtube.com/playlist?list=PLplXQ2cg9B_qrCVd1J_iId5SvP8Kf_BfS")
    assert title == "first"
    assert _RecordingYoutubeDL.seen[-1].get("playlist_items") == "1"


async def test_the_lookup_has_no_filter_that_rejects_normal_videos(monkeypatch):
    # The old members-only filter returned True for every normal video, which yt-dlp reads
    # as a reason to skip it, and then crashed joining that True into its log line
    _RecordingYoutubeDL.seen = []
    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", _RecordingYoutubeDL)
    await MusicManager().extract_info(PAGE)
    match_filter = _RecordingYoutubeDL.seen[-1].get("match_filter")
    assert match_filter is None or match_filter({"title": "a normal song"}) is None


import pytest  # noqa: E402


@pytest.mark.parametrize("link", [
    "https://www.youtube.com/playlist?list=PLplXQ2cg9B_qrCVd1J_iId5SvP8Kf_BfS",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
])
async def test_every_kind_of_youtube_link_goes_straight_to_youtube(monkeypatch, link):
    # These used to be treated as search words: the AI rewrote them and YouTube found nothing
    _RecordingYoutubeDL.urls = []
    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", _RecordingYoutubeDL)
    manager = MusicManager()
    manager._enhance_search_query = AsyncMock(side_effect=AssertionError("a link is not a search"))
    await manager.extract_info(link)
    assert _RecordingYoutubeDL.urls == [link]


async def test_words_still_become_a_search(monkeypatch):
    _RecordingYoutubeDL.urls = []
    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", _RecordingYoutubeDL)
    manager = MusicManager()
    manager._enhance_search_query = AsyncMock(return_value="rick astley never gonna give you up")
    await manager.extract_info("that rickroll song")
    assert _RecordingYoutubeDL.urls == ["ytsearch1:rick astley never gonna give you up"]
