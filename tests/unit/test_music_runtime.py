"""yt-dlp needs a JavaScript runtime and its challenge solver for YouTube, or it starts losing formats."""
import importlib.util
import shutil
import subprocess


def test_the_image_has_deno_for_yt_dlp():
    # yt-dlp only turns on deno by default. Without it, it warns that YouTube extraction is deprecated
    deno = shutil.which("deno")
    assert deno, "deno is missing from the image"
    version = subprocess.run([deno, "--version"], capture_output=True, text=True, check=True).stdout
    assert version.startswith("deno 2.")


def test_the_image_has_the_youtube_challenge_solver():
    assert importlib.util.find_spec("yt_dlp_ejs") is not None


# The lookup runs without JavaScript first, as fast and light as before (1 s, no deno). Only when
# that fails does it try once more with deno, which takes about 3 s and 320 MB.

import pytest  # noqa: E402
import yt_dlp  # noqa: E402

from src import music_manager  # noqa: E402
from src.music_manager import MusicManager  # noqa: E402

PAGE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
GOOD = {"url": "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1795000000", "title": "song",
        "duration": 213, "webpage_url": PAGE}


def _fake_youtube(monkeypatch, *answers):
    """Each YoutubeDL made gets the next answer: a dict to return or an exception to raise."""
    calls = []
    answers = list(answers)

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(options)
            self.answer = answers.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    monkeypatch.setattr(music_manager.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    real_which = shutil.which
    monkeypatch.setattr(music_manager.shutil, "which",
                        lambda name: "/usr/local/bin/deno" if name == "deno" else real_which(name))
    return calls


async def test_a_lookup_that_works_never_starts_deno(monkeypatch):
    calls = _fake_youtube(monkeypatch, GOOD)
    url, title, _, _ = await MusicManager().extract_info(PAGE)
    assert title == "song"
    assert [c.get("js_runtimes") for c in calls] == [{}]


async def test_a_failed_lookup_tries_again_with_deno(monkeypatch):
    calls = _fake_youtube(monkeypatch, yt_dlp.utils.DownloadError("Requested format is not available"), GOOD)
    _, title, _, _ = await MusicManager().extract_info(PAGE)
    assert title == "song"
    assert [c.get("js_runtimes") for c in calls] == [{}, {"deno": {}}]


async def test_a_lookup_with_no_stream_tries_again_with_deno(monkeypatch):
    calls = _fake_youtube(monkeypatch, {"title": "song", "duration": 213, "formats": []}, GOOD)
    url, _, _, _ = await MusicManager().extract_info(PAGE)
    assert url == GOOD["url"]
    assert [c.get("js_runtimes") for c in calls] == [{}, {"deno": {}}]


async def test_a_search_with_no_results_does_not_try_deno(monkeypatch):
    calls = _fake_youtube(monkeypatch, {"entries": []})
    manager = MusicManager()
    manager._enhance_search_query = lambda query, requested_by=None: _async("nothing like this exists")
    with pytest.raises(ValueError, match="No results found"):
        await manager.extract_info("nothing like this exists")
    assert len(calls) == 1


async def test_without_deno_the_first_error_is_kept(monkeypatch):
    calls = _fake_youtube(monkeypatch, yt_dlp.utils.DownloadError("Video unavailable"))
    real_which = shutil.which
    monkeypatch.setattr(music_manager.shutil, "which", lambda name: None if name == "deno" else real_which(name))
    with pytest.raises(yt_dlp.utils.DownloadError, match="Video unavailable"):
        await MusicManager().extract_info(PAGE)
    assert len(calls) == 1


async def test_when_both_tries_fail_the_deno_error_is_raised(monkeypatch):
    _fake_youtube(monkeypatch, yt_dlp.utils.DownloadError("first"), yt_dlp.utils.DownloadError("Video unavailable"))
    with pytest.raises(yt_dlp.utils.DownloadError, match="Video unavailable"):
        await MusicManager().extract_info(PAGE)


async def _async(value):
    return value
