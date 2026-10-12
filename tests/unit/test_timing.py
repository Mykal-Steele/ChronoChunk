"""The stopwatch that times the steps of one answer."""
import re
from datetime import datetime, timedelta, timezone

from src.timing import Stopwatch


def test_steps_are_listed_in_order_with_what_is_left_over():
    watch = Stopwatch()
    watch.started -= 5
    watch.steps = {"history": 0.5, "model and tools": 2.0}
    assert re.fullmatch(r"5\.0\ds: history 0\.50s, model and tools 2\.00s, other 2\.5\ds", watch.summary())


def test_a_step_used_twice_adds_up():
    watch = Stopwatch()
    watch.steps["context"] = 1.0
    with watch.step("context"):
        pass
    assert list(watch.steps) == ["context"]
    assert 1.0 <= watch.steps["context"] < 1.5


def test_a_step_that_fails_is_still_timed():
    watch = Stopwatch()
    try:
        with watch.step("send"):
            raise RuntimeError("discord said no")
    except RuntimeError:
        pass
    assert "send" in watch.steps


def test_the_summary_says_how_long_the_message_took_to_arrive():
    watch = Stopwatch(datetime.now(timezone.utc) - timedelta(seconds=2))
    assert re.search(r"picked up 2\.\d\ds after it was sent$", watch.summary())


def test_a_message_dated_in_the_future_counts_as_no_delay():
    # The bot's clock can run a little behind Discord's
    watch = Stopwatch(datetime.now(timezone.utc) + timedelta(seconds=5))
    assert watch.summary().endswith("picked up 0.00s after it was sent")


def test_without_a_send_time_the_summary_leaves_it_out():
    assert "picked up" not in Stopwatch().summary()
    assert "picked up" not in Stopwatch("not a date").summary()
