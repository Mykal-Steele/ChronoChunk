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


def _row(text, label):
    return next(line for line in text.splitlines() if line.startswith(label))


def test_a_fresh_person_has_everything_left(handler):
    text = handler.usage_summary("42")
    assert text.startswith("**ur usage**\n```\nleft right now\n") and text.endswith("```")
    assert _row(text, "messages").split() == ["messages", "50", "of", "50", "per", "30", "min"]
    assert _row(text, "pdfs read").split()[2:] == ["10", "of", "10", "per", "day"]
    assert _row(text, "files made").split()[2:] == ["15", "of", "15", "per", "day"]
    assert _row(text, "web lookups").split()[2:] == ["20", "of", "20", "per", "day"]
    assert _row(text, "recaps").split()[1:] == ["6", "of", "6", "per", "hour"]
    assert "░░░░░░░░░░   0%" in _row(text, "today") and "resets in" in _row(text, "today")
    assert _row(text, "bot").endswith("this month, resets " + _row(text, "bot").split("resets ")[1])


def test_counts_go_down_as_things_are_used(handler):
    for _ in range(3):
        handler.rate_limiter.check_rate_limit("42", "pdf")
    handler.rate_limiter.check_rate_limit("42", "web")
    text = handler.usage_summary("42")
    assert _row(text, "pdfs read").split()[2:5] == ["7", "of", "10"]
    assert _row(text, "web lookups").split()[2:5] == ["19", "of", "20"]
    assert _row(handler.usage_summary("43"), "pdfs read").split()[2:5] == ["10", "of", "10"]  # someone else


def test_an_empty_allowance_says_when_more_comes_back(handler):
    for _ in range(6):
        handler.rate_limiter.check_rate_limit("42", "tldr")
    row = _row(handler.usage_summary("42"), "recaps")
    assert row.split()[1:4] == ["0", "of", "6"] and "more in 1h 0m" in row


def test_the_bars_show_their_own_spending(handler):
    _spend("42", 0.10)  # half of the $0.20 day, a sixth of the $0.60 week
    _spend("99", 0.15)
    text = handler.usage_summary("42")
    assert "▓▓▓▓▓░░░░░  50%" in _row(text, "today")
    assert "▓▓░░░░░░░░  17%" in _row(text, "7 days")
    assert "▓░░░░░░░░░   2%" in _row(text, "bot")  # $0.25 of the bot's $15, everyone together


def test_someone_with_no_limit_is_told_so(handler, monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"42"})
    text = handler.usage_summary("42")
    assert "no limit for u" in text and "7 days" not in text


def test_bars_and_durations():
    assert _bar(0, 1) == "░░░░░░░░░░   0%"
    assert _bar(0.001, 1) == "▓░░░░░░░░░   0%"   # any use shows
    assert _bar(2, 1) == "▓▓▓▓▓▓▓▓▓▓ 100%"
    assert _duration(30) == "1m" and _duration(3600) == "1h 0m" and _duration(22200) == "6h 10m"
