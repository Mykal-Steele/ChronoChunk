"""The dated list of assignments: which ones a question is about, and how far off each is from right now."""
from datetime import datetime

from src.course_retrieval import BANGKOK, assignment_rows


def _at(hour, minute=0, day=13):
    return datetime(2026, 10, day, hour, minute, tzinfo=BANGKOK)


def _item(title, due, points=None, text=""):
    return {"course": "CSC345", "title": title, "date": due[:10], "url": "", "text": text or title,
            "due": due if "T" in due else "", "meta": {"points": points} if points else {}}


MORNING = _item("Quiz", "2026-10-13T09:00:00+07:00")
NIGHT = _item("Lab 7", "2026-10-13T23:59:00+07:00", points=5)


def test_what_is_due_later_today_says_how_long_is_left():
    lines, count = assignment_rows([MORNING, NIGHT], _at(15))
    # The quiz at nine is over, even though it is still the same day
    assert lines == ['- CSC345 "Lab 7", due today (13 Oct 2026) at 23:59, 9 hours left, 5 points', "1 past one is not listed"]
    assert count == 1
    assert "29 minutes left" in assignment_rows([NIGHT], _at(23, 30))[0][0]
    assert "1 hour left" in assignment_rows([NIGHT], _at(22, 50))[0][0]


def test_what_was_due_earlier_today_says_how_long_ago():
    lines, _ = assignment_rows([MORNING, NIGHT], _at(15), everything=True)
    assert lines == ['- CSC345 "Lab 7", due today (13 Oct 2026) at 23:59, 9 hours left, 5 points',
                     '- CSC345 "Quiz", was due today (13 Oct 2026) at 09:00, 6 hours ago']


def test_another_day_is_counted_in_days_with_its_time():
    lines, _ = assignment_rows([NIGHT], _at(8, day=15), everything=True)
    assert lines == ['- CSC345 "Lab 7", was due 2 days ago (13 Oct 2026) at 23:59, 5 points']


def test_an_assignment_from_an_older_course_file_is_read_from_its_text():
    old = {"course": "CSC340", "title": "Mini project", "date": "2026-11-16", "url": "", "due": "", "meta": {},
           "text": "Mini project\nDue 2026-11-16 23:59 Bangkok time\n10 points\nBuild an agent."}
    lines, _ = assignment_rows([old], _at(15))
    assert lines == ['- CSC340 "Mini project", due in 34 days (16 Nov 2026) at 23:59, 10 points']


# ── the source line is written by code, from the numbers the model gives ───────

from src.course_retrieval import format_hits, render_sources, source_label  # noqa: E402
from src.course_store import Hit  # noqa: E402


def _hit(kind="slide", title="CBLL Week 9.pdf", locator="page 47", day="2026-10-09", url="https://example.test/w9.pdf",
         course="LNG320"):
    return Hit(course=course, kind=kind, title=title, section="Week 9", locator=locator, url=url, date=day,
               text="Team homework: write a draft introduction", original=None, before="", after="", similarity=0.6)


SOURCES = {"2": ("CBLL Week 9, page 47 (9 Oct 2026)", "https://example.test/w9.pdf"),
           "3": ("LNG320 lecture 2 Oct 2026, 1:51:48", "https://example.test/w8.mp4"),
           "A": ("teams assignments: none posted for LNG320", "")}


def test_a_source_says_what_it_is_where_in_it_and_from_which_day():
    assert source_label(_hit()) == "CBLL Week 9, page 47 (9 Oct 2026)"
    assert source_label(_hit(kind="transcript", title="Week 8 class", locator="1:51:48", day="2026-10-02")) == \
        "LNG320 lecture 2 Oct 2026, 1:51:48"
    assert source_label(_hit(kind="assignment", title="Lab 7", locator="", day="2026-10-13", course="CSC345")) == \
        "Lab 7 (assignment, due 13 Oct 2026)"
    # No date known, and a title with brackets, which would break the link
    assert source_label(_hit(title="Notes [draft].pdf", day="")) == "Notes (draft), page 47"


def test_the_numbers_the_model_gives_become_named_links():
    reply = "write a draft introduction before next class\n-# Source: [2] [3]"
    assert render_sources(reply, SOURCES) == (
        "write a draft introduction before next class\n"
        "-# Source: [CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>), "
        "[LNG320 lecture 2 Oct 2026, 1:51:48](<https://example.test/w8.mp4>)")


def test_the_assignment_list_is_named_without_a_link():
    assert render_sources("nothing is posted\n-# Source: [A]", SOURCES) == \
        "nothing is posted\n-# Source: teams assignments: none posted for LNG320"
    assert "(<>)" not in render_sources("x\n-# Source: [A] [2]", SOURCES)


def test_a_number_that_is_no_source_is_dropped_and_a_line_with_none_left_goes():
    assert render_sources("x\n-# Source: [2] [9]", SOURCES) == \
        "x\n-# Source: [CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>)"
    assert render_sources("couldnt find that\n-# Source: [9]", SOURCES) == "couldnt find that"
    assert render_sources("couldnt find that\n-# Source: [0]", SOURCES) == "couldnt find that"


def test_a_source_named_twice_is_listed_once_and_numbers_in_the_answer_are_taken_out():
    reply = "the homework is a draft introduction [2]. it is a team task [2][3].\n-# Source: [2], [3] and [2]"
    assert render_sources(reply, SOURCES) == (
        "the homework is a draft introduction. it is a team task.\n"
        "-# Source: [CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>), "
        "[LNG320 lecture 2 Oct 2026, 1:51:48](<https://example.test/w8.mp4>)")


def test_a_reply_with_no_source_line_is_left_alone():
    assert render_sources("pasta, probably", SOURCES) == "pasta, probably"
    assert render_sources("use list[2] to get the third item", {}) == "use list[2] to get the third item"


def test_passages_can_be_numbered_on_from_an_earlier_lookup():
    text = format_hits("homework", [_hit(), _hit(locator="page 48")], {"LNG320": "Content-based Language Learning"}, start=4)
    assert "[4] LNG320 Content-based Language Learning" in text and "[5] LNG320" in text and "[1]" not in text


def test_code_in_an_answer_keeps_its_brackets():
    # The check on the real model caught this: "new int[3][]" came out as "new int[]", since 3 was a passage number
    reply = ("declare it like this [2]:\n```java\nint[][] rows = new int[3][];\nrows[1] = new int[2];\n```\n"
             "or inline: `rows[2] = new int[3]` works too [3].\n-# Source: [2] [3]")
    shown = render_sources(reply, SOURCES)
    assert "int[][] rows = new int[3][];\nrows[1] = new int[2];" in shown
    assert "`rows[2] = new int[3]`" in shown
    assert "declare it like this:" in shown and "works too." in shown
    # A number glued to a word is an index, not a passage
    assert render_sources("take list[2] and matrix[3]\n-# Source: [2]", SOURCES).startswith("take list[2] and matrix[3]\n")


def test_a_score_only_says_strong_when_it_is_high_enough_to_mean_it():
    from src.course_retrieval import label_of
    # Measured on 300 passages a judge read: from 0.55 up, 3 in 48 were off the topic. Under it, about 1 in 3.
    assert label_of([_hit()]) == "strong"
    weak = _hit()
    weak.similarity = 0.54
    assert label_of([weak]) == "unsure" and label_of([]) == "nothing"
    # A weaker score is no reason to tell the model its passages are off: a third of them answer the question
    assert "loosely" not in format_hits("homework", [weak], {})


def test_a_source_line_written_at_the_end_of_a_sentence_is_still_filled_in():
    # Seen on the real model: "...gives no average. -# Source: [2] [7]", all on one line, went out as it was
    shown = render_sources("couldnt find the average. -# Source: [2] [9]", SOURCES)
    assert shown == "couldnt find the average.\n-# Source: [CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>)"
    assert render_sources("couldnt find it. -# Source: [9]", SOURCES) == "couldnt find it."
    assert render_sources("x\n-# source: [2]", SOURCES).endswith("(<https://example.test/w9.pdf>)")


def test_a_source_line_in_the_middle_of_a_reply_joins_the_last_one():
    reply = "the first part\n-# Source: [3]\n\nmore of it\n-# Source: [2]"
    assert render_sources(reply, SOURCES) == (
        "the first part\n\nmore of it\n"
        "-# Source: [LNG320 lecture 2 Oct 2026, 1:51:48](<https://example.test/w8.mp4>), "
        "[CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>)")


def test_an_empty_source_line_in_the_middle_of_a_reply_goes():
    assert render_sources("the first part -# Source: \n\nmore of it\n-# Source: [2]", SOURCES) == (
        "the first part\n\nmore of it\n-# Source: [CBLL Week 9, page 47 (9 Oct 2026)](<https://example.test/w9.pdf>)")


def test_a_source_line_inside_code_is_part_of_the_code():
    reply = "an example:\n```\n-# Source: [2]\n```\nthats it\n-# Source: [3]"
    assert render_sources(reply, SOURCES) == (
        "an example:\n```\n-# Source: [2]\n```\nthats it\n"
        "-# Source: [LNG320 lecture 2 Oct 2026, 1:51:48](<https://example.test/w8.mp4>)")


def test_a_question_that_names_its_day_is_not_about_the_latest():
    from src.course_retrieval import about_latest
    assert about_latest("what did the latest lecture cover")
    assert not about_latest("what did the latest lecture on 2026-10-12 cover")
    assert not about_latest("what did the last lecture on oct 6 cover")


def test_a_long_source_line_is_split_between_links_not_through_one():
    from src.course_retrieval import SOURCE_LINE_CHARS
    named = {str(number): (f"CBLL lecture {number} Oct 2026, 0:0{number}:00", f"https://example.test/lecture-{number}.mp4")
             for number in range(1, 40)}
    reply = "an answer\n-# Source: " + " ".join(f"[{number}]" for number in named)
    shown = render_sources(reply, named)
    lines = [line for line in shown.splitlines() if line.startswith("-# Source: ")]
    assert len(lines) > 1
    assert all(len(line) <= SOURCE_LINE_CHARS + len("-# Source: ") for line in lines)
    assert all(line.count("](") == line.count("(<https://") for line in lines)
    assert "[CBLL lecture 1 Oct 2026" in lines[0] and "[CBLL lecture 39 Oct 2026" in lines[-1]
