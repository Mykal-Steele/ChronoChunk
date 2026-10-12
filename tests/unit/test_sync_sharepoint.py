"""
Unit tests for how the sync fetches from SharePoint: what it skips because the cache
has it, and what it falls back to when a fetch fails. The browser is a fake.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sync import sharepoint
from sync.cache import Cache


@pytest.fixture
def cache(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    yield cache
    cache.close()


@pytest.fixture(autouse=True)
def files_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sharepoint, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(sharepoint.time, "sleep", lambda seconds: None)
    return tmp_path / "files"


class FakeContext:
    """Answers the request for a file's content with a redirect, as SharePoint does."""

    def __init__(self, status=302):
        self.asked = []
        self.request = SimpleNamespace(get=self._get)
        self.status = status

    def _get(self, url, max_redirects=0):
        self.asked.append(url)
        return SimpleNamespace(status=self.status, headers={"location": url.replace("/content", "/signed")})


def _file(name: str, ctag: str = "c1", size: int = 5) -> dict:
    return {"id": name.split(".")[0], "api": f"https://example.test/items/{name}", "name": name, "size": size, "ctag": ctag}


def _saver(content: bytes = b"hello", fail_for: str = ""):
    saved = []

    def save(link, target):
        saved.append(link)
        if fail_for and fail_for in link:
            raise OSError("connection reset")
        Path(target).write_bytes(content)
    return save, saved


def test_a_new_file_is_downloaded_and_remembered(cache, files_dir, monkeypatch):
    save, saved = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    file = _file("deck.pdf")
    assert sharepoint.download(FakeContext(), [file], cache) == (1, [])
    assert Path(file["path"]) == files_dir / "deck.pdf" and len(file["sha"]) == 64
    assert saved == ["https://example.test/items/deck.pdf/signed"]
    assert cache.get("download", "deck") == {"ctag": "c1", "path": file["path"], "sha": file["sha"]}


def test_an_unchanged_file_is_not_fetched_again(cache, monkeypatch):
    save, saved = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    sharepoint.download(FakeContext(), [_file("deck.pdf")], cache)
    context = FakeContext()
    again = _file("deck.pdf")
    assert sharepoint.download(context, [again], cache) == (0, [])
    assert context.asked == [] and len(saved) == 1 and "path" in again


def test_a_changed_file_is_fetched_again(cache, monkeypatch):
    save, saved = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    sharepoint.download(FakeContext(), [_file("deck.pdf", ctag="c1")], cache)
    assert sharepoint.download(FakeContext(), [_file("deck.pdf", ctag="c2")], cache) == (1, [])
    assert len(saved) == 2 and cache.get("download", "deck")["ctag"] == "c2"


def test_a_cached_file_that_is_gone_from_disk_is_fetched_again(cache, monkeypatch):
    save, saved = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    first = _file("deck.pdf")
    sharepoint.download(FakeContext(), [first], cache)
    Path(first["path"]).unlink()
    assert sharepoint.download(FakeContext(), [_file("deck.pdf")], cache) == (1, [])


def test_a_download_is_tried_three_times_and_then_reported(cache, monkeypatch):
    save, saved = _saver(fail_for="broken")
    monkeypatch.setattr(sharepoint, "_save", save)
    good, bad = _file("deck.pdf"), _file("broken.pdf")
    fetched, failed = sharepoint.download(FakeContext(), [good, bad], cache)
    assert (fetched, failed) == (1, [bad])
    assert len([link for link in saved if "broken" in link]) == 3
    assert "connection reset" in bad["error"] and "path" not in bad
    assert "path" in good and "error" not in good


def test_a_changed_file_that_cannot_be_fetched_keeps_its_last_copy(cache, monkeypatch):
    save, _ = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    first = _file("deck.pdf", ctag="c1")
    sharepoint.download(FakeContext(), [first], cache)
    failing, _ = _saver(fail_for="deck")
    monkeypatch.setattr(sharepoint, "_save", failing)
    changed = _file("deck.pdf", ctag="c2")
    fetched, failed = sharepoint.download(FakeContext(), [changed], cache)
    assert (fetched, failed) == (0, [changed])
    assert (changed["path"], changed["sha"]) == (first["path"], first["sha"])
    assert changed["error"].endswith("the last copy is used")
    # The cache still says c1, so the next run tries the new version again
    assert cache.get("download", "deck")["ctag"] == "c1"


def test_a_file_with_no_download_link_is_reported(cache, monkeypatch):
    save, saved = _saver()
    monkeypatch.setattr(sharepoint, "_save", save)
    file = _file("deck.pdf")
    fetched, failed = sharepoint.download(FakeContext(status=403), [file], cache)
    assert (fetched, failed, saved) == (0, [file], [])
    assert file["error"] == "no download link, status 403"


def test_a_short_or_empty_download_is_thrown_away(tmp_path, monkeypatch):
    class Reply:
        def __init__(self, blocks, promised):
            self.blocks = [*blocks, b""]
            self.headers = {"Content-Length": promised} if promised else {}

        def read(self, size):
            return self.blocks.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def serve(blocks, promised):
        monkeypatch.setattr(sharepoint.urllib.request, "urlopen", lambda link, timeout: Reply(blocks, promised))

    target = tmp_path / "deck.pdf"
    target.write_bytes(b"the copy from last time")
    serve([b"abc"], "10")
    with pytest.raises(OSError, match="got 3 bytes, expected 10"):
        sharepoint._save("https://example.test/signed", target)
    serve([], None)
    with pytest.raises(OSError, match="got 0 bytes"):
        sharepoint._save("https://example.test/signed", target)
    assert target.read_bytes() == b"the copy from last time"
    assert not (tmp_path / "deck.pdf.part").exists()
    serve([b"abc", b"def"], "6")
    sharepoint._save("https://example.test/signed", target)
    assert target.read_bytes() == b"abcdef"
    # A server that does not say the length is taken at its word
    serve([b"xyz"], None)
    sharepoint._save("https://example.test/signed", target)
    assert target.read_bytes() == b"xyz"


class FakePage:
    """Answers the in-page fetches from a table of {address ending: (status, body)}."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def evaluate(self, script, arguments):
        self.asked.append(arguments["urls"])
        results = []
        for url in arguments["urls"]:
            status, body = next((answer for ending, answer in self.answers.items() if url.endswith(ending)), (404, ""))
            results.append({"status": status, "text": json.dumps(body) if status == 200 else ""})
        return results


def _video(name: str) -> dict:
    return {"id": name, "api": f"https://example.test/items/{name}", "name": f"{name}.mp4"}


def _listing(ctag: str) -> dict:
    return {"value": [{"id": "other", "cTag": "x", "isDefault": False}, {"id": "t1", "cTag": ctag, "isDefault": True}]}


BODY = {"entries": [{"text": "hello", "startOffset": "00:00:01.0", "endOffset": "00:00:02.0", "speakerDisplayName": "A Student"}]}
ENTRIES = [{"text": "hello", "startOffset": "00:00:01.0", "endOffset": "00:00:02.0"}]


def test_transcripts_are_fetched_once_and_keep_no_speaker_names(cache):
    page = FakePage({"rec1/media/transcripts": (200, _listing("v1")), "t1/streamContent?format=json": (200, BODY),
                     "rec2/media/transcripts": (200, {"value": []})})
    files = [_video("rec1"), _video("rec2"), {"id": "deck", "api": "x", "name": "deck.pdf"}]
    assert sharepoint.fetch_transcripts(page, files, cache) == {"rec1": ENTRIES}
    assert len(page.asked) == 2 and len(page.asked[0]) == 2
    # The second run asks for the lists only: the content tag has not changed
    page.asked.clear()
    assert sharepoint.fetch_transcripts(page, files, cache) == {"rec1": ENTRIES}
    assert len(page.asked) == 1


def test_a_changed_transcript_is_fetched_again(cache):
    answers = {"rec1/media/transcripts": (200, _listing("v1")), "t1/streamContent?format=json": (200, BODY)}
    sharepoint.fetch_transcripts(FakePage(answers), [_video("rec1")], cache)
    answers["rec1/media/transcripts"] = (200, _listing("v2"))
    answers["t1/streamContent?format=json"] = (200, {"entries": []})
    assert sharepoint.fetch_transcripts(FakePage(answers), [_video("rec1")], cache) == {"rec1": []}


def test_a_transcript_that_cannot_be_fetched_keeps_its_last_copy(cache):
    answers = {"rec1/media/transcripts": (200, _listing("v1")), "t1/streamContent?format=json": (200, BODY)}
    sharepoint.fetch_transcripts(FakePage(answers), [_video("rec1")], cache)
    answers["rec1/media/transcripts"] = (200, _listing("v2"))
    answers["t1/streamContent?format=json"] = (503, "")
    assert sharepoint.fetch_transcripts(FakePage(answers), [_video("rec1")], cache) == {"rec1": ENTRIES}
    answers["rec1/media/transcripts"] = (429, "")
    assert sharepoint.fetch_transcripts(FakePage(answers), [_video("rec1")], cache) == {"rec1": ENTRIES}
    # A recording never fetched before has nothing to fall back to
    assert sharepoint.fetch_transcripts(FakePage({"new/media/transcripts": (503, "")}), [_video("new")], cache) == {}


def test_a_retry_after_header_is_read_as_seconds_or_as_a_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from sync.session import retry_after
    assert retry_after("7", 2.0) == 7.0
    assert retry_after(None, 2.0) == 2.0
    assert retry_after("soon", 2.0) == 2.0
    assert retry_after("-5", 2.0) == 0.0
    later = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    assert 25 < retry_after(later, 2.0) <= 30
    assert retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 2.0) == 0.0
