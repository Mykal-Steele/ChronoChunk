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
    assert _row(text, "today").startswith("░░░░░░░░░░ 0%, resets in")
    assert "this month, resets " in _row(text, "bot")


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
    assert "▓▓░░░░░░░░ 20%" in _row(text, "today")
    assert "▓░░░░░░░░░ 7%" in _row(text, "7 days")
    assert "▓░░░░░░░░░ 1%" in _row(text, "bot")  # $0.25 of the bot's $20, everyone together


def test_someone_with_no_limit_is_told_so(handler, monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"42"})
    text = handler.usage_summary("42")
    assert "no limit for u" in text and "7 days" not in text


def test_bars_and_durations():
    assert _bar(0, 1) == "░░░░░░░░░░ 0%"
    assert _bar(0.001, 1) == "▓░░░░░░░░░ 0%"   # any use shows
    assert _bar(2, 1) == "▓▓▓▓▓▓▓▓▓▓ 100%"
    assert _duration(30) == "1m" and _duration(3600) == "1h 0m" and _duration(22200) == "6h 10m"
