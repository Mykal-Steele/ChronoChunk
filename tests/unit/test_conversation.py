"""Unit tests for who is talking to the bot in a channel, and when it may join in uncalled."""
from datetime import datetime, timezone

import pytest

from config.config import Config
from src.conversation import (
    MAX_UNCALLED_REPLIES_PER_CALL, MAX_WEIGHED_PER_CALL, SESSION_GAP_SECONDS, Conversation, stays_out,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def conversation(clock):
    return Conversation(clock)


def test_one_person_is_not_a_crowd(conversation):
    conversation.called("c", "alex")
    conversation.called("c", "alex")
    assert not conversation.is_crowd("c")


def test_two_people_calling_within_a_minute_are_a_crowd(conversation, clock):
    conversation.called("c", "alex")
    clock.now += 20
    conversation.called("c", "sam")
    assert conversation.is_crowd("c")
    assert not conversation.is_crowd("another channel")


def test_a_crowd_ends_when_one_of_them_has_been_quiet_for_a_minute(conversation, clock):
    conversation.called("c", "alex")
    clock.now += 50
    conversation.called("c", "sam")
    clock.now += 20
    assert not conversation.is_crowd("c")


def test_the_bot_may_join_in_right_after_a_call_and_not_before_one(conversation):
    assert not conversation.may_join_in("c")
    conversation.called("c", "alex")
    assert conversation.may_join_in("c")
    assert not conversation.may_join_in("another channel")


def test_joining_in_ends_when_the_window_has_passed(conversation, clock):
    conversation.called("c", "alex")
    clock.now += Config.JOIN_IN_SECONDS
    assert conversation.may_join_in("c")
    clock.now += 1
    assert not conversation.may_join_in("c")


def test_only_one_uncalled_message_is_weighed_at_a_time(conversation):
    conversation.called("c", "alex")
    conversation.start_deciding("c")
    assert not conversation.may_join_in("c")
    conversation.done_deciding("c", answered=False)
    assert conversation.may_join_in("c")


def test_one_call_allows_a_fixed_number_of_uncalled_answers(conversation):
    conversation.called("c", "alex")
    for _ in range(MAX_UNCALLED_REPLIES_PER_CALL):
        assert conversation.may_join_in("c")
        conversation.start_deciding("c")
        conversation.done_deciding("c", answered=True)
    assert not conversation.may_join_in("c")


def test_one_call_allows_a_fixed_number_of_messages_to_be_weighed(conversation):
    conversation.called("c", "alex")
    for _ in range(MAX_WEIGHED_PER_CALL):
        conversation.start_deciding("c")
        conversation.done_deciding("c", answered=False)
    assert not conversation.may_join_in("c")


def test_a_new_call_starts_the_count_again(conversation):
    conversation.called("c", "alex")
    for _ in range(MAX_WEIGHED_PER_CALL):
        conversation.start_deciding("c")
        conversation.done_deciding("c", answered=True)
    conversation.called("c", "alex")
    assert conversation.may_join_in("c")


def test_joining_in_can_be_switched_off(conversation, monkeypatch):
    monkeypatch.setattr(Config, "JOIN_IN_SECONDS", 0)
    conversation.called("c", "alex")
    assert not conversation.may_join_in("c")


@pytest.mark.parametrize("text", ["SKIP", "skip", " Skip. ", '"SKIP"', "*SKIP*", "", "   ", "SKIP not for me"])
def test_the_models_way_of_staying_out_is_recognized(text):
    assert stays_out(text)


@pytest.mark.parametrize("text", ["lmao what", "skipping class again?", "nah thats wild", "u should skip it"])
def test_a_real_answer_is_not_mistaken_for_staying_out(text):
    assert not stays_out(text)


def test_a_conversation_begins_with_the_first_call_and_runs_while_calls_keep_coming(conversation, clock):
    first = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert conversation.session_start("c") is None
    conversation.called("c", "alex", first)
    clock.now += 100
    conversation.called("c", "sam", datetime(2026, 10, 12, 12, 1, 40, tzinfo=timezone.utc))
    assert conversation.session_start("c") == first


def test_a_call_after_a_long_quiet_begins_a_new_conversation(conversation, clock):
    conversation.called("c", "alex", datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc))
    clock.now += SESSION_GAP_SECONDS + 1
    assert conversation.session_start("c") is None
    later = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)
    conversation.called("c", "alex", later)
    assert conversation.session_start("c") == later
