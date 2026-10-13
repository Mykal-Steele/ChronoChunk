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
