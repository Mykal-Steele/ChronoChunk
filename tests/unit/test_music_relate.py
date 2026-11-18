"""/relate picks a song from YouTube's Mix for the song that is playing (yt-dlp no longer gives related videos)."""
from unittest.mock import AsyncMock

from src import music_manager
from src.music_manager import MusicManager, Song


def _watch(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


class FakeYDL:
    """Answers the Mix playlist and title searches like yt-dlp would."""
    calls = []
    mix = []
    search_hit = "AAA"

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(url)
        if url.startswith("ytsearch"):
            return {"entries": [{"id": FakeYDL.search_hit}]}
        return {"entries": list(FakeYDL.mix)}


PLAYING = _watch("AAA")


def _setup(monkeypatch, mix, playing_page=PLAYING):
    FakeYDL.calls = []
    FakeYDL.mix = mix
    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(music_manager.random, "choice", lambda items: items[0])
    manager = MusicManager()
    state = manager.get_guild_music_state(1)
    state.current_song = Song("https://stream.example/a?expire=1", "Song A", 200, "K", page_url=playing_page)
    manager.play = AsyncMock(return_value=(True, "queued"))
    return manager, state


def _entry(video_id, title="x", duration=200):
    return {"id": video_id, "title": title, "duration": duration}


async def test_relate_queues_a_song_from_the_mix_not_the_one_playing(monkeypatch):
    manager, _ = _setup(monkeypatch, [_entry("AAA"), _entry("BBB"), _entry("CCC")])
    ok, _ = await manager.get_related_song(1, "Kruskal", "42")
    assert ok
    assert FakeYDL.calls == ["https://www.youtube.com/watch?v=AAA&list=RDAAA"]
    manager.play.assert_awaited_once_with(1, _watch("BBB"), "Kruskal", "42")


async def test_relate_skips_songs_already_in_the_queue(monkeypatch):
    manager, state = _setup(monkeypatch, [_entry("AAA"), _entry("BBB"), _entry("CCC")])
    state.queue = [Song("https://stream.example/b", "Song B", 200, "K", page_url=_watch("BBB"))]
    await manager.get_related_song(1, "Kruskal", "42")
    manager.play.assert_awaited_once_with(1, _watch("CCC"), "Kruskal", "42")


async def test_relate_skips_long_videos_and_members_only(monkeypatch):
    manager, _ = _setup(monkeypatch, [_entry("LONG", duration=3 * 3600), _entry("MEM", "Members only stream"),
                                      _entry("OK1")])
    await manager.get_related_song(1, "Kruskal", "42")
    manager.play.assert_awaited_once_with(1, _watch("OK1"), "Kruskal", "42")


async def test_relate_finds_the_video_by_title_when_the_song_has_no_page(monkeypatch):
    manager, _ = _setup(monkeypatch, [_entry("AAA"), _entry("BBB")], playing_page="")
    await manager.get_related_song(1, "Kruskal", "42")
    assert FakeYDL.calls[0].startswith("ytsearch1:Song A")
    assert FakeYDL.calls[1] == "https://www.youtube.com/watch?v=AAA&list=RDAAA"


async def test_relate_says_so_when_the_mix_is_empty(monkeypatch):
    manager, _ = _setup(monkeypatch, [_entry("AAA")])
    ok, message = await manager.get_related_song(1, "Kruskal", "42")
    assert not ok and "related" in message
    manager.play.assert_not_called()
