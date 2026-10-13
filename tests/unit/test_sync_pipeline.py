"""
Unit tests for the course sync after collecting: the cache, the model calls, reading a
document, and writing the course file. The model is a fake, and nothing touches the network.
"""
import base64
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
import weasyprint
from PIL import Image

from src.course_store import DIMENSIONS, CourseStore
from sync import reading
from sync.build import BadRun, CourseMaterial, Report, build, check
from sync.cache import Cache, file_sha, sha
from sync.material import Course
from sync.models import Models, SpendCapReached
from sync.reading import ReadError, read_document, split_descriptions
from sync.upload import UploadFailed, upload

THAI = "หยิบไพ่ หยิบ 5 ใบ จากกองไพ่ จะมีกี่วิธี"


class FakeClient:
    """Stands in for the Azure client. Records what it was asked and answers from a script."""

    def __init__(self, describe=None):
        self.embedded = []
        self.chats = []
        self.describe = describe or (lambda prompt, images: "")
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _embed(self, model, input, dimensions):
        self.embedded.append(list(input))
        data = []
        for index, text in enumerate(input):
            seed = hashlib.sha256(text.encode()).digest()
            vector = [0.0] * dimensions
            vector[seed[0]] = 1.0
            data.append(SimpleNamespace(index=index, embedding=vector))
        # Out of order on purpose: the caller has to go by index
        return SimpleNamespace(data=data[::-1], usage=SimpleNamespace(prompt_tokens=1000 * len(input), completion_tokens=0))

    def _chat(self, model, reasoning_effort, max_completion_tokens, messages):
        content = messages[0]["content"]
        if isinstance(content, str):
            self.chats.append(("translate", content))
            answer = "English: " + content.split("\n\n", 1)[1][:20]
        else:
            images = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
            self.chats.append(("describe", content[0]["text"], len(images)))
            answer = self.describe(content[0]["text"], images)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
                               usage=SimpleNamespace(prompt_tokens=10_000, completion_tokens=2_000))


@pytest.fixture(autouse=True)
def no_nicknames(monkeypatch):
    """The real list of course nicknames is a setting. A test that is about them sets its own."""
    from sync import build as build_module
    monkeypatch.setattr(build_module, "NICKNAMES", {})


@pytest.fixture
def cache(tmp_path):
    cache = Cache(tmp_path / "home" / "cache.db")
    yield cache
    cache.close()


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def models(cache, client):
    return Models(cache, client=client, cap=5.0)


def _pdf(tmp_path: Path, pages, name="deck.pdf") -> Path:
    """pages is a list of "text", "picture", "thai" or "empty"."""
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), "navy").save(buffer, "PNG")
    picture = base64.b64encode(buffer.getvalue()).decode()
    body = {
        "text": "<p>" + "Plain words about gradient descent and learning rates. " * 6 + "</p>",
        "picture": f'<p>Confusion matrix</p><img src="data:image/png;base64,{picture}">',
        "thai": f"<p style=\"font-family: 'Noto Sans Thai'\">{THAI} {THAI}</p>",
        "empty": "<p>&nbsp;</p>",
    }
    html = "".join(f'<div style="page-break-after:always">{body[kind]}</div>' for kind in pages)
    path = tmp_path / name
    path.write_bytes(weasyprint.HTML(string=html).write_pdf())
    return path


# The cache

def test_the_cache_keeps_values_across_runs(tmp_path):
    first = Cache(tmp_path / "cache.db")
    first.put("download", "item-1", {"ctag": "a", "path": "/x"})
    first.put("download", "item-1", {"ctag": "b", "path": "/x"})
    first.put_vectors("model:512", ["one", "two"], [[0.5, 0.25], [1.0, 0.0]])
    first.close()
    second = Cache(tmp_path / "cache.db")
    assert second.get("download", "item-1") == {"ctag": "b", "path": "/x"}
    assert second.get("download", "item-2") is None
    assert second.get("reading", "item-1") is None
    assert second.vectors("model:512", ["two", "three", "one"]) == {"two": [1.0, 0.0], "one": [0.5, 0.25]}
    assert second.vectors("other-model", ["one"]) == {}
    second.close()


def test_a_file_hash_is_the_hash_of_its_bytes(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"course material")
    assert file_sha(path) == hashlib.sha256(b"course material").hexdigest()
    assert sha("course material") == file_sha(path)


# Model calls

def test_embeddings_come_back_in_order_and_are_paid_for_once(models, client):
    first = models.embed(["alpha", "beta", "alpha"])
    assert len(first) == 3 and all(len(vector) == DIMENSIONS for vector in first)
    assert first[0] == first[2] != first[1]
    assert client.embedded == [["alpha", "beta"]]
    assert models.embed(["beta", "gamma"])[0] == first[1]
    assert client.embedded == [["alpha", "beta"], ["gamma"]]
    # 2,000 and 1,000 tokens at $0.02 per million
    assert models.spent == pytest.approx(3000 * 0.02 / 1e6)


def test_long_lists_are_embedded_in_batches(models, client):
    models.embed([f"text {number}" for number in range(250)])
    assert [len(batch) for batch in client.embedded] == [100, 100, 50]
    client.embedded.clear()
    models.embed(["x" * 50_000 + str(number) for number in range(5)])
    assert [len(batch) for batch in client.embedded] == [2, 2, 1]


def test_a_run_stops_at_its_spending_limit(cache, client):
    models = Models(cache, client=client, cap=0.001)
    models.describe("what is on these pages", ["data:image/jpeg;base64,AAAA"])   # $0.002
    with pytest.raises(SpendCapReached):
        models.embed(["never sent"])
    assert client.embedded == []


def test_a_translation_is_cached_and_an_empty_answer_keeps_the_original(models, client, cache):
    assert models.translate([THAI, THAI]) == ["English: " + THAI[:20]] * 2
    assert models.translate([THAI]) == ["English: " + THAI[:20]]
    assert cache.get("translation", sha(THAI)) == "English: " + THAI[:20]
    assert len({chat for chat in client.chats}) == 1
    client.chat.completions.create = lambda **_: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0))
    assert models.translate(["ข้อความอื่นที่ยังไม่เคยแปล"]) == ["ข้อความอื่นที่ยังไม่เคยแปล"]


# Reading a document

def test_the_answer_is_split_by_page_heading():
    answer = "## Page 3\nA table:\n| a | b |\n\n## Page 4\nnothing\n\n### page 5 (chart)\nBar chart of sales.\n## Page 6\nNothing."
    assert split_descriptions(answer) == {3: "A table:\n| a | b |", 5: "Bar chart of sales."}
    assert split_descriptions("no headings at all") == {}


def test_a_pdf_page_is_its_text_plus_what_the_picture_adds(tmp_path, cache):
    client = FakeClient(describe=lambda prompt, images: "## Page 1\nnothing\n## Page 2\nRows: 120 true positives, 40 false negatives.")
    path = _pdf(tmp_path, ["text", "picture"])
    result = read_document(path, ".pdf", "hash-1", Models(cache, client=client), cache)
    assert [page.number for page in result.pages] == [1, 2]
    assert result.pages[0].text.startswith("Plain words about gradient descent")
    assert result.pages[1].text == "Confusion matrix\nRows: 120 true positives, 40 false negatives."
    assert result.pages[1].original is None
    assert (result.described, result.failed_requests) == (1, 0)
    # Only the page with the picture was sent, with the text already extracted from it
    kind, prompt, pictures = client.chats[0]
    assert (kind, pictures) == ("describe", 1)
    assert "These are pages 2 of" in prompt and "Page 2: Confusion matrix" in prompt


def test_descriptions_are_cached_per_group_of_pages(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(reading, "PAGES_PER_REQUEST", 2)
    client = FakeClient(describe=lambda prompt, images: "## Page 1\nfirst\n## Page 2\nsecond\n## Page 3\nthird")
    path = _pdf(tmp_path, ["picture", "picture", "picture"])
    read_document(path, ".pdf", "hash-2", Models(cache, client=client), cache)
    assert [chat[2] for chat in client.chats] == [2, 1]
    read_document(path, ".pdf", "hash-2", Models(cache, client=client), cache)
    assert len(client.chats) == 2
    # Other content is another document, even under the same name
    read_document(path, ".pdf", "hash-3", Models(cache, client=client), cache)
    assert len(client.chats) == 4


def test_a_description_for_a_page_that_was_not_sent_is_ignored(tmp_path, cache):
    client = FakeClient(describe=lambda prompt, images: "## Page 1\nmade up\n## Page 2\nreal")
    path = _pdf(tmp_path, ["text", "picture"])
    result = read_document(path, ".pdf", "hash-4", Models(cache, client=client), cache)
    assert "made up" not in result.pages[0].text
    assert result.pages[1].text.endswith("real")


def test_a_failed_request_keeps_the_text_and_is_tried_again_next_time(tmp_path, cache):
    def broken(prompt, images):
        raise TimeoutError("the model did not answer")
    path = _pdf(tmp_path, ["picture"])
    first = read_document(path, ".pdf", "hash-5", Models(cache, client=FakeClient(describe=broken)), cache)
    assert first.failed_requests == 1
    assert [page.text for page in first.pages] == ["Confusion matrix"]
    working = FakeClient(describe=lambda prompt, images: "## Page 1\nA 2 by 2 table.")
    second = read_document(path, ".pdf", "hash-5", Models(cache, client=working), cache)
    assert (second.failed_requests, second.pages[0].text) == (0, "Confusion matrix\nA 2 by 2 table.")


def test_the_spending_limit_stops_a_reading_instead_of_being_counted_as_a_failure(tmp_path, cache):
    path = _pdf(tmp_path, ["picture"])
    models = Models(cache, client=FakeClient(), cap=0.0)
    with pytest.raises(SpendCapReached):
        read_document(path, ".pdf", "hash-6", models, cache)


def test_a_thai_page_is_translated_and_keeps_its_own_wording(tmp_path, cache, client):
    path = _pdf(tmp_path, ["thai"])
    result = read_document(path, ".pdf", "hash-7", Models(cache, client=client), cache)
    assert len(result.pages) == 1
    assert result.pages[0].text.startswith("English: ")
    assert "หยิบ" in (result.pages[0].original or "")


def test_an_empty_page_is_not_a_page(tmp_path, cache, client):
    path = _pdf(tmp_path, ["text", "empty"])
    result = read_document(path, ".pdf", "hash-8", Models(cache, client=client), cache)
    assert [page.number for page in result.pages] == [1]


def test_a_text_file_is_cut_into_parts(tmp_path, cache, client, monkeypatch):
    monkeypatch.setattr(reading, "TEXT_PIECE_CHARS", 40)
    monkeypatch.setattr(reading, "TEXT_PIECES", 2)
    path = tmp_path / "data.csv"
    path.write_text("name,score\n" + "\n".join(f"student{number},{number}" for number in range(20)), encoding="utf-8")
    result = read_document(path, ".csv", "hash-9", Models(cache, client=client), cache)
    assert len(result.pages) == 2 and result.pages[0].text.startswith("name,score")
    assert result.note.startswith("only the first 2 of ")
    assert client.chats == []


def test_a_file_that_is_not_a_pdf_is_reported(tmp_path, cache, client):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(ReadError):
        read_document(path, ".pdf", "hash-10", Models(cache, client=client), cache)


# Writing the course file

def _entry(second: int, text: str) -> dict:
    def offset(total: int) -> str:
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}.0000000"
    return {"text": text, "startOffset": offset(second), "endOffset": offset(second + 8)}


def _document(path: Path, library="Class Materials", folder="/Week 5", name=None) -> dict:
    return {"id": path.name, "name": name or path.name, "library": library, "folder": folder, "path": str(path),
            "sha": file_sha(path), "modified": "2026-09-01T03:00:00Z", "created": "2026-09-01T03:00:00Z",
            "url": f"https://example.test/{path.name}"}


@pytest.fixture
def material(tmp_path):
    deck = _pdf(tmp_path, ["text", "picture"])
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(deck.read_bytes())
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    recording = {"id": "rec-1", "name": "CSC340_20260804-20260804_090049-Meeting Recording.mp4",
                 "created": "2026-08-04T02:00:49Z", "url": "https://example.test/recording"}
    entries = [_entry(second, f"the agent perceives its environment {second}") for second in range(0, 300, 10)]
    return CourseMaterial(
        Course("CSC340", "Artificial Intelligence", ("BIF651",)),
        sites=[{"title": "1-2569_CSC340", "url": "https://example.test/site", "group": "group-1"}],
        documents=[_document(copy, library="Class Files", folder="/Assignments/A2"), _document(deck), _document(broken)],
        transcripts=[(recording, entries)],
        recordings_without_transcript=["CSC340_week3.mp4"])


def test_names_said_in_a_lecture_do_not_reach_the_course_file(tmp_path, material, cache):
    import sqlite3
    recording, _ = material.transcripts[0]
    material.transcripts = [(recording, [_entry(second, f"OK SOMCHAI JAIDEE, the agent perceives its environment {second}")
                                         for second in range(0, 300, 10)])]
    out = tmp_path / "out" / "courses.db"
    build([material], Models(cache, client=FakeClient(describe=lambda prompt, images: "## Page 2\nRows.")), cache, out, Report())
    spoken = " ".join(row[0] for row in sqlite3.connect(out).execute(
        "SELECT text FROM chunks JOIN sources ON sources.id = source_id WHERE kind = 'transcript'"))
    assert "OK [name], the agent perceives" in spoken
    assert "SOMCHAI" not in spoken and "JAIDEE" not in spoken


def test_a_run_writes_a_course_file_the_bot_can_search(tmp_path, material, cache):
    client = FakeClient(describe=lambda prompt, images: "## Page 2\nRows: 120 true positives.")
    models = Models(cache, client=client)
    out = tmp_path / "out" / "courses.db"
    report = Report()
    build([material], models, cache, out, report)

    store = CourseStore.open(str(out))
    assert store.courses() == [{"code": "CSC340", "name": "Artificial Intelligence", "aliases": ["BIF651"]}]
    # The copy under Class Files is the same content, so the deck is stored once, under its lecture
    assert store.counts() == {"CSC340": {"slide": 1, "transcript": 1}}
    assert store.get_meta("embedding_model") == "text-embedding-3-small"
    assert store.get_meta("embedding_dimensions") == "512" and store.get_meta("synced_at")

    wording = "CSC340 Artificial Intelligence | slide | deck.pdf | Week 5 | page 2\nConfusion matrix\nRows: 120 true positives."
    hit = store.search("confusion matrix rows", models.embed([wording])[0])[0]
    assert (hit.kind, hit.title, hit.section, hit.locator) == ("slide", "deck.pdf", "Week 5", "page 2")
    assert (hit.url, hit.date) == ("https://example.test/deck.pdf", "2026-09-01")
    assert hit.before.startswith("Plain words")

    spoken = store.search("agent environment", models.embed([
        "CSC340 Artificial Intelligence | transcript | CSC340_20260804 | 0:00:00\n"
        + " ".join(f"the agent perceives its environment {second}" for second in range(0, 80, 10))])[0], kind="transcript")[0]
    assert (spoken.title, spoken.locator, spoken.date) == ("CSC340_20260804", "0:00:00", "2026-08-04")
    store.close()

    text = "\n".join(report.lines)
    assert "CSC340 Artificial Intelligence: 2 documents, 2 pages (1 described), 1 transcripts" in text
    assert "1 copies of a file already read" in text and "no transcript: CSC340_week3.mp4" in text
    assert any("read broken.pdf" in problem for problem in report.problems)
    assert not (tmp_path / "out" / "courses.db.new").exists()


def _assignment(number: int, due: str, instructions: str) -> dict:
    return {"id": f"a{number}", "classId": "group-1", "displayName": f"Lab {number}", "dueDateTime": due,
            "instructions": {"content": f"<div>{instructions}</div>", "contentType": "html"},
            "grading": {"maxPoints": 5}, "resources": [{"resource": {"displayName": f"Lab{number}.pptx"}}],
            "webUrl": f"https://example.test/assignment/{number}"}


def test_assignments_are_stored_as_material_of_their_course(tmp_path, material, cache):
    material.assignments = [_assignment(7, "2026-09-27T18:30:00Z", "Train a classifier and report its confusion matrix."),
                            _assignment(2, "2026-08-13T16:59:00Z", f"{THAI} {THAI}")]
    models = Models(cache, client=FakeClient(describe=lambda prompt, images: "## Page 2\nRows."))
    out = tmp_path / "courses.db"
    report = Report()
    build([material], models, cache, out, report)

    store = CourseStore.open(str(out))
    assert store.counts()["CSC340"] == {"slide": 1, "transcript": 1, "assignment": 2}
    # The date of an assignment is the Bangkok day it is due
    assert store.newest()["CSC340"]["assignment"] == "2026-09-28"
    wording = ("CSC340 Artificial Intelligence | assignment | Lab 7\nLab 7\nDue 2026-09-28 01:30 Bangkok time\n5 points\n"
               "Train a classifier and report its confusion matrix.\nAttached: Lab7.pptx")
    hit = store.search("classifier confusion matrix", models.embed([wording])[0], kind="assignment")[0]
    assert (hit.kind, hit.title, hit.date, hit.url) == ("assignment", "Lab 7", "2026-09-28", "https://example.test/assignment/7")
    assert hit.text == wording.split("\n", 1)[1]

    import sqlite3
    thai = sqlite3.connect(out).execute(
        "SELECT text, original FROM chunks JOIN sources ON sources.id = source_id WHERE title = 'Lab 2'").fetchone()
    # Thai instructions are searched in English and keep their own wording
    assert thai[0].startswith("English: ") and THAI in thai[1]
    store.close()
    assert "2 assignments" in "\n".join(report.lines)


def test_the_nicknames_of_a_course_are_written_beside_its_codes(tmp_path, material, cache, monkeypatch):
    from sync import build as build_module
    monkeypatch.setattr(build_module, "NICKNAMES", {"CSC340": ("ai", "ai class"), "CSC999": ("not synced",)})
    out = tmp_path / "courses.db"
    build([material], Models(cache, client=FakeClient(describe=lambda prompt, images: "## Page 2\nRows.")), cache, out, Report())
    store = CourseStore.open(str(out))
    # The codes from the team's title first, then what people call the course
    assert store.courses() == [{"code": "CSC340", "name": "Artificial Intelligence", "aliases": ["BIF651", "ai", "ai class"]}]
    store.close()


def test_a_second_run_with_nothing_new_calls_no_model(tmp_path, material, cache):
    client = FakeClient(describe=lambda prompt, images: "## Page 2\nRows.")
    out = tmp_path / "courses.db"
    build([material], Models(cache, client=client), cache, out, Report())
    calls = (len(client.chats), len(client.embedded))
    again = Models(cache, client=client)
    build([material], again, cache, out, Report())
    assert (len(client.chats), len(client.embedded)) == calls
    assert again.spent == 0
    # The file from the first run is kept beside the new one
    assert CourseStore.open(str(tmp_path / "courses.db.previous")).chunk_counts() == CourseStore.open(str(out)).chunk_counts()


def test_a_run_that_lost_most_of_a_course_does_not_replace_the_old_file(tmp_path, material, cache, client):
    out = tmp_path / "courses.db"
    build([material], Models(cache, client=client), cache, out, Report())
    before = out.read_bytes()
    material.transcripts = []
    material.documents = material.documents[:1]
    with pytest.raises(BadRun, match="CSC340 had"):
        build([material], Models(cache, client=client), cache, out, Report())
    assert out.read_bytes() == before
    assert not (tmp_path / "courses.db.new").exists()
    build([material], Models(cache, client=client), cache, out, Report(), force=True)
    assert out.read_bytes() != before


def test_a_course_that_vanished_or_an_empty_file_fails_the_check(tmp_path, material, cache, client):
    old = tmp_path / "old.db"
    build([material], Models(cache, client=client), cache, old, Report())
    other = CourseMaterial(Course("CSC345", "Data Science", ()), sites=[{"title": "x", "url": "u", "group": ""}],
                           transcripts=material.transcripts)
    new = tmp_path / "new.db"
    build([other], Models(cache, client=client), cache, new, Report())
    with pytest.raises(BadRun, match="CSC340 had"):
        check(new, old)
    check(new, None)

    empty = tmp_path / "empty.db"
    CourseStore.create(str(empty)).close()
    with pytest.raises(BadRun, match="no chunks"):
        check(empty, None)
    garbage = tmp_path / "garbage.db"
    garbage.write_text("not a database")
    with pytest.raises(BadRun, match="cannot be read back"):
        check(garbage, None)


# Uploading

class FakeShell:
    """Stands in for scp and ssh. Answers the hash check with the hash it was given."""

    def __init__(self, remote_hash="", fail_on=""):
        self.commands = []
        self.remote_hash = remote_hash
        self.fail_on = fail_on

    def __call__(self, command, **_):
        self.commands.append(command)
        failed = bool(self.fail_on) and self.fail_on in " ".join(command)
        out = f"{self.remote_hash}  /srv/courses.db.arriving\n" if "sha256sum" in command[-1] else ""
        return SimpleNamespace(returncode=1 if failed else 0, stdout=out, stderr="connection refused" if failed else "")


def test_an_upload_is_copied_checked_and_then_renamed(tmp_path):
    local = tmp_path / "courses.db"
    local.write_bytes(b"course file")
    shell = FakeShell(remote_hash=file_sha(local))
    upload(local, server="bot@example.test", remote="/srv/courses.db", run=shell)
    assert [command[0] for command in shell.commands] == ["scp", "ssh", "ssh"]
    assert shell.commands[0][-2:] == [str(local), "bot@example.test:/srv/courses.db.arriving"]
    assert shell.commands[2][-1] == "mv -f /srv/courses.db.arriving /srv/courses.db"


def test_a_copy_that_differs_is_removed_and_never_renamed(tmp_path):
    local = tmp_path / "courses.db"
    local.write_bytes(b"course file")
    shell = FakeShell(remote_hash="0" * 64)
    with pytest.raises(UploadFailed, match="differs"):
        upload(local, server="bot@example.test", remote="/srv/courses.db", run=shell)
    assert shell.commands[-1][-1] == "rm -f /srv/courses.db.arriving"
    assert not any(command[-1].startswith("mv") for command in shell.commands)


def test_a_failed_copy_stops_the_upload(tmp_path):
    local = tmp_path / "courses.db"
    local.write_bytes(b"course file")
    shell = FakeShell(fail_on="scp")
    with pytest.raises(UploadFailed, match="copying the file failed: connection refused"):
        upload(local, server="bot@example.test", remote="/srv/courses.db", run=shell)
    assert len(shell.commands) == 1
    with pytest.raises(UploadFailed, match="no server is set"):
        upload(local, server="", run=shell)
