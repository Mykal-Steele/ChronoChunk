"""The text of /usage: what one person has left of each allowance, and the AI limits as bars."""
import pytest
from types import SimpleNamespace

from src import usage_context
from src.command_handler import CommandHandler, _bar, _duration
from src.rate_limiter import RateLimiter
from src.usage_guard import get_usage_guard


@pytest.fixture
def handler():
    return CommandHandler(bot=None, rate_limiter=RateLimiter())


def _spend(user_id, dollars):
    """Record a model call costing this much at gpt-5-mini output prices, for one person."""
    usage_context._current.set((user_id, "chat"))
    get_usage_guard().record(SimpleNamespace(prompt_tokens=0, completion_tokens=int(dollars / 2.0 * 1_000_000),
                                             prompt_tokens_details=None))
    usage_context._current.set(("", "chat"))


def _bar_line(text, name):
    """A "- `bar` name ..." line, with the bar first so the bars line up."""
    return next(line for line in text.splitlines() if line.startswith("- `") and line.split("` ", 1)[1].startswith(name))


def _row(text, label):
    """The value of one "- label: value" line."""
    return next(line for line in text.splitlines() if line.startswith(f"- {label}:")).split(": ", 1)[1]


def test_a_fresh_person_has_everything_left(handler):
    text = handler.usage_summary("42")
    # Plain text, not a code block, so bold and lists show (the user asked for this on 2026-11-19)
    assert text.startswith("**ur usage**\n**left right now**\n") and "```" not in text
    assert _row(text, "messages") == "50 of 50, per 30 min"
    assert _row(text, "pdfs read") == "30 of 30, per day"
    assert _row(text, "files made") == "30 of 30, per day"
    assert _row(text, "web lookups") == "20 of 20, per day"
    assert _row(text, "course asks") == "120 of 120, per day"
    assert _row(text, "recaps") == "6 of 6, per hour"
    assert _bar_line(text, "today").startswith("- `░░░░░░░░░░   0%` today, resets in")
    assert _bar_line(text, "bot this month, resets ")


def test_counts_go_down_as_things_are_used(handler):
    for _ in range(3):
        handler.rate_limiter.check_rate_limit("42", "pdf")
    handler.rate_limiter.check_rate_limit("42", "web")
    text = handler.usage_summary("42")
    assert _row(text, "pdfs read").startswith("27 of 30")
    assert _row(text, "web lookups").startswith("19 of 20")
    assert _row(handler.usage_summary("43"), "pdfs read").startswith("30 of 30")  # someone else


def test_an_empty_allowance_says_when_more_comes_back(handler):
    for _ in range(6):
        handler.rate_limiter.check_rate_limit("42", "tldr")
    row = _row(handler.usage_summary("42"), "recaps")
    assert row.startswith("0 of 6") and "more in 1h 0m" in row


def test_the_bars_show_their_own_spending(handler):
    _spend("42", 0.10)  # a fifth of the $0.50 day, a fifteenth of the $1.50 week
    _spend("99", 0.15)
    text = handler.usage_summary("42")
    assert _bar_line(text, "today").startswith("- `▓▓░░░░░░░░  20%` today")
    assert _bar_line(text, "7 days") == "- `▓░░░░░░░░░   7%` 7 days"
    assert _bar_line(text, "bot").startswith("- `▓░░░░░░░░░   1%` bot")  # $0.25 of the bot's $20, everyone together


def test_the_bars_line_up(handler):
    # Discord's font is not monospaced, so each bar starts the line in a code span of the same width
    text = handler.usage_summary("42", allowance=ALLOWANCE)
    bars = [line for line in text.splitlines() if line.startswith("- `")]
    assert len(bars) == 4
    assert len({line.index("` ", 3) for line in bars}) == 1


def test_someone_with_no_limit_is_told_so(handler, monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"42"})
    text = handler.usage_summary("42")
    assert "no limit for u" in text and "7 days" not in text


def test_bars_and_durations():
    # The percentage is padded to 4 characters so 0% and 100% take the same room
    assert _bar(0, 1) == "░░░░░░░░░░   0%"
    assert _bar(0.001, 1) == "▓░░░░░░░░░   0%"   # any use shows
    assert _bar(2, 1) == "▓▓▓▓▓▓▓▓▓▓ 100%"
    assert _duration(30) == "1m" and _duration(3600) == "1h 0m" and _duration(22200) == "6h 10m"


# ── the Unicorp Gemini allowance that Uniloom replies are paid from ──────────

ALLOWANCE = {"limitUsd": 40, "spentUsd": 10.5, "reservedUsd": 0.25, "remainingUsd": 29.25, "paused": False}


def test_the_uniloom_allowance_shows_what_is_used_and_left(handler):
    line = _bar_line(handler.usage_summary("42", allowance=ALLOWANCE), "uniloom")
    # Money held for a call still running counts as used, since it is not free to spend yet
    assert line == "- `▓▓▓░░░░░░░  27%` uniloom, $29.25 left of $40"


def test_a_used_up_allowance_says_uniloom_is_on_the_bots_own_model(handler):
    spent = {**ALLOWANCE, "spentUsd": 40, "reservedUsd": 0, "remainingUsd": 0}
    line = _bar_line(handler.usage_summary("42", allowance=spent), "uniloom")
    assert line.startswith("- `▓▓▓▓▓▓▓▓▓▓ 100%` uniloom, $0.00 left of $40") and "bot's own" in line


def test_a_paused_allowance_says_so(handler):
    assert "paused" in _bar_line(handler.usage_summary("42", allowance={**ALLOWANCE, "paused": True}), "uniloom")


def test_when_the_proxy_cannot_be_reached_the_row_says_so(handler):
    assert _row(handler.usage_summary("42", allowance={}), "uniloom") == "couldnt check right now"


def test_without_the_proxy_set_up_there_is_no_uniloom_row(handler):
    assert "uniloom" not in handler.usage_summary("42")
