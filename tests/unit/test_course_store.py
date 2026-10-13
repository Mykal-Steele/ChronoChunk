"""Unit tests for the course material file: writing it, searching it, and noticing when the sync replaces it."""
import math
import os
import sqlite3

import pytest

from src.course_store import DIMENSIONS, MIN_SIMILARITY, SCHEMA_VERSION, CourseLibrary, CourseStore


def direction(**weights: float) -> list:
    """
    A made-up embedding. Each name is one axis, so two vectors are as alike as the
    axes they share: direction(pca=1) and direction(pca=0.8, other=0.6) score 0.8.
    """
    axes = {"pca": 0, "bayes": 1, "agents": 2, "other": 3, "filler": 4}
    vector = [0.0] * DIMENSIONS
    for name, weight in weights.items():
        vector[axes[name]] = weight
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector]


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "state" / "courses.db")


@pytest.fixture
def store(path):
    """Two courses: an AI deck and lecture, and a data science lecture."""
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence", aliases=["the AI class", "BIF651"], group_id="g1")
    store.add_course("CSC345", "Data Science")
    deck = store.add_source("CSC340", "slide", "5th--Predict Classify Regression.pdf", section="Week 5",
                            url="https://example.test/deck", date="2026-09-01")
    store.add_chunk(deck, 0, "page 34", "Curse of dimensionality", direction(pca=0.6, other=0.8))
    store.add_chunk(deck, 1, "page 35", "Principal component analysis reduces the dimensions", direction(pca=1))
    store.add_chunk(deck, 2, "page 36", "PC1 and PC2", direction(pca=0.7, other=0.7))
    lecture = store.add_source("CSC340", "transcript", "CSC340_20260804", date="2026-08-04")
    store.add_chunk(lecture, 0, "0:01:12", "This lesson is about the intelligent agent", direction(agents=1))
    other = store.add_source("CSC345", "transcript", "Lecture 9", date="2026-10-06")
    store.add_chunk(other, 0, "2:45:25", "Naive Bayes is simple and pretty good", direction(bayes=1))
    store.save()
    yield store
    store.close()


def test_a_new_file_can_be_opened_read_only(store, path):
    reader = CourseStore.open(path)
    assert reader.courses() == [
        {"code": "CSC340", "name": "Artificial Intelligence", "aliases": ["the AI class", "BIF651"]},
        {"code": "CSC345", "name": "Data Science", "aliases": []}]
    assert reader.counts() == {"CSC340": {"slide": 1, "transcript": 1}, "CSC345": {"transcript": 1}}
    with pytest.raises(sqlite3.OperationalError):
        reader.add_course("CSC999", "Not allowed")
    reader.close()


def test_create_does_not_write_over_an_existing_file(store, path):
    with pytest.raises(FileExistsError):
        CourseStore.create(path)


def test_a_file_from_another_version_of_the_sync_is_refused(path):
    store = CourseStore.create(path)
    store._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    store.save()
    store.close()
    with pytest.raises(ValueError, match="schema version"):
        CourseStore.open(path)


def test_a_file_written_before_due_times_were_stored_can_still_be_read(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    source = store.add_source("CSC340", "assignment", "Mini project", date="2026-11-16")
    store.add_chunk(source, 0, "", "Mini project\nDue 2026-11-16 23:59 Bangkok time", direction(agents=1))
    # Version 1 had neither column
    store._connection.executescript(
        "CREATE TABLE old AS SELECT id, course, kind, title, section, url, date, content_hash FROM sources;"
        "DROP TABLE sources; ALTER TABLE old RENAME TO sources; PRAGMA user_version = 1;")
    store.save()
    store.close()
    reader = CourseStore.open(path)
    assert reader.assignments() == [{"course": "CSC340", "title": "Mini project", "date": "2026-11-16", "url": "",
                                     "text": "Mini project\nDue 2026-11-16 23:59 Bangkok time", "due": "", "meta": {}}]
    assert reader.search("x", direction(agents=1))[0].title == "Mini project"
    reader.close()


def test_a_source_keeps_its_due_time_and_what_else_is_known_about_it(store):
    assert SCHEMA_VERSION == 2
    source = store.add_source("CSC340", "assignment", "Lab 7", date="2026-10-13", due="2026-10-13T23:59:00+07:00",
                              meta={"points": 5, "attached": ["Lab7.pptx"]})
    store.add_chunk(source, 0, "", "Lab 7", direction(agents=1))
    [kept] = store.assignments("CSC340")
    assert kept["due"] == "2026-10-13T23:59:00+07:00" and kept["meta"] == {"points": 5, "attached": ["Lab7.pptx"]}


def test_opening_a_missing_file_fails_and_creates_nothing(path):
    with pytest.raises(sqlite3.OperationalError):
        CourseStore.open(path)
    assert not os.path.exists(path)


def test_meta_values_are_kept_and_replaced(store):
    assert store.get_meta("synced_at") is None
    store.set_meta("synced_at", "2026-10-12")
    store.set_meta("synced_at", "2026-10-19")
    assert store.get_meta("synced_at") == "2026-10-19"


def test_newest_gives_the_latest_date_of_each_kind_in_each_course(store):
    store.add_source("CSC340", "transcript", "CSC340_20260818", date="2026-08-18")
    store.add_source("CSC340", "transcript", "CSC340_20260811", date="2026-08-11")
    assert store.newest() == {"CSC340": {"slide": "2026-09-01", "transcript": "2026-08-18"},
                              "CSC345": {"transcript": "2026-10-06"}}


def test_newest_leaves_out_sources_with_no_date(store):
    store.add_source("CSC345", "slide", "Undated handout.pdf")
    store.add_source("CSC345", "post", "Welcome")
    assert store.newest()["CSC345"] == {"transcript": "2026-10-06"}


def test_assignments_come_back_by_due_day_with_what_they_ask_for(store):
    late = store.add_source("CSC340", "assignment", "Mini project", url="https://example.test/a/2", date="2026-11-16")
    store.add_chunk(late, 0, "", "Mini project\nDue 2026-11-16 23:59 Bangkok time", direction(agents=1))
    early = store.add_source("CSC340", "assignment", "Assignment 5: Search", url="https://example.test/a/1", date="2026-09-22")
    store.add_chunk(early, 0, "", "Assignment 5: Search\nDue 2026-09-22 23:59 Bangkok time\n10 points", direction(agents=1))
    undated = store.add_source("CSC345", "assignment", "Survey")
    store.add_chunk(undated, 0, "", "Survey", direction(bayes=1))
    assert [(item["course"], item["title"], item["date"]) for item in store.assignments()] == [
        ("CSC345", "Survey", ""), ("CSC340", "Assignment 5: Search", "2026-09-22"), ("CSC340", "Mini project", "2026-11-16")]
    first = store.assignments("CSC340")[0]
    assert first == {"course": "CSC340", "title": "Assignment 5: Search", "date": "2026-09-22", "due": "", "meta": {},
                     "url": "https://example.test/a/1", "text": "Assignment 5: Search\nDue 2026-09-22 23:59 Bangkok time\n10 points"}
    assert [item["title"] for item in store.assignments("CSC345")] == ["Survey"]
    assert store.assignments("CSC999") == []


def test_search_returns_the_chunk_with_its_source_and_neighbours(store):
    hits = store.search("how to reduce dimensions", direction(pca=1))
    assert len(hits) == 1
    hit = hits[0]
    assert (hit.course, hit.kind, hit.title, hit.section) == ("CSC340", "slide", "5th--Predict Classify Regression.pdf", "Week 5")
    assert (hit.locator, hit.url, hit.date) == ("page 35", "https://example.test/deck", "2026-09-01")
    assert hit.text == "Principal component analysis reduces the dimensions"
    assert (hit.before, hit.after) == ("Curse of dimensionality", "PC1 and PC2")
    assert hit.similarity == 1.0


def test_the_first_and_last_chunk_have_one_neighbour(store):
    hit = store.search("intelligent agent", direction(agents=1))[0]
    assert (hit.before, hit.after) == ("", "")


def test_neighbouring_chunks_come_back_as_one_result(store):
    # All three pages of the deck are close enough, and they sit next to each other
    hits = store.search("dimensions", direction(pca=1))
    assert [hit.locator for hit in hits] == ["page 35"]


def test_one_source_gives_at_most_two_results_that_are_apart(path):
    store = CourseStore.create(path)
    store.add_course("CSC345", "Data Science")
    lecture = store.add_source("CSC345", "transcript", "Lecture 8")
    for position in range(12):
        store.add_chunk(lecture, position, f"0:{position:02d}:00", f"gut microbiome part {position}", direction(pca=1))
    hits = store.search("gut microbiome", direction(pca=1))
    assert len(hits) == 2
    first, second = sorted(int(hit.locator.split(":")[1]) for hit in hits)
    assert second - first > 1
    store.close()


def test_the_same_page_in_two_files_comes_back_once(path):
    store = CourseStore.create(path)
    store.add_course("CSC345", "Data Science")
    for title in ("Announcement.pdf", "Announcement_with-links.pdf"):
        store.add_chunk(store.add_source("CSC345", "slide", title), 0, "page 1", "Form a project team of 4-5 students", direction(pca=1))
    assert len(store.search("project team", direction(pca=1))) == 1
    store.close()


def test_nothing_close_enough_means_no_results(store):
    assert store.search("best pizza near siam", direction(filler=1)) == []


def test_a_keyword_match_does_not_rescue_an_unrelated_chunk(store):
    # "agent" is in the transcript, but the question is about something else entirely
    barely = direction(agents=MIN_SIMILARITY - 0.05, filler=1)
    assert store.search("agent", barely) == []


def test_keywords_break_a_tie_between_equally_close_chunks(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    for title, text in (("first", "we talk about splitting"), ("second", "the gini index measures purity"), ("third", "more about splitting")):
        store.add_chunk(store.add_source("CSC340", "slide", title), 0, "page 1", text, direction(pca=1))
    assert store.search("what is the gini index", direction(pca=1))[0].title == "second"
    # Stemming: "indexes" and "measure" find "index" and "measures"
    assert store.search("indexes that measure", direction(pca=1))[0].title == "second"
    store.close()


def test_a_keyword_match_outside_the_closest_vectors_is_still_found(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    crowd = store.add_source("CSC340", "transcript", "long lecture")
    for position in range(60):
        store.add_chunk(crowd, position, f"0:{position:02d}:00", "talking about components", direction(pca=1))
    store.add_chunk(store.add_source("CSC340", "slide", "deck"), 0, "page 9", "the mushroom dataset", direction(pca=0.6, other=0.8))
    hits = store.search("mushroom", direction(pca=1))
    assert ("deck", 0.6) in [(hit.title, hit.similarity) for hit in hits]
    store.close()


def test_course_and_kind_narrow_the_search(store):
    everything = direction(pca=1, agents=1, bayes=1)
    assert {hit.course for hit in store.search("lecture", everything)} == {"CSC340", "CSC345"}
    assert {hit.course for hit in store.search("lecture", everything, course="CSC345")} == {"CSC345"}
    assert [hit.title for hit in store.search("lecture", everything, course="CSC340", kind="transcript")] == ["CSC340_20260804"]
    assert store.search("lecture", everything, course="CSC345", kind="slide") == []


def test_since_drops_older_sources(store):
    everything = direction(pca=1, agents=1, bayes=1)
    assert [hit.date for hit in store.search("lecture", everything, since="2026-09-15")] == ["2026-10-06"]


def test_since_is_applied_before_the_candidates_are_counted(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    old = store.add_source("CSC340", "transcript", "old lecture", date="2026-08-04")
    for position in range(120):
        store.add_chunk(old, position, f"{position}", "principal component analysis", direction(pca=1))
    store.add_chunk(store.add_source("CSC340", "slide", "new deck", date="2026-10-06"), 0, "page 1",
                    "principal component analysis again", direction(pca=0.8, other=0.6))
    store.add_chunk(store.add_source("CSC340", "slide", "undated"), 0, "page 1", "principal component analysis", direction(pca=1))
    # 120 older chunks are closer by meaning and by keyword, and the newer one is still found
    assert [hit.title for hit in store.search("principal component analysis", direction(pca=1), since="2026-09-01")] == ["new deck"]
    store.close()


def test_the_limit_caps_the_results(store):
    assert len(store.search("lecture", direction(pca=1, agents=1, bayes=1), limit=2)) == 2


def test_short_terms_and_numbers_count_as_keywords(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    for title, text in (("first", "Assignment 1: search algorithms"), ("second", "Assignment 2: techniques in AI"),
                        ("third", "Assignment 3: model evaluation with F1")):
        store.add_chunk(store.add_source("CSC340", "slide", title), 0, "page 1", text, direction(pca=1))
    assert store.search("what is assignment 2", direction(pca=1))[0].title == "second"
    assert store.search("is F1 in it", direction(pca=1))[0].title == "third"
    store.close()


def test_a_question_with_no_meaningful_words_still_searches_by_meaning(store):
    assert store.search("what is it?", direction(bayes=1))[0].text == "Naive Bayes is simple and pretty good"


def test_a_translated_chunk_keeps_its_original_wording(path):
    store = CourseStore.create(path)
    store.add_course("CSC340", "Artificial Intelligence")
    deck = store.add_source("CSC340", "slide", "8th -- Naive Bayes.pdf")
    store.add_chunk(deck, 0, "page 42", "Example 9: drawing 5 cards from a deck, how many ways?", direction(bayes=1),
                    original="Example 9 : หยิบไพ่ หยิบ 5 ใบ จากกองไพ่ จะมีกี่วิธี")
    hit = store.search("drawing cards", direction(bayes=1))[0]
    assert hit.original == "Example 9 : หยิบไพ่ หยิบ 5 ใบ จากกองไพ่ จะมีกี่วิธี"
    store.close()


def test_bad_input_is_refused(store):
    with pytest.raises(ValueError, match="512 numbers"):
        store.search("pca", [1.0, 0.0])
    with pytest.raises(ValueError, match="unknown kind"):
        store.add_source("CSC340", "video", "a recording")
    with pytest.raises(ValueError, match="no source"):
        store.add_chunk(999, 0, "page 1", "text", direction(pca=1))


def test_the_library_has_nothing_until_a_file_exists(path):
    library = CourseLibrary(path)
    assert library.store() is None


def test_the_library_opens_the_file_and_keeps_it_open(store, path):
    library = CourseLibrary(path)
    opened = library.store()
    assert opened is not None and opened.courses()[0]["code"] == "CSC340"
    assert library.store() is opened


def test_the_library_switches_to_a_file_the_sync_swapped_in(store, path, tmp_path):
    library = CourseLibrary(path)
    first = library.store()
    fresh_path = str(tmp_path / "state" / "courses.db.new")
    fresh = CourseStore.create(fresh_path)
    fresh.add_course("LNG320", "Content-based Language Learning")
    fresh.save()
    fresh.close()
    os.replace(fresh_path, path)
    second = library.store()
    assert second is not first
    assert second is not None and [course["code"] for course in second.courses()] == ["LNG320"]


def test_the_library_reports_a_broken_file_once(path, caplog):
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as file:
        file.write("this is not a database")
    library = CourseLibrary(path)
    assert library.store() is None
    assert library.store() is None
    assert len([record for record in caplog.records if "cannot be read" in record.message]) == 1
