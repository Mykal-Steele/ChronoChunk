"""A course session: after a flagged message, what was looked up stays with the person for a while."""
import pytest

from config.config import Config
from src.course_session import MAX_MATERIAL_CHARS, CourseSessions


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sessions(clock):
    return CourseSessions(clock)


def test_there_is_no_session_until_a_flag_opens_one(sessions):
    assert sessions.get("channel:alex") is None
    sessions.open("channel:alex", ["CSC340"])
    session = sessions.get("channel:alex")
    assert session.codes == ["CSC340"] and session.material == []
    # Someone else in the channel, and the same person elsewhere, have none
    assert sessions.get("channel:sam") is None and sessions.get("other:alex") is None


def test_a_session_lasts_an_hour_from_the_last_flag(sessions, clock):
    assert Config.COURSE_SESSION_SECONDS == 3600
    sessions.open("k", ["CSC340"])
    clock.now += 3600
    assert sessions.get("k") is not None
    clock.now += 1
    assert sessions.get("k") is None


def test_chatting_does_not_extend_a_session_and_a_new_flag_does(sessions, clock):
    sessions.open("k", ["CSC340"])
    clock.now += 3000
    sessions.add("k", "block one")
    assert sessions.get("k") is not None
    clock.now += 601
    assert sessions.get("k") is None

    sessions.open("k", ["CSC340"])
    clock.now += 3000
    sessions.open("k", ["CSC340"])
    clock.now += 3000
    assert sessions.get("k") is not None


def test_what_was_looked_up_is_kept_in_order(sessions):
    sessions.open("k", ["CSC340"])
    sessions.add("k", "first lookup")
    sessions.add("k", "second lookup")
    sessions.add("k", "first lookup")
    # The same lookup twice is one piece of material
    assert sessions.get("k").material == ["first lookup", "second lookup"]


def test_a_new_flag_for_another_course_keeps_what_was_looked_up(sessions):
    sessions.open("k", ["CSC340"])
    sessions.add("k", "ai material")
    sessions.open("k", ["CSC345"])
    session = sessions.get("k")
    assert session.codes == ["CSC345"] and session.material == ["ai material"]


def test_the_oldest_material_goes_when_there_is_too_much(sessions):
    sessions.open("k", [])
    piece = "x" * (MAX_MATERIAL_CHARS // 2 - 10)
    sessions.add("k", "old " + piece)
    sessions.add("k", "mid " + piece)
    sessions.add("k", "new " + piece)
    kept = sessions.get("k").material
    assert [block[:3] for block in kept] == ["mid", "new"]
    # One lookup bigger than the limit is still kept, since it is what was just asked for
    sessions.add("k", "big " + "y" * (MAX_MATERIAL_CHARS * 2))
    assert [block[:3] for block in sessions.get("k").material] == ["big"]


def test_nothing_is_added_without_a_session(sessions):
    sessions.add("k", "block")
    assert sessions.get("k") is None
